import pandas as pd

from rich_stock.backtest.engine import apply_slippage, run_s5_backtest
from rich_stock.config import S5Config
from rich_stock.strategies.base import Fill, Trade


def _sample_trade():
    trade = Trade(ticker="TEST", signal_date=pd.Timestamp("2024-01-02"))
    trade.fills.append(Fill(pd.Timestamp("2024-01-02"), 10000.0, 1.0, "entry"))
    trade.fills.append(Fill(pd.Timestamp("2024-01-03"), 11000.0, -1.0, "exit"))
    return trade


def test_zero_slippage_returns_same_trades_unchanged():
    trades = [_sample_trade()]
    result = apply_slippage(trades, 0.0)
    assert result is trades  # 0이면 원본 그대로 반환(불필요한 복사 없음)


def test_slippage_makes_buys_more_expensive_and_sells_cheaper():
    trades = [_sample_trade()]
    adjusted = apply_slippage(trades, 0.01)
    buy_fill = adjusted[0].fills[0]
    sell_fill = adjusted[0].fills[1]
    assert round(buy_fill.price) == round(10000 * 1.01)
    assert round(sell_fill.price) == round(11000 * 0.99)
    # 원본은 변경되지 않아야 함(새 Trade/Fill 객체 반환)
    assert trades[0].fills[0].price == 10000.0


def test_slippage_reduces_realized_pnl():
    trades = [_sample_trade()]
    no_slip = apply_slippage(trades, 0.0)
    with_slip = apply_slippage(trades, 0.02)
    assert with_slip[0].pnl < no_slip[0].pnl


def test_k2_plus_backtest_with_slippage_degrades_relative_to_zero_slippage():
    opens = [10000, 10000, 11500, 12100, 12200]
    highs = [10000, 13000, 11600, 12300, 12400]
    lows = [9800, 11900, 11300, 12100, 12200]
    closes = [10000, 13000, 11450, 12250, 12350]
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * n},
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = 100_000_000_000
    ohlcv = {"TEST": df}

    baseline = run_s5_backtest(ohlcv, S5Config(slippage_pct=0.0))
    slipped = run_s5_backtest(ohlcv, S5Config(slippage_pct=0.02))

    assert len(baseline.trades) == 1
    assert len(slipped.trades) == 1
    assert slipped.trades[0].pnl < baseline.trades[0].pnl
