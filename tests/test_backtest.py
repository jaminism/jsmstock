import pandas as pd

from rich_stock.backtest.engine import (
    run_k1_backtest,
    run_k1_plus_backtest,
    run_k2_backtest,
    run_k2_plus_backtest,
    run_sp_backtest,
    run_sr_backtest,
)
from rich_stock.config import K1Config, K1PlusConfig, K2Config, K2PlusConfig, SPConfig, SRConfig


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


def _winning_ticker_df():
    closes = [10000, 13000, 12500, 13100, 12800, 12500, 12000, 11500]
    highs, lows = list(closes), list(closes)
    highs[1] = closes[1]
    lows[2], highs[2] = 11800, 12200  # entry R1=12000
    highs[3] = 13100  # exit at R0=13000
    lows[3] = 12600
    return make_df(closes, highs=highs, lows=lows)


def _losing_ticker_df():
    closes = [10000, 13000, 12000, 11000, 10000, 9500]
    highs, lows = list(closes), list(closes)
    highs[1] = closes[1]
    lows[2], highs[2] = 11800, 12200  # entry R1=12000
    lows[3], highs[3] = 10900, 11200
    lows[4], highs[4] = 9800, 10200  # stop at R3=10000
    return make_df(closes, highs=highs, lows=lows)


def test_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _winning_ticker_df(),
        "LOSE": _losing_ticker_df(),
    }
    result = run_sr_backtest(ohlcv, SRConfig(initial_capital_krw=100_000_000))

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert 0 < result.metrics.win_rate < 1
    assert not result.portfolio.equity_curve.empty
    # 초기자본 대비 최종 자산이 합리적 범위 내에 있어야 함 (포지션당 3% 배분이므로 극단적 변화는 없음)
    final_equity = result.portfolio.equity_curve.iloc[-1]
    assert 90_000_000 < final_equity < 110_000_000


def test_max_concurrent_positions_limits_acceptance():
    ohlcv = {f"T{i}": _winning_ticker_df() for i in range(5)}
    config = SRConfig(max_concurrent_positions=2)
    result = run_sr_backtest(ohlcv, config)
    assert len(result.portfolio.accepted) <= 2
    assert len(result.portfolio.skipped) >= 1


def _k2_winning_ticker_df():
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    return make_df(closes, highs=highs, lows=lows)


def _k2_losing_ticker_df():
    closes = [10000, 13000, 12500, 11200, 10500]
    highs = [10000, 13000, 12600, 11500, 11000]
    lows = [9800, 12000, 12300, 11300, 10500]
    return make_df(closes, highs=highs, lows=lows)


def test_k2_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _k2_winning_ticker_df(),
        "LOSE": _k2_losing_ticker_df(),
    }
    result = run_k2_backtest(ohlcv, K2Config(initial_capital_krw=100_000_000))

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert not result.portfolio.equity_curve.empty


def _k1_winning_ticker_df():
    # K1 = 13000 - (13000-9800)*0.236 = 12244.8. D+1(day2) 종가 12000 <= K1 -> 체결.
    # 이후 High(13100) >= target(13000) -> 익절
    closes = [10000, 13000, 12000, 12900, 12800]
    highs = [10000, 13000, 12100, 13100, 12900]
    lows = [9800, 12000, 11900, 12500, 12500]
    return make_df(closes, highs=highs, lows=lows)


def _k1_losing_ticker_df():
    # entry: day2 종가 12000 -> 체결. stop_price = 12000*0.93 = 11160. day3 Low(11100) <= stop -> 손절
    closes = [10000, 13000, 12000, 11500, 11400]
    highs = [10000, 13000, 12100, 11800, 11700]
    lows = [9800, 12000, 11900, 11100, 11000]
    return make_df(closes, highs=highs, lows=lows)


def test_k1_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _k1_winning_ticker_df(),
        "LOSE": _k1_losing_ticker_df(),
    }
    result = run_k1_backtest(ohlcv, K1Config(initial_capital_krw=100_000_000))

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert not result.portfolio.equity_curve.empty


def _k1_plus_winning_ticker_df():
    # 이벤트(index1, 캔들형태 조건): RH=13000,RL=9800 -> K1+=12244.8. 종가(12000)<=K1+ -> 진입.
    # 이후 손절/익절 없이 4일차 강제청산(종가 12350)으로 순이익 마감.
    opens = [10000, 10000, 12100, 12200, 12300]
    highs = [10000, 13000, 12200, 12300, 12400]
    lows = [9800, 11000, 12000, 12100, 12200]
    closes = [10000, 12000, 12150, 12250, 12350]
    return make_df(closes, highs=highs, lows=lows, opens=opens)


