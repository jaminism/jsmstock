"""S45 전략(S4/S5) — "세력봉" 이벤트 기반 피보나치 되돌림 매매.

규칙 출처: research/step_5_S45기법.md §2, §3.
S1/S2/S3([[project-sr-backtest-engine]], [[project-s2-backtest-engine]], [[project-s3-backtest-engine]])
와의 핵심 차이:

  1. **진입 트리거가 상한가가 아니라 "세력봉"이다.** 원문 수식(§3)을 근사 구현했다:
     `A = 거래대금>=임계값 and 전일종가*1.15<=고가 and 저가*1.15<=고가 and 시가*1.09<=종가`,
     `AA = 거래대금>=임계값`, `if(UL(), AA, A)` — 상한가일은 거래대금 기준만, 아니면 캔들형태
     조건(전일종가 대비 고가 15%+, 당일 변동폭 15%+, 시가 대비 종가 9%+)까지 요구한다.
     원문의 `세력봉대금`/`상한가대금` 실제 수치는 확인 불가해(§8-1) 둘 다 `min_trading_value_krw`
     (500억, §2 프로즈 기준)로 통일했다.
  2. **RH/RL 고정 방식이 S1/S2/S3와 다르다 — RL 계산 구간에서 이벤트 당일을 제외한다.** S45는
     세력봉 당일(S2+) 또는 그 이후 며칠(S3+) 자체가 진입 유효 구간이라, 원조 S2/S3처럼 "이벤트
     다음날부터 진입"으로 자연히 분리할 수 없다. 그래서 이벤트 당일의 저가가 RL 계산에 섞이면
     그날 저가가 정의상 항상 되돌림선을 만족해버리는 자기순환적 결함([[project-s3-backtest-engine]]
     에서 처음 발견)이 재발할 수 있어, `detect_s45_signals`가 RL을 이벤트 당일을 제외한
     lookback 구간에서만 계산한다. RH(세력봉 당일 고가)는 S1/S2/S3와 동일하게 이벤트 당일 값을
     그대로 고정한다.
  3. **S2+와 S3+는 같은 세력봉 신호를 공유하되 진입 방식이 다르다** — S2+(종가베팅)는 세력봉
     당일 종가가 S2+선(0.236)을 하회하면 그날 종가에 매수(당일 1회 한정), S3+(장중매매)는
     세력봉 발생일 포함 며칠 이내 장중 저가가 S3+선(0.5)을 터치하면 그 레벨 가격에 매수 —
     원조 S2/S3(step_2/3)의 "종가베팅 vs 장중터치" 구도를 그대로 물려받았다.
  4. 익절/손절은 S1/S2/S3와 동일한 방식(구간 상단 단일가 익절, -7% 손절, 4일차 강제청산)으로
     단순화했고, 추매(대장주 한정)는 판별 불가로 미구현이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rich_stock.config import S4Config, S5Config, S45BaseConfig
from rich_stock.limits import is_limit_up_day
from rich_stock.strategies.base import Bracket, EntryOrderPlan, Fill, Trade, TradePlan, tradable_lows


@dataclass
class RallySignal:
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
    config: S45BaseConfig,
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


def detect_s45_signals(df: pd.DataFrame, config: S45BaseConfig) -> list[RallySignal]:
    """세력봉 이벤트를 찾아 고정된 RH/RL을 계산한다 (RL은 이벤트 당일을 제외한 lookback 최저가)."""
    signals: list[RallySignal] = []
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
            continue  # 연속 세력봉은 첫날만 이벤트로 카운트 (S1/S2/S3와 동일한 단순화)

        lookback_start = max(0, i - config.pre_event_lookback_days)
        if lookback_start == i:
            continue  # 이벤트 이전 데이터가 없으면 RL을 구할 수 없음

        window = df.iloc[lookback_start:i]
        # 거래정지일의 0값을 RL로 오인하지 않도록 유효 거래일만 남긴다(tradable_lows 독스트링 참고).
        valid_lows = tradable_lows(window)
        if valid_lows.empty:
            continue  # lookback 구간 전부 거래정지 등으로 유효 저가를 구할 수 없음

        rh = float(df["High"].iloc[i])
        rl = float(valid_lows.min())  # 이벤트 당일(i) 제외

        signals.append(RallySignal(event_index=i, event_date=df.index[i], high=rh, low=rl))
    return signals


def simulate_s4_trade(
    df: pd.DataFrame, signal: RallySignal, config: S4Config, ticker: str
) -> Trade | None:
    """S4(종가베팅) — 세력봉 당일 종가가 S2+선을 하회하면 그날 종가에 매수(당일 1회 한정)."""
    n = len(df)
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    row = df.iloc[signal.event_index]

    if row["Close"] > level:
        return None

    entry_idx = signal.event_index
    entry_price = float(row["Close"])
    trade = Trade(ticker=ticker, signal_date=signal.event_date)
    trade.fills.append(Fill(df.index[entry_idx], entry_price, 1.0, "entry_s4"))

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


def simulate_s5_trade(
    df: pd.DataFrame, signal: RallySignal, config: S5Config, ticker: str
) -> Trade | None:
    """S5(장중매매) — 세력봉 발생일 포함 entry_valid_days일 이내 장중 저가가 S3+선을
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
            trade.fills.append(Fill(df.index[i], level, 1.0, "entry_s5"))
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


def backtest_ticker_s4(df: pd.DataFrame, ticker: str, config: S4Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S4 트레이드 목록을 생성한다."""
    config = config or S4Config()
    if df.empty or len(df) < 5:
        return []
    signals = detect_s45_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s4_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades


def backtest_ticker_s5(df: pd.DataFrame, ticker: str, config: S5Config | None = None) -> list[Trade]:
    """단일 종목 전체 기간에 대해 S5 트레이드 목록을 생성한다."""
    config = config or S5Config()
    if df.empty or len(df) < 5:
        return []
    signals = detect_s45_signals(df, config)
    trades = []
    for sig in signals:
        trade = simulate_s5_trade(df, sig, config, ticker)
        if trade is not None:
            trades.append(trade)
    return trades


def describe_trade_plan_s4(df: pd.DataFrame, signal: RallySignal, config: S4Config) -> TradePlan:
    """아직 진입 전인 S4 신호의 매수조건/손절가/익절가를 사람이 읽을 수 있게 정리한다.

    S4(종가베팅)는 세력봉 **당일** 종가가 레벨 이하로 마감해야만 매수되고(entry_valid_days=1),
    체결가는 그 종가라 장 마감 전까지 정확히 알 수 없다(entry_price=None) — simulate_s4_trade와
    동일 규칙.
    """
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    # 진입가 미확정이라 익절%은 레벨을 진입가의 근사치로 써서 계산한다(실제 체결가는 그날
    # 종가라 레벨과 정확히 같지 않은 근사값).
    approx_target_pct = (signal.high / level - 1) * 100
    return TradePlan(
        entry_price=None,
        entry_desc=f"세력봉 당일 종가가 {level:,.0f}원 이하로 마감해야 매수(당일 1회 한정 — 당일이 지났으면 기회 종료)",
        stop_price=None,
        stop_pct=config.stop_loss_pct * 100,
        stop_desc=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%",
        target_price=signal.high,
        target_pct=approx_target_pct,
        target_desc=f"{signal.high:,.0f}원(전고점)",
    )


def plan_entry_order_s4(signal: RallySignal, config: S4Config) -> EntryOrderPlan:
    """종가베팅, 세력봉 당일 1회만 유효 — 15:20 이후 장마감 임박 폴링으로 조건 판단 후 시장가 매수."""
    return EntryOrderPlan(order_style="close_bet", limit_price=None, entry_valid_trading_days=config.entry_valid_days)


def compute_bracket_s4(fill_price: float, signal: RallySignal, config: S4Config) -> Bracket:
    """실제 체결가(그날 종가) 기준 -7% 손절, 전고점 익절 — simulate_s4_trade와 동일 규칙."""
    stop_price = fill_price * (1 + config.stop_loss_pct)
    return Bracket(
        stop_price=stop_price, target_price=signal.high, max_hold_trading_days=config.hold_days,
        stop_reason=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%", target_reason="전고점(RH)",
    )


def plan_entry_order_s5(signal: RallySignal, config: S5Config) -> EntryOrderPlan:
    """되돌림선에 지정가 매수를 걸어두면 거래소가 터치 시 체결해준다 — entry_valid_days(기본 4일)
    동안 매일 같은 가격으로 재주문해야 한다."""
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    return EntryOrderPlan(order_style="fixed_limit", limit_price=level, entry_valid_trading_days=config.entry_valid_days)


def compute_bracket_s5(fill_price: float, signal: RallySignal, config: S5Config) -> Bracket:
    """실제 체결가 기준 -7% 손절/+7% 익절 — simulate_s5_trade와 동일 규칙."""
    stop_price = fill_price * (1 + config.stop_loss_pct)
    target_price = fill_price * (1 + config.profit_target_pct)
    return Bracket(
        stop_price=stop_price, target_price=target_price, max_hold_trading_days=config.hold_days,
        stop_reason=f"매수가 대비 {config.stop_loss_pct * 100:.0f}%",
        target_reason=f"매수가 대비 +{config.profit_target_pct * 100:.0f}%",
    )


def describe_trade_plan_s5(df: pd.DataFrame, signal: RallySignal, config: S5Config) -> TradePlan:
    """아직 진입 전인 S5 신호의 매수가/손절가/익절가를 사람이 읽을 수 있게 정리한다.

    S5(장중매매)는 레벨을 장중 터치하면 그 레벨 가격에 체결되므로(simulate_s5_trade) 매수가가
    확정값이고, 손절/익절도 전부 그 값 기준 비율이라 미리 계산 가능하다.
    """
    level = signal.high - (signal.high - signal.low) * config.fib_ratio
    stop_price = level * (1 + config.stop_loss_pct)
    target_price = level * (1 + config.profit_target_pct)
    return TradePlan(
        entry_price=level,
        entry_desc=f"{level:,.0f}원 터치 시 매수",
        stop_price=stop_price,
        stop_pct=config.stop_loss_pct * 100,
        stop_desc=f"{stop_price:,.0f}원(매수가 대비 {config.stop_loss_pct * 100:.0f}%)",
        target_price=target_price,
        target_pct=config.profit_target_pct * 100,
        target_desc=f"{target_price:,.0f}원(매수가 대비 +{config.profit_target_pct * 100:.0f}%)",
    )
