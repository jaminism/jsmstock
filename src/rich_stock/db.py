"""백테스트 신호·트레이드 결과를 DuckDB에 누적 저장한다.

여러 기법(S1/S2/S3/S2+/S3+)이 CLI 스크립트를 각각 따로 실행할 때마다 결과가 stdout/CSV로만
남아 기법 간 비교(같은 날 S1·S3 신호가 겹치는지 등)나 나중 fund-manager 포트폴리오 구성 단계에서
여러 기법 결과를 한 번에 querying하기가 번거로웠던 문제를 해결하기 위해 도입했다. 서버 프로세스가
필요 없는 파일 기반 DB(DuckDB)를 쓰며, pandas/CSV와의 연동이 기본 내장이라 기존 스크립트 구조를
크게 바꾸지 않고 얹을 수 있다.

스키마:
  runs(run_id, technique, start_date, end_date, params, created_at)
  signals(run_id, technique, ticker, event_date, high, low, level, extra)
    - `level`은 기법별 "1차 진입 트리거 가격"의 공통 명칭이다(S1=R1, S2=S2선, S3=S3선).
      S2+/S3+는 되돌림 비율(fib_ratio)이 변형(S2+/S3+)마다 달라 detect 단계에서 아직 확정되지
      않으므로 NULL로 둔다. 기법 고유 필드(S1의 R0~R3, 정성적 점수 등)는 `extra`에 JSON으로 넣는다.
  trades(run_id, technique, ticker, signal_date, entry_date, exit_date, exit_reason,
         return_pct, pnl_per_unit, closed)
    - 각 CLI 스크립트가 이미 --trades-csv로 내보내던 것과 동일한 컬럼 구성. `load_trades_csv()`가
      해당 CSV를 DuckDB의 네이티브 CSV 리더(read_csv_auto)로 그대로 읽어 적재한다.
  decisions(decision_id, ticker, technique, signal_date, action, status, buy_date, buy_price,
            quantity, sell_date, sell_price, pnl, return_pct, note, created_at, updated_at)
    - 실제 매수/보류 판단과 매도 결과를 기록하는 개인 매매일지. signals/trades와 달리 순수
      연구용 백테스트가 아니라 실제 의사결정 기록이라 scripts/local/(git 미추적)의 CLI에서만
      쓰는 걸 전제로 한다 — 이 모듈(db.py) 자체는 공개 저장소에 있지만, 여기 담기는 실제 데이터
      (.cache/rich_stock.duckdb)는 완전히 로컬 전용이다.
  auto_positions(position_id, decision_id, ticker, technique, signal_date, status, order_style,
                 entry_order_no, entry_valid_until_trading_day, fill_price, fill_quantity,
                 fill_date, stop_price, target_price, max_hold_trading_days, trading_days_held,
                 is_safety_override, exit_order_no, exit_reason, last_checked_at, last_price,
                 note, created_at, updated_at)
    - 자동매매(scripts/local/auto_trader.py)가 관리하는 "진행 중" 포지션 상태. decisions와
      역할이 다르다 — decisions는 매수/매도가 실제로 체결된 뒤의 감사기록이고, 여기는 주문
      제출~체결 대기 중(status='pending_entry')이거나 보유 중(status='open')인 동안 필요한
      손절가/익절가/경과일 같은 살아있는 상태를 담는다. status='open'이 되는 시점(체결 확인)에
      비로소 decisions row가 생성되고 decision_id로 연결된다 — 그 전까지 decision_id는 NULL
      (주문 제출 ≠ 체결 확정이므로 미체결 상태를 decisions에 기록하지 않기 위함).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

DEFAULT_DB_PATH = ".cache/rich_stock.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR PRIMARY KEY,
    technique VARCHAR,
    start_date VARCHAR,
    end_date VARCHAR,
    params VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS signals (
    run_id VARCHAR,
    technique VARCHAR,
    ticker VARCHAR,
    event_date DATE,
    high DOUBLE,
    low DOUBLE,
    level DOUBLE,
    extra VARCHAR
);
CREATE TABLE IF NOT EXISTS trades (
    run_id VARCHAR,
    technique VARCHAR,
    ticker VARCHAR,
    signal_date DATE,
    entry_date DATE,
    exit_date DATE,
    exit_reason VARCHAR,
    return_pct DOUBLE,
    pnl_per_unit DOUBLE,
    closed BOOLEAN
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id VARCHAR PRIMARY KEY,
    ticker VARCHAR,
    technique VARCHAR,
    signal_date DATE,
    action VARCHAR,
    status VARCHAR,
    buy_date DATE,
    buy_price DOUBLE,
    quantity DOUBLE,
    sell_date DATE,
    sell_price DOUBLE,
    pnl DOUBLE,
    return_pct DOUBLE,
    note VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS auto_positions (
    position_id VARCHAR PRIMARY KEY,
    decision_id VARCHAR,
    ticker VARCHAR,
    technique VARCHAR,
    signal_date DATE,
    status VARCHAR,
    order_style VARCHAR,
    entry_order_no VARCHAR,
    entry_valid_until_trading_day INTEGER,
    fill_price DOUBLE,
    fill_quantity DOUBLE,
    fill_date DATE,
    stop_price DOUBLE,
    target_price DOUBLE,
    max_hold_trading_days INTEGER,
    trading_days_held INTEGER DEFAULT 0,
    is_safety_override BOOLEAN DEFAULT FALSE,
    exit_order_no VARCHAR,
    exit_reason VARCHAR,
    last_checked_at TIMESTAMP,
    last_price DOUBLE,
    note VARCHAR,
    created_at TIMESTAMP DEFAULT current_timestamp,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> duckdb.DuckDBPyConnection:
    """DuckDB 파일에 연결하고(없으면 생성) 스키마를 보장한다."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute(_SCHEMA)
    return conn


