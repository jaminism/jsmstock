import pandas as pd

from rich_stock.config import S4Config, S5Config
from rich_stock.strategies.s45 import (
    backtest_ticker_s4,
    backtest_ticker_s5,
    compute_bracket_s4,
    compute_bracket_s5,
    describe_trade_plan_s4,
    describe_trade_plan_s5,
    detect_s45_signals,
    plan_entry_order_s4,
    plan_entry_order_s5,
)


def make_df(opens, highs, lows, closes, trading_value=100_000_000_000):
    n = len(closes)
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * n},
        index=dates,
    )
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = trading_value
    return df


def test_detect_power_candle_via_shape_condition():
    # day1: 전일종가(10000)*1.15=11500<=고가(11600) True, 저가(9900)*1.15=11385<=11600 True,
    # 시가(10200)*1.09=11118<=종가(11550) True. 상한가는 아님(고가!=종가)이므로 조건A로 판정.
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    signals = detect_s45_signals(df, S4Config())
    assert len(signals) == 1
    assert signals[0].event_index == 1
    assert signals[0].high == 11600


def test_describe_trade_plan_s4_entry_unknown_target_fixed():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S4Config())[0]

    plan = describe_trade_plan_s4(df, sig, S4Config())

    level = sig.high - (sig.high - sig.low) * S4Config().fib_ratio
    assert plan.entry_price is None  # 종가베팅이라 장마감 전까지 확정 안 됨
    assert f"{level:,.0f}" in plan.entry_desc
    assert plan.stop_pct == S4Config().stop_loss_pct * 100
    assert plan.target_price == sig.high
    assert round(plan.target_pct, 2) == round((sig.high / level - 1) * 100, 2)


def test_plan_entry_order_s4_is_close_bet():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S4Config())[0]

    plan = plan_entry_order_s4(sig, S4Config())

    assert plan.order_style == "close_bet"
    assert plan.limit_price is None
    assert plan.entry_valid_trading_days == S4Config().entry_valid_days


def test_compute_bracket_s4_stop_relative_to_fill_price():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S4Config())[0]
    config = S4Config()

    bracket = compute_bracket_s4(fill_price=11400, signal=sig, config=config)

    assert bracket.stop_price == 11400 * (1 + config.stop_loss_pct)
    assert bracket.target_price == sig.high
    assert bracket.is_safety_override is False


def test_describe_trade_plan_s5_entry_stop_target_all_fixed():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S5Config())[0]
    config = S5Config()

    plan = describe_trade_plan_s5(df, sig, config)

    level = sig.high - (sig.high - sig.low) * config.fib_ratio
    assert plan.entry_price == level
    assert plan.stop_price == level * (1 + config.stop_loss_pct)
    assert plan.stop_pct == config.stop_loss_pct * 100
    assert plan.target_price == level * (1 + config.profit_target_pct)
    assert plan.target_pct == config.profit_target_pct * 100


def test_plan_entry_order_s5_fixed_limit_at_level():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S5Config())[0]
    config = S5Config()

    plan = plan_entry_order_s5(sig, config)

    level = sig.high - (sig.high - sig.low) * config.fib_ratio
    assert plan.order_style == "fixed_limit"
    assert plan.limit_price == level
    assert plan.entry_valid_trading_days == config.entry_valid_days


def test_compute_bracket_s5_uses_fill_price_for_both_stop_and_target():
    opens = [10000, 10200, 11550, 11500, 11450]
    highs = [10000, 11600, 11650, 11600, 11550]
    lows = [10000, 9900, 11400, 11350, 11300]
    closes = [10000, 11550, 11600, 11550, 11500]
    df = make_df(opens, highs, lows, closes)
    sig = detect_s45_signals(df, S5Config())[0]
    config = S5Config()

    bracket = compute_bracket_s5(fill_price=11000, signal=sig, config=config)

    assert bracket.stop_price == 11000 * (1 + config.stop_loss_pct)
    assert bracket.target_price == 11000 * (1 + config.profit_target_pct)
    assert bracket.is_safety_override is False


def test_no_power_candle_when_shape_condition_fails():
    # 등락률/변동폭이 세력봉 기준에 못 미치는 완만한 상승 -> 신호 없음
    opens = [10000, 10100, 10200, 10300, 10400]
    highs = [10000, 10150, 10250, 10350, 10450]
    lows = [10000, 10050, 10150, 10250, 10350]
    closes = [10000, 10120, 10220, 10320, 10420]
    df = make_df(opens, highs, lows, closes)
    signals = detect_s45_signals(df, S4Config())
    assert signals == []


def test_detect_power_candle_via_ul_branch_ignores_shape():
    # 상한가(등락률>=29.5%, 고가==종가)면 캔들형태 조건 없이 거래대금만으로 판정
    opens = [10000, 10000, 12950]
    highs = [10000, 13000, 13000]
    lows = [10000, 9800, 12700]
    closes = [10000, 13000, 12950]
    df = make_df(opens, highs, lows, closes)
    signals = detect_s45_signals(df, S4Config())
    assert len(signals) == 1
    assert signals[0].event_index == 1


def test_rl_lookback_excludes_event_day_itself():
    # 이벤트 당일(index=2) 저가(8000)가 lookback 구간 내 최저값이더라도, RL 계산에서 제외되어야
    # 자기순환적 결함(당일 저가가 항상 되돌림선을 만족하는 버그)이 발생하지 않는다.
    opens = [10000, 10000, 10200]
    highs = [10000, 10050, 13000]
    lows = [10000, 9900, 8000]
    closes = [10000, 10000, 13000]
    df = make_df(opens, highs, lows, closes)
    signals = detect_s45_signals(df, S4Config(pre_event_lookback_days=5))
    assert len(signals) == 1
    assert signals[0].event_index == 2
    assert signals[0].low == 9900  # 8000(이벤트 당일)이 아니라 그 이전 최저가(day0/day1 중)여야 함


