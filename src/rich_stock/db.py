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
