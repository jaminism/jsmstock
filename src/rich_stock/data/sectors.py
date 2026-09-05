"""종목별 섹터/업종 매핑 — 원문의 "동일 테마" 판정을 자동화하기 위한 근사 데이터.

원문 기법들은 "같은 테마에서 상한가가 여러 개 나오면 **테마를 주도하는 1등주만** 사고
2등주·후발주는 건너뛴다"는 규칙을 반복해서 쓴다(step_2 §2 실전 로그에서 "매우 빈번하게
관찰됨"). 문제는 "테마"가 시장 참여자들이 그때그때 만들어내는 개념이라(쿠팡 관련주, 루시드
관련주, 비트코인 테마 등) 고정된 데이터가 없다는 것이다.

**섹터를 테마의 대리변수로 쓴다** — 정확하진 않지만(테마는 섹터를 가로지르는 경우가 많다)
자동으로 얻을 수 있는 것 중에서는 가장 가깝다. 섹터가 다른데 같은 테마인 경우를 놓치므로
필터가 **덜 걸러내는 쪽으로만** 틀린다 — 배제 필터에서는 이쪽이 안전한 방향이다.

**한계(반드시 인지할 것)**: `fdr.StockListing("KRX-DESC")`는 **현재 시점 스냅샷**이라
과거 특정 시점의 섹터를 알 수 없다. 섹터는 자주 바뀌지 않아 근사로 감수하지만, 상장폐지
종목은 아예 조회되지 않는다(그런 종목은 섹터 미상으로 처리 → 필터가 건드리지 않음).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CACHE_NAME = "sectors.parquet"


def get_sector_map(cache_dir: str | Path = ".cache") -> dict[str, str]:
    """{종목코드: 섹터명}. 조회 실패나 섹터 미상은 키가 없다(호출부가 "미상"으로 처리)."""
    cache_path = Path(cache_dir) / CACHE_NAME
    if cache_path.exists():
        df = pd.read_parquet(cache_path)
    else:
        import FinanceDataReader as fdr

        raw = fdr.StockListing("KRX-DESC")
        df = raw[["Code", "Sector"]].dropna().reset_index(drop=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)

    return {str(c): str(s) for c, s in zip(df["Code"], df["Sector"]) if s}
