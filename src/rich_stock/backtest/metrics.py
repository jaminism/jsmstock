"""백테스트 성과지표: 승률, 손익비, MDD, 샤프비율, CAGR."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from rich_stock.backtest.portfolio import PortfolioResult
from rich_stock.strategies.base import Trade

_TRADING_DAYS_PER_YEAR = 252


@dataclass
class TradeStats:
    """자본/동시보유 제약과 무관하게, 트레이드 표본 자체의 품질을 보는 지표.

    포트폴리오 자산곡선이 필요 없어 '자본 제약 없이 모든 신호를 다 받았다면'을 근사하는 데 쓴다.
    """

    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    avg_return_pct: float
    profit_factor: float  # 총이익 / |총손실|
    avg_win_loss_ratio: float  # 평균이익 / |평균손실| (손익비)
    exit_reason_counts: dict[str, int]


def compute_trade_stats(trades: list[Trade]) -> TradeStats:
    closed = [t for t in trades if t.entry_date is not None and t.is_closed]
    returns = [t.return_pct for t in closed]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    pnls = [t.pnl for t in closed]
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(p for p in pnls if p < 0)

    exit_reason_counts: dict[str, int] = {}
    for t in closed:
        reason = t.exit_reason or "unknown"
        exit_reason_counts[reason] = exit_reason_counts.get(reason, 0) + 1

    return TradeStats(
        n_trades=len(closed),
        n_wins=len(wins),
        n_losses=len(losses),
        win_rate=len(wins) / len(closed) if closed else 0.0,
        avg_return_pct=float(np.mean(returns)) * 100 if returns else 0.0,
        profit_factor=(gross_profit / abs(gross_loss)) if gross_loss else float("inf") if gross_profit else 0.0,
        avg_win_loss_ratio=(
            (np.mean(wins) / abs(np.mean(losses))) if wins and losses else float("inf") if wins else 0.0
        ),
        exit_reason_counts=exit_reason_counts,
    )


@dataclass
class Metrics:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    avg_return_pct: float
    profit_factor: float  # 총이익 / |총손실|
    avg_win_loss_ratio: float  # 평균이익 / |평균손실| (손익비)
    max_drawdown_pct: float
    """직전 고점 대비 최대낙폭(%). **이 값만 보고 위험을 판단하면 안 된다** — 포지션 크기가
    `position_size_pct x initial_capital_krw`로 **고정**이라(portfolio.py의 scale, 라이브
    compute_share_quantity와 동일), 자기자본이 불어나도 베팅 크기는 그대로다. 그러면 같은
    금액의 손실이 뒤로 갈수록 작은 비율로 찍혀 **낙폭이 기계적으로 축소된다.**
    실측(2026-09-05, S5 슬리피지 2%): 첫 1년 -6.69%(자본 3.77배) → 전체 5.7년 -2.33%(자본
    17.54배). 전략이 안전해진 게 아니라 분모가 커진 것이다. 아래 max_drawdown_vs_initial_pct를
    같이 볼 것."""

    max_drawdown_vs_initial_pct: float
    """최대낙폭을 **초기자본**으로 나눈 값(%). 분모가 고정이라 기간이 길어져도 희석되지 않아,
    "고정 베팅 전략이 최악의 순간에 원금의 몇 %를 잃었나"를 그대로 보여준다(2026-09-05 추가).
    포지션 크기가 원금 기준으로 고정된 이 시스템에서는 이쪽이 실질적인 위험 척도다."""
    sharpe_ratio: float
    cagr_pct: float
    n_skipped_capital_limit: int
    exit_reason_counts: dict[str, int]
    signal_level: TradeStats  # 자본 제약 없이 전체 신호(트레이드)를 다 받았다고 가정했을 때의 지표


def compute_metrics(result: PortfolioResult, all_trades: list[Trade] | None = None) -> Metrics:
    accepted_trades = [acc.trade for acc in result.accepted]
    accepted_stats = compute_trade_stats(accepted_trades)
    signal_level = compute_trade_stats(all_trades) if all_trades is not None else accepted_stats

    equity = result.equity_curve
    max_dd = _max_drawdown(equity) if not equity.empty else 0.0
    max_dd_init = _max_drawdown_vs_initial(equity) if not equity.empty else 0.0
    sharpe = _sharpe_ratio(equity) if not equity.empty else 0.0
    cagr = _cagr(equity) if not equity.empty else 0.0

    return Metrics(
        n_trades=accepted_stats.n_trades,
        n_wins=accepted_stats.n_wins,
        n_losses=accepted_stats.n_losses,
        win_rate=accepted_stats.win_rate,
        avg_return_pct=accepted_stats.avg_return_pct,
        profit_factor=accepted_stats.profit_factor,
        avg_win_loss_ratio=accepted_stats.avg_win_loss_ratio,
        max_drawdown_pct=max_dd * 100,
        max_drawdown_vs_initial_pct=max_dd_init * 100,
        sharpe_ratio=sharpe,
        cagr_pct=cagr * 100,
        n_skipped_capital_limit=len(result.skipped),
        exit_reason_counts=accepted_stats.exit_reason_counts,
        signal_level=signal_level,
    )


def _max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min())


def _max_drawdown_vs_initial(equity: pd.Series) -> float:
    """낙폭을 초기자본으로 나눈다 — 분모가 고정이라 자본이 불어나도 희석되지 않는다.

    `_max_drawdown`은 직전 고점을 분모로 쓰는 표준 정의라, **베팅 크기가 고정인 이 시스템**
    에서는 자본이 커질수록 같은 손실이 작아 보인다(S5에서 자본 17배가 되며 -6.69% → -2.33%로
    줄었다). 원금 대비로 보면 그 착시가 사라진다."""
    running_max = equity.cummax()
    return float((equity - running_max).min() / equity.iloc[0])


def _sharpe_ratio(equity: pd.Series, risk_free: float = 0.0) -> float:
    daily_returns = equity.pct_change().dropna()
    if daily_returns.std() == 0 or daily_returns.empty:
        return 0.0
    excess = daily_returns - risk_free / _TRADING_DAYS_PER_YEAR
    return float(np.sqrt(_TRADING_DAYS_PER_YEAR) * excess.mean() / daily_returns.std())


def _cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    n_days = (equity.index[-1] - equity.index[0]).days
    if n_days <= 0:
        return 0.0
    years = n_days / 365.25
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)
