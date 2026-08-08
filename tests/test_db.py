import numpy as np
import pandas as pd
import pytest

from rich_stock import db
from rich_stock.config import K2Config
from rich_stock.strategies.k2 import K2Signal
from rich_stock.strategies.sp import SPSignal
from rich_stock.strategies.sr import SRSignal


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.duckdb")
    yield c
    c.close()


def test_connect_creates_schema(conn):
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    assert {"runs", "signals", "trades"} <= tables


def test_save_run_persists_params_as_json(conn):
    run_id = db.new_run_id("K2", "2021-01-01", "2021-12-31")
    db.save_run(conn, run_id, "K2", "2021-01-01", "2021-12-31", K2Config())
    row = conn.execute("SELECT run_id, technique, params FROM runs WHERE run_id = ?", [run_id]).fetchone()
    assert row[0] == run_id
    assert row[1] == "K2"
    assert "fib_k2_ratio" in row[2]


def test_save_signals_for_k2(conn):
    run_id = "test_run"
    sig = K2Signal(ul_index=1, ul_date=pd.Timestamp("2024-01-03"), high=13000.0, low=9800.0, k2_level=11400.0)
    db.save_signals(conn, run_id, "K2", "005930", [sig])
    row = conn.execute(
        "SELECT ticker, event_date, high, low, level FROM signals WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "005930"
    assert row[2] == 13000.0
    assert row[3] == 9800.0
    assert row[4] == 11400.0


def test_save_signals_for_sr_casts_numpy_scalars(conn):
    # OHLCV 컬럼이 numpy int64/float64라 SRSignal의 r0~r3에 numpy 스칼라가 그대로 들어오는 경우를
    # 재현한다 — DuckDB 바인딩이 numpy 스칼라를 직접 못 받아 실제 스모크 테스트에서 터졌던 버그.
    sig = SRSignal(
        ul_index=1, ul_date=pd.Timestamp("2024-01-03"),
        r0=np.int64(13000), r1=np.int64(12000), r2=np.int64(11000), r3=np.int64(10000),
    )
    db.save_signals(conn, "run", "SR", "005930", [sig])
    row = conn.execute("SELECT high, low, level, extra FROM signals WHERE run_id = 'run'").fetchone()
    assert row[0] == 13000.0
    assert row[1] == 10000.0
    assert row[2] == 12000.0
    assert '"r0": 13000.0' in row[3]


def test_save_signals_for_sp(conn):
    run_id = "test_run"
    sig = SPSignal(
        peak_index=6, peak_date=pd.Timestamp("2024-01-11"),
        peak_price=235.0, pre_rally_low=98.0, streak_len=3,
    )
    db.save_signals(conn, run_id, "SP", "005930", [sig])
    row = conn.execute(
        "SELECT ticker, event_date, high, low, level, extra FROM signals WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "005930"
    assert row[2] == 235.0
    assert row[3] == 98.0
    assert row[4] is None  # 이동평균 기반이라 detect 단계에는 고정 level이 없음
    assert '"streak_len": 3' in row[5]


def test_save_signals_empty_list_is_noop(conn):
    db.save_signals(conn, "run", "K2", "005930", [])
    count = conn.execute("SELECT count(*) FROM signals").fetchone()[0]
    assert count == 0


def test_load_trades_csv(conn, tmp_path):
    csv_path = tmp_path / "trades.csv"
    df = pd.DataFrame(
        [
            {
                "ticker": "005930",
                "signal_date": "2024-01-02",
                "entry_date": "2024-01-03",
                "exit_date": "2024-01-08",
                "exit_reason": "exit_target_high",
                "return_pct": 7.0,
                "pnl_per_unit": 798.0,
                "closed": True,
            }
        ]
    )
    df.to_csv(csv_path, index=False)

    n = db.load_trades_csv(conn, "test_run", "K2+", csv_path)
    assert n == 1

    row = conn.execute("SELECT run_id, technique, ticker, exit_reason FROM trades").fetchone()
    assert row == ("test_run", "K2+", "005930", "exit_target_high")


def test_save_signals_for_universe_calls_detect_per_ticker(conn):
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    dates = pd.bdate_range("2024-01-02", periods=5)
    df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * 5}, index=dates)
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = 100_000_000_000
    ohlcv = {"005930": df, "000660": df}

    from rich_stock.strategies.k2 import detect_k2_signals

    db.save_signals_for_universe(conn, "run2", "K2", ohlcv, K2Config(), detect_k2_signals)
    count = conn.execute("SELECT count(*) FROM signals WHERE run_id = 'run2'").fetchone()[0]
    assert count == 2  # 두 종목 모두 동일한 신호 1건씩
