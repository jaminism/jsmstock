import os
import sys
import types

import pandas as pd
import pytest

from rich_stock.data.loader import load_ohlcv, load_universe_ohlcv


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


def test_universe_force_refresh_bypasses_cache_tolerance(tmp_path):
    # force_refresh=True면 7일 허용오차 이내라도 무조건 재다운로드를 시도해야 한다(라이브
    # 스캐너가 "어제 데이터로 계속 캐시 적중"에 안 걸리게 하려는 목적). load_universe_ohlcv는
    # 종목별 예외를 삼키고 on_progress로만 알리므로, 그 콜백으로 재다운로드 시도(=네트워크 호출
    # 발생) 여부를 확인한다.
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-30")
    cached.to_parquet(cache_dir / "TEST.parquet")

    errors = []
    load_universe_ohlcv(
        ["TEST"], "2021-01-01", "2024-12-31", cache_dir=cache_dir, force_refresh=True,
        on_progress=lambda i, total, ticker, error: errors.append(error),
    )
    assert len(errors) == 1
    assert "네트워크" in str(errors[0])


def test_universe_default_still_uses_cache(tmp_path):
    # force_refresh 기본값(False)에서는 기존처럼 캐시 적중이 유지되어야 한다(회귀 방지).
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-30")
    cached.to_parquet(cache_dir / "TEST.parquet")

    result = load_universe_ohlcv(["TEST"], "2021-01-01", "2024-12-31", cache_dir=cache_dir)
    assert "TEST" in result
    assert not result["TEST"].empty


class _RecordingFDR(types.ModuleType):
    """실제 네트워크 대신 호출 인자를 기록하고 지정된 구간의 새 봉만 돌려주는 가짜 FinanceDataReader."""

    def __init__(self, name, new_price=2000):
        super().__init__(name)
        self.calls = []
        self._new_price = new_price

    def DataReader(self, ticker, start, end):  # noqa: N802 - 원본 API 이름을 맞춤
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        self.calls.append((ticker, start_ts, end_ts))
        dates = pd.bdate_range(start_ts, end_ts)
        if len(dates) == 0:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return pd.DataFrame(
            {"Open": self._new_price, "High": self._new_price, "Low": self._new_price,
             "Close": self._new_price, "Volume": 500},
            index=dates,
        )


def test_force_refresh_requests_only_recent_range_when_cache_exists(tmp_path, monkeypatch):
    # 캐시가 이미 start~(거의)end를 커버하면, force_refresh=True라도 원래 start(2021년)부터가
    # 아니라 캐시 마지막 거래일 부근부터만 다시 받아야 한다(전종목 재다운로드 방지가 핵심 목적).
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-20")
    cached.to_parquet(cache_dir / "TEST.parquet")

    fake_fdr = _RecordingFDR("FinanceDataReader")
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fake_fdr)

    result = load_ohlcv("TEST", "2021-01-01", "2024-12-31", cache_dir=cache_dir, force_refresh=True)

    assert len(fake_fdr.calls) == 1
    requested_start = fake_fdr.calls[0][1]
    assert requested_start > pd.Timestamp("2024-12-01")  # 2021년부터가 아니라 캐시 마지막일 근처부터
    assert not result.empty


def test_force_refresh_merges_new_data_and_keeps_old_untouched(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-20")
    cached.to_parquet(cache_dir / "TEST.parquet")

    fake_fdr = _RecordingFDR("FinanceDataReader", new_price=2000)
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fake_fdr)

    result = load_ohlcv("TEST", "2021-01-01", "2024-12-31", cache_dir=cache_dir, force_refresh=True)

    # 기존 캐시(가격 1000)의 오래된 구간은 그대로 남아있어야 한다.
    assert result.loc["2021-01-04", "Close"] == 1000
    # 새로 받은(가격 2000) 구간이 실제로 병합돼 마지막 날짜까지 확장돼야 한다.
    assert result.index.max() >= pd.Timestamp("2024-12-30")
    assert result.loc[result.index.max(), "Close"] == 2000
    # 병합 경계에서 PrevClose가 NaN 없이 이어져야 한다(구간별 개별 계산이 아니라 병합 후 재계산).
    assert not result["PrevClose"].iloc[1:].isna().any()


def test_force_refresh_persists_merged_result_to_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()
    cached = _make_cached_df("2021-01-01", "2024-12-20")
    cached.to_parquet(cache_dir / "TEST.parquet")

    fake_fdr = _RecordingFDR("FinanceDataReader", new_price=2000)
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fake_fdr)

    load_ohlcv("TEST", "2021-01-01", "2024-12-31", cache_dir=cache_dir, force_refresh=True)

    on_disk = pd.read_parquet(cache_dir / "TEST.parquet")
    assert on_disk.index.max() >= pd.Timestamp("2024-12-30")
    assert on_disk.loc["2021-01-04", "Close"] == 1000


def test_force_refresh_falls_back_to_full_fetch_when_no_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ohlcv"
    cache_dir.mkdir()

    fake_fdr = _RecordingFDR("FinanceDataReader", new_price=2000)
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fake_fdr)

    result = load_ohlcv("TEST", "2024-12-01", "2024-12-31", cache_dir=cache_dir, force_refresh=True)

    assert len(fake_fdr.calls) == 1
    requested_start = fake_fdr.calls[0][1]
    assert requested_start <= pd.Timestamp("2024-12-01")  # 캐시가 없으니 요청 시작일부터 전체 조회
    assert not result.empty


def test_universe_cache_age_uses_epoch_not_local_naive_clock(tmp_path, monkeypatch):
    """캐시 나이를 잴 때 파일 mtime(진짜 epoch)과 로컬 naive 시각을 섞으면 안 된다(2026-09-04).

    예전 코드는 `pd.Timestamp.now() - pd.Timestamp(mtime, unit="s")`였는데, 뒤쪽은 epoch를
    **UTC naive**로 바꾸고 앞쪽은 머신 로컬 naive라 KST 머신에서 나이가 항상 9시간 부풀려졌다
    — TTL 1일짜리 캐시가 실질 15시간 만에 만료됐다(UTC 컨테이너에서만 우연히 맞았다)."""
    import time

    from rich_stock.data import universe

    cache_path = tmp_path / "universe.parquet"
    pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"], "Marcap": [1]}).to_parquet(
        cache_path, index=False
    )
    # TTL(1일) 안쪽이지만 9시간 skew가 있으면 만료로 오판되는 나이로 맞춘다.
    age_sec = 20 * 3600
    stamp = time.time() - age_sec
    os.utime(cache_path, (stamp, stamp))

    assert universe._load_cached(cache_path) is not None

    # TTL을 넘긴 캐시는 여전히 정상적으로 버려야 한다.
    stale = time.time() - 30 * 3600
    os.utime(cache_path, (stale, stale))
    assert universe._load_cached(cache_path) is None
