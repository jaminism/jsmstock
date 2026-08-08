"""상한가 판정 로직.

원 자료(research/step_1_SR기법.md, step_2_K2기법.md)의 UL()/상한가체크() 수식은 전일종가에
호가단위(tick size)를 적용해 정확한 상한가 가격을 계산한다. 그러나:
  1) 호가단위 표는 2023-01-25 KRX 개편으로 구간이 바뀌었고,
  2) 원 자료 자신도 보조 검색식(K1_v1)에서는 exact tick 계산 대신
     "등락률 29.5% 이상"이라는 근사 기준을 이미 사용하고 있다.

따라서 이번 구현은 근사 기준을 1차 판정 방식으로 채택한다(설정값: config.SRConfig.limit_up_return_threshold).
정확한 tick 기반 계산이 필요해지면 compute_limit_up_price()를 사용하되, 호가단위 구간은
반드시 대상 시점 기준으로 재검증할 것.
"""

from __future__ import annotations

import pandas as pd

_TICK_TABLE_LEGACY = (
    # (미만 가격, 호가단위) — 2023-01-25 개편 이전
    (1_000, 1),
    (5_000, 5),
    (10_000, 10),
    (50_000, 50),
    (100_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)

_TICK_TABLE_2023 = (
    # 2023-01-25 개편 이후 (코스피/코스닥 공통 단순화)
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)

_TICK_REGIME_CUTOVER = pd.Timestamp("2023-01-25")


def tick_size(price: float, date: pd.Timestamp) -> int:
    """지정 시점 기준 KRX 호가단위."""
    table = _TICK_TABLE_2023 if pd.Timestamp(date) >= _TICK_REGIME_CUTOVER else _TICK_TABLE_LEGACY
    for ceiling, tick in table:
        if price < ceiling:
            return tick
    return table[-1][1]


def round_down_to_tick(price: float, date: pd.Timestamp) -> int:
    tick = tick_size(price, date)
    return int(price // tick) * tick


def compute_limit_up_price(prev_close: float, date: pd.Timestamp) -> int:
    """전일종가 기준 정확한 상한가 가격 (tick 반영, 참고/검증용)."""
    limit_pct = 0.15 if pd.Timestamp(date) < pd.Timestamp("2015-06-15") else 0.30
    raw = prev_close * (1 + limit_pct)
    return round_down_to_tick(raw, date)


def is_limit_up_day(
    high: float,
    close: float,
    prev_close: float,
    threshold: float = 0.295,
) -> bool:
    """'진짜' 상한가 판정: 당일 고가==당일 종가(고가에서 마감) AND 등락률>=threshold.

    고가/종가 완전 일치 대신 근사 비교(1e-6)를 쓰는 이유는 부동소수 오차 방지용이며,
    가격이 정수(원) 단위인 실제 데이터에서는 정확히 일치해야 정상이다.
    """
    if prev_close <= 0:
        return False
    if abs(high - close) > max(1e-6, close * 1e-9):
        return False
    ret = close / prev_close - 1
    return ret >= threshold
