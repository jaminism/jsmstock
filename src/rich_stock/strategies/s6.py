"""SP2 전략 — 이동평균선(15일/20일) 눌림목 매매. 신호 탐지 및 개별 종목 트레이드 시뮬레이션.

규칙 출처: research/step_4_S6기법.md §2(원문 8개 규칙 + Q&A).
S1/S2/S3/S2+/S3+와 구조적으로 가장 다른 기법이다:

  1. **진입 트리거가 단일 상한가가 아니라 "3연속상한가 이상 + 고점대비 200~300% 상승" 랠리
     이벤트다.** `detect_s6_signals`가 연속 상한가 스트릭을 찾고, 스트릭 종료 후 `peak_search_days`
     이내 최고가를 "고점"(RH 역할)으로, 스트릭 시작 전 `pre_rally_lookback_days` 구간 최저가를
     "랠리 시작 저점"으로 삼아 누적상승률을 계산한다. 두 값 모두 고점 발생 시점에 고정되며,
     이후 갱신되지 않는다(S1/S2/S3의 R0~R3/S2선/S3선 고정 방식과 동일한 원칙).
  2. **되돌림 기준선이 피보나치 비율이 아니라 실제 이동평균선(15일/20일)이다.** 이동평균은
     매일 갱신되는 값이라, 고정된 그리드 대신 `df["Close"].rolling(...)`로 계산한 롤링 시리즈를
     매수 판정에 직접 사용한다(entry는 장중 저가가 그날의 이동평균값을 하회하면 그 이동평균값에
     체결되는 것으로 근사 — S3의 "장중 터치" 관례를 그대로 따름).
  3. **손절이 원문에 아예 없다 — 공백이 아니라 설계 의도.** S1/S2/S3/S2+/S3+는 전부 원문에
     없는 손절가를 인접 기법에서 차용해 신규 설계했지만, SP2는 원문 스스로 "가격 손절 없음"을
     선언한다(연구노트 §2 Q&A "존버"). 이번 구현은 이를 그대로 반영해 가격 기반 손절을 두지
     않는다 — 결과 해석 시 이 점이 다른 4개 기법과 가장 크게 다르다는 것을 반드시 감안해야 한다.
  4. **시간청산 기준점이 "2차(20일선) 매수 시점"이며, 1차(15일선)만 보유 중이면 시간청산도
     적용되지 않는다.** 즉 2차 매수 없이 15일선 부근에서 장기 횡보하면 이론상 무기한 보유
     경로가 열려 있다(원문 Q&A에 명시된 내용을 그대로 반영 — 버그 아님, `S6Config.hold_days`
     docstring 참고). 이 경우 트레이드는 데이터 마지막 구간까지 보유하다 `exit_data_end`로
     마감된다(다른 기법과 동일한 기존 관례를 재사용).
  5. **청산 규칙이 매수 단계(1차만 / 2차까지 / 3차까지)에 따라 달라진다**: 1차만 보유 시
     +5%부터 절반매도(S1의 본절매도 단순화와 동일 방식) + 저점대비 +17% 전량매도. 2차 매수
     이후("역삼각매도")는 최초 매수가(1차가, 15일선가)에서 전량 청산. 3차 매수까지 간 경우는
     원문 Q&A("수익·손절 여부를 무시하고 2차 매수자리에서 청산")를 따라 2차가(20일선가)에서
     전량 청산으로 전환한다.
  6. "대장주/시장중심주" 판정, "정지 포함이 긍정 신호"라는 경험칙은 S1의 1등주/2등주 문제와
     동일한 한계로 미구현이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import S6Config
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Bracket, EntryOrderPlan, Fill, Trade, TradePlan, tradable_lows


@dataclass
class S6Signal:
    peak_index: int
    peak_date: pd.Timestamp
    peak_price: float  # 고점 (스트릭 종료 후 peak_search_days 이내 최고가, 고정)
    pre_rally_low: float  # 랠리 시작 전 저점 (고정)
    streak_len: int  # 연속상한가 개수 (참고용)


def detect_s6_signals(df: pd.DataFrame, config: S6Config) -> list[S6Signal]:
    """3연속상한가 이상 + 고점대비 200%+ 상승 랠리 이벤트를 찾아 고정된 고점/저점을 계산한다."""
    n = len(df)
    is_ul = [
        is_limit_up_day(h, c, pc, config.limit_up_return_threshold)
        for h, c, pc in zip(df["High"], df["Close"], df["PrevClose"])
    ]
    signals: list[S6Signal] = []
    i = 0
    while i < n:
        if not is_ul[i]:
            i += 1
            continue

        streak_start = i
        while i < n and is_ul[i]:
            i += 1
        streak_end = i - 1
        streak_len = streak_end - streak_start + 1

        if streak_len < config.streak_min_len:
            continue
        if df["TradingValue"].iloc[streak_end] < config.min_trading_value_krw:
            continue

        lookback_start = max(0, streak_start - config.pre_rally_lookback_days)
        if lookback_start == streak_start:
            continue
        # 거래정지일의 0값을 빼고 구한다 — 예전엔 0이 그대로 들어와 아래 `pre_rally_low <= 0`
        # 가드에 걸려 정상 신호가 통째로 사라졌다(tradable_lows 독스트링 참고).
        valid_lows = tradable_lows(df.iloc[lookback_start:streak_start])
        if valid_lows.empty:
            continue  # 구간 전체가 거래정지 — 상승률을 신뢰할 수 없다
        pre_rally_low = float(valid_lows.min())

        peak_search_end = min(streak_end + config.peak_search_days, n - 1)
        peak_window = df["High"].iloc[streak_end : peak_search_end + 1]
        peak_price = float(peak_window.max())
        peak_index = streak_end + int(peak_window.values.argmax())

        if pre_rally_low <= 0:
            continue
        rise = peak_price / pre_rally_low - 1
        if rise < config.min_rise_pct:
            continue

        signals.append(
            S6Signal(
                peak_index=peak_index,
                peak_date=df.index[peak_index],
                peak_price=peak_price,
                pre_rally_low=pre_rally_low,
                streak_len=streak_len,
            )
        )
    return signals


def simulate_s6_trade(
    df: pd.DataFrame,
    signal: S6Signal,
    config: S6Config,
    ticker: str,
    ma_short: pd.Series,
    ma_long: pd.Series,
) -> Trade | None:
    """단일 S6 신호에 대해 진입 이후 흐름을 일봉 기준으로 시뮬레이션한다."""
    n = len(df)
    entry_window_end = min(signal.peak_index + config.entry_valid_days, n - 1)

    entry1_idx: int | None = None
    for i in range(signal.peak_index + 1, entry_window_end + 1):
        level = ma_short.iloc[i]
        if pd.isna(level):
            continue
        if df["Low"].iloc[i] <= level:
            entry1_idx = i
            break

    if entry1_idx is None:
        return None

    trade = Trade(ticker=ticker, signal_date=signal.peak_date)
    entry1_price = float(ma_short.iloc[entry1_idx])
    trade.fills.append(Fill(df.index[entry1_idx], entry1_price, 1.0, "entry_ma15"))

    shares = 1.0
    running_low = float(df["Low"].iloc[entry1_idx])
    half_sold = False
    entry2_idx: int | None = None
    entry2_price: float | None = None
    entry3_idx: int | None = None
    force_idx: int | None = None

    i = entry1_idx + 1
    while i < n:
        row = df.iloc[i]
        date = df.index[i]
        running_low = min(running_low, float(row["Low"]))

        if entry2_idx is None:
            level20 = ma_long.iloc[i]
            if i <= entry_window_end and not pd.isna(level20) and row["Low"] <= level20:
                entry2_idx = i
                entry2_price = float(level20)
                trade.fills.append(Fill(date, entry2_price, 1.0, "addon_ma20"))
                shares += 1.0
                force_idx = min(entry2_idx + config.hold_days - 1, n - 1)
                i += 1
                continue
        elif entry3_idx is None:
            stop3_price = entry2_price * (1 + config.addon3_drop_pct)
            if row["Low"] <= stop3_price:
                entry3_idx = i
                trade.fills.append(Fill(date, stop3_price, 1.0, "addon_stop3"))
                shares += 1.0
                i += 1
                continue

        if entry2_idx is not None:
            exit_target = entry2_price if entry3_idx is not None else entry1_price
            if row["High"] >= exit_target:
                trade.fills.append(Fill(date, exit_target, -shares, "exit_reverse_triangle"))
                shares = 0.0
                break
        else:
            if not half_sold and row["High"] >= entry1_price * (1 + config.partial_sell_pct):
                half = shares / 2
                trade.fills.append(
                    Fill(date, entry1_price * (1 + config.partial_sell_pct), -half, "exit_partial_5pct")
                )
                shares -= half
                half_sold = True
                i += 1
                continue
            full_target = running_low * (1 + config.full_sell_from_low_pct)
            if row["High"] >= full_target:
                trade.fills.append(Fill(date, full_target, -shares, "exit_target_17pct_from_low"))
                shares = 0.0
                break

        if force_idx is not None and i == force_idx:
            trade.fills.append(Fill(date, row["Close"], -shares, "exit_forced_hold"))
            shares = 0.0
            break

        i += 1

    if shares > 1e-9:
        # 2차(20일선) 매수 없이 1차만 보유한 채 데이터 끝에 도달한 경우 포함 — 원문 설계상
        # 시간청산이 없어 무기한 보유가 가능하므로, 다른 기법과 동일한 마지막 종가 마감 관례를
        # 그대로 적용한다(버그 아님, S6Config.hold_days docstring 참고).
        trade.fills.append(Fill(df.index[-1], df["Close"].iloc[-1], -shares, "exit_data_end"))

    return trade


def backtest_ticker(df: pd.DataFrame, ticker: str, config: S6Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S6 트레이드 목록을 생성한다."""
    config = config or S6Config()
    min_len = max(config.ma_long, config.pre_rally_lookback_days) + 5
    if df.empty or len(df) < min_len:
        return []
    ma_short = df["Close"].rolling(config.ma_short).mean()
    ma_long = df["Close"].rolling(config.ma_long).mean()
    signals = detect_s6_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s6_trade(df, sig, config, ticker, ma_short, ma_long)
        if trade is not None:
            trades.append(trade)
    return trades


def describe_trade_plan(df: pd.DataFrame, signal: S6Signal, config: S6Config) -> TradePlan:
    """아직 진입 전인 S6 신호의 매수조건/손절/익절을 사람이 읽을 수 있게 정리한다.

    S6는 다른 5개 기법과 달리 고정 그리드가 아니라 **매일 갱신되는 이동평균선**이 매수 기준선이라
    (simulate_s6_trade 참고), entry_price는 df의 가장 최근 종가 기준 현재 이동평균값이다 — 내일
    이후로는 값이 달라진다는 걸 감안해야 한다(entry_desc에 명시). 원문 설계상 **가격 손절이
    아예 없다**(S6Config docstring 참고) — 그래서 stop_price=None/stop_desc가 "없음"을 뜻한다.
    """
    ma_short = float(df["Close"].rolling(config.ma_short).mean().iloc[-1])
    ma_long = float(df["Close"].rolling(config.ma_long).mean().iloc[-1])
    return TradePlan(
        entry_price=ma_short,
        entry_desc=(
            f"{config.ma_short}일선(현재 {ma_short:,.0f}원, 매일 갱신됨) 터치 시 1차매수, "
            f"{config.ma_long}일선(현재 {ma_long:,.0f}원) 터치 시 2차매수"
        ),
        stop_price=None,
        stop_pct=None,
        stop_desc="없음 — 원문 설계상 가격 손절이 아예 없는 기법(존버)",
        target_price=None,
        target_pct=None,
        target_desc=(
            "1차만 보유 시: 매수가+5%부터 절반매도, 저점대비+17%에서 전량매도. "
            "2차매수 이후엔 1차매수가(15일선가)에서 전량청산."
        ),
    )


def plan_entry_order_s6(df: pd.DataFrame, signal: S6Signal, config: S6Config) -> EntryOrderPlan:
    """15일선(당일 값)에 지정가 매수를 걸어둔다 — 이동평균은 매일 값이 바뀌므로 체결 전까지
    매일 아침 새 값으로 재주문해야 한다(order_style="daily_recompute_limit")."""
    ma_short = float(df["Close"].rolling(config.ma_short).mean().iloc[-1])
    return EntryOrderPlan(order_style="daily_recompute_limit", limit_price=ma_short, entry_valid_trading_days=config.entry_valid_days)


AUTO_TRADE_SAFETY_STOP_PCT = -0.15
"""S6은 원문 설계상 가격 손절이 아예 없지만("존버"), 무인 자동매매에 그대로 넣으면 슬롯 하나가
무기한 물려 나머지 슬롯의 기회비용만 커진다(2026-08-09 technical-analyst 분석 — force_idx가
2차매수 없이는 아예 안 걸리는 구조가 PF 0.09의 주 원인이라는 게 밝혀짐). 정상적인 눌림목 변동
범위에서는 거의 발동하지 않도록 넉넉하게(-15%) 잡아, "손절 없이 버티는 성질"을 최대한 그대로
관찰하면서도 파국적 손실만 차단한다."""

AUTO_TRADE_SAFETY_TARGET_PCT = 0.20
"""같은 취지의 안전장치 익절(+20%) — 원문의 "저점대비+17%" 전량매도 규칙과 비슷한 크기로 잡았다."""

AUTO_TRADE_SAFETY_MAX_HOLD_DAYS = 20
"""안전장치 최대 보유 거래일수 — entry_valid_days(진입 유효기간)와는 별개로, 일단 체결된 뒤
얼마나 오래 들고 있을 수 있는지의 상한이다. 넉넉하게 잡아 정상적인 단계별 청산 타이밍보다
훨씬 늦게만 발동하게 한다."""


def compute_bracket_s6(fill_price: float, signal: S6Signal, config: S6Config) -> Bracket:
    """S6 안전장치 브라켓 — 원문에 없는 값이라 is_safety_override=True로 표시한다(사후 분석 시
    "이 청산이 없었다면 어떻게 됐을지"를 구분해서 볼 수 있도록). 1단계 범위는 원문의 1차/2차/3차
    단계별 청산(러닝로우 기반 부분매도 등)을 생략한 단순 브라켓이다."""
    return Bracket(
        stop_price=fill_price * (1 + AUTO_TRADE_SAFETY_STOP_PCT),
        target_price=fill_price * (1 + AUTO_TRADE_SAFETY_TARGET_PCT),
        max_hold_trading_days=AUTO_TRADE_SAFETY_MAX_HOLD_DAYS,
        stop_reason=f"안전장치(원문에 없음): 매수가 대비 {AUTO_TRADE_SAFETY_STOP_PCT * 100:.0f}%",
        target_reason=f"안전장치(원문 근사): 매수가 대비 +{AUTO_TRADE_SAFETY_TARGET_PCT * 100:.0f}%",
        is_safety_override=True,
    )
