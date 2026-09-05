import pandas as pd
import pytest

from rich_stock.config import S3Config
from rich_stock.strategies.s3 import (
    backtest_ticker,
    compute_bracket_s3,
    describe_trade_plan,
    detect_s3_signals,
    plan_entry_order_s3,
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


def test_detect_signal_computes_fixed_k2_grid():
    # RH=13000(UL 고가), lookback(기본5, 실제로는 index0~1 두 날만 존재)내 최저가 RL=9800
    # S3 = 13000 - (13000-9800)*0.5 = 11400
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    signals = detect_s3_signals(df, S3Config())
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ul_index == 1
    assert sig.high == 13000
    assert sig.low == 9800
    assert round(sig.s3_level) == 11400


def test_describe_trade_plan_entry_and_stop_are_fixed_values():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s3_signals(df, S3Config())[0]

    plan = describe_trade_plan(df, sig, S3Config())

    assert plan.entry_price == sig.s3_level
    assert plan.stop_price == sig.s3_level * (1 + S3Config().stop_loss_pct)
    assert plan.stop_pct == S3Config().stop_loss_pct * 100
    assert plan.target_price == sig.high
    assert round(plan.target_pct, 2) == round((sig.high / sig.s3_level - 1) * 100, 2)


def test_plan_entry_order_s3_fixed_limit_at_level():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s3_signals(df, S3Config())[0]

    plan = plan_entry_order_s3(sig, S3Config())

    assert plan.order_style == "fixed_limit"
    assert plan.limit_price == sig.s3_level
    assert plan.entry_valid_trading_days == S3Config().entry_valid_days


def test_compute_bracket_s3_uses_actual_fill_price_for_stop():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    sig = detect_s3_signals(df, S3Config())[0]
    config = S3Config()

    bracket = compute_bracket_s3(fill_price=sig.s3_level, signal=sig, config=config)

    assert bracket.stop_price == sig.s3_level * (1 + config.stop_loss_pct)
    assert bracket.target_price == sig.high
    assert bracket.is_safety_override is False


def _scenario_df(day4_close, day4_low, day4_high):
    closes = [10000, 13000, 12500, 11200, day4_close]
    highs = [10000, 13000, 12600, 11500, day4_high]
    lows = [9800, 12000, 12300, 11300, day4_low]
    return make_df(closes, highs=highs, lows=lows)


def test_entry_at_k2_level_and_target_exit():
    # entry: day3 Low(11300) <= S3(11400) -> 11400에 체결. day4 High(13100)>=target(13000) -> 익절
    df = _scenario_df(day4_close=13100, day4_low=12700, day4_high=13100)
    trades = backtest_ticker(df, "TEST", S3Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_s3"
    assert round(trade.fills[0].price) == 11400
    assert trade.fills[-1].reason == "exit_target_high"
    assert round(trade.fills[-1].price) == 13000
    assert trade.is_closed
    assert trade.pnl > 0


def test_stop_loss_minus_7_percent():
    # stop_price = 11400 * 0.93 = 10602. day4 Low(10500) <= 10602 -> 손절
    df = _scenario_df(day4_close=10500, day4_low=10500, day4_high=11000)
    trades = backtest_ticker(df, "TEST", S3Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_stop_loss"
    assert trade.pnl < 0
    assert abs(trade.fills[-1].price - 11400 * 0.93) < 1


def test_forced_exit_after_hold_days():
    closes = [10000, 13000, 12500, 11200, 11250, 11300, 11350]
    highs = [10000, 13000, 12600, 11500, 11600, 11650, 11700]
    lows = [9800, 12000, 12300, 11300, 11200, 11250, 11300]
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S3Config(hold_days=4))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_forced_hold"
    entry_idx = 3
    assert trade.fills[-1].date == df.index[entry_idx + 4 - 1]


def test_no_entry_when_k2_never_touched():
    closes = [10000, 13000, 12900, 12800, 12700, 12600, 12500, 12400, 12300]
    highs = [10000, 13000] + [c + 100 for c in closes[2:]]
    lows = [9800, 12000] + [c - 100 for c in closes[2:]]  # 항상 S3(11400)보다 훨씬 위
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", S3Config())
    assert trades == []


def test_pre_rally_lookback_widens_low_anchor():
    # lookback을 늘리면 그 이전 더 낮은 저가(day0=9000)까지 포함돼 RL이 더 낮아지고,
    # 그 결과 S3 레벨도 낮아져야 한다(진입이 더 어려워짐).
    closes = [9500, 9200, 10000, 13000]
    highs = [9500, 9200, 10000, 13000]
    lows = [9000, 9100, 9800, 12000]
    df = make_df(closes, highs=highs, lows=lows)

    narrow = detect_s3_signals(df, S3Config(pre_rally_lookback_days=1))[0]
    wide = detect_s3_signals(df, S3Config(pre_rally_lookback_days=5))[0]

    assert narrow.low == 12000  # UL 당일 저가만 봄
    assert wide.low == 9000  # day0의 저가까지 포함
    assert wide.s3_level < narrow.s3_level


def test_rl_excludes_halted_zero_volume_rows():
    # test_s2.py의 동봉 테스트와 같은 버그 — S3도 RL을 필터 없이 구하고 있었다(2026-09-05 발견).
    closes = [10000, 10000, 13000]
    highs = [10000, 0, 13000]
    lows = [9900, 0, 12000]
    opens = [10000, 0, 12100]
    df = make_df(closes, highs=highs, lows=lows, opens=opens)
    df.loc[df.index[1], "Volume"] = 0

    signals = detect_s3_signals(df, S3Config())

    assert len(signals) == 1
    assert signals[0].low == 9900
    # S3 = 13000 - (13000-9900)*0.5 = 11450 (버그 상태였다면 13000-13000*0.5 = 6500)
    assert signals[0].s3_level == pytest.approx(11450)


def test_signal_skipped_when_every_lookback_row_is_halted():
    closes = [10000, 13000]
    highs = [0, 13000]
    lows = [0, 12000]
    df = make_df(closes, highs=highs, lows=lows)
    df["Volume"] = 0

    assert detect_s3_signals(df, S3Config()) == []


# --- 2026-09-05 거래정지일 / 매수 가능 기간 -------------------------------------------


def test_no_entry_on_halted_day():
    """거래정지일은 Low=0이라 어떤 되돌림선도 만족시킨다 — 그날 체결이 생기면 안 된다.

    실측(캐시 2,713종목): 이 방어가 없을 때 S3 체결의 10.8%가 정지일에 발생했고, 그것만 빼면
    CAGR이 +18.51% → -0.30%로 떨어졌다. 공표 성과의 거의 전부가 이 산물이었다."""
    # day1 상한가(고가 13000), day2가 거래정지, day3에 정상적으로 되돌림선(11400)을 터치
    closes = [10000, 13000, 13000, 11000, 11200]
    highs = [10000, 13000, 0, 11500, 11400]
    lows = [9800, 12000, 0, 11000, 11100]
    df = make_df(closes, highs=highs, lows=lows)
    df.loc[df.index[2], "Volume"] = 0

    trades = backtest_ticker(df, "TEST", S3Config())

    assert len(trades) == 1
    entry = trades[0].fills[0]
    assert entry.date == df.index[3]  # 정지일(day2)이 아니라 다음 거래일에 진입
    assert entry.reason == "entry_s3"


def test_entry_window_follows_source_rule():
    """원문 "고점 갱신일 포함 4일차까지" — 상한가 다음날부터 3거래일까지만 진입 유효."""
    assert S3Config().entry_valid_days == 3

    # 상한가(day1) 후 day2~day4는 되돌림선 위에서만 놀다가 day5에 처음 터치 → 진입 없음
    closes = [10000, 13000, 12800, 12700, 12600, 11000]
    highs = [10000, 13000, 12900, 12800, 12700, 11500]
    lows = [9800, 12000, 12700, 12600, 12500, 11000]
    df = make_df(closes, highs=highs, lows=lows)

    assert backtest_ticker(df, "TEST", S3Config()) == []
