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

    stop_pct/target_pct는 매수가 대비 손절/익절 수익률(%)이다 — 절대가격(stop_price 등)이
    None이어도(종가베팅이라 진입가 미확정) 비율 자체는 알 수 있는 경우가 있어(예: S2/S4는
    "매수가 대비 -7%"가 config.stop_loss_pct로 고정) 따로 둔다. 익절%은 진입가가 미확정인
    기법(S2/S4)은 되돌림선 레벨을 진입가의 근사치로 써서 계산한 근사값이다(실제 체결가는 그날
    종가라 레벨과 정확히 같지 않음) — 정확한 값이 아니라 참고용임을 호출부에서 감안할 것.
    """

    entry_price: float | None
    entry_desc: str
    stop_price: float | None
    stop_pct: float | None
    stop_desc: str
    target_price: float | None
    target_pct: float | None
    target_desc: str


@dataclass
class EntryOrderPlan:
    """자동매매(scripts/local/auto_trader.py)가 진입 주문을 실제로 낼 때 참고하는 계획 —
    TradePlan(사람이 읽는 설명)과 별개로 프로그램이 문자열 파싱 없이 바로 쓸 수 있는 버전이다.

    order_style:
      - "fixed_limit": limit_price에 지정가 매수를 걸어두면 거래소가 터치 시 체결해준다
        (S1/S3/S5). KRX 지정가는 당일만 유효해 entry_valid_trading_days에 걸쳐 있으면
        매일 같은 가격으로 재주문해야 한다.
      - "daily_recompute_limit": 매일 값이 바뀌는 기준선(이동평균 등)이라 매일 아침 새
        limit_price로 재계산해 재주문해야 한다(S6).
      - "close_bet": 그날 종가가 어떤 레벨 이하로 마감해야 매수되는 방식이라 limit_price가
        사전에 확정되지 않는다(S2/S4) — 장마감 임박(15:20~) 폴링으로 조건을 판단해 시장가로
        매수한다.
    """

    order_style: str
    limit_price: float | None
    entry_valid_trading_days: int


@dataclass
class Bracket:
    """체결 확정 후 계산되는 손절가/익절가/최대 보유 거래일수 — evaluate_bracket()이 이 값들과
    현재가를 비교해 청산 여부를 판정한다. 1단계 범위는 추매/부분매도 없는 단순 브라켓이다
    (S1의 추매·본절매도, S6의 단계별 청산은 2단계로 미룸).

    is_safety_override=True는 원문 기법에는 없는 값을 자동매매 안전장치로 추가한 경우다(S6은
    원문 설계상 가격 손절이 아예 없음) — 사후 분석 시 "이 청산이 원문 규칙이 아니라 안전장치
    때문"임을 구분할 수 있도록 표시만 하고 값 자체는 넉넉하게 잡아 정상적인 변동에서는 거의
    발동하지 않게 한다.
    """

    stop_price: float | None
    target_price: float | None
    max_hold_trading_days: int | None
    stop_reason: str
    target_reason: str
    is_safety_override: bool = False


def evaluate_bracket(bracket: Bracket, current_price: float, trading_days_held: int) -> str:
    """Bracket과 현재가/경과 거래일을 비교해 청산 판정을 반환한다.

    "hold" | "exit_stop" | "exit_target" | "exit_forced_hold" 중 하나 — 6개 기법 공통으로
    쓰는 단일 함수다(simulate_*_trade의 손절/익절 판정과 동일한 부등호 방향: 손절은 현재가가
    stop_price "이하", 익절은 현재가가 target_price "이상"). 손절과 익절 조건을 같은 폴링에서
    동시에 만족하면(일봉 시뮬레이션의 "하루 안에 둘 다 터치" 상황과 동일한 종류의 모호함)
    보수적으로 손절을 먼저 반환한다.
    """
    if bracket.stop_price is not None and current_price <= bracket.stop_price:
        return "exit_stop"
    if bracket.target_price is not None and current_price >= bracket.target_price:
        return "exit_target"
    if bracket.max_hold_trading_days is not None and trading_days_held >= bracket.max_hold_trading_days:
        return "exit_forced_hold"
    return "hold"


def tradable_lows(window: pd.DataFrame) -> pd.Series:
    """거래정지/이상거래로 OHLCV가 0으로 찍힌 로우를 제외한 저가 시리즈.

    되돌림 저점(RL)을 구할 때 이 필터를 반드시 거쳐야 한다 — 거래정지일은 데이터 제공처가
    OHLC를 전부 0으로 내려주는데(Close만 직전 종가로 이어짐), 그 0이 구간 최저가로 잡히면
    되돌림선이 실제 가격대와 무관하게 훨씬 아래로 내려간다. 2026-08-12에 S4/S5에서 이 버그가
    실거래로 드러나(226340: 상/하한가 오류로 진입주문 반복 거부) 그때 s45.py만 고쳤는데,
    2026-09-05에 S2/S3도 같은 계산을 필터 없이 하고 있었음이 확인됐다(같은 종목 8/11 신호의
    RL이 0으로 저장되어 있었고, 그 결과 S2 진입선이 6,826원이어야 할 자리에 6,158원으로 잡혀
    8/13 종가 6,530원에서 체결됐어야 할 매수를 조용히 놓쳤다). 계산이 네 군데로 흩어져 한 곳만
    고쳐진 것이 재발의 직접 원인이라, 이후로는 전 기법이 이 헬퍼 하나를 공유한다.
    """
    return window.loc[window["Volume"] > 0, "Low"]
