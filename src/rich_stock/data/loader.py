"""종목별 일봉 OHLCV 로더 (로컬 parquet 캐시).

FinanceDataReader.DataReader (Naver 소스)는 로그인 없이 동작하지만 거래대금(원화)
컬럼을 제공하지 않는다. SR 기법의 500억원 거래대금 필터를 위해
research/step_1_SR기법.md §3 에서 원 자료가 실시간 근사식으로 쓴 것과 동일한 방식
(OHLC 평균 * 거래량)으로 근사 거래대금을 계산한다. 일봉 단위에서는 원 자료가 경고한
"분봉이 길수록 오차가 커진다"는 문제가 없어 근사 오차는 크지 않다. 정밀 검증이 필요하면
KRX_ID 로그인 후 pykrx의 공식 거래대금(ACC_TRDVAL)으로 교체할 것.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd


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

    if not force_refresh and cache_path.exists():
        cached = pd.read_parquet(cache_path)
        if not cached.empty and cached.index.min() <= start_ts and cached.index.max() >= end_ts - end_tolerance:
            return cached.loc[start_ts:end_ts]

    import FinanceDataReader as fdr

    df = fdr.DataReader(ticker, start_ts - pd.Timedelta(days=5), end_ts)
    if df.empty:
        return df

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = df["Volume"] * (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4

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
) -> dict[str, pd.DataFrame]:
    """여러 종목의 일봉 데이터를 순차 수집한다 (Naver 소스 과호출 방지용 sleep 포함)."""
    result: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(tickers):
        try:
            df = load_ohlcv(ticker, start, end, cache_dir=cache_dir)
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