def test_rl_lookback_excludes_halted_zero_volume_rows():
    # lookback 구간에 거래정지(Volume=0, OHLC가 0 또는 이상값)로우가 섞이면 그 저가를 RL로
    # 오인해선 안 된다 — 2026-08-12 실거래에서 발견된 버그(226340): 거래정지일의 Low=0이
    # 되돌림 저점(RL)으로 잡혀 되돌림선이 실제 가격대와 무관하게 왜곡, 상/하한가 오류로 진입주문이
    # 반복 거부됐다.
    opens = [10000, 0, 10000]
    highs = [10000, 0, 13000]
    lows = [9900, 0, 9800]
    closes = [10000, 10000, 13000]
    df = make_df(opens, highs, lows, closes)
    df.loc[df.index[1], "Volume"] = 0

    signals = detect_s45_signals(df, S4Config(pre_event_lookback_days=5))

    assert len(signals) == 1
    assert signals[0].event_index == 2
    assert signals[0].low == 9900  # 0(거래정지일)이 아니라 유효 거래일 중 최저가여야 함


def test_rl_lookback_skips_event_when_all_lookback_rows_halted():
    # lookback 구간 전체가 거래정지면 RL을 신뢰할 수 없으므로 이벤트 자체를 건너뛴다
    # (0을 RL로 쓰는 것보다 신호를 안 만드는 게 안전).
    opens = [0, 10000]
    highs = [0, 13000]
    lows = [0, 9800]
    closes = [10000, 13000]
    df = make_df(opens, highs, lows, closes)
    df.loc[df.index[0], "Volume"] = 0

    signals = detect_s45_signals(df, S4Config(pre_event_lookback_days=5))

    assert signals == []


def test_k1_plus_entry_at_close_same_day_only():
    # RH=13000, RL=9800(이벤트 이전) -> S2+ = 13000-(13000-9800)*0.236 = 12244.8
    # 이벤트 당일(index=1) 종가 12000 <= S2+ -> 그날 종가(12000)에 체결
    opens = [10000, 10000, 12100, 12200, 12300]
    highs = [10000, 13000, 12200, 12300, 12400]
    lows = [9800, 11000, 12000, 12100, 12200]
    closes = [10000, 12000, 12150, 12250, 12350]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s4(df, "TEST", S4Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_s4"
    assert trade.fills[0].date == df.index[1]
    assert round(trade.fills[0].price) == 12000


def test_k1_plus_no_entry_when_close_above_level():
    # S2+ = 13000-(13000-9800)*0.236 = 12244.8. 이벤트 당일 종가(12500)가 이보다 높으면
    # 진입 없음(하루만 유효라 다음날도 무효).
    opens = [10000, 10000, 12600, 12700, 12800]
    highs = [10000, 13000, 12700, 12800, 12900]
    lows = [9800, 11000, 12550, 12600, 12700]
    closes = [10000, 12500, 12650, 12750, 12850]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s4(df, "TEST", S4Config())
    assert trades == []


def test_k1_plus_stop_loss():
    # entry: index1 종가 12000 -> stop = 12000*0.93=11160. index2 저가(11000)<=stop -> 손절
    opens = [10000, 10000, 11900, 12900, 13000]
    highs = [10000, 13000, 12000, 13100, 13200]
    lows = [9800, 11000, 11000, 12500, 12600]
    closes = [10000, 12000, 11500, 13000, 13100]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s4(df, "TEST", S4Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_stop_loss"
    assert abs(trade.fills[-1].price - 12000 * 0.93) < 1


def test_k2_plus_entry_within_window_at_level_price():
    # RH=13000, RL=9800 -> S3+ = 13000-(13000-9800)*0.5 = 11400
    # index1(이벤트 당일) 저가(11900) > 11400 -> 미체결. index2 저가(11300)<=11400 -> 11400에 체결
    opens = [10000, 10000, 11500, 11500, 11500]
    highs = [10000, 13000, 11600, 11600, 11600]
    lows = [9800, 11900, 11300, 11350, 11350]
    closes = [10000, 13000, 11450, 11500, 11500]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s5(df, "TEST", S5Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_s5"
    assert trade.fills[0].date == df.index[2]
    assert round(trade.fills[0].price) == 11400


def test_k2_plus_no_entry_outside_window():
    # entry_valid_days=4 -> 유효구간 index1~4. S3+=11400. 그 구간 내내 11400보다 위에 머물다가
    # 구간 밖(index5)에서 처음 하회하면 무효.
    opens = [10000, 10000, 12700, 12600, 12500, 11300]
    highs = [10000, 13000, 12800, 12700, 12600, 11400]
    lows = [9800, 12600, 12500, 12400, 12300, 11200]
    closes = [10000, 13000, 12600, 12500, 12400, 11350]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s5(df, "TEST", S5Config())
    assert trades == []


def test_k2_plus_profit_target_plus_7_pct():
    # entry level=11400(index2), target=11400*1.07=12198
    opens = [10000, 10000, 11500, 12100, 12200]
    highs = [10000, 13000, 11600, 12300, 12400]
    lows = [9800, 11900, 11300, 12100, 12200]
    closes = [10000, 13000, 11450, 12250, 12350]
    df = make_df(opens, highs, lows, closes)
    trades = backtest_ticker_s5(df, "TEST", S5Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_target_high"
    assert abs(trade.fills[-1].price - 11400 * 1.07) < 1
