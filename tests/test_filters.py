"""재량 배제 필터 근사 테스트 (backtest/filters.py).

원문 기법은 "신호 + 재량 배제 필터"인데 구현은 오랫동안 신호 부분만 있었다(2026-09-05 확인).
여기 필터들은 그 격차를 자동화 가능한 범위에서 메우려는 시도이며, **전부 기본값 off**다 —
켰을 때 성과가 어떻게 달라지는지 측정한 결과가 config.py 주석에 적혀 있다.
"""

import pandas as pd

from rich_stock.backtest.filters import (
    drop_administrative_issues,
    drop_after_disclosure,
    keep_theme_leader,
)
from rich_stock.strategies.base import Fill, Trade


def _trade(ticker: str, signal_date: str) -> Trade:
    d = pd.Timestamp(signal_date)
    t = Trade(ticker=ticker, signal_date=d)
    t.fills.append(Fill(d, 1000.0, 1.0, "entry"))
    t.fills.append(Fill(d + pd.Timedelta(days=1), 1100.0, -1.0, "exit"))
    return t


def _ohlcv(rows: dict[str, dict[str, float]]) -> dict[str, pd.DataFrame]:
    """{ticker: {날짜: 거래대금}} → 최소 컬럼만 갖춘 DataFrame 묶음."""
    out = {}
    for ticker, by_date in rows.items():
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in by_date])
        out[ticker] = pd.DataFrame({"TradingValue": list(by_date.values())}, index=idx)
    return out


def test_theme_leader_keeps_only_highest_trading_value():
    """같은 날·같은 섹터면 거래대금 1위만 남는다 — 원문의 '1등주만 채택'."""
    trades = [_trade("AAA", "2024-03-04"), _trade("BBB", "2024-03-04"), _trade("CCC", "2024-03-04")]
    ohlcv = _ohlcv({
        "AAA": {"2024-03-04": 300e8},
        "BBB": {"2024-03-04": 900e8},   # 1등
        "CCC": {"2024-03-04": 500e8},
    })
    sectors = {"AAA": "반도체", "BBB": "반도체", "CCC": "반도체"}

    kept, dropped = keep_theme_leader(trades, ohlcv, sectors)

    assert [t.ticker for t in kept] == ["BBB"]
    assert sorted(t.ticker for t in dropped) == ["AAA", "CCC"]


def test_theme_leader_groups_by_sector_and_date():
    """섹터가 다르거나 날짜가 다르면 서로 경쟁하지 않는다."""
    trades = [_trade("AAA", "2024-03-04"), _trade("BBB", "2024-03-04"), _trade("CCC", "2024-03-05")]
    ohlcv = _ohlcv({
        "AAA": {"2024-03-04": 300e8},
        "BBB": {"2024-03-04": 900e8},
        "CCC": {"2024-03-05": 100e8},
    })
    sectors = {"AAA": "반도체", "BBB": "제약", "CCC": "반도체"}

    kept, dropped = keep_theme_leader(trades, ohlcv, sectors)

    assert sorted(t.ticker for t in kept) == ["AAA", "BBB", "CCC"]
    assert dropped == []


def test_theme_leader_keeps_unknown_sector():
    """섹터를 모르면 테마를 특정할 수 없으니 배제 근거도 없다 — 건드리지 않는다.
    (배제 필터는 확신이 없을 때 덜 거르는 쪽이 안전하다.)"""
    trades = [_trade("AAA", "2024-03-04"), _trade("ZZZ", "2024-03-04")]
    ohlcv = _ohlcv({"AAA": {"2024-03-04": 900e8}, "ZZZ": {"2024-03-04": 100e8}})

    kept, dropped = keep_theme_leader(trades, ohlcv, {"AAA": "반도체"})

    assert sorted(t.ticker for t in kept) == ["AAA", "ZZZ"]
    assert dropped == []


def test_theme_leader_is_deterministic_on_ties():
    """거래대금이 같으면 종목코드로 결정한다 — 실행할 때마다 결과가 달라지면 안 된다."""
    trades = [_trade("BBB", "2024-03-04"), _trade("AAA", "2024-03-04")]
    ohlcv = _ohlcv({"AAA": {"2024-03-04": 500e8}, "BBB": {"2024-03-04": 500e8}})
    sectors = {"AAA": "반도체", "BBB": "반도체"}

    for _ in range(3):
        kept, _dropped = keep_theme_leader(trades, ohlcv, sectors)
        assert [t.ticker for t in kept] == ["AAA"]


def test_administrative_filter_only_drops_after_designation():
    """지정일 **이후**의 신호만 배제한다 — 지정 전 신호까지 지우면 미래 정보를 쓰는 셈이다."""
    trades = [_trade("AAA", "2024-01-10"), _trade("AAA", "2024-06-10")]
    designated = {"AAA": pd.Timestamp("2024-03-01")}

    kept, dropped = drop_administrative_issues(trades, designated)

    assert [t.signal_date.date().isoformat() for t in kept] == ["2024-01-10"]
    assert [t.signal_date.date().isoformat() for t in dropped] == ["2024-06-10"]


def test_administrative_filter_ignores_unlisted_tickers():
    trades = [_trade("AAA", "2024-06-10")]

    kept, dropped = drop_administrative_issues(trades, {})

    assert len(kept) == 1 and dropped == []


# --- 공시 기반 배제 (2026-09-06) ------------------------------------------------------
# 재량 판단을 흉내 낸 근사 필터들이 번번이 실패한 것과 달리, 공시는 "났느냐 안 났느냐"라
# 근사가 아니다 — 실제로 이 필터에서 처음 성과 개선이 나왔다.


def test_disclosure_filter_drops_signals_inside_window():
    trades = [_trade("AAA", "2024-03-10"), _trade("AAA", "2024-05-20")]
    disclosures = {"AAA": [pd.Timestamp("2024-03-01")]}

    kept, dropped = drop_after_disclosure(trades, disclosures, window_days=30)

    assert [t.signal_date.date().isoformat() for t in dropped] == ["2024-03-10"]
    assert [t.signal_date.date().isoformat() for t in kept] == ["2024-05-20"]


def test_disclosure_filter_ignores_signals_before_disclosure():
    """공시 전 신호까지 지우면 그때는 알 수 없던 정보를 쓰는 셈이다 — 미래 정보 금지."""
    trades = [_trade("AAA", "2024-02-20")]
    disclosures = {"AAA": [pd.Timestamp("2024-03-01")]}

    kept, dropped = drop_after_disclosure(trades, disclosures, window_days=90)

    assert len(kept) == 1 and dropped == []


def test_disclosure_filter_window_boundary_is_inclusive():
    trades = [_trade("AAA", "2024-03-31"), _trade("AAA", "2024-04-01")]
    disclosures = {"AAA": [pd.Timestamp("2024-03-01")]}

    kept, dropped = drop_after_disclosure(trades, disclosures, window_days=30)

    assert len(dropped) == 1 and dropped[0].signal_date == pd.Timestamp("2024-03-31")
    assert len(kept) == 1


def test_disclosure_filter_leaves_untracked_tickers():
    trades = [_trade("ZZZ", "2024-03-10")]

    kept, dropped = drop_after_disclosure(trades, {"AAA": [pd.Timestamp("2024-03-01")]}, 30)

    assert len(kept) == 1 and dropped == []
