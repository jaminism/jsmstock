"""종목별 일봉 OHLCV 로더 (로컬 parquet 캐시).

FinanceDataReader.DataReader (Naver 소스)는 로그인 없이 동작하지만 거래대금(원화)
컬럼을 제공하지 않는다. S1 기법의 500억원 거래대금 필터를 위해
research/step_1_S1기법.md §3 에서 원 자료가 실시간 근사식으로 쓴 것과 동일한 방식
(OHLC 평균 * 거래량)으로 근사 거래대금을 계산한다. 일봉 단위에서는 원 자료가 경고한
"분봉이 길수록 오차가 커진다"는 문제가 없어 근사 오차는 크지 않다. 정밀 검증이 필요하면
KRX_ID 로그인 후 pykrx의 공식 거래대금(ACC_TRDVAL)으로 교체할 것.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

_BASE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

INCREMENTAL_REFRESH_OVERLAP_DAYS = 5
"""force_refresh 증분 갱신 시, 캐시 마지막 거래일보다 이만큼 이전 날짜부터 다시 받아 겹쳐
붙인다. 데이터 제공처가 최근 며칠치 수정(정정)을 반영하는 경우를 대비한 여유 — 그 이전
구간(이미 여러 날 지나 확정됐을 값)까지 매일 재다운로드할 필요는 없다."""


def _with_derived_columns(raw: pd.DataFrame) -> pd.DataFrame:
    """원시 OHLCV(Open/High/Low/Close/Volume)에 PrevClose/근사 TradingValue를 계산해 붙인다."""
    df = raw[_BASE_COLUMNS].copy()
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = df["Volume"] * (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    return df


def load_ohlcv(
    ticker: str,
    start: str,
    end: str,
    cache_dir: str | Path = ".cache/ohlcv",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """단일 종목의 일봉 OHLCV + 근사 거래대금(TradingValue)을 반환한다.

    Returns:
        index: Date (DatetimeIndex)
        columns: Open, High, Low, Close, Volume, PrevClose, TradingValue
    """
    cache_path = Path(cache_dir) / f"{ticker}.parquet"
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    # end_ts가 주말/공휴일이면 실제 마지막 거래일은 항상 end_ts보다 며칠 이른 날짜이므로,
    # 정확히 end_ts와 일치하는지가 아니라 근접한지(7일 이내)로 캐시 충족 여부를 판정한다.
    # 엄격히 일치시키면 매 실행마다 불필요한 재다운로드가 전종목에 걸쳐 발생한다 (실측: 2562종목
    # 기준 캐시 적중 시 수십 초면 끝날 백테스트가 재다운로드 때문에 10분 이상 걸리는 원인이었음).
    end_tolerance = pd.Timedelta(days=7)

    cached: pd.DataFrame | None = None
    if cache_path.exists():
        loaded = pd.read_parquet(cache_path)
        if not loaded.empty:
            cached = loaded

    fresh_enough = cached is not None and cached.index.min() <= start_ts and cached.index.max() >= end_ts - end_tolerance
    if not force_refresh and fresh_enough:
        return cached.loc[start_ts:end_ts]

    import FinanceDataReader as fdr

    if force_refresh and cached is not None and cached.index.min() <= start_ts:
        # 캐시가 이미 필요한 시작일 이전부터 존재하면, 라이브 스캐너용 "최신 데이터 보장"을 위해
        # 전종목을 처음(2021년~)부터 매번 재다운로드하는 대신 캐시 마지막 거래일 부근부터만 새로
        # 받아 합친다 — 이게 force_refresh가 매일 몇 분~몇십 분씩 걸리던 주된 원인이었다.
        fetch_from = cached.index.max() - pd.Timedelta(days=INCREMENTAL_REFRESH_OVERLAP_DAYS)
        fetched = fdr.DataReader(ticker, fetch_from, end_ts)
        raw = pd.concat([cached[_BASE_COLUMNS], fetched[_BASE_COLUMNS]]) if not fetched.empty else cached[_BASE_COLUMNS]
    else:
        fetched = fdr.DataReader(ticker, start_ts - pd.Timedelta(days=5), end_ts)
        if fetched.empty:
            return fetched
        raw = fetched[_BASE_COLUMNS]

    # 겹치는 날짜는 새로 받은 값(뒤에 concat된 쪽)으로 덮어쓴다 — 데이터 제공처의 정정 반영.
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    df = _with_derived_columns(raw)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return df.loc[start_ts:end_ts]


def load_universe_ohlcv(
    tickers: list[str],
    start: str,
    end: str,
    cache_dir: str | Path = ".cache/ohlcv",
    sleep_sec: float = 0.05,
    on_progress=None,
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """여러 종목의 일봉 데이터를 순차 수집한다 (Naver 소스 과호출 방지용 sleep 포함).

    force_refresh=True면 캐시의 7일 허용오차를 무시하고 최신 데이터를 다시 받는다 — 매일 도는
    라이브 스캐너처럼 "어제/오늘 데이터가 실제로 반영됐는지"가 중요한 경우에 쓴다. 캐시가 이미
    있으면 전체 히스토리가 아니라 캐시 마지막 거래일 부근(INCREMENTAL_REFRESH_OVERLAP_DAYS)부터만
    증분으로 받아 합치므로, 캐시가 없는 최초 실행이 아닌 이상 전종목 재다운로드보다 훨씬 빠르다.
    백테스트처럼 과거 구간을 재현할 때는 기본값(False)이 (캐시가 있다면) 여전히 가장 빠르다.
    """
    result: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(tickers):
        try:
            df = load_ohlcv(ticker, start, end, cache_dir=cache_dir, force_refresh=force_refresh)
            if not df.empty:
                result[ticker] = df
        except Exception as exc:  # noqa: BLE001 - 개별 종목 실패는 건너뛰고 계속 진행
            if on_progress:
                on_progress(i + 1, len(tickers), ticker, error=exc)
            time.sleep(sleep_sec)
            continue
        if on_progress:
            on_progress(i + 1, len(tickers), ticker, error=None)
        time.sleep(sleep_sec)
    return result
