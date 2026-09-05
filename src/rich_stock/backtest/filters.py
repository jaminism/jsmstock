"""원문의 재량 배제 필터를 자동화 가능한 범위에서 근사한다.

**왜 필요한가(2026-09-05 검증 결론)**: 원문 기법은 "신호 + 재량 배제 필터" 두 부분인데,
구현은 오랫동안 **신호 부분만** 있었다. S1 원문 로그의 "안함"(매수 보류) 사유 빈도순은
`날짜오버 > 무공방 > 긴N자상한가 > 단기과열 > 동테마/후발주 > 유증무증권리락 > 관리종목/악재`
인데, 자동화돼 있던 건 날짜오버(entry_valid_days) 하나뿐이었다. 즉 백테스트도 실전검증도
**원문 기법의 절반**을 측정하고 있었다.

여기 있는 필터는 여러 종목을 가로질러 봐야 하는 것들이라(같은 날 어느 종목이 1등인가 등)
종목 단위 `backtest_ticker`가 아니라 포트폴리오 배분 직전 단계에서 적용한다.

**전부 기본값 off다** — 켰을 때 성과가 어떻게 달라지는지 측정해 보고 결정할 대상이지,
검증 없이 켜둘 값이 아니다.
"""

from __future__ import annotations

import pandas as pd

from rich_stock.strategies.base import Trade


def keep_theme_leader(
    trades: list[Trade],
    ohlcv: dict[str, pd.DataFrame],
    sector_by_ticker: dict[str, str],
) -> tuple[list[Trade], list[Trade]]:
    """같은 날·같은 섹터에 신호가 여러 개면 **거래대금 1위 하나만** 남긴다.

    원문: "동일 테마(쿠팡 관련주, 루시드 관련주, 비트코인 테마 등)에서 상한가가 여러 개 나올
    경우 테마를 주도하는 **1등주만 채택**하고 2등주·3등주·꼴등주는 '안함'으로 스킵" — 실전
    로그 전반에서 매우 빈번하게 관찰된 규칙이다.

    "테마 주도력"의 대리변수로 **신호일 거래대금**을 쓴다. 원문이 1등주를 고르는 기준으로
    반복해 언급하는 게 "시장의 관심(거래대금, 인기순위)"이라 가장 가까운 관측값이다.

    섹터를 모르는 종목(상장폐지 등으로 조회 안 되는 경우)은 **건드리지 않는다** — 테마를
    특정할 수 없으면 배제 근거도 없기 때문. 배제 필터는 확신이 없을 때 덜 거르는 쪽이 안전하다.

    Returns:
        (남긴 트레이드, 걸러낸 트레이드)
    """
    groups: dict[tuple, list[tuple[float, Trade]]] = {}
    unknown: list[Trade] = []

    for t in trades:
        sector = sector_by_ticker.get(t.ticker)
        if sector is None:
            unknown.append(t)
            continue
        df = ohlcv.get(t.ticker)
        if df is None or t.signal_date not in df.index:
            unknown.append(t)
            continue
        tv = float(df.loc[t.signal_date, "TradingValue"])
        groups.setdefault((t.signal_date, sector), []).append((tv, t))

    kept, dropped = list(unknown), []
    for members in groups.values():
        if len(members) == 1:
            kept.append(members[0][1])
            continue
        # 거래대금 내림차순 — 동률이면 종목코드로 결정론적 처리(재현성)
        members.sort(key=lambda x: (-x[0], x[1].ticker))
        kept.append(members[0][1])
        dropped.extend(m[1] for m in members[1:])

    kept.sort(key=lambda t: (t.signal_date, t.ticker))
    return kept, dropped


ADVERSE_DISCLOSURE_PATTERN = (
    "횡령|배임|관리종목|불성실공시|감사의견|의견거절|회생절차|상장폐지|매매거래정지"
)
"""원문 배제 리스트의 "관리종목/악재"에 해당하는 공시 보고서명 패턴.

**이건 재량 판단의 근사가 아니라 사실 데이터다** — 무공방/긴N자/동테마 같은 필터는 강사의
차트 판단을 흉내 내야 해서 번번이 실패했지만(2026-08-09, 2026-09-06 두 차례), 공시는
"났느냐 안 났느냐"라 근사가 아니다. 실제로 이 필터만 성과가 개선됐다."""

RIGHTS_DISCLOSURE_PATTERN = "유상증자결정|무상증자결정|감자결정|주식분할결정|주식병합결정"
"""원문 배제 리스트의 "유증무증권리락 구간 내 금지"에 해당하는 공시.

권리락일 자체는 공시에 없으므로 **공시일로부터 일정 기간**을 배제 구간으로 근사한다
(공시에서 권리락까지 통상 한 달 이내)."""


def drop_after_disclosure(
    trades: list[Trade],
    disclosure_dates: dict[str, list[pd.Timestamp]],
    window_days: int,
) -> tuple[list[Trade], list[Trade]]:
    """공시일로부터 `window_days` 이내에 발생한 신호의 트레이드를 걸러낸다.

    **공시일 이후만 본다** — 신호일이 공시일보다 앞서면 그때는 알 수 없었던 정보이므로
    배제하면 미래 정보를 쓰는 게 된다.

    Args:
        disclosure_dates: {종목코드: [공시일...]}
        window_days: 공시일 기준 며칠까지 배제할지(캘린더일)
    """
    kept, dropped = [], []
    limit = pd.Timedelta(days=window_days)
    for t in trades:
        dates = disclosure_dates.get(t.ticker)
        if dates and any(d <= t.signal_date <= d + limit for d in dates):
            dropped.append(t)
        else:
            kept.append(t)
    return kept, dropped


def drop_administrative_issues(
    trades: list[Trade],
    designated_at: dict[str, pd.Timestamp],
) -> tuple[list[Trade], list[Trade]]:
    """신호일 시점에 **이미 관리종목으로 지정돼 있던** 종목의 트레이드를 걸러낸다.

    원문의 배제 리스트에 "관리종목/악재"가 들어 있다(S1 "긴 무당똥 유후홀" 니모닉의 마지막).

    **데이터 한계**: `fdr.StockListing("KRX-ADMINISTRATIVE")`는 **현재 관리종목인 종목들의
    지정일**만 준다 — 과거에 지정됐다가 해제된 종목은 목록에 없다. 따라서 이 필터는 실제보다
    **덜 걸러낸다**(놓치는 쪽으로만 틀린다). 지정일 이후의 신호만 배제하므로 미래 정보를
    쓰지는 않는다.
    """
    kept, dropped = [], []
    for t in trades:
        at = designated_at.get(t.ticker)
        if at is not None and t.signal_date >= at:
            dropped.append(t)
        else:
            kept.append(t)
    return kept, dropped
