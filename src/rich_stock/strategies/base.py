"""전략 공통 데이터 구조."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Fill:
    date: pd.Timestamp
    price: float
    shares: float  # 매수는 양수, 매도는 음수
    reason: str  # "entry_r1" | "addon_r2" | "exit_breakeven" | "exit_target_r0" | "exit_stop_r3" | "exit_forced_hold"


@dataclass
class Trade:
    ticker: str
    signal_date: pd.Timestamp  # 신호(예: 상한가) 발생일
    fills: list[Fill] = field(default_factory=list)

    @property
    def entry_date(self) -> pd.Timestamp | None:
        entries = [f for f in self.fills if f.shares > 0]
        return entries[0].date if entries else None

    @property
    def exit_date(self) -> pd.Timestamp | None:
        exits = [f for f in self.fills if f.shares < 0]
        return exits[-1].date if exits else None

    @property
    def invested_cash(self) -> float:
        return sum(f.price * f.shares for f in self.fills if f.shares > 0)

    @property
    def realized_cash(self) -> float:
        return sum(f.price * -f.shares for f in self.fills if f.shares < 0)

    @property
    def is_closed(self) -> bool:
        shares_out = sum(f.shares for f in self.fills)
        return abs(shares_out) < 1e-9

    @property
    def pnl(self) -> float:
        return self.realized_cash - self.invested_cash

    @property
    def return_pct(self) -> float:
        invested = self.invested_cash
        return self.pnl / invested if invested else 0.0

    @property
    def exit_reason(self) -> str | None:
        exits = [f for f in self.fills if f.shares < 0]
        return exits[-1].reason if exits else None

    @property
    def holding_days(self) -> int | None:
        if self.entry_date is None or self.exit_date is None:
            return None
        return (self.exit_date - self.entry_date).days


@dataclass
class TradePlan:
    """아직 진입 전인 신호 하나에 대해 "얼마에 사고, 얼마에 손절하고, 얼마에 익절할지"를
    사람이 읽을 수 있게 정리한 것 — simulate_*_trade가 실제로 쓰는 것과 동일한 규칙/숫자다
    (새로 설계한 게 아니라 이미 있는 진입/청산 로직을 미리 보여주는 용도).

    entry_price/stop_price/target_price는 규칙상 확정 가능한 경우에만 채워진다 — 예를 들어
    S2/S4처럼 "종가가 어떤 레벨 아래로 마감하면 그 종가에 매수"하는 기법은 장 마감 전까지
    정확한 체결가를 알 수 없어 None(조건은 entry_desc에 설명). S6은 원문 설계상 가격 손절이
    아예 없어 stop_price/stop_desc가 "손절 없음"을 뜻하는 값이 된다.
    """

    entry_price: float | None
    entry_desc: str
    stop_price: float | None
    stop_desc: str
    target_price: float | None
    target_desc: str
