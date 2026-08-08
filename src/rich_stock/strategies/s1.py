"""S1(상한가리바운딩) 전략 — 신호 탐지 및 개별 종목 트레이드 시뮬레이션.

규칙 출처: research/step_1_S1기법.md §2, §3.
이번 구현이 원문 규칙에 추가/보완한 부분(원문 공백 또는 일봉 단위 시뮬레이션을 위한 필수 가정):

  1. 정성적 배제 필터(무공방/긴N자/동테마 후발주/단기과열/유증무증권리락 등)는 기본적으로 꺼져
     있다 (`S1Config.qualitative_filter_enabled=False`) — risk-manager 검토(§7.3)가 권고한 대로
     "신호 생성 자동화 + 최종 승인 수동"을 전제로, 순수 정량 규칙만의 기저 성과(baseline)를 먼저
     측정할 수 있게 하기 위함이다. 2026-08-08 step_0_common 추가조사로 무공방/긴N자/120일신고가
     override/선반등에 대한 근사 정의를 확보해 `strategies/qualitative.py`에 구현했으며, 활성화
     시에만 적용된다 (근거: research/step_0_공통자료.md §3~5,7). 동테마 후발주(1등주/2등주 판정)는
     원 자료를 확인해도 정량 기준이 없어 여전히 미구현이다.
  2. R0 도달 시 "분할매도"를 잔여 물량 전량 매도로 단순화했다 (원문은 분할 비율을 명시하지 않음).
  3. R2 추가매수는 "동일 수량"(원문 §2-6) 규칙을 그대로 따라 주식 수량 기준으로 구현했고,
     이에 따라 추매 시점의 투입 현금은 초기 진입보다 작다 (R2 < R1이므로).
  4. "본절 매도"(원문 §2-7)는 추매 이후 가격이 평단가(=(R1+R2)/2, 동일 수량이므로 단순평균)
     이상으로 회복하는 날 잔여 물량의 절반을 매도하는 것으로 해석했다.
  5. 일봉 데이터의 한계로 하루 안에서 손절(R3)과 익절(R0)이 동시에 터치되는 경우 손절을
     먼저 체크한다 (보수적 가정).
  6. R1을 아직 터치하지 못한 채 가격이 곧바로 R2 밑으로 갭하락하는 경우는 진입하지 않는다
     (원문이 "R1에서 매수"를 1차 진입으로 명시하기 때문).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import S1Config
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Fill, Trade
from rich_stock.strategies.qualitative import QualScore, compute_qualitative_score


@dataclass
class S1Signal:
    ul_index: int
    ul_date: pd.Timestamp
    r0: float
    r1: float
    r2: float
    r3: float
    qual: QualScore | None = None


def detect_s1_signals(df: pd.DataFrame, config: S1Config) -> list[S1Signal]:
    """일봉 데이터프레임에서 S1 진입 대상이 되는 신규 상한가 이벤트를 찾는다.

    Args:
        df: index=Date, columns 최소 [Open, High, Low, Close, PrevClose, TradingValue] 포함
    """
    signals: list[S1Signal] = []
    is_ul = [
        is_limit_up_day(h, c, pc, config.limit_up_return_threshold)
        for h, c, pc in zip(df["High"], df["Close"], df["PrevClose"])
    ]
    last_ul_index: int | None = None  # 정성적 필터 on/off와 무관하게 '모든' 상한가 이벤트를 추적
    for i in range(len(df)):
        if not is_ul[i]:
            continue
        if i > 0 and is_ul[i - 1]:
            continue  # 연속 상한가 중 첫날만 신규 이벤트로 채택
        if df["TradingValue"].iloc[i] < config.min_trading_value_krw:
            continue

        qual = compute_qualitative_score(df, i, last_ul_index, config)
        last_ul_index = i

        if config.qualitative_filter_enabled and qual.score < config.qualitative_score_threshold:
            continue

        a = df["High"].iloc[i]  # R0 기준 = 상한가일 고가
        b = df["PrevClose"].iloc[i]  # R3 기준 = 상한가 발생 전일 종가
        ab = (a - b) / config.grid_divisions
        signals.append(
            S1Signal(
                ul_index=i,
                ul_date=df.index[i],
                r0=a,
                r1=a - ab,
                r2=a - 2 * ab,
                r3=b,
                qual=qual,
            )
        )
    return signals


def simulate_s1_trade(df: pd.DataFrame, signal: S1Signal, config: S1Config, ticker: str) -> Trade | None:
    """단일 S1 신호에 대해 진입 이후 흐름을 일봉 기준으로 시뮬레이션한다.

    Returns:
        진입이 한 번도 발생하지 않으면 None, 그렇지 않으면 Trade(체결 내역 포함).
    """
    n = len(df)
    entry_window_end = min(signal.ul_index + config.entry_valid_days, n - 1)

    trade = Trade(ticker=ticker, signal_date=signal.ul_date)
    entry_idx: int | None = None

    # 1) 진입(R1) 탐색: 상한가 발생 후 entry_valid_days 이내
    for i in range(signal.ul_index + 1, entry_window_end + 1):
        row = df.iloc[i]
        if row["Low"] <= signal.r1 <= row["High"]:
            entry_idx = i
            trade.fills.append(Fill(df.index[i], signal.r1, 1.0, "entry_r1"))
            break

    if entry_idx is None:
        return None

    # 2) 진입 이후 관리: 손절/익절/추매/본절매도/시간청산 (4일차까지)
    shares = 1.0
    addon_done = False
    breakeven_done = False
    force_idx = min(entry_idx + config.hold_days - 1, n - 1)

    for i in range(entry_idx + 1, force_idx + 1):
        row = df.iloc[i]
        date = df.index[i]

        if row["Low"] <= signal.r3:
            trade.fills.append(Fill(date, signal.r3, -shares, "exit_stop_r3"))
            shares = 0.0
            break

        if addon_done and not breakeven_done:
            breakeven_price = (signal.r1 + signal.r2) / 2
            if row["High"] >= breakeven_price:
                half = shares / 2
                trade.fills.append(Fill(date, breakeven_price, -half, "exit_breakeven"))
                shares -= half
                breakeven_done = True
                continue

        if row["High"] >= signal.r0:
            trade.fills.append(Fill(date, signal.r0, -shares, "exit_target_r0"))
            shares = 0.0
            break

        if not addon_done and i <= entry_window_end and row["Low"] <= signal.r2:
            trade.fills.append(Fill(date, signal.r2, 1.0, "addon_r2"))
            shares += 1.0
            addon_done = True
            continue

        if i == force_idx:
            trade.fills.append(Fill(date, row["Close"], -shares, "exit_forced_hold"))
            shares = 0.0

    if shares > 1e-9:
        # 데이터 마지막 구간이라 4일차 청산일 자체가 데이터 범위를 벗어난 경계 케이스.
        # 마지막 가용 종가로 마감(미실현 손익을 실현손익으로 근사) 처리.
        trade.fills.append(Fill(df.index[-1], df["Close"].iloc[-1], -shares, "exit_data_end"))

    return trade


def backtest_ticker(df: pd.DataFrame, ticker: str, config: S1Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S1 트레이드 목록을 생성한다."""
    config = config or S1Config()
    if df.empty or len(df) < 5:
        return []
    signals = detect_s1_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s1_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades
