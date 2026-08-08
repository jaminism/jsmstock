"""K+ 전략(K1플러스/K2플러스) — "세력봉" 이벤트 기반 피보나치 되돌림 매매.

규칙 출처: research/step_5_K+기법.md §2, §3.
SR/K1/K2([[project-sr-backtest-engine]], [[project-k1-backtest-engine]], [[project-k2-backtest-engine]])
와의 핵심 차이:

  1. **진입 트리거가 상한가가 아니라 "세력봉"이다.** 원문 수식(§3)을 근사 구현했다:
     `A = 거래대금>=임계값 and 전일종가*1.15<=고가 and 저가*1.15<=고가 and 시가*1.09<=종가`,
     `AA = 거래대금>=임계값`, `if(UL(), AA, A)` — 상한가일은 거래대금 기준만, 아니면 캔들형태
     조건(전일종가 대비 고가 15%+, 당일 변동폭 15%+, 시가 대비 종가 9%+)까지 요구한다.
     원문의 `세력봉대금`/`상한가대금` 실제 수치는 확인 불가해(§8-1) 둘 다 `min_trading_value_krw`
     (500억, §2 프로즈 기준)로 통일했다.
  2. **RH/RL 고정 방식이 SR/K1/K2와 다르다 — RL 계산 구간에서 이벤트 당일을 제외한다.** K+는
     세력봉 당일(K1+) 또는 그 이후 며칠(K2+) 자체가 진입 유효 구간이라, 원조 K1/K2처럼 "이벤트
     다음날부터 진입"으로 자연히 분리할 수 없다. 그래서 이벤트 당일의 저가가 RL 계산에 섞이면
     그날 저가가 정의상 항상 되돌림선을 만족해버리는 자기순환적 결함([[project-k2-backtest-engine]]
     에서 처음 발견)이 재발할 수 있어, `detect_power_candle_signals`가 RL을 이벤트 당일을 제외한
     lookback 구간에서만 계산한다. RH(세력봉 당일 고가)는 SR/K1/K2와 동일하게 이벤트 당일 값을
     그대로 고정한다.
  3. **K1+와 K2+는 같은 세력봉 신호를 공유하되 진입 방식이 다르다** — K1+(종가베팅)는 세력봉
     당일 종가가 K1+선(0.236)을 하회하면 그날 종가에 매수(당일 1회 한정), K2+(장중매매)는
     세력봉 발생일 포함 며칠 이내 장중 저가가 K2+선(0.5)을 터치하면 그 레벨 가격에 매수 —
     원조 K1/K2(step_2/3)의 "종가베팅 vs 장중터치" 구도를 그대로 물려받았다.
  4. 익절/손절은 SR/K1/K2와 동일한 방식(구간 상단 단일가 익절, -7% 손절, 4일차 강제청산)으로
     단순화했고, 추매(대장주 한정)는 판별 불가로 미구현이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import K1PlusConfig, K2PlusConfig, KPlusConfig
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Fill, Trade


@dataclass
class PowerCandleSignal:
    event_index: int
    event_date: pd.Timestamp
    high: float  # RH — 되돌림 고점 (세력봉 당일 고가, 고정)
    low: float  # RL — 되돌림 저점 (세력봉 발생일 이전 lookback 최저가, 이벤트 당일 제외하고 고정)


def _is_power_candle_row(
    open_: float,
    high: float,
    low: float,
    close: float,
    prev_close: float,
    trading_value: float,
    is_ul: bool,
    config: KPlusConfig,
) -> bool:
    if trading_value < config.min_trading_value_krw:
        return False
    if is_ul:
        return True  # AA: 상한가일은 거래대금 기준만
    return (
        prev_close * config.candle_range_ratio <= high
        and low * config.candle_range_ratio <= high
        and open_ * config.candle_body_ratio <= close
    )


def detect_power_candle_signals(df: pd.DataFrame, config: KPlusConfig) -> list[PowerCandleSignal]:
    """세력봉 이벤트를 찾아 고정된 RH/RL을 계산한다 (RL은 이벤트 당일을 제외한 lookback 최저가)."""
    signals: list[PowerCandleSignal] = []
    is_ul = [
        is_limit_up_day(h, c, pc, config.limit_up_return_threshold)
        for h, c, pc in zip(df["High"], df["Close"], df["PrevClose"])
    ]
    is_power = [
        _is_power_candle_row(o, h, l, c, pc, tv, ul, config)
        for o, h, l, c, pc, tv, ul in zip(
            df["Open"], df["High"], df["Low"], df["Close"], df["PrevClose"], df["TradingValue"], is_ul
        )
    ]
    for i in range(len(df)):
        if pd.isna(df["PrevClose"].iloc[i]):
            continue  # 첫날은 등락률/캔들형태 조건 계산 불가
        if not is_power[i]:
            continue
        if i > 0 and is_power[i - 1]:
            continue  # 연속 세력봉은 첫날만 이벤트로 카운트 (SR/K1/K2와 동일한 단순화)

        lookback_start = max(0, i - config.pre_event_lookback_days)
        if lookback_start == i:
            continue  # 이벤트 이전 데이터가 없으면 RL을 구할 수 없음

        rh = float(df["High"].iloc[i])
        rl = float(df["Low"].iloc[lookback_start:i].min())  # 이벤트 당일(i) 제외

        signals.append(PowerCandleSignal(event_index=i, event_date=df.index[i], high=rh, low=rl))
    return signals


def simulate_k1_plus_trade(
    df: pd.DataFrame, signal: PowerCandleSignal, config: K1PlusConfig, ticker: str
) -> Trade | None:
    """K1플러스(종가베팅) — 세력봉 당일 종가가 K1+선을 하회하면 그날 종가에 매수(당일 1회 한정)."""
    n = len(df)
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    row = df.iloc[signal.event_index]

    if row["Close"] > level:
        return None

    entry_idx = signal.event_index
    entry_price = float(row["Close"])
    trade = Trade(ticker=ticker, signal_date=signal.event_date)
    trade.fills.append(Fill(df.index[entry_idx], entry_price, 1.0, "entry_k1_plus"))

    shares = 1.0
    force_idx = min(entry_idx + config.hold_days - 1, n - 1)
    target_price = signal.high
    stop_price = entry_price * (1 + config.stop_loss_pct)

    for i in range(entry_idx + 1, force_idx + 1):
        r = df.iloc[i]
        date = df.index[i]
        if r["Low"] <= stop_price:
            trade.fills.append(Fill(date, stop_price, -shares, "exit_stop_loss"))
            shares = 0.0
            break
        if r["High"] >= target_price:
            trade.fills.append(Fill(date, target_price, -shares, "exit_target_high"))
            shares = 0.0
            break
        if i == force_idx:
            trade.fills.append(Fill(date, r["Close"], -shares, "exit_forced_hold"))
            shares = 0.0

    if shares > 1e-9:
        trade.fills.append(Fill(df.index[-1], df["Close"].iloc[-1], -shares, "exit_data_end"))

    return trade


def simulate_k2_plus_trade(
    df: pd.DataFrame, signal: PowerCandleSignal, config: K2PlusConfig, ticker: str
) -> Trade | None:
    """K2플러스(장중매매) — 세력봉 발생일 포함 entry_valid_days일 이내 장중 저가가 K2+선을
    터치하면 그 레벨 가격에 매수."""
    n = len(df)
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    entry_window_end = min(signal.event_index + config.entry_valid_days - 1, n - 1)

    trade = Trade(ticker=ticker, signal_date=signal.event_date)
    entry_idx: int | None = None

    for i in range(signal.event_index, entry_window_end + 1):
        row = df.iloc[i]
        if row["Low"] <= level:
            entry_idx = i
            trade.fills.append(Fill(df.index[i], level, 1.0, "entry_k2_plus"))
            break

    if entry_idx is None:
        return None

    shares = 1.0
    force_idx = min(entry_idx + config.hold_days - 1, n - 1)
    target_price = level * (1 + config.profit_target_pct)
    stop_price = level * (1 + config.stop_loss_pct)

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


def backtest_ticker_k1_plus(df: pd.DataFrame, ticker: str, config: K1PlusConfig | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 K1플러스 트레이드 목록을 생성한다."""
    config = config or K1PlusConfig()
    if df.empty or len(df) < 5:
        return []
    signals = detect_power_candle_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_k1_plus_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades


def backtest_ticker_k2_plus(df: pd.DataFrame, ticker: str, config: K2PlusConfig | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 K2플러스 트레이드 목록을 생성한다."""
    config = config or K2PlusConfig()
    if df.empty or len(df) < 5:
        return []
    signals = detect_power_candle_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_k2_plus_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades
