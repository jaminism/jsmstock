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
