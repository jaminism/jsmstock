"""백테스트 엔진 진입점 (기법 공용)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from rich_stock.backtest.metrics import Metrics, compute_metrics
from rich_stock.backtest.portfolio import PortfolioResult, allocate_portfolio
from rich_stock.config import K1Config, K1PlusConfig, K2Config, K2PlusConfig, PortfolioConfig, SRConfig
from rich_stock.strategies.base import Trade
from rich_stock.strategies.k1 import backtest_ticker as k1_backtest_ticker
from rich_stock.strategies.k2 import backtest_ticker as k2_backtest_ticker
from rich_stock.strategies.kplus import backtest_ticker_k1_plus, backtest_ticker_k2_plus
from rich_stock.strategies.sr import backtest_ticker as sr_backtest_ticker

BacktestTickerFn = Callable[[pd.DataFrame, str, PortfolioConfig], list[Trade]]


@dataclass
class BacktestResult:
    trades: list[Trade]
    portfolio: PortfolioResult
    metrics: Metrics


def apply_slippage(trades: list[Trade], slippage_pct: float) -> list[Trade]:
    """모든 체결가에 편도 슬리피지를 적용한 새 Trade 리스트를 반환한다.

    기법(SR/K1/K2/K1+/K2+)이 공통으로 쓰는 "장중 저가/고가 터치 = 정확히 그 가격에 체결"이라는
    낙관적 가정([[project-kplus-backtest-engine]]에서 K2+의 CAGR+68%가 이 가정과 고빈도 거래의
    복리효과로 부풀려졌음을 확인)을 보정하기 위한 최소 모델이다. 매수(shares>0)는 (1+slippage_pct)
    배 더 비싸게, 매도(shares<0)는 (1-slippage_pct)배 더 싸게 체결된 것으로 가정한다 — 종가 기준
    체결(entry_k1/exit_forced_hold 등)에도 동일하게 적용한다(스프레드·수수료 등 실거래 비용은
    체결 방식과 무관하게 항상 존재하므로).
    """
    if slippage_pct <= 0:
        return trades
    adjusted: list[Trade] = []
    for t in trades:
        new_fills = []
        for f in t.fills:
            factor = (1 + slippage_pct) if f.shares > 0 else (1 - slippage_pct)
            new_fills.append(dataclasses.replace(f, price=f.price * factor))
        adjusted.append(dataclasses.replace(t, fills=new_fills))
    return adjusted


def run_backtest(
    ohlcv: dict[str, pd.DataFrame],
    config: PortfolioConfig,
    backtest_ticker_fn: BacktestTickerFn,
) -> BacktestResult:
    """여러 종목의 일봉 데이터로부터 기법별 트레이드를 생성하고 포트폴리오 성과를 계산한다.

    Args:
        ohlcv: {ticker: DataFrame(index=Date, columns=[Open,High,Low,Close,Volume,PrevClose,TradingValue])}
        backtest_ticker_fn: 기법별 단일 종목 백테스트 함수 (예: strategies.sr.backtest_ticker)
    """
    all_trades: list[Trade] = []
    for ticker, df in ohlcv.items():
        all_trades.extend(backtest_ticker_fn(df, ticker, config))

    all_trades = apply_slippage(all_trades, config.slippage_pct)

    portfolio = allocate_portfolio(all_trades, ohlcv, config)
    metrics = compute_metrics(portfolio, all_trades=all_trades)
    return BacktestResult(trades=all_trades, portfolio=portfolio, metrics=metrics)


def run_sr_backtest(ohlcv: dict[str, pd.DataFrame], config: SRConfig | None = None) -> BacktestResult:
    return run_backtest(ohlcv, config or SRConfig(), sr_backtest_ticker)


def run_k2_backtest(ohlcv: dict[str, pd.DataFrame], config: K2Config | None = None) -> BacktestResult:
    return run_backtest(ohlcv, config or K2Config(), k2_backtest_ticker)


def run_k1_backtest(ohlcv: dict[str, pd.DataFrame], config: K1Config | None = None) -> BacktestResult:
    return run_backtest(ohlcv, config or K1Config(), k1_backtest_ticker)


def run_k1_plus_backtest(ohlcv: dict[str, pd.DataFrame], config: K1PlusConfig | None = None) -> BacktestResult:
    return run_backtest(ohlcv, config or K1PlusConfig(), backtest_ticker_k1_plus)


def run_k2_plus_backtest(ohlcv: dict[str, pd.DataFrame], config: K2PlusConfig | None = None) -> BacktestResult:
    return run_backtest(ohlcv, config or K2PlusConfig(), backtest_ticker_k2_plus)
