import pandas as pd
import pytest

from rich_stock.config import S6Config
from rich_stock.strategies.s6 import (
    AUTO_TRADE_SAFETY_MAX_HOLD_DAYS,
    AUTO_TRADE_SAFETY_STOP_PCT,
    AUTO_TRADE_SAFETY_TARGET_PCT,
    S6Signal,
    backtest_ticker,
    compute_bracket_s6,
    describe_trade_plan,
    detect_s6_signals,
    plan_entry_order_s6,
    simulate_s6_trade,
)


def _detect_df(opens, highs, lows, closes, trading_value=100_000_000_000):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * n},
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = trading_value
    return df, dates


# --- detect_s6_signals -----------------------------------------------------


def test_detect_signal_for_qualifying_streak():
    # 랠리 전 저점(day0~1)=4800, 3연상(day2~4), 이후 이틀(day5~6) 중 고점=16000
    # 상승률 = 16000/4800 - 1 = 233.3% >= 200%(기본값) -> 신호 인정
    opens = [5000, 5000, 5000, 6500, 8450, 15000, 14000]
    highs = [5000, 5000, 6500, 8450, 10985, 16000, 14500]
    lows = [4800, 4900, 5000, 6500, 8450, 14000, 13500]
    closes = [5000, 5000, 6500, 8450, 10985, 15000, 14000]
    df, dates = _detect_df(opens, highs, lows, closes)
    signals = detect_s6_signals(df, S6Config())
    assert len(signals) == 1
    sig = signals[0]
    assert sig.streak_len == 3
    assert sig.pre_rally_low == 4800
    assert sig.peak_price == 16000
    assert sig.peak_index == 5


def test_describe_trade_plan_uses_current_ma_and_has_no_stop_price():
    # 랠리 구간(day0~6) + 이동평균이 유효해지도록 횡보 구간(day7~29, 23일치)을 덧붙인다.
    opens = [5000, 5000, 5000, 6500, 8450, 15000, 14000] + [14000] * 23
    highs = [5000, 5000, 6500, 8450, 10985, 16000, 14500] + [14100] * 23
    lows = [4800, 4900, 5000, 6500, 8450, 14000, 13500] + [13900] * 23
    closes = [5000, 5000, 6500, 8450, 10985, 15000, 14000] + [14000] * 23
    df, _dates = _detect_df(opens, highs, lows, closes)
    sig = detect_s6_signals(df, S6Config())[0]

    plan = describe_trade_plan(df, sig, S6Config())

    ma_short = df["Close"].rolling(S6Config().ma_short).mean().iloc[-1]
    ma_long = df["Close"].rolling(S6Config().ma_long).mean().iloc[-1]
    assert plan.entry_price == ma_short
    assert f"{ma_short:,.0f}" in plan.entry_desc
    assert f"{ma_long:,.0f}" in plan.entry_desc
    assert plan.stop_price is None
    assert plan.stop_pct is None
    assert plan.target_pct is None
    assert "손절" in plan.stop_desc


def test_plan_entry_order_s6_uses_current_ma_short_as_daily_recompute_limit():
    opens = [5000, 5000, 5000, 6500, 8450, 15000, 14000] + [14000] * 23
    highs = [5000, 5000, 6500, 8450, 10985, 16000, 14500] + [14100] * 23
    lows = [4800, 4900, 5000, 6500, 8450, 14000, 13500] + [13900] * 23
    closes = [5000, 5000, 6500, 8450, 10985, 15000, 14000] + [14000] * 23
    df, _dates = _detect_df(opens, highs, lows, closes)
    sig = detect_s6_signals(df, S6Config())[0]
    config = S6Config()

    plan = plan_entry_order_s6(df, sig, config)

    ma_short = df["Close"].rolling(config.ma_short).mean().iloc[-1]
    assert plan.order_style == "daily_recompute_limit"
    assert plan.limit_price == ma_short
    assert plan.entry_valid_trading_days == config.entry_valid_days


