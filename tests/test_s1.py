import pandas as pd

from rich_stock.config import S1Config
from rich_stock.strategies.s1 import backtest_ticker, describe_trade_plan, detect_s1_signals


def make_df(closes, highs=None, lows=None, opens=None, trading_value=100_000_000_000):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    highs = highs or closes
    lows = lows or closes
    opens = opens or closes
    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = trading_value
    return df


def test_detect_signal_on_fresh_limit_up():
    # day0: 기준가(전일종가 role) / day1: 상한가(진짜, 고가=종가) / 이후 하락
    closes = [10000, 13000, 12500, 12000, 11500, 11000, 10500, 10000, 10000, 10000]
    df = make_df(closes)
    config = S1Config()
    signals = detect_s1_signals(df, config)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ul_index == 1
    assert sig.r0 == 13000
    assert sig.r3 == 10000
    assert round(sig.r1) == 12000
    assert round(sig.r2) == 11000


def test_consecutive_limit_up_counts_once():
    closes = [10000, 13000, 16900, 16000, 15000]  # day2도 상한가(30%) 연속
    highs = closes
    df = make_df(closes, highs=highs)
    signals = detect_s1_signals(df, S1Config())
    assert len(signals) == 1
    assert signals[0].ul_index == 1


def test_below_trading_value_filter_excluded():
    closes = [10000, 13000, 12000, 11000, 10000]
    df = make_df(closes, trading_value=1_000_000_000)  # 10억, 500억 미달
    signals = detect_s1_signals(df, S1Config())
    assert signals == []


def test_entry_and_target_exit_full_cycle():
    ul_close = 13000
    closes = [10000, ul_close, 12500, 13100, 12800, 12500, 12000, 11500]
    highs = list(closes)
    lows = list(closes)
    # day2(index2): R1(12000) 터치
    lows[2] = 11800
    highs[2] = 12200
    # day3(index3): R0(13000) 터치 -> 익절
    highs[3] = 13100
    lows[3] = 12600
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S1Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_r1"
    assert trade.fills[-1].reason == "exit_target_r0"
    assert trade.is_closed
    assert trade.pnl > 0


def test_stop_loss_at_r3():
    ul_close = 13000
    closes = [10000, ul_close, 12000, 11000, 10000, 9500]
    highs = list(closes)
    lows = list(closes)
    lows[2] = 11800
    highs[2] = 12200  # entry at R1=12000
    lows[3] = 10900
    highs[3] = 11200
    lows[4] = 9800  # day4: R3(10000) 하회 -> 손절
    highs[4] = 10200
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S1Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_stop_r3"
    assert trade.pnl < 0


def test_forced_exit_after_hold_days():
    ul_close = 13000
    # entry 이후 목표가/손절가 어느 것도 안 닿고 계속 좁게 횡보 -> 4일차 강제청산
    closes = [10000, 13000, 12100, 12050, 12080, 12070, 12060]
    highs = [c + 50 for c in closes]
    lows = [c - 50 for c in closes]
    highs[1] = closes[1]  # 상한가일: 고가==종가여야 '진짜' 상한가로 판정됨
    lows[2], highs[2] = 11800, 12200  # entry at R1=12000
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S1Config(hold_days=4))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_forced_hold"
    entry_idx = 2
    assert trade.fills[-1].date == df.index[entry_idx + 4 - 1]


def test_no_entry_when_r1_never_touched():
    closes = [10000, 13000, 12900, 12800, 12700, 12600, 12500, 12400, 12300]
    highs = [c + 10 for c in closes]
    lows = [c - 10 for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S1Config())
    assert trades == []


def test_describe_trade_plan_uses_fixed_r0_r3_grid():
    closes = [10000, 13000, 12500, 12000, 11500, 11000, 10500, 10000, 10000, 10000]
    df = make_df(closes)
    sig = detect_s1_signals(df, S1Config())[0]

    plan = describe_trade_plan(df, sig, S1Config())

    assert plan.entry_price == sig.r1
    assert plan.stop_price == sig.r3
    assert plan.target_price == sig.r0
    assert f"{sig.r1:,.0f}" in plan.entry_desc
    assert f"{sig.r3:,.0f}" in plan.stop_desc
    assert f"{sig.r0:,.0f}" in plan.target_desc


def test_addon_and_breakeven_partial():
    ul_close = 13000  # R0=13000 R1=12000 R2=11000 R3=10000
    closes = [10000, 13000, 11900, 10900, 11500, 13100, 13000]
    highs = list(closes)
    lows = list(closes)
    lows[2], highs[2] = 11800, 12200  # entry R1
    lows[3], highs[3] = 10900, 11200  # addon R2
    lows[4], highs[4] = 11300, 11550  # breakeven(=(12000+11000)/2=11500) 터치 -> 절반 매도
    highs[5] = 13200  # R0 터치 -> 잔여 매도
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S1Config())
    assert len(trades) == 1
    trade = trades[0]
    reasons = [f.reason for f in trade.fills]
    assert reasons == ["entry_r1", "addon_r2", "exit_breakeven", "exit_target_r0"]
    assert trade.is_closed