def new_run_id(technique: str, start: str, end: str) -> str:
    return f"{technique}_{start}_{end}_{uuid.uuid4().hex[:8]}"


def save_run(conn: duckdb.DuckDBPyConnection, run_id: str, technique: str, start: str, end: str, config: Any) -> None:
    params = json.dumps(dataclasses.asdict(config), default=str, ensure_ascii=False) if dataclasses.is_dataclass(config) else str(config)
    conn.execute(
        "INSERT INTO runs (run_id, technique, start_date, end_date, params) VALUES (?, ?, ?, ?, ?)",
        [run_id, technique, start, end, params],
    )


def _signal_row(technique: str, ticker: str, sig: Any) -> dict:
    # OHLCV 컬럼이 numpy int64/float64 dtype이라 신호 필드에 numpy 스칼라가 그대로 남아있는
    # 경우가 있다(특히 S1의 r0~r3) — DuckDB Python 바인딩이 numpy 스칼라를 직접 받아들이지
    # 못해 float()로 명시 캐스팅한다.
    if technique == "S1":
        extra = {"r0": float(sig.r0), "r1": float(sig.r1), "r2": float(sig.r2), "r3": float(sig.r3)}
        if sig.qual is not None:
            extra["qual_score"] = float(sig.qual.score)
        return {
            "ticker": ticker, "event_date": sig.ul_date, "high": float(sig.r0), "low": float(sig.r3),
            "level": float(sig.r1), "extra": json.dumps(extra, ensure_ascii=False),
        }
    if technique == "S2":
        return {"ticker": ticker, "event_date": sig.ul_date, "high": float(sig.high), "low": float(sig.low), "level": float(sig.s2_level), "extra": None}
    if technique == "S3":
        return {"ticker": ticker, "event_date": sig.ul_date, "high": float(sig.high), "low": float(sig.low), "level": float(sig.s3_level), "extra": None}
    if technique in ("S4", "S5"):
        # fib_ratio가 변형(S2+/S3+)마다 달라 detect 단계의 신호 객체에는 level이 없다.
        return {"ticker": ticker, "event_date": sig.event_date, "high": float(sig.high), "low": float(sig.low), "level": None, "extra": None}
    if technique == "S6":
        # 되돌림 기준선이 고정 그리드가 아니라 매일 갱신되는 이동평균이라 detect 단계에는 level이
        # 없다(진입가는 매매 시뮬레이션 시점의 15일선 값으로 결정됨).
        extra = json.dumps({"streak_len": int(sig.streak_len)}, ensure_ascii=False)
        return {
            "ticker": ticker, "event_date": sig.peak_date, "high": float(sig.peak_price),
            "low": float(sig.pre_rally_low), "level": None, "extra": extra,
        }
    raise ValueError(f"unknown technique: {technique}")