def test_compute_bracket_s6_is_safety_override_with_generous_margins():
    opens = [5000, 5000, 5000, 6500, 8450, 15000, 14000] + [14000] * 23
    highs = [5000, 5000, 6500, 8450, 10985, 16000, 14500] + [14100] * 23
    lows = [4800, 4900, 5000, 6500, 8450, 14000, 13500] + [13900] * 23
    closes = [5000, 5000, 6500, 8450, 10985, 15000, 14000] + [14000] * 23
    df, _dates = _detect_df(opens, highs, lows, closes)
    sig = detect_s6_signals(df, S6Config())[0]

    bracket = compute_bracket_s6(fill_price=10000, signal=sig, config=S6Config())

    assert bracket.is_safety_override is True
    assert bracket.stop_price == 10000 * (1 + AUTO_TRADE_SAFETY_STOP_PCT)
    assert bracket.target_price == 10000 * (1 + AUTO_TRADE_SAFETY_TARGET_PCT)
    assert bracket.max_hold_trading_days == AUTO_TRADE_SAFETY_MAX_HOLD_DAYS
    assert "안전장치" in bracket.stop_reason
    assert "안전장치" in bracket.target_reason


def test_no_signal_when_streak_too_short():
    # 2연상뿐이라 streak_min_len(3) 미달 -> 신호 없음
    opens = [5000, 5000, 6500, 6000]
    highs = [5000, 6500, 8450, 6200]
    lows = [4800, 5000, 6500, 5900]
    closes = [5000, 6500, 8450, 6000]
    df, _ = _detect_df(opens, highs, lows, closes)
    signals = detect_s6_signals(df, S6Config())
    assert signals == []


def test_no_signal_when_rise_below_threshold():
    # 3연상은 만족하지만 상승률이 200% 미만 -> 신호 없음
    opens = [8000, 8000, 8000, 10400, 13520, 14000, 13800]
    highs = [8000, 8000, 10400, 13520, 17576, 17700, 17500]
    lows = [7900, 7950, 8000, 10400, 13520, 17000, 16800]
    closes = [8000, 8000, 10400, 13520, 17576, 17700, 17500]
    df, _ = _detect_df(opens, highs, lows, closes)
    # 상승률 = 17700/7900 - 1 = 124% < 200%
    signals = detect_s6_signals(df, S6Config())
    assert signals == []


# --- simulate_s6_trade (합성 이동평균 시리즈로 진입/청산 로직만 검증) ----------


def _sim_df(opens, highs, lows, closes):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * n},
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = 100_000_000_000
    return df, dates


def test_entry_ma15_partial_and_full_exit_without_addon():
    highs = [100, 96, 104, 106, 110, 112]
    lows = [100, 95, 96, 96, 96, 96]
    closes = [100, 98, 102, 104, 108, 110]
    opens = [100] * 6
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 6, index=dates)
    ma_long = pd.Series([1] * 6, index=dates)  # 절대 터치되지 않도록 아주 낮게 고정 -> 2차매수 없음

    signal = S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3)
    trade = simulate_s6_trade(df, signal, S6Config(entry_valid_days=5), "TEST", ma_short, ma_long)

    assert trade is not None
    reasons = [f.reason for f in trade.fills]
    assert reasons[0] == "entry_ma15"
    assert round(trade.fills[0].price) == 100
    assert "exit_partial_5pct" in reasons
    assert reasons[-1] == "exit_target_17pct_from_low"
    assert abs(trade.fills[-1].price - 95 * 1.17) < 1e-6


def test_addon_ma20_triggers_reverse_triangle_exit_at_entry1_price():
    opens = [100] * 5
    highs = [100, 96, 91, 98, 101]
    lows = [100, 95, 89, 85, 86]
    closes = [100, 96, 90, 92, 95]
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 5, index=dates)
    ma_long = pd.Series([90] * 5, index=dates)

    signal = S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3)
    trade = simulate_s6_trade(df, signal, S6Config(entry_valid_days=5, hold_days=4), "TEST", ma_short, ma_long)

    assert trade is not None
    reasons = [f.reason for f in trade.fills]
    assert reasons == ["entry_ma15", "addon_ma20", "exit_reverse_triangle"]
    assert round(trade.fills[0].price) == 100
    assert round(trade.fills[1].price) == 90
    assert round(trade.fills[2].price) == 100
    assert trade.fills[2].shares == -2.0


