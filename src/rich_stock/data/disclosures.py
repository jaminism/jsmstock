"""DART 전자공시 캐시를 배제 필터가 쓸 형태로 읽는다.

수집은 `scripts/local/fetch_dart_disclosures.py`가 담당한다(무료 API 키 필요, 로컬 전용).
여기서는 이미 받아둔 parquet만 읽으므로 **네트워크를 타지 않는다** — 백테스트 도중에
외부 호출이 일어나면 재현성이 깨지기 때문이다.

**왜 공시 필터만 효과가 있었나(2026-09-06)**: 원문 기법의 배제 리스트를 자동화하려는 시도가
여러 번 있었는데(무공방·긴N자·선반등·동테마1등주·관리종목스냅샷) 전부 성과 개선이 없었다.
그것들은 **강사의 재량 판단을 가격 데이터로 흉내 낸 근사**였다. 반면 공시는 "났느냐 안 났느냐"가
객관적으로 정해져 있어 근사가 아니고, 실제로 이 필터에서 처음으로 개선이 나왔다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from rich_stock.backtest.filters import ADVERSE_DISCLOSURE_PATTERN, RIGHTS_DISCLOSURE_PATTERN

DEFAULT_CACHE = Path(".cache/dart_disclosures.parquet")

_PATTERNS = {
    "adverse": ADVERSE_DISCLOSURE_PATTERN,
    "rights": RIGHTS_DISCLOSURE_PATTERN,
}


def load_disclosure_dates(
    kind: str, cache_path: Path | str = DEFAULT_CACHE
) -> dict[str, list[pd.Timestamp]]:
    """{종목코드: [해당 유형 공시일...]}.

    Args:
        kind: "adverse"(악재) 또는 "rights"(유증/무증/감자 등 권리락 계열)

    캐시가 없으면 **빈 dict가 아니라 예외**를 던진다 — 빈 dict면 필터가 아무것도 안 걸러내면서
    조용히 통과해, "공시가 없다"와 "데이터를 못 읽었다"가 구분되지 않는다.
    """
    if kind not in _PATTERNS:
        raise ValueError(f"kind는 {sorted(_PATTERNS)} 중 하나여야 합니다: {kind!r}")

    path = Path(cache_path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} 없음 — 먼저 공시를 수집하세요:\n"
            "  python scripts/local/fetch_dart_disclosures.py\n"
            "(DART OpenAPI 무료 키가 dart_credentials.json에 있어야 합니다)"
        )

    df = pd.read_parquet(path)
    names = df["report_nm"].str.replace(r"\s+", " ", regex=True).str.strip()
    matched = df[names.str.contains(_PATTERNS[kind], regex=True, na=False)]
    return {
        str(code): sorted(dates)
        for code, dates in matched.groupby("stock_code")["rcept_dt"].apply(list).items()
    }