def last_livescan_date(conn: duckdb.DuckDBPyConnection, technique: str) -> pd.Timestamp | None:
    """해당 기법의 가장 최근 라이브 스캔 기준일(end_date)을 반환한다. 스캔 이력이 없으면 None.

    라이브 스캔 스크립트가 매번 전체 히스토리를 처음부터 재계산하는 대신, 이 날짜 이전은 이미
    스캔했다고 보고 그 부근부터만(기법별 lookback 여유를 두고) 다시 훑도록 증분 스캔의 기준점으로
    쓴다. 신규 종목처럼 아직 한 번도 스캔되지 않은 기법이면 None을 반환해 호출자가 전체 스캔으로
    폴백하게 한다.
    """
    row = conn.execute(
        "SELECT MAX(end_date) FROM runs WHERE technique = ? AND run_id LIKE 'livescan_%'",
        [technique],
    ).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] is not None else None


def existing_livescan_signal_dates(conn: duckdb.DuckDBPyConnection, technique: str) -> set[tuple[str, pd.Timestamp]]:
    """이미 라이브 스캔(run_id가 'livescan_'으로 시작)으로 저장된 (ticker, event_date) 조합.

    라이브 스캔 스크립트(scripts/local/daily_watchlist.py, find_recent_signals.py)는 실행할 때마다
    최근 며칠치 신호를 전체 히스토리 재계산으로 다시 찾아내는데, 과거 확정봉 기준 신호는 어제
    계산한 결과와 동일하므로 이미 저장된 조합은 걸러내고 새 신호만 저장하기 위해 쓴다. 백테스트
    연구용 run(run_id가 livescan_ 이 아닌 것)은 같은 신호를 여러 run에 걸쳐 의도적으로 반복
    기록할 수 있으므로 이 dedup 대상에서 제외한다.
    """
    rows = conn.execute(
        "SELECT DISTINCT ticker, event_date FROM signals WHERE technique = ? AND run_id LIKE 'livescan_%'",
        [technique],
    ).fetchall()
    return {(ticker, pd.Timestamp(event_date)) for ticker, event_date in rows}


def save_signals(conn: duckdb.DuckDBPyConnection, run_id: str, technique: str, ticker: str, signals: list) -> None:
    if not signals:
        return
    rows = [_signal_row(technique, ticker, s) for s in signals]
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, technique, r["ticker"], r["event_date"], r["high"], r["low"], r["level"], r["extra"]) for r in rows],
    )


def save_signals_for_universe(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    technique: str,
    ohlcv: dict[str, pd.DataFrame],
    config: Any,
    detect_signals_fn: Callable,
) -> None:
    """유니버스 전체 종목에 대해 detect_*_signals를 다시 호출해 신호를 저장한다.

    backtest_ticker()가 트레이드만 반환하고 신호 자체는 내부에서 소비되고 버려지므로, 저장을
    위해 detect 함수를 한 번 더 호출한다(전략 모듈은 건드리지 않는 선택 — strategies/*.py 참고).
    """
    for ticker, df in ohlcv.items():
        if df.empty or len(df) < 5:
            continue
        signals = detect_signals_fn(df, config)
        save_signals(conn, run_id, technique, ticker, signals)


TRADE_CSV_COLUMNS = [
    "ticker", "signal_date", "entry_date", "exit_date", "exit_reason",
    "return_pct", "pnl_per_unit", "closed",
]


def trades_to_dataframe(trades: list) -> pd.DataFrame:
    """Trade 리스트를 각 CLI 스크립트의 --trades-csv와 동일한 컬럼 구성으로 변환한다.

    trades가 비어 있어도(신호가 아예 없던 백테스트) TRADE_CSV_COLUMNS 헤더는 유지한다 —
    그래야 read_csv_auto()로 다시 읽을 때 컬럼을 인식할 수 있다.
    """
    rows = [
        {
            "ticker": t.ticker,
            "signal_date": t.signal_date,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "exit_reason": t.exit_reason,
            "return_pct": t.return_pct * 100 if t.entry_date else None,
            "pnl_per_unit": t.pnl if t.entry_date else None,
            "closed": t.is_closed,
        }
        for t in trades
    ]
    return pd.DataFrame(rows, columns=TRADE_CSV_COLUMNS)


