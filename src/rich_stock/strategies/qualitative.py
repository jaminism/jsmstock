"""SR 정성적 배제 필터의 정량적 근사치.

출처: research/step_0_공통자료.md §3~5,7 (2026-08-08 step_0_common/step_1 추가조사).
바른손 사례(원문)의 감점제(긴N자 -20, 선반등 -10/건, 후발주 -30, 총점 0점이 매수 적정)를
그대로 본떠 이진 배제가 아닌 가중합 점수로 구현한다.

**의도적으로 구현하지 않은 것**: 후발주(테마 내 2등주 이하) 판정. 원 자료(1등주와 2등주
매매법.docx)를 전문 확인한 결과 정량 기준이 전혀 없고("재료 지속성"이라는 순수 정성적 판단뿐),
테마 그룹핑 데이터도 없어 신뢰할 만한 프록시를 만들 근거가 없다. risk-manager가 기존 리서치들에서
반복 지적한 공백이 그대로 남아있다는 뜻이며, 억지로 근사치를 넣기보다 미구현 상태로 명시하는 편이
낫다고 판단했다.

**주의**: 아래 각 임계값(60일/85%/5일/15%/60% 등)은 원문에 없거나("무공방≈3개월"처럼 대략적으로만
언급됨) 이번에 새로 설계한 근사치다. 검증된 최적값이 아니라 출발점이므로, 백테스트 결과를 보고
민감도를 점검해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from rich_stock.config import SRConfig


@dataclass
class QualScore:
    score: float
    new_high_120: bool
    long_n_shape: bool
    no_resistance: bool
    pre_rebound: bool
    breakdown: dict[str, float] = field(default_factory=dict)


def compute_qualitative_score(
    df: pd.DataFrame,
    ul_index: int,
    prior_ul_index: int | None,
    config: SRConfig,
) -> QualScore:
    """상한가 이벤트 하나에 대한 정성적 점수를 계산한다.

    Args:
        df: 종목의 일봉 데이터프레임 (index=Date, High/Close 필요)
        ul_index: 이번 상한가 이벤트의 df상 위치
        prior_ul_index: 같은 종목의 직전 상한가 이벤트 위치 (없으면 None)
    """
    new_high_120 = _is_new_high(df, ul_index, config.new_high_lookback_days)
    long_n = _is_long_n_shape(df, ul_index, config)
    no_resistance = _is_no_resistance(df, ul_index, config)
    pre_rebound = _is_pre_rebound(ul_index, prior_ul_index, config.pre_rebound_lookback_days)

    breakdown: dict[str, float] = {}

    # 120일 신고가는 "긴N자로 보여도 신고가 찍은 날부터는 매매"(질문게시판 원문)를 근거로
    # 긴N자/무공방 감점을 모두 취소하는 override로 취급한다 (원문은 긴N자 사례만 명시했으나,
    # 두 배제사유 모두 '뚜렷한 저항 없이 새 가격대로 진입'이라는 동일한 구조적 우려를 공유한다고
    # 보아 이번 구현에서 확장 적용했다 — 이는 추정이며 원문에 명시된 사실이 아니다).
    if not new_high_120:
        if long_n:
            breakdown["long_n_shape"] = config.long_n_penalty
        if no_resistance:
            breakdown["no_resistance"] = config.no_resistance_penalty

    if pre_rebound:
        breakdown["pre_rebound"] = config.pre_rebound_penalty

    score = sum(breakdown.values())

    return QualScore(
        score=score,
        new_high_120=new_high_120,
        long_n_shape=long_n,
        no_resistance=no_resistance,
        pre_rebound=pre_rebound,
        breakdown=breakdown,
    )


def _is_new_high(df: pd.DataFrame, ul_index: int, lookback_days: int) -> bool:
    start = max(0, ul_index - lookback_days)
    if start >= ul_index:
        return True  # 이력이 짧아 비교 불가 -> 사실상 신고가로 간주(관대하게 처리)
    prior_max_high = df["High"].iloc[start:ul_index].max()
    return bool(df["High"].iloc[ul_index] >= prior_max_high)


def _is_long_n_shape(df: pd.DataFrame, ul_index: int, config: SRConfig) -> bool:
    n = config.long_n_lookback_days
    start = ul_index - n
    if start < 0:
        return False
    closes = df["Close"].iloc[start:ul_index]
    if len(closes) < n or closes.iloc[0] <= 0:
        return False
    total_return = closes.iloc[-1] / closes.iloc[0] - 1
    if total_return < config.long_n_min_total_return:
        return False
    daily_returns = closes.pct_change().dropna()
    if daily_returns.empty:
        return False
    max_single_day = daily_returns.max()
    return bool(max_single_day < total_return * config.long_n_max_single_day_share)


def _is_no_resistance(df: pd.DataFrame, ul_index: int, config: SRConfig) -> bool:
    lookback = config.no_resistance_lookback_days
    start = max(0, ul_index - lookback)
    if start >= ul_index:
        return False  # 이력이 짧아 판단 불가 -> 배제하지 않음(관대하게 처리)
    prior_max_high = df["High"].iloc[start:ul_index].max()
    ul_high = df["High"].iloc[ul_index]
    if ul_high <= 0:
        return False
    return bool(prior_max_high < ul_high * config.no_resistance_price_ratio)


def _is_pre_rebound(ul_index: int, prior_ul_index: int | None, lookback_days: int) -> bool:
    if prior_ul_index is None:
        return False
    return (ul_index - prior_ul_index) <= lookback_days
