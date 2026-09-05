"""S3 전략 — 신호 탐지 및 개별 종목 트레이드 시뮬레이션.

규칙 출처: research/step_2_S3기법.md §2, §3.
이번 구현이 원문 규칙에 추가/보완/단순화한 부분:

  1. **익절/손절 규칙은 원문에 없다.** S3Config 참고 — step_3(S2)/step_5(S45) 문서의 인접 패턴
     ("-7% 손절", "전고점까지 익절", "4일차 강제청산")을 차용해 신규 설계했다. S3 원문에서
     검증된 값이 아니라는 점을 결과 해석 시 반드시 감안해야 한다.
  2. **되돌림 기준선(S3)을 S1의 R0~R3 그리드와 동일하게 상한가 발생일에 고정한다.** 원문은
     5분봉으로 "상한가 직전 RSI 과매도 지점(저점) ~ 상한가 당일 최고점(고점)"을 긋는데, 우리는
     장중(분봉) 데이터가 없어 저점을 "상한가 발생일 포함 직전 5거래일 중 최저가"로 근사한다.
     이 값은 상한가 발생 시점에 고정되며 이후 눌림에 따라 다시 낮아지지 않는다 — 처음에는
     "상한가 이후의 롤링 최저가"로 구현했으나, 그러면 상한가 다음날 어떤 하락폭이든 그날 자신의
     저가가 곧 그날 기준 되돌림선을 정의상 항상 만족시켜버리는 자기순환적 결함이 있어 폐기했다.
  3. **HD<4(신고가 갱신 시 되돌림선 재산정) 로직은 생략**했다. 원문은 상한가 이후 신고가가
     경신되면 그 신고가를 새 고점으로 되돌림선을 다시 긋지만, 이번 버전은 S1과의 구조적 일관성과
     단순성을 위해 상한가 당일 고가를 고정 고점으로 사용한다.
  4. **S2(0.236, D<2 한정 유효) 신호는 이번 모듈에서 다루지 않는다** — step_3 리서치가 S2을
     "종가베팅"이라는 별도 진입 방식(장중 터치가 아닌 종가 매수, Stochastic 스크리닝 등)을 가진
     사실상 다른 기법으로 취급하고 있어, 여기서는 "S3 기법"(0.5 되돌림, 장중 터치 매수) 범위만
     구현한다.
  5. 익절은 "전고점까지 분할매도"를 S1과 동일하게 잔여 물량 전량 매도로 단순화했다.
  6. 손절은 "-7% 하락시 추매(대장주) 또는 손절"에서 1등주 판정이 불가능해(S1의 qualitative.py와
     동일한 한계) 전량 손절로 통일했다. 추매(피라미딩)는 구현하지 않는다.
  7. 일봉 데이터의 한계로 하루 안에서 손절과 익절이 동시에 터치되는 경우 손절을 먼저 체크한다
     (S1과 동일한 보수적 가정).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import S3Config
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Bracket, EntryOrderPlan, Fill, Trade, TradePlan, tradable_lows


@dataclass
class S3Signal:
    ul_index: int
    ul_date: pd.Timestamp
    high: float  # RH — 되돌림 고점 (상한가일 고가, 고정)
    low: float  # RL — 되돌림 저점 (상한가 직전 lookback 최저가, 고정)
    s3_level: float


def detect_s3_signals(df: pd.DataFrame, config: S3Config) -> list[S3Signal]:
    """상한가(신규) 이벤트를 찾아 고정된 S3 되돌림선을 계산한다."""
    signals: list[S3Signal] = []
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
        # RL(되돌림 저점)은 거래정지일의 0값을 빼고 구한다 — 안 그러면 되돌림선이 실제
        # 가격대보다 훨씬 아래로 잡혀 매수가 조용히 안 일어난다(tradable_lows 독스트링 참고).
        valid_lows = tradable_lows(df.iloc[lookback_start : i + 1])
        if valid_lows.empty:
            continue  # 구간 전체가 거래정지 — 0을 RL로 쓰느니 신호를 만들지 않는다
        rl = float(valid_lows.min())
        s3_level = rh - (rh - rl) * config.fib_s3_ratio

        signals.append(S3Signal(ul_index=i, ul_date=df.index[i], high=rh, low=rl, s3_level=s3_level))
    return signals


def simulate_s3_trade(df: pd.DataFrame, signal: S3Signal, config: S3Config, ticker: str) -> Trade | None:
    """단일 S3 신호에 대해 진입 이후 흐름을 일봉 기준으로 시뮬레이션한다."""
    n = len(df)
    entry_window_end = min(signal.ul_index + config.entry_valid_days, n - 1)

    trade = Trade(ticker=ticker, signal_date=signal.ul_date)
    entry_idx: int | None = None

    for i in range(signal.ul_index + 1, entry_window_end + 1):
        row = df.iloc[i]
        if row["Low"] <= signal.s3_level:
            entry_idx = i
            trade.fills.append(Fill(df.index[i], signal.s3_level, 1.0, "entry_s3"))
            break

    if entry_idx is None:
        return None

    shares = 1.0
    force_idx = min(entry_idx + config.hold_days - 1, n - 1)
    target_price = signal.high
    stop_price = signal.s3_level * (1 + config.stop_loss_pct)

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


def backtest_ticker(df: pd.DataFrame, ticker: str, config: S3Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S3 트레이드 목록을 생성한다."""
    config = config or S3Config()
    if df.empty or len(df) < 5:
        return []
    signals = detect_s3_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s3_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades


def describe_trade_plan(df: pd.DataFrame, signal: S3Signal, config: S3Config) -> TradePlan:
    """아직 진입 전인 S3 신호의 매수가(S3선)/손절가/익절가(전고점)를 사람이 읽을 수 있게 정리한다.

    S3는 장중 터치 매수라 체결가가 s3_level로 확정돼 있고(simulate_s3_trade와 동일), 손절가도
    그 값 기준 -7%로 미리 계산 가능하다(S2의 종가베팅과 달리 entry_price가 확정값).
    """
    stop_price = signal.s3_level * (1 + config.stop_loss_pct)
    return TradePlan(
        entry_price=signal.s3_level,
        entry_desc=f"{signal.s3_level:,.0f}원(S3선) 터치 시 매수",
        stop_price=stop_price,
        stop_pct=config.stop_loss_pct * 100,
        stop_desc=f"{stop_price:,.0f}원(매수가 대비 {config.stop_loss_pct * 100:.0f}%)",
        target_price=signal.high,
        target_pct=(signal.high / signal.s3_level - 1) * 100,
        target_desc=f"{signal.high:,.0f}원(전고점)",
    )


def plan_entry_order_s3(signal: S3Signal, config: S3Config) -> EntryOrderPlan:
    """S3선에 지정가 매수를 걸어두면 거래소가 터치 시 체결해준다 — entry_valid_days(기본 7일)
    동안 매일 같은 가격으로 재주문해야 한다(KRX 지정가는 당일만 유효)."""
    return EntryOrderPlan(order_style="fixed_limit", limit_price=signal.s3_level, entry_valid_trading_days=config.entry_valid_days)


def compute_bracket_s3(fill_price: float, signal: S3Signal, config: S3Config) -> Bracket:
    """실제 체결가 기준 -7% 손절, 전고점 익절 — simulate_s3_trade와 동일 규칙(S3는 장중 터치라
    fill_price는 이론상 signal.s3_level과 같지만, 실거래 슬리피지를 감안해 실제 체결가를 쓴다)."""
    stop_price = fill_price * (1 + config.stop_loss_pct)
    return Bracket(
        stop_price=stop_price, target_price=signal.high, max_hold_trading_days=config.hold_days,
        stop_reason=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%", target_reason="전고점(RH)",
    )