def save_backtest_results(
    db_path: str | Path,
    technique: str,
    start: str,
    end: str,
    config: Any,
    ohlcv: dict[str, pd.DataFrame],
    trades: list,
    detect_signals_fn: Callable,
    trades_csv_path: str | Path | None = None,
) -> str:
    """CLI 스크립트 공용: 신호+트레이드를 DB에 저장한다.

    trades_csv_path가 주어지면(사용자가 --trades-csv를 지정한 경우) 그 파일을 그대로 재사용해
    적재하고, 없으면 DB 파일 옆에 run_id 이름으로 임시 CSV를 만들어 적재한 뒤 남겨둔다(재사용 가능한
    감사 기록 겸용).

    Returns: 생성된 run_id.
    """
    run_id = new_run_id(technique, start, end)
    conn = connect(db_path)
    try:
        save_run(conn, run_id, technique, start, end, config)
        save_signals_for_universe(conn, run_id, technique, ohlcv, config, detect_signals_fn)

        csv_path = Path(trades_csv_path) if trades_csv_path else Path(db_path).parent / "trades_csv" / f"{run_id}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not (trades_csv_path and Path(trades_csv_path).exists()):
            trades_to_dataframe(trades).to_csv(csv_path, index=False, encoding="utf-8-sig")
        load_trades_csv(conn, run_id, technique, csv_path)
    finally:
        conn.close()
    return run_id


def new_decision_id(ticker: str) -> str:
    return f"dec_{ticker}_{uuid.uuid4().hex[:8]}"


def record_buy(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
    buy_price: float,
    technique: str | None = None,
    signal_date: str | None = None,
    buy_date: str | None = None,
    quantity: float | None = None,
    note: str | None = None,
) -> str:
    """매수 결정을 기록한다(status='open'). Returns: decision_id."""
    decision_id = new_decision_id(ticker)
    conn.execute(
        """
        INSERT INTO decisions (decision_id, ticker, technique, signal_date, action, status,
                                buy_date, buy_price, quantity, note)
        VALUES (?, ?, ?, ?, 'buy', 'open', COALESCE(?, current_date), ?, ?, ?)
        """,
        [decision_id, ticker, technique, signal_date, buy_date, buy_price, quantity, note],
    )
    return decision_id


def record_skip(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
    technique: str | None = None,
    signal_date: str | None = None,
    note: str | None = None,
) -> str:
    """신호를 보고 매수하지 않기로 한 결정을 기록한다(status='skipped'). Returns: decision_id."""
    decision_id = new_decision_id(ticker)
    conn.execute(
        """
        INSERT INTO decisions (decision_id, ticker, technique, signal_date, action, status, note)
        VALUES (?, ?, ?, ?, 'skip', 'skipped', ?)
        """,
        [decision_id, ticker, technique, signal_date, note],
    )
    return decision_id