def test_addon3_switches_exit_target_to_entry2_price():
    # 2차 매수(90) 후 -7%(83.7)까지 더 하락해 3차 매수 -> 청산 기준이 1차가(100)가 아니라
    # 2차가(90)로 바뀌어야 한다.
    opens = [100] * 6
    highs = [100, 96, 91, 84, 89, 91]
    lows = [100, 95, 89, 83, 87, 88]
    closes = [100, 96, 90, 84, 88, 90]
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 6, index=dates)
    ma_long = pd.Series([90] * 6, index=dates)

    signal = S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3)
    trade = simulate_s6_trade(df, signal, S6Config(entry_valid_days=5, hold_days=4), "TEST", ma_short, ma_long)

    assert trade is not None
    reasons = [f.reason for f in trade.fills]
    assert reasons == ["entry_ma15", "addon_ma20", "addon_stop3", "exit_reverse_triangle"]
    assert round(trade.fills[2].price) == round(90 * 0.93)
    assert round(trade.fills[-1].price) == 90  # 2차가(20일선가)에서 청산 — 1차가(100)가 아님
    assert trade.fills[-1].shares == -3.0


def test_forced_exit_4_days_after_addon_ma20_when_no_target_hit():
    opens = [100] * 6
    highs = [100, 96, 91, 95, 96, 97]
    lows = [100, 95, 89, 85, 86, 87]
    closes = [100, 96, 90, 92, 94, 96]
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 6, index=dates)
    ma_long = pd.Series([90] * 6, index=dates)

    trade = simulate_s6_trade(
        df,
        S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3),
        S6Config(entry_valid_days=5, hold_days=4),
        "TEST", ma_short, ma_long,
    )

    assert trade is not None
    assert trade.fills[-1].reason == "exit_forced_hold"
    addon_idx = 2  # addon_ma20이 체결된 인덱스
    assert trade.fills[-1].date == dates[addon_idx + 4 - 1]
    assert round(trade.fills[-1].price) == 96  # 강제청산일 종가


def test_no_time_stop_and_unbounded_hold_when_only_entry1():
    # 2차(20일선) 매수 없이 1차만 보유 -> 시간청산이 아예 없어 데이터 끝까지 보유(exit_data_end)
    opens = [100] * 5
    highs = [100, 96, 101, 102, 103]
    lows = [100, 95, 96, 97, 98]
    closes = [100, 97, 99, 100, 101]
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 5, index=dates)
    ma_long = pd.Series([1] * 5, index=dates)  # 절대 터치 안 됨

    trade = simulate_s6_trade(
        df,
        S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3),
        S6Config(entry_valid_days=5),
        "TEST", ma_short, ma_long,
    )

    assert trade is not None
    reasons = [f.reason for f in trade.fills]
    assert reasons == ["entry_ma15", "exit_data_end"]
    assert trade.fills[-1].date == dates[-1]


def test_no_entry_when_ma15_never_touched():
    opens = [100] * 5
    highs = [100, 110, 111, 112, 113]
    lows = [100, 105, 106, 107, 108]  # 항상 ma_short(100)보다 위
    closes = [100, 108, 109, 110, 111]
    df, dates = _sim_df(opens, highs, lows, closes)
    ma_short = pd.Series([100] * 5, index=dates)
    ma_long = pd.Series([1] * 5, index=dates)

    trade = simulate_s6_trade(
        df,
        S6Signal(peak_index=0, peak_date=dates[0], peak_price=999, pre_rally_low=1, streak_len=3),
        S6Config(entry_valid_days=5),
        "TEST", ma_short, ma_long,
    )
    assert trade is None


# --- backtest_ticker (실제 rolling 이동평균 계산까지 포함한 통합 검증) ----------


def test_backtest_ticker_end_to_end_with_real_rolling_averages():
    closes = [100, 100, 100, 130, 169, 219.7, 230, 225, 200, 190, 180, 170, 165, 160, 158, 156, 154, 152, 150, 148]
    highs = [100, 100, 100, 130, 169, 219.7, 235, 228, 205, 195, 185, 175, 168, 163, 161, 159, 157, 155, 153, 151]
    lows = [98, 98, 98, 100, 130, 169, 220, 198, 188, 178, 168, 158, 155, 150, 148, 146, 144, 142, 140, 138]
    opens = closes
    df, dates = _sim_df(opens, highs, lows, closes)

    config = S6Config(
        streak_min_len=3, min_rise_pct=1.0, pre_rally_lookback_days=3, peak_search_days=2,
        ma_short=3, ma_long=5, entry_valid_days=10, min_trading_value_krw=0,
    )
    trades = backtest_ticker(df, "TEST", config)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_ma15"

    ma3 = df["Close"].rolling(3).mean()
    entry_date = trade.fills[0].date
    assert trade.fills[0].price == pytest.approx(ma3.loc[entry_date])
