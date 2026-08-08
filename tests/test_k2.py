import pandas as pd

from rich_stock.config import K2Config
from rich_stock.strategies.k2 import backtest_ticker, detect_k2_signals


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
    # K2 = 13000 - (13000-9800)*0.5 = 11400
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    df = make_df(closes, highs=highs, lows=lows)
    signals = detect_k2_signals(df, K2Config())
    assert len(signals) == 1
    sig = signals[0]
    assert sig.ul_index == 1
    assert sig.high == 13000
    assert sig.low == 9800
    assert round(sig.k2_level) == 11400


def _scenario_df(day4_close, day4_low, day4_high):
    closes = [10000, 13000, 12500, 11200, day4_close]
    highs = [10000, 13000, 12600, 11500, day4_high]
    lows = [9800, 12000, 12300, 11300, day4_low]
    return make_df(closes, highs=highs, lows=lows)


def test_entry_at_k2_level_and_target_exit():
    # entry: day3 Low(11300) <= K2(11400) -> 11400에 체결. day4 High(13100)>=target(13000) -> 익절
    df = _scenario_df(day4_close=13100, day4_low=12700, day4_high=13100)
    trades = backtest_ticker(df, "TEST", K2Config())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[0].reason == "entry_k2"
    assert round(trade.fills[0].price) == 11400
    assert trade.fills[-1].reason == "exit_target_high"
    assert round(trade.fills[-1].price) == 13000
    assert trade.is_closed
    assert trade.pnl > 0


def test_stop_loss_minus_7_percent():
    # stop_price = 11400 * 0.93 = 10602. day4 Low(10500) <= 10602 -> 손절
    df = _scenario_df(day4_close=10500, day4_low=10500, day4_high=11000)
    trades = backtest_ticker(df, "TEST", K2Config())
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
    trades = backtest_ticker(df, "TEST", K2Config(hold_days=4))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.fills[-1].reason == "exit_forced_hold"
    entry_idx = 3
    assert trade.fills[-1].date == df.index[entry_idx + 4 - 1]


def test_no_entry_when_k2_never_touched():
    closes = [10000, 13000, 12900, 12800, 12700, 12600, 12500, 12400, 12300]
    highs = [10000, 13000] + [c + 100 for c in closes[2:]]
    lows = [9800, 12000] + [c - 100 for c in closes[2:]]  # 항상 K2(11400)보다 훨씬 위
    df = make_df(closes, highs=highs, lows=lows)
    trades = backtest_ticker(df, "TEST", K2Config())
    assert trades == []


def test_pre_rally_lookback_widens_low_anchor():
    # lookback을 늘리면 그 이전 더 낮은 저가(day0=9000)까지 포함돼 RL이 더 낮아지고,
    # 그 결과 K2 레벨도 낮아져야 한다(진입이 더 어려워짐).
    closes = [9500, 9200, 10000, 13000]
    highs = [9500, 9200, 10000, 13000]
    lows = [9000, 9100, 9800, 12000]
    df = make_df(closes, highs=highs, lows=lows)

    narrow = detect_k2_signals(df, K2Config(pre_rally_lookback_days=1))[0]
    wide = detect_k2_signals(df, K2Config(pre_rally_lookback_days=5))[0]

    assert narrow.low == 12000  # UL 당일 저가만 봄
    assert wide.low == 9000  # day0의 저가까지 포함
    assert wide.k2_level < narrow.k2_level