def _k1_plus_losing_ticker_df():
    # entry: index1 종가 12000 -> stop=12000*0.93=11160. index2 저가(11000)<=stop -> 손절
    opens = [10000, 10000, 11900, 12900, 13000]
    highs = [10000, 13000, 12000, 13100, 13200]
    lows = [9800, 11000, 11000, 12500, 12600]
    closes = [10000, 12000, 11500, 13000, 13100]
    return make_df(closes, highs=highs, lows=lows, opens=opens)


def test_k1_plus_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _k1_plus_winning_ticker_df(),
        "LOSE": _k1_plus_losing_ticker_df(),
    }
    result = run_k1_plus_backtest(ohlcv, K1PlusConfig(initial_capital_krw=100_000_000))

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert not result.portfolio.equity_curve.empty


def _k2_plus_winning_ticker_df():
    # 이벤트(index1, UL): RH=13000,RL=9800 -> K2+=11400. index2 저가(11300)<=11400 -> 진입.
    # target=11400*1.07=12198, index3 High(12300)>=12198 -> 익절
    opens = [10000, 10000, 11500, 12100, 12200]
    highs = [10000, 13000, 11600, 12300, 12400]
    lows = [9800, 11900, 11300, 12100, 12200]
    closes = [10000, 13000, 11450, 12250, 12350]
    return make_df(closes, highs=highs, lows=lows, opens=opens)


def _k2_plus_losing_ticker_df():
    # entry: index2 저가(11300)<=11400 -> 11400에 체결. stop=11400*0.93=10602.
    # index3 저가(10500)<=stop -> 손절
    opens = [10000, 10000, 11500, 10600, 10500]
    highs = [10000, 13000, 11600, 10800, 10700]
    lows = [9800, 11900, 11300, 10500, 10400]
    closes = [10000, 13000, 11450, 10600, 10500]
    return make_df(closes, highs=highs, lows=lows, opens=opens)


def test_k2_plus_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _k2_plus_winning_ticker_df(),
        "LOSE": _k2_plus_losing_ticker_df(),
    }
    result = run_k2_plus_backtest(ohlcv, K2PlusConfig(initial_capital_krw=100_000_000))

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert not result.portfolio.equity_curve.empty


_SP_TEST_CONFIG = SPConfig(
    streak_min_len=3, min_rise_pct=1.0, pre_rally_lookback_days=3, peak_search_days=2,
    ma_short=3, ma_long=5, entry_valid_days=10, min_trading_value_krw=0, initial_capital_krw=100_000_000,
)


def _sp_winning_ticker_df():
    # 진입(224 부근) 후 빠르게 반등 -> +5% 절반매도 + 저점대비 17% 전량매도로 순이익 마감
    closes = [100, 100, 100, 130, 169, 219.7, 230, 222, 260, 280]
    highs = [100, 100, 100, 130, 169, 219.7, 235, 225, 270, 290]
    lows = [98, 98, 98, 100, 130, 169, 220, 215, 250, 260]
    return make_df(closes, highs=highs, lows=lows)


def _sp_losing_ticker_df():
    # 진입 후 계속 하락 -> 2차(20일선)/3차(-7%) 추매까지 갔다가 4일 강제청산으로 손실 마감
    closes = [100, 100, 100, 130, 169, 219.7, 230, 225, 200, 190, 180, 170, 165, 160, 158, 156, 154, 152, 150, 148]
    highs = [100, 100, 100, 130, 169, 219.7, 235, 228, 205, 195, 185, 175, 168, 163, 161, 159, 157, 155, 153, 151]
    lows = [98, 98, 98, 100, 130, 169, 220, 198, 188, 178, 168, 158, 155, 150, 148, 146, 144, 142, 140, 138]
    return make_df(closes, highs=highs, lows=lows)


def test_sp_engine_runs_and_produces_metrics_for_multiple_tickers():
    ohlcv = {
        "WIN": _sp_winning_ticker_df(),
        "LOSE": _sp_losing_ticker_df(),
    }
    result = run_sp_backtest(ohlcv, _SP_TEST_CONFIG)

    assert result.metrics.n_trades == 2
    assert result.metrics.n_wins == 1
    assert result.metrics.n_losses == 1
    assert not result.portfolio.equity_curve.empty
