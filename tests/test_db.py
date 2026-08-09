import numpy as np
import pandas as pd
import pytest

from rich_stock import db
from rich_stock.config import S3Config
from rich_stock.strategies.s1 import S1Signal
from rich_stock.strategies.s3 import S3Signal
from rich_stock.strategies.s6 import S6Signal


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.duckdb")
    yield c
    c.close()


def test_connect_creates_schema(conn):
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    assert {"runs", "signals", "trades", "decisions"} <= tables


def test_save_run_persists_params_as_json(conn):
    run_id = db.new_run_id("S3", "2021-01-01", "2021-12-31")
    db.save_run(conn, run_id, "S3", "2021-01-01", "2021-12-31", S3Config())
    row = conn.execute("SELECT run_id, technique, params FROM runs WHERE run_id = ?", [run_id]).fetchone()
    assert row[0] == run_id
    assert row[1] == "S3"
    assert "fib_s3_ratio" in row[2]


def test_save_signals_for_k2(conn):
    run_id = "test_run"
    sig = S3Signal(ul_index=1, ul_date=pd.Timestamp("2024-01-03"), high=13000.0, low=9800.0, s3_level=11400.0)
    db.save_signals(conn, run_id, "S3", "005930", [sig])
    row = conn.execute(
        "SELECT ticker, event_date, high, low, level FROM signals WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "005930"
    assert row[2] == 13000.0
    assert row[3] == 9800.0
    assert row[4] == 11400.0


def test_save_signals_for_sr_casts_numpy_scalars(conn):
    # OHLCV 컬럼이 numpy int64/float64라 S1Signal의 r0~r3에 numpy 스칼라가 그대로 들어오는 경우를
    # 재현한다 — DuckDB 바인딩이 numpy 스칼라를 직접 못 받아 실제 스모크 테스트에서 터졌던 버그.
    sig = S1Signal(
        ul_index=1, ul_date=pd.Timestamp("2024-01-03"),
        r0=np.int64(13000), r1=np.int64(12000), r2=np.int64(11000), r3=np.int64(10000),
    )
    db.save_signals(conn, "run", "S1", "005930", [sig])
    row = conn.execute("SELECT high, low, level, extra FROM signals WHERE run_id = 'run'").fetchone()
    assert row[0] == 13000.0
    assert row[1] == 10000.0
    assert row[2] == 12000.0
    assert '"r0": 13000.0' in row[3]


def test_save_signals_for_sp(conn):
    run_id = "test_run"
    sig = S6Signal(
        peak_index=6, peak_date=pd.Timestamp("2024-01-11"),
        peak_price=235.0, pre_rally_low=98.0, streak_len=3,
    )
    db.save_signals(conn, run_id, "S6", "005930", [sig])
    row = conn.execute(
        "SELECT ticker, event_date, high, low, level, extra FROM signals WHERE run_id = ?", [run_id]
    ).fetchone()
    assert row[0] == "005930"
    assert row[2] == 235.0
    assert row[3] == 98.0
    assert row[4] is None  # 이동평균 기반이라 detect 단계에는 고정 level이 없음
    assert '"streak_len": 3' in row[5]


def test_last_livescan_date_returns_none_when_no_runs(conn):
    assert db.last_livescan_date(conn, "S3") is None


def test_last_livescan_date_ignores_non_livescan_runs(conn):
    db.save_run(conn, "backtest_research_run", "S3", "2021-01-01", "2021-12-31", S3Config())
    assert db.last_livescan_date(conn, "S3") is None


def test_last_livescan_date_returns_latest_end_date_for_technique(conn):
    db.save_run(conn, "livescan_s3_2026-08-05_120000", "S3", "2026-07-06", "2026-08-05", S3Config())
    db.save_run(conn, "livescan_s3_2026-08-08_120000", "S3", "2026-07-09", "2026-08-08", S3Config())
    db.save_run(conn, "livescan_s2_2026-08-09_120000", "S2", "2026-07-10", "2026-08-09", S3Config())

    assert db.last_livescan_date(conn, "S3") == pd.Timestamp("2026-08-08")


def test_existing_livescan_signal_dates_excludes_non_livescan_runs(conn):
    sig = S3Signal(ul_index=1, ul_date=pd.Timestamp("2024-01-03"), high=13000.0, low=9800.0, s3_level=11400.0)
    db.save_signals(conn, "livescan_s3_2024-01-04_120000", "S3", "005930", [sig])
    db.save_signals(conn, "backtest_research_run", "S3", "000660", [sig])

    existing = db.existing_livescan_signal_dates(conn, "S3")
    assert existing == {("005930", pd.Timestamp("2024-01-03"))}


def test_existing_livescan_signal_dates_scoped_by_technique(conn):
    sig = S3Signal(ul_index=1, ul_date=pd.Timestamp("2024-01-03"), high=13000.0, low=9800.0, s3_level=11400.0)
    db.save_signals(conn, "livescan_s3_2024-01-04_120000", "S3", "005930", [sig])

    assert db.existing_livescan_signal_dates(conn, "S2") == set()


def test_save_signals_empty_list_is_noop(conn):
    db.save_signals(conn, "run", "S3", "005930", [])
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

    n = db.load_trades_csv(conn, "test_run", "S5", csv_path)
    assert n == 1

    row = conn.execute("SELECT run_id, technique, ticker, exit_reason FROM trades").fetchone()
    assert row == ("test_run", "S5", "005930", "exit_target_high")


def test_save_signals_for_universe_calls_detect_per_ticker(conn):
    closes = [10000, 13000, 12500, 11200, 13100]
    highs = [10000, 13000, 12600, 11500, 13100]
    lows = [9800, 12000, 12300, 11300, 12700]
    dates = pd.bdate_range("2024-01-02", periods=5)
    df = pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": [1_000_000] * 5}, index=dates)
    df["PrevClose"] = df["Close"].shift(1)
    df["TradingValue"] = 100_000_000_000
    ohlcv = {"005930": df, "000660": df}

    from rich_stock.strategies.s3 import detect_s3_signals

    db.save_signals_for_universe(conn, "run2", "S3", ohlcv, S3Config(), detect_s3_signals)
    count = conn.execute("SELECT count(*) FROM signals WHERE run_id = 'run2'").fetchone()[0]
    assert count == 2  # 두 종목 모두 동일한 신호 1건씩


# --- decisions (매수/보류/매도 결과 기록) --------------------------------------


def test_record_buy_creates_open_decision(conn):
    decision_id = db.record_buy(
        conn, "005930", buy_price=70000, technique="S1", signal_date="2026-08-01",
        buy_date="2026-08-02", quantity=10, note="테스트 매수",
    )
    row = conn.execute(
        "SELECT ticker, technique, action, status, buy_price, quantity, note FROM decisions WHERE decision_id = ?",
        [decision_id],
    ).fetchone()
    assert row == ("005930", "S1", "buy", "open", 70000, 10, "테스트 매수")


def test_record_buy_defaults_buy_date_to_today(conn):
    decision_id = db.record_buy(conn, "005930", buy_price=70000)
    row = conn.execute("SELECT buy_date FROM decisions WHERE decision_id = ?", [decision_id]).fetchone()
    assert row[0] is not None


def test_record_skip_creates_skipped_decision(conn):
    decision_id = db.record_skip(conn, "069540", technique="S1", note="이평선 왜곡 의심")
    row = conn.execute("SELECT action, status, note FROM decisions WHERE decision_id = ?", [decision_id]).fetchone()
    assert row == ("skip", "skipped", "이평선 왜곡 의심")


def test_close_decision_computes_pnl_and_return_pct(conn):
    decision_id = db.record_buy(conn, "005930", buy_price=70000, quantity=10)
    db.close_decision(conn, decision_id, sell_price=77000, sell_date="2026-08-10")

    row = conn.execute(
        "SELECT status, sell_price, sell_date, pnl, return_pct FROM decisions WHERE decision_id = ?",
        [decision_id],
    ).fetchone()
    status, sell_price, sell_date, pnl, return_pct = row
    assert status == "closed"
    assert sell_price == 77000
    assert str(sell_date) == "2026-08-10"
    assert pnl == 70000  # (77000-70000)*10
    assert round(return_pct, 2) == 10.0  # (77000/70000-1)*100


def test_close_decision_raises_for_unknown_id(conn):
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        db.close_decision(conn, "dec_nonexistent_00000000", sell_price=1000)


def test_close_decision_raises_if_already_closed(conn):
    decision_id = db.record_buy(conn, "005930", buy_price=70000)
    db.close_decision(conn, decision_id, sell_price=71000)
    with pytest.raises(ValueError, match="open만 가능"):
        db.close_decision(conn, decision_id, sell_price=72000)


def test_find_open_decision_returns_most_recent_open(conn):
    db.record_buy(conn, "005930", buy_price=70000, buy_date="2026-08-01")
    newer_id = db.record_buy(conn, "005930", buy_price=71000, buy_date="2026-08-05")

    assert db.find_open_decision(conn, "005930") == newer_id


def test_find_open_decision_returns_none_when_no_open_position(conn):
    decision_id = db.record_buy(conn, "005930", buy_price=70000)
    db.close_decision(conn, decision_id, sell_price=71000)

    assert db.find_open_decision(conn, "005930") is None
    assert db.find_open_decision(conn, "999999") is None
