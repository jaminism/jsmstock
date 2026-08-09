import pandas as pd

from rich_stock.config import S2Config
from rich_stock.strategies.s2 import (
    backtest_ticker,
    compute_bracket_s2,
    describe_trade_plan,
    detect_s2_signals,
    plan_entry_order_s2,
)


def make_df(closes, highs=None, lows=None, opens=None, trading_value=100_000_000_000):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    highs = highs or list(closes)
    lows = lows or list(closes)
    opens = opens or list(closes)
    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * n},
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = trading_value
    return df


def test_detect_signal_computes_fixed_k1_grid():
    # RH=13000(UL 고가), lookback(기본5, 실제로는 index0~1 두 날만 존재)내 최저가 RL=9800
    # S2 = 13000 - (13000-9800)*0.236 = 12244.8
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    signals = detect_s2_signals(df, S2Config())
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ul_index == 1
    assert sig.high == 13000
    assert sig.low == 9800
    assert round(sig.s2_level) == round(13000 - (13000 - 9800) * 0.236)


def test_entry_at_close_when_close_breaches_k1_within_window():
    # S2 = 13000 - (13000-9800)*0.236 = 12244.8
    # day2(D+1) 종가 12500 > S2선 -> 미체결. day3(D+2) 종가 11800 <= S2선 -> 종가(11800)에 체결
    closes = [10000, 13000, 12500, 11800, 12000, 12100, 12200]
    highs = [10000, 13000, 12600, 11900, 12100, 12200, 12300]
    lows = [9800, 12000, 12300, 11700, 11900, 12000, 12100]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S2Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_s2"
    assert trade.fills[0].date == df.index[3]
    assert round(trade.fills[0].price) == 11800


def test_no_entry_after_d2_window_even_if_close_breaches_later():
    # D+1, D+2(index2,3) 종가 모두 S2선(12244.8) 위 -> 진입 없음. D+3(index4)에 하회해도 무효.
    closes = [10000, 13000, 12900, 12800, 11000]
    highs = [10000, 13000, 13000, 12900, 12900]
    lows = [9800, 12000, 12700, 12700, 10900]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S2Config())
    assert trades == []


def test_stop_loss_minus_7_percent_from_entry_close():
    # entry: day2(D+1) 종가 12000 <= S2(12244.8) -> 12000에 체결
    # stop_price = 12000 * 0.93 = 11160. day3 Low(11100) <= stop -> 손절
    closes = [10000, 13000, 12000, 11500, 11400]
    highs = [10000, 13000, 12100, 11800, 11700]
    lows = [9800, 12000, 11900, 11100, 11000]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S2Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_stop_loss"
    assert trade.pnl < 0
    assert abs(trade.fills[-1].price - 12000 * 0.93) < 1


def test_target_exit_at_previous_high():
    # entry: day2(D+1) 종가 12000 <= S2(12244.8) -> 12000에 체결
    # day3 High(13100) >= target(13000) -> 익절
    closes = [10000, 13000, 12000, 12900, 12800]
    highs = [10000, 13000, 12100, 13100, 12900]
    lows = [9800, 12000, 11900, 12500, 12500]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S2Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_target_high"
    assert round(trade.fills[-1].price) == 13000
    assert trade.pnl > 0


def test_forced_exit_after_hold_days():
    # entry: day2(D+1) 종가 12000 -> 체결. 4일차(entry+3) 강제청산
    closes = [10000, 13000, 12000, 12050, 12100, 12150, 12200]
    highs = [10000, 13000, 12100, 12200, 12250, 12300, 12350]
    lows = [9800, 12000, 11900, 11950, 12000, 12050, 12100]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S2Config(hold_days=4))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_forced_hold"
    entry_idx = 2
    assert trade.fills[-1].date == df.index[entry_idx + 4 - 1]


def test_describe_trade_plan_entry_unknown_until_close_but_target_fixed():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s2_signals(df, S2Config())[0]

    plan = describe_trade_plan(df, sig, S2Config())

    assert plan.entry_price is None  # 종가베팅이라 장마감 전까지 확정 안 됨
    assert plan.stop_price is None
    assert plan.stop_pct == S2Config().stop_loss_pct * 100  # 비율은 진입가와 무관하게 확정값
    assert f"{sig.s2_level:,.0f}" in plan.entry_desc
    assert plan.target_price == sig.high
    assert round(plan.target_pct, 2) == round((sig.high / sig.s2_level - 1) * 100, 2)


def test_plan_entry_order_s2_is_close_bet_with_no_limit_price():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s2_signals(df, S2Config())[0]

    plan = plan_entry_order_s2(sig, S2Config())

    assert plan.order_style == "close_bet"
    assert plan.limit_price is None
    assert plan.entry_valid_trading_days == S2Config().entry_valid_days


def test_compute_bracket_s2_stop_relative_to_actual_fill_price():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s2_signals(df, S2Config())[0]
    config = S2Config()

    bracket = compute_bracket_s2(fill_price=11800, signal=sig, config=config)

    assert bracket.stop_price == 11800 * (1 + config.stop_loss_pct)
    assert bracket.target_price == sig.high
    assert bracket.is_safety_override is False


def test_pre_rally_lookback_widens_low_anchor():
    # lookback을 늘리면 그 이전 더 낮은 저가(day0=9000)까지 포함돼 RL이 더 낮아지고,
    # 그 결과 S2 레벨도 낮아져야 한다(진입이 더 어려워짐).
    closes = [9500, 9200, 10000, 13000]
    highs = [9500, 9200, 10000, 13000]
    lows = [9000, 9100, 9800, 12000]
    df = make_df(closes, highs=highs, lows=lows)

    narrow = detect_s2_signals(df, S2Config(pre_rally_lookback_days=1))[0]
    wide = detect_s2_signals(df, S2Config(pre_rally_lookback_days=5))[0]

    assert narrow.low == 12000  # UL 당일 저가만 봄
    assert wide.low == 9000  # day0의 저가까지 포함
    assert wide.s2_level < narrow.s2_level
