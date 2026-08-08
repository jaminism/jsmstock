import sys
import types

import pandas as pd
import pytest

from rich_stock.data.loader import load_ohlcv


class _ExplodingFDR(types.ModuleType):
    """네트워크 호출이 실제로 일어나면 즉시 실패시키기 위한 가짜 FinanceDataReader."""

    def DataReader(self, *args, **kwargs):  # noqa: N802 - 원본 API 이름을 맞춤
        raise AssertionError("캐시가 있는데도 네트워크 재다운로드를 시도했다 (캐시 적중 로직 회귀)")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    monkeypatch.setitem(sys.modules, "FinanceDataReader", _ExplodingFDR("FinanceDataReader"))
    yield


def _make_cached_df(start, end):
    dates = pd.bdate_range(start, end)
    df = pd.DataFrame(
        {
            "Open": 1000,
            "High": 1000,
            "Low": 1000,
            "Close": 1000,
            "Volume": 1000,
            "PrevClose": 1000,
            "TradingValue": 1_000_000,
        },
        index=dates,
    )
    return df


def test_cache_hit_when_end_date_is_a_holiday(tmp_path):
    # 캐시 마지막 거래일이 2024-12-30 (2024-12-31은 휴장일 가정)인 상황을 재현.
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-30")
    cached.to_parquet(cache_dir / "TEST.parquet")

    # 요청 종료일이 캐시의 실제 마지막 거래일보다 하루 늦어도(주말/공휴일), 네트워크를 다시 타면 안 된다.
    result = load_ohlcv("TEST", "2021-01-01", "2024-12-31", cache_dir=cache_dir)
    assert not result.empty
    assert result.index.max() == pd.Timestamp("2024-12-30")


def test_cache_miss_when_end_date_far_beyond_cache():
    # 이 테스트는 네트워크 호출이 "일어나야" 정상이므로 _ExplodingFDR이 의도적으로 터뜨려야 한다.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "ohlcv"
        cache_dir.mkdir()
        cached = _make_cached_df("2021-01-01", "2021-06-30")
        cached.to_parquet(cache_dir / "TEST.parquet")

        with pytest.raises(AssertionError, match="네트워크"):
            load_ohlcv("TEST", "2021-01-01", "2024-12-31", cache_dir=cache_dir)
