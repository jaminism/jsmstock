"""S2 전략 — 신호 탐지 및 개별 종목 트레이드 시뮬레이션.

규칙 출처: research/step_3_S2기법.md §2, §3.
S3([[project-s3-backtest-engine]])와 동일한 피보나치 셋업(상한가 발생일에 고정된 RH/RL)을 공유하되,
되돌림 비율과 진입 방식이 다르다:

  1. **진입 방식이 S3와 다르다 — "종가베팅"**. S3는 장중 저가가 되돌림선을 터치하면 그 레벨
     가격에 체결(장중 지정가 매수)로 근사했지만, S2은 원문상 "S2선을 훼손하는 시점에 종가 부근에서
     매수"하는 종가 기준 기법이다(§2). 이번 구현은 해당 일자 **종가가 S2선을 하회(훼손)하면
     그날 종가로 체결**하는 것으로 근사한다.
  2. **매수 가능 기간이 D+1~D+2로 S3(D+7)보다 훨씬 짧다** (원문 §2-4 "매수 가능 기간: 상한가
     당일 제외, D+1·D+2까지만"). `S2Config.entry_valid_days=2`로 반영.
  3. RH/RL 고정 방식은 S3와 완전히 동일 — 상한가 당일 고가를 RH, 상한가 발생일 포함 직전
     lookback일 최저가를 RL로 고정한다(자기순환적 결함 회피).
  4. **익절/손절/시간청산 규칙은 S3와 동일하게 차용**: -7% 손절(원문의 "1등주면 추매, 아니면
     손절"에서 1등주 판정 불가로 손절 통일), 전고점(RH) 도달 시 전량 익절(원문의 "+4%~전고점
     분할매도" 단순화), 4일차 강제청산. 원문에 명시적 가격 손절가가 없어(§7.3) 신규 설계했다는
     점은 S3와 동일한 한계.
  5. 훼손 캔들 패턴 분류(국민1음봉/양봉종가베팅/2음봉종가베팅)는 진입가·청산가에 영향을 주지 않는
     명명법이라 이번 구현에서는 다루지 않는다.
  6. 추매(물타기, -7% 시 1등주 추가매수)는 1등주 판정 불가 한계로 미구현 — S3와 동일.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import S2Config
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Bracket, EntryOrderPlan, Fill, Trade, TradePlan


@dataclass
class S2Signal:
    ul_index: int
    ul_date: pd.Timestamp
    high: float  # RH — 되돌림 고점 (상한가일 고가, 고정)
    low: float  # RL — 되돌림 저점 (상한가 직전 lookback 최저가, 고정)
    s2_level: float


def detect_s2_signals(df: pd.DataFrame, config: S2Config) -> list[S2Signal]:
    """상한가(신규) 이벤트를 찾아 고정된 S2 되돌림선을 계산한다."""
    signals: list[S2Signal] = []
    is_ul = [
        is_limit_up_day(h, c, pc, config.limit_up_return_threshold)
        for h, c, pc in zip(df["High"], df["Close"], df["PrevClose"])
    ]
    for i in range(len(df)):
        if not is_ul[i]:
            continue
        if i > 0 and is_ul[i - 1]:
            continue
        if df["TradingValue"].iloc[i] < config.min_trading_value_krw:
            continue

        rh = float(df["High"].iloc[i])
        lookback_start = max(0, i - config.pre_rally_lookback_days + 1)
        rl = float(df["Low"].iloc[lookback_start : i + 1].min())
        s2_level = rh - (rh - rl) * config.fib_s2_ratio

        signals.append(S2Signal(ul_index=i, ul_date=df.index[i], high=rh, low=rl, s2_level=s2_level))
    return signals


def simulate_s2_trade(df: pd.DataFrame, signal: S2Signal, config: S2Config, ticker: str) -> Trade | None:
    """단일 S2 신호에 대해 진입 이후 흐름을 일봉 기준으로 시뮬레이션한다."""
    n = len(df)
    entry_window_end = min(signal.ul_index + config.entry_valid_days, n - 1)

    trade = Trade(ticker=ticker, signal_date=signal.ul_date)
    entry_idx: int | None = None

    for i in range(signal.ul_index + 1, entry_window_end + 1):
        row = df.iloc[i]
        if row["Close"] <= signal.s2_level:
            entry_idx = i
            trade.fills.append(Fill(df.index[i], float(row["Close"]), 1.0, "entry_s2"))
            break

    if entry_idx is None:
        return None

    shares = 1.0
    entry_price = trade.fills[0].price
    force_idx = min(entry_idx + config.hold_days - 1, n - 1)
    target_price = signal.high
    stop_price = entry_price * (1 + config.stop_loss_pct)

    for i in range(entry_idx + 1, force_idx + 1):
        row = df.iloc[i]
        date = df.index[i]

        if row["Low"] <= stop_price:
            trade.fills.append(Fill(date, stop_price, -shares, "exit_stop_loss"))
            shares = 0.0
            break

        if row["High"] >= target_price:
            trade.fills.append(Fill(date, target_price, -shares, "exit_target_high"))
            shares = 0.0
            break

        if i == force_idx:
            trade.fills.append(Fill(date, row["Close"], -shares, "exit_forced_hold"))
            shares = 0.0

    if shares > 1e-9:
        trade.fills.append(Fill(df.index[-1], df["Close"].iloc[-1], -shares, "exit_data_end"))

    return trade


def backtest_ticker(df: pd.DataFrame, ticker: str, config: S2Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S2 트레이드 목록을 생성한다."""
    config = config or S2Config()
    if df.empty or len(df) < 5:
        return []
    signals = detect_s2_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s2_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades


def describe_trade_plan(df: pd.DataFrame, signal: S2Signal, config: S2Config) -> TradePlan:
    """아직 진입 전인 S2 신호의 매수조건/손절가/익절가를 사람이 읽을 수 있게 정리한다.

    S2는 "종가베팅"이라 정확한 체결가는 그날 장 마감 전까지 알 수 없다(entry_price=None) —
    simulate_s2_trade와 동일하게, 손절가도 실제 체결가가 나와야 계산되므로 여기서는 비율만
    설명한다. 익절가(전고점)는 상한가 발생 시점에 고정돼 확정돼 있다.
    """
    # 진입가가 미확정이라 익절%은 S2선(진입 트리거 레벨)을 진입가의 근사치로 써서 계산한다 —
    # 실제 체결가(그날 종가)는 S2선 이하이기만 하면 되므로 정확히 같지는 않은 근사값이다.
    approx_target_pct = (signal.high / signal.s2_level - 1) * 100
    return TradePlan(
        entry_price=None,
        entry_desc=f"종가가 {signal.s2_level:,.0f}원(S2선) 이하로 마감하면 그 종가에 매수",
        stop_price=None,
        stop_pct=config.stop_loss_pct * 100,
        stop_desc=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%",
        target_price=signal.high,
        target_pct=approx_target_pct,
        target_desc=f"{signal.high:,.0f}원(전고점)",
    )


def plan_entry_order_s2(signal: S2Signal, config: S2Config) -> EntryOrderPlan:
    """종가베팅 — 그날 종가가 S2선 이하로 마감해야 매수되므로 사전에 지정가를 걸 수 없다.
    자동매매는 15:20 이후 장마감 임박 폴링으로 조건을 판단해 시장가로 매수한다."""
    return EntryOrderPlan(order_style="close_bet", limit_price=None, entry_valid_trading_days=config.entry_valid_days)


def compute_bracket_s2(fill_price: float, signal: S2Signal, config: S2Config) -> Bracket:
    """실제 체결가(그날 종가) 기준 -7% 손절, 전고점 익절 — simulate_s2_trade와 동일 규칙."""
    stop_price = fill_price * (1 + config.stop_loss_pct)
    return Bracket(
        stop_price=stop_price, target_price=signal.high, max_hold_trading_days=config.hold_days,
        stop_reason=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%", target_reason="전고점(RH)",
    )
