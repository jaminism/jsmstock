"""종목 유니버스 조회.

KRX data.krx.co.kr 의 전종목 스냅샷 API는 최근 로그인(KRX_ID/KRX_PW)을 요구하도록 바뀌어
pykrx의 get_market_ticker_list / get_market_ohlcv_by_ticker 는 로그인 없이 동작하지 않는다
(2026-08 확인). 대신 FinanceDataReader.StockListing('KRX')은 로그인 없이 현재 시점의
KOSPI/KOSDAQ 전종목 스냅샷(시가총액 포함)을 제공하므로 이를 유니버스 소스로 사용한다.

한계: 이 스냅샷은 "현재 상장된" 종목만 포함하므로, 과거 백테스트 기간 중 상장폐지/합병된
종목은 유니버스에서 누락된다 (생존편향, survivorship bias). 1차 버전에서는 이를 감수하고
진행하며, 정밀 검증 단계에서는 KRX_ID 계정을 발급받아 정확한 과거 시점별 유니버스로
교체할 것을 권장한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_CACHE_TTL = pd.Timedelta(days=1)


def get_universe(cache_dir: str | Path = ".cache", min_market_cap: float | None = None) -> pd.DataFrame:
    """KOSPI/KOSDAQ 전종목 스냅샷을 반환한다.

    Returns:
        columns: Code, Name, Market, Marcap
    """
    cache_path = Path(cache_dir) / "universe.parquet"
    df = _load_cached(cache_path)
    if df is None:
        import FinanceDataReader as fdr

        raw = fdr.StockListing("KRX")
        df = raw[["Code", "Name", "Market", "Marcap"]].dropna(subset=["Code"]).reset_index(drop=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)

    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    if min_market_cap is not None:
        df = df[df["Marcap"] >= min_market_cap]
    return df.sort_values("Marcap", ascending=False).reset_index(drop=True)


def _load_cached(cache_path: Path) -> pd.DataFrame | None:
    if not cache_path.exists():
        return None
    age = pd.Timestamp.now() - pd.Timestamp(cache_path.stat().st_mtime, unit="s")
    if age > _CACHE_TTL:
        return None
    return pd.read_parquet(cache_path)
