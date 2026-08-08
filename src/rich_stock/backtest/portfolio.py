"""개별 종목 SR 트레이드를 자본/동시보유종목 제약 하에서 포트폴리오로 배분한다.

단순화 가정 (v1, 문서화된 한계):
  1. 자본 배분은 초기자본(initial_capital) 기준 고정 비율이며 복리(equity 기반 재투자)로
     하지 않는다 — 사이징 로직을 결정적(deterministic)으로 유지해 여러 종목의 진입 순서가
     자본 배분에 순환적으로 영향을 주지 않게 하기 위함.
  2. 트레이드 수락 여부는 진입 시점에 "최대 노출(초기진입+추매)"을 미리 전액 예약해 결정한다.
     실제로 추매가 발생하지 않는 트레이드는 예약된 자본의 일부만 쓰지만, 이 단순화로 인해
     일부 트레이드가 실제로는 자리가 있었음에도 "자본부족/슬롯부족"으로 스킵될 수 있다(보수적).
  3. 동일 날짜에 여러 트레이드가 마감/신규진입이 겹치는 경우, 마감(청산)을 먼저 반영해
     자리를 비운 뒤 신규 진입을 받는다.
  4. 자산가치(equity)는 일별 종가 기준 마크투마켓이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from rich_stock.config import PortfolioConfig
from rich_stock.strategies.base import Trade


@dataclass
class AcceptedTrade:
    trade: Trade
    scale: float  # k = position_size_pct * initial_capital / entry_price


@dataclass
class PortfolioResult:
    accepted: list[AcceptedTrade] = field(default_factory=list)
    skipped: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    cash_curve: pd.Series = field(default_factory=pd.Series)


def allocate_portfolio(
    trades: list[Trade],
    ohlcv: dict[str, pd.DataFrame],
    config: PortfolioConfig,
) -> PortfolioResult:
    closed_trades = [t for t in trades if t.entry_date is not None and t.is_closed]
    closed_trades.sort(key=lambda t: t.entry_date)

    reserved_cash = config.max_position_pct * config.initial_capital_krw

    accepted: list[AcceptedTrade] = []
    skipped: list[Trade] = []
    active: list[tuple[pd.Timestamp, float]] = []  # (exit_date, reserved_amount)
    total_reserved = 0.0

    for trade in closed_trades:
        active = [(d, amt) for d, amt in active if d >= trade.entry_date]
        total_reserved = sum(amt for _, amt in active)

        if len(active) >= config.max_concurrent_positions:
            skipped.append(trade)
            continue
        if total_reserved + reserved_cash > config.initial_capital_krw:
            skipped.append(trade)
            continue

        entry_price = trade.fills[0].price
        scale = (config.position_size_pct * config.initial_capital_krw) / entry_price
        accepted.append(AcceptedTrade(trade=trade, scale=scale))
        active.append((trade.exit_date, reserved_cash))

    equity_curve, cash_curve = _build_curves(accepted, ohlcv, config)
    return PortfolioResult(accepted=accepted, skipped=skipped, equity_curve=equity_curve, cash_curve=cash_curve)


def _build_curves(
    accepted: list[AcceptedTrade],
    ohlcv: dict[str, pd.DataFrame],
    config: PortfolioConfig,
) -> tuple[pd.Series, pd.Series]:
    if not accepted:
        empty = pd.Series(dtype=float)
        return empty, empty

    master_index = sorted(set().union(*(df.index for df in ohlcv.values())))
    master_index = pd.DatetimeIndex(master_index)

    positions_value = pd.Series(0.0, index=master_index)
    cash_flows = pd.Series(0.0, index=master_index)

    for acc in accepted:
        trade, k = acc.trade, acc.scale
        ticker_df = ohlcv[trade.ticker]
        entry_date, exit_date = trade.entry_date, trade.exit_date
        date_range = ticker_df.index[(ticker_df.index >= entry_date) & (ticker_df.index <= exit_date)]

        fill_shares = pd.Series({f.date: f.shares for f in trade.fills})
        fill_shares = fill_shares.groupby(level=0).sum()
        cum_shares = fill_shares.reindex(date_range).fillna(0).cumsum() * k
        value = cum_shares * ticker_df.loc[date_range, "Close"]
        positions_value = positions_value.add(value.reindex(master_index).fillna(0), fill_value=0)

        for f in trade.fills:
            cash_flows.loc[f.date] += -(f.price * f.shares) * k

    cash_curve = cash_flows.cumsum() + config.initial_capital_krw
    equity_curve = cash_curve + positions_value
    return equity_curve, cash_curve