def close_decision(
    conn: duckdb.DuckDBPyConnection,
    decision_id: str,
    sell_price: float,
    sell_date: str | None = None,
) -> None:
    """매도 결과를 기록하고 status를 'closed'로 바꾼다. pnl/return_pct는 buy_price*quantity 기준으로 자동 계산."""
    row = conn.execute(
        "SELECT buy_price, quantity, status FROM decisions WHERE decision_id = ?", [decision_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"decision_id를 찾을 수 없습니다: {decision_id}")
    buy_price, quantity, status = row
    if status != "open":
        raise ValueError(f"'{decision_id}'는 status='{status}'라 매도 처리할 수 없습니다(open만 가능).")
    qty = quantity if quantity is not None else 1.0
    pnl = (sell_price - buy_price) * qty
    return_pct = (sell_price / buy_price - 1) * 100 if buy_price else None
    conn.execute(
        """
        UPDATE decisions
        SET sell_date = COALESCE(?, current_date), sell_price = ?, pnl = ?, return_pct = ?,
            status = 'closed', updated_at = current_timestamp
        WHERE decision_id = ?
        """,
        [sell_date, sell_price, pnl, return_pct, decision_id],
    )


def find_open_decision(conn: duckdb.DuckDBPyConnection, ticker: str) -> str | None:
    """해당 종목의 가장 최근 open(매수 후 미매도) 결정의 decision_id를 찾는다. 없으면 None."""
    row = conn.execute(
        "SELECT decision_id FROM decisions WHERE ticker = ? AND status = 'open' ORDER BY buy_date DESC LIMIT 1",
        [ticker],
    ).fetchone()
    return row[0] if row else None


# --- auto_positions (자동매매 진행중 포지션 상태) -------------------------


def new_position_id(ticker: str) -> str:
    return f"pos_{ticker}_{uuid.uuid4().hex[:8]}"


def create_pending_position(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
    technique: str,
    signal_date: str,
    order_style: str,
    entry_order_no: str | None = None,
    entry_valid_until_trading_day: int | None = None,
    note: str | None = None,
) -> str:
    """진입 주문을 막 제출한 상태를 기록한다(status='pending_entry'). Returns: position_id."""
    position_id = new_position_id(ticker)
    conn.execute(
        """
        INSERT INTO auto_positions (position_id, ticker, technique, signal_date, status,
                                     order_style, entry_order_no, entry_valid_until_trading_day, note)
        VALUES (?, ?, ?, ?, 'pending_entry', ?, ?, ?, ?)
        """,
        [position_id, ticker, technique, signal_date, order_style, entry_order_no, entry_valid_until_trading_day, note],
    )
    return position_id


def update_pending_entry_order(conn: duckdb.DuckDBPyConnection, position_id: str, entry_order_no: str) -> None:
    """KRX 지정가는 당일만 유효해 매일 재주문해야 하는 기법(S1/S3/S5/S6)의 새 주문번호로 갱신한다."""
    conn.execute(
        "UPDATE auto_positions SET entry_order_no = ?, updated_at = current_timestamp WHERE position_id = ?",
        [entry_order_no, position_id],
    )


def confirm_position_fill(
    conn: duckdb.DuckDBPyConnection,
    position_id: str,
    fill_price: float,
    fill_quantity: float,
    stop_price: float | None,
    target_price: float | None,
    max_hold_trading_days: int | None,
    fill_date: str | None = None,
    is_safety_override: bool = False,
) -> str:
    """체결이 실제로 확인된 시점에만 호출한다 — status를 'open'으로 바꾸고, 이 시점에 비로소
    decisions row를 생성해(record_buy) decision_id로 연결한다(주문 제출만으로는 호출 금지 —
    미체결/부분체결일 수 있어서다). Returns: 새로 생성된 decision_id.
    """
    row = conn.execute(
        "SELECT ticker, technique, signal_date FROM auto_positions WHERE position_id = ?", [position_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"position_id를 찾을 수 없습니다: {position_id}")
    ticker, technique, signal_date = row
    decision_id = record_buy(
        conn, ticker, fill_price, technique=technique, signal_date=str(signal_date) if signal_date else None,
        buy_date=fill_date, quantity=fill_quantity, note="auto_trader",
    )
    conn.execute(
        """
        UPDATE auto_positions
        SET decision_id = ?, status = 'open', fill_price = ?, fill_quantity = ?,
            fill_date = COALESCE(?, current_date), stop_price = ?, target_price = ?,
            max_hold_trading_days = ?, is_safety_override = ?, updated_at = current_timestamp
        WHERE position_id = ?
        """,
        [decision_id, fill_price, fill_quantity, fill_date, stop_price, target_price,
         max_hold_trading_days, is_safety_override, position_id],
    )
    return decision_id


def close_position(
    conn: duckdb.DuckDBPyConnection,
    position_id: str,
    exit_order_no: str,
    exit_reason: str,
    sell_price: float,
    sell_date: str | None = None,
) -> None:
    """청산 체결이 확인된 시점에 호출 — 연결된 decisions row도 함께 닫는다(close_decision)."""
    row = conn.execute("SELECT decision_id FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    if row is None:
        raise ValueError(f"position_id를 찾을 수 없습니다: {position_id}")
    decision_id = row[0]
    if decision_id:
        close_decision(conn, decision_id, sell_price, sell_date)
    conn.execute(
        """
        UPDATE auto_positions
        SET status = 'closed', exit_order_no = ?, exit_reason = ?, updated_at = current_timestamp
        WHERE position_id = ?
        """,
        [exit_order_no, exit_reason, position_id],
    )


def expire_position(conn: duckdb.DuckDBPyConnection, position_id: str, note: str | None = None) -> None:
    """진입 유효기간이 지나도록 한 번도 체결 안 된 포지션을 종료한다(decisions row 자체가 없음)."""
    conn.execute(
        "UPDATE auto_positions SET status = 'expired', note = COALESCE(?, note), updated_at = current_timestamp WHERE position_id = ?",
        [note, position_id],
    )


def mark_position_error(conn: duckdb.DuckDBPyConnection, position_id: str, note: str) -> None:
    """브로커 실계좌와 DB 상태가 어긋나는 등 사람이 확인해야 하는 상황 — 자동 재개하지 않고 격리."""
    conn.execute(
        "UPDATE auto_positions SET status = 'error', note = ?, updated_at = current_timestamp WHERE position_id = ?",
        [note, position_id],
    )


def touch_position(conn: duckdb.DuckDBPyConnection, position_id: str, last_price: float) -> None:
    """매 폴링마다 마지막 확인시각/관측가만 갱신(모니터링/디버깅용, 상태 변경 없음)."""
    conn.execute(
        "UPDATE auto_positions SET last_checked_at = current_timestamp, last_price = ? WHERE position_id = ?",
        [last_price, position_id],
    )


def increment_trading_days_held(conn: duckdb.DuckDBPyConnection) -> None:
    """하루 중 첫 폴링에서 1회 호출 — 보유 중인 모든 포지션의 경과 거래일을 +1한다."""
    conn.execute("UPDATE auto_positions SET trading_days_held = trading_days_held + 1 WHERE status = 'open'")


def decrement_pending_entry_validity(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """하루 중 첫 폴링에서 1회 호출 — 진입 대기 중인 포지션의 남은 유효 거래일을 -1한다.
    0 이하가 된 position_id 목록을 반환하니, 호출부가 expire_position()으로 마감 처리할 것
    (entry_valid_until_trading_day가 NULL인 포지션은 유효기간 추적 대상이 아니라 건드리지 않음)."""
    conn.execute(
        "UPDATE auto_positions SET entry_valid_until_trading_day = entry_valid_until_trading_day - 1 "
        "WHERE status = 'pending_entry' AND entry_valid_until_trading_day IS NOT NULL"
    )
    rows = conn.execute(
        "SELECT position_id FROM auto_positions WHERE status = 'pending_entry' AND entry_valid_until_trading_day <= 0"
    ).fetchall()
    return [row[0] for row in rows]


def get_pending_positions(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    cols = [
        "position_id", "ticker", "technique", "signal_date", "order_style",
        "entry_order_no", "entry_valid_until_trading_day", "updated_at",
    ]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM auto_positions WHERE status = 'pending_entry'").fetchall()
    return [dict(zip(cols, row)) for row in rows]


def get_open_positions(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    cols = [
        "position_id", "decision_id", "ticker", "technique", "fill_price", "fill_quantity",
        "stop_price", "target_price", "max_hold_trading_days", "trading_days_held", "is_safety_override",
        "last_price",
    ]
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM auto_positions WHERE status = 'open'").fetchall()
    return [dict(zip(cols, row)) for row in rows]


def held_tickers(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """자동매매가 이미 진입 대기/보유 중인 종목 + 수동매매로 보유 중인 종목(decisions.status='open')
    전부 합친 것 — 신규 후보 선정 시 중복 진입을 막는 데 쓴다."""
    rows = conn.execute(
        "SELECT ticker FROM auto_positions WHERE status IN ('pending_entry', 'open') "
        "UNION SELECT ticker FROM decisions WHERE status = 'open'"
    ).fetchall()
    return {row[0] for row in rows}


def count_open_positions(conn: duckdb.DuckDBPyConnection) -> int:
    """동시보유 한도(max_concurrent_positions) 체크용 — 자동/수동 매매가 슬롯을 공유하므로
    decisions.status='open' 기준으로 센다(auto_positions.pending_entry는 아직 미체결이라 제외)."""
    return conn.execute("SELECT count(*) FROM decisions WHERE status = 'open'").fetchone()[0]


def load_trades_csv(conn: duckdb.DuckDBPyConnection, run_id: str, technique: str, csv_path: str | Path) -> int:
    """트레이드 CSV(각 CLI 스크립트의 --trades-csv와 동일한 컬럼 구성)를 DuckDB에 적재한다.

    Returns: 적재된 행 수.
    """
    result = conn.execute(
        """
        INSERT INTO trades
        SELECT ? AS run_id, ? AS technique, ticker, signal_date, entry_date, exit_date,
               exit_reason, return_pct, pnl_per_unit, closed
        FROM read_csv_auto(?, header=true)
        """,
        [run_id, technique, str(csv_path)],
    )
    row = result.fetchone()
    return int(row[0]) if row else 0
