import duckdb
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
    assert {"runs", "signals", "trades", "decisions", "auto_positions"} <= tables


def test_connect_read_only_can_query_existing_db(tmp_path):
    # writer가 close()된 뒤엔 read_only=True로 문제없이 조회할 수 있다. 다만 writer가 "계속
    # 열려있는 동안"은 read_only=True도 막힌다는 게 별개로 확인된 사실이다(상시 데몬처럼) —
    # 그 경우엔 아래 test_connect_import_from_export_directory의 EXPORT/IMPORT DATABASE
    # 방식(db_path가 디렉터리면 자동 감지)을 써야 한다.
    db_path = tmp_path / "test.duckdb"
    writer = db.connect(db_path)
    writer.execute(
        "INSERT INTO decisions (decision_id, ticker, buy_price, status) VALUES ('d1', '005930', 70000, 'open')"
    )
    writer.close()

    reader = db.connect(db_path, read_only=True)
    row = reader.execute("SELECT ticker, buy_price, status FROM decisions WHERE decision_id = 'd1'").fetchone()
    reader.close()

    assert row == ("005930", 70000, "open")


def test_connect_read_only_rejects_writes(tmp_path):
    db_path = tmp_path / "test.duckdb"
    db.connect(db_path).close()  # 파일만 먼저 만들어둠(스키마 보장)

    reader = db.connect(db_path, read_only=True)
    with pytest.raises(duckdb.Error):
        reader.execute("INSERT INTO decisions (decision_id, ticker, buy_price, status) VALUES ('d1', '005930', 70000, 'open')")
    reader.close()


def test_connect_import_from_export_directory(tmp_path):
    # 2026-08-21: writer가 계속 열려있는 동안은 read_only=True조차 막힌다(실측 확인) — 상시
    # 데몬처럼 writer가 절대 안 닫히는 상황에서 안전하게 조회하려면, writer 자신의 커넥션으로
    # EXPORT DATABASE를 내보낸 디렉터리를 읽어야 한다. connect()는 db_path가 디렉터리면 이
    # 스냅샷으로 보고 :memory: DB에 IMPORT DATABASE한 독립 사본을 돌려준다.
    db_path = tmp_path / "live.duckdb"
    writer = db.connect(db_path)
    writer.execute(
        "INSERT INTO decisions (decision_id, ticker, buy_price, status) VALUES ('d1', '005930', 70000, 'open')"
    )

    export_dir = tmp_path / "export_snapshot"
    export_dir.mkdir()
    writer.execute(f"EXPORT DATABASE '{export_dir.as_posix()}' (FORMAT PARQUET)")

    # writer가 아직 열려있는 채로 스냅샷 디렉터리를 통해 조회 — 라이브 파일 자체는 여전히 잠겨있다.
    with pytest.raises(duckdb.Error):
        db.connect(db_path, read_only=True)

    reader = db.connect(export_dir)
    row = reader.execute("SELECT ticker, buy_price, status FROM decisions WHERE decision_id = 'd1'").fetchone()
    reader.close()
    writer.close()

    assert row == ("005930", 70000, "open")


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


# --- auto_positions (자동매매 진행중 포지션 상태) --------------------------


def test_create_pending_position_status_and_fields(conn):
    position_id = db.create_pending_position(
        conn, "005930", technique="S5", signal_date="2026-08-01",
        order_style="fixed_limit", entry_order_no="000123", entry_valid_until_trading_day=4,
    )
    row = conn.execute(
        "SELECT ticker, technique, status, order_style, entry_order_no, entry_valid_until_trading_day "
        "FROM auto_positions WHERE position_id = ?", [position_id],
    ).fetchone()
    assert row == ("005930", "S5", "pending_entry", "fixed_limit", "000123", 4)


def test_update_pending_entry_order_replaces_order_no(conn):
    position_id = db.create_pending_position(
        conn, "005930", technique="S6", signal_date="2026-08-01",
        order_style="daily_recompute_limit", entry_order_no="000001",
    )
    db.update_pending_entry_order(conn, position_id, "000002")
    row = conn.execute("SELECT entry_order_no FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row[0] == "000002"


def test_create_pending_position_stores_entry_target_price(conn):
    position_id = db.create_pending_position(
        conn, "005930", technique="S5", signal_date="2026-08-01",
        order_style="fixed_limit", entry_target_price=68000,
    )
    row = conn.execute("SELECT target_price FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row[0] == 68000


def test_update_pending_entry_target_price_replaces_value(conn):
    position_id = db.create_pending_position(
        conn, "035420", technique="S6", signal_date="2026-08-01",
        order_style="daily_recompute_limit", entry_target_price=100000,
    )
    db.update_pending_entry_target_price(conn, position_id, 101500)
    row = conn.execute("SELECT target_price FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row[0] == 101500


def test_confirm_position_fill_creates_decision_and_updates_position(conn):
    position_id = db.create_pending_position(
        conn, "005930", technique="S5", signal_date="2026-08-01", order_style="fixed_limit",
    )
    decision_id = db.confirm_position_fill(
        conn, position_id, fill_price=70000, fill_quantity=10,
        stop_price=65100, target_price=74900, max_hold_trading_days=4, fill_date="2026-08-02",
    )

    decision_row = conn.execute(
        "SELECT ticker, technique, buy_price, quantity, status FROM decisions WHERE decision_id = ?", [decision_id]
    ).fetchone()
    assert decision_row == ("005930", "S5", 70000, 10, "open")

    pos_row = conn.execute(
        "SELECT decision_id, status, fill_price, stop_price, target_price, max_hold_trading_days, is_safety_override "
        "FROM auto_positions WHERE position_id = ?", [position_id],
    ).fetchone()
    assert pos_row == (decision_id, "open", 70000, 65100, 74900, 4, False)


def test_confirm_position_fill_raises_for_unknown_position(conn):
    with pytest.raises(ValueError, match="찾을 수 없습니다"):
        db.confirm_position_fill(conn, "pos_nonexistent", fill_price=1000, fill_quantity=1,
                                  stop_price=None, target_price=None, max_hold_trading_days=None)


def test_close_position_closes_linked_decision(conn):
    position_id = db.create_pending_position(conn, "005930", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, position_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)

    db.close_position(conn, position_id, exit_order_no="000456", exit_reason="exit_target", sell_price=74900)

    pos_row = conn.execute("SELECT status, exit_order_no, exit_reason FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert pos_row == ("closed", "000456", "exit_target")
    decision_row = conn.execute("SELECT status, sell_price FROM decisions WHERE ticker = '005930'").fetchone()
    assert decision_row == ("closed", 74900)


def test_update_pending_exit_order_sets_status_without_closing(conn):
    # 2026-08-21: 시장가 매도 제출 시점에 곧바로 close_position을 부르면 실제 체결 확인 없이
    # DB만 "청산 완료"로 믿게 된다(하한가 등으로 진짜 미체결일 위험) — 주문 제출은 이 함수로
    # 'pending_exit'만 기록하고, 실제 체결 확인 후에만 close_position을 부르는 게 맞다.
    position_id = db.create_pending_position(conn, "005930", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, position_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)

    db.update_pending_exit_order(conn, position_id, exit_order_no="000789", exit_reason="exit_stop")

    row = conn.execute(
        "SELECT status, exit_order_no, exit_reason FROM auto_positions WHERE position_id = ?", [position_id]
    ).fetchone()
    assert row == ("pending_exit", "000789", "exit_stop")
    decision_row = conn.execute("SELECT status FROM decisions WHERE ticker = '005930'").fetchone()
    assert decision_row[0] == "open"  # 체결 확인 전까지는 decisions도 아직 닫히면 안 됨


def test_get_pending_exit_positions_filters_by_status(conn):
    open_id = db.create_pending_position(conn, "005930", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, open_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)
    exiting_id = db.create_pending_position(conn, "000660", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, exiting_id, fill_price=50000, fill_quantity=5,
                              stop_price=46500, target_price=53500, max_hold_trading_days=4)
    db.update_pending_exit_order(conn, exiting_id, exit_order_no="000789", exit_reason="exit_target")

    pending_exits = db.get_pending_exit_positions(conn)

    assert [p["position_id"] for p in pending_exits] == [exiting_id]
    assert pending_exits[0]["exit_order_no"] == "000789"
    assert pending_exits[0]["fill_quantity"] == 5


def test_expire_position_sets_status_without_decision(conn):
    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    db.expire_position(conn, position_id, note="entry_valid_days 초과")

    row = conn.execute("SELECT status, note, decision_id FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row == ("expired", "entry_valid_days 초과", None)


def test_mark_position_error_sets_status_and_note(conn):
    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    db.mark_position_error(conn, position_id, "브로커 잔고와 불일치")

    row = conn.execute("SELECT status, note FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row == ("error", "브로커 잔고와 불일치")


def test_mark_position_error_also_closes_linked_open_decision(conn):
    """decisions.status='open'인 채로 남으면 count_open_positions()의 슬롯을 영구 점유한다
    (2026-08-13 dec_365660 실사고 — 신규 후보 선정이 11일간 안 돌았음)."""
    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    decision_id = db.confirm_position_fill(
        conn, position_id, fill_price=71000, fill_quantity=10,
        stop_price=66030, target_price=75970, max_hold_trading_days=4,
    )
    assert db.count_open_positions(conn) == 1

    db.mark_position_error(conn, position_id, "브로커 계좌와 불일치(reconcile)")

    decision_row = conn.execute("SELECT status, note FROM decisions WHERE decision_id = ?", [decision_id]).fetchone()
    assert decision_row == ("error", "브로커 계좌와 불일치(reconcile)")
    assert db.count_open_positions(conn) == 0


def test_mark_position_error_without_linked_decision_is_a_noop_on_decisions(conn):
    """pending_entry(체결 전) 포지션은 decision_id가 없다 — decisions 테이블을 건드리면 안 된다."""
    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    db.mark_position_error(conn, position_id, "브로커 잔고와 불일치")

    assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0


def test_touch_position_updates_last_price_without_status_change(conn):
    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    db.touch_position(conn, position_id, last_price=71000)

    row = conn.execute("SELECT status, last_price, last_checked_at FROM auto_positions WHERE position_id = ?", [position_id]).fetchone()
    assert row[0] == "pending_entry"
    assert row[1] == 71000
    assert row[2] is not None


def test_increment_trading_days_held_only_affects_open_positions(conn):
    open_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, open_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)
    pending_id = db.create_pending_position(conn, "000660", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")

    db.increment_trading_days_held(conn)
    db.increment_trading_days_held(conn)

    open_row = conn.execute("SELECT trading_days_held FROM auto_positions WHERE position_id = ?", [open_id]).fetchone()
    pending_row = conn.execute("SELECT trading_days_held FROM auto_positions WHERE position_id = ?", [pending_id]).fetchone()
    assert open_row[0] == 2
    assert pending_row[0] == 0


def test_decrement_pending_entry_validity_expires_at_zero(conn):
    expiring_soon = db.create_pending_position(
        conn, "005930", technique="S2", signal_date="2026-08-01",
        order_style="close_bet", entry_valid_until_trading_day=1,
    )
    still_valid = db.create_pending_position(
        conn, "000660", technique="S3", signal_date="2026-08-01",
        order_style="fixed_limit", entry_valid_until_trading_day=7,
    )
    no_tracking = db.create_pending_position(
        conn, "035420", technique="S6", signal_date="2026-08-01", order_style="daily_recompute_limit",
    )

    expired_ids = db.decrement_pending_entry_validity(conn)

    assert expired_ids == [expiring_soon]
    still_valid_row = conn.execute(
        "SELECT entry_valid_until_trading_day FROM auto_positions WHERE position_id = ?", [still_valid]
    ).fetchone()
    assert still_valid_row[0] == 6
    no_tracking_row = conn.execute(
        "SELECT entry_valid_until_trading_day FROM auto_positions WHERE position_id = ?", [no_tracking]
    ).fetchone()
    assert no_tracking_row[0] is None


def test_get_pending_positions_and_get_open_positions(conn):
    pending_id = db.create_pending_position(
        conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit", entry_target_price=68000,
    )
    open_id = db.create_pending_position(conn, "000660", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, open_id, fill_price=50000, fill_quantity=5,
                              stop_price=46500, target_price=53500, max_hold_trading_days=4)

    pending = db.get_pending_positions(conn)
    open_ = db.get_open_positions(conn)

    assert [p["position_id"] for p in pending] == [pending_id]
    assert pending[0]["target_price"] == 68000
    assert [p["position_id"] for p in open_] == [open_id]
    assert open_[0]["stop_price"] == 46500


def test_held_tickers_combines_auto_and_manual(conn):
    db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    open_id = db.create_pending_position(conn, "000660", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, open_id, fill_price=50000, fill_quantity=5,
                              stop_price=46500, target_price=53500, max_hold_trading_days=4)
    db.record_buy(conn, "035420", buy_price=200000)  # 수동매매로 보유중

    assert db.held_tickers(conn) == {"005930", "000660", "035420"}


def test_held_tickers_includes_pending_exit(conn):
    # 2026-08-21: 매도 체결 확인 전(pending_exit)에 같은 종목을 신규 진입 후보로 다시 집으면
    # 안 된다 — 아직 계좌에 그 종목을 들고 있는 상태이므로 held_tickers에 남아있어야 한다.
    exiting_id = db.create_pending_position(conn, "005930", technique="S5", signal_date="2026-08-01", order_style="fixed_limit")
    db.confirm_position_fill(conn, exiting_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)
    db.update_pending_exit_order(conn, exiting_id, exit_order_no="000789", exit_reason="exit_stop")

    assert db.held_tickers(conn) == {"005930"}


def test_count_open_positions_counts_decisions_open_status(conn):
    assert db.count_open_positions(conn) == 0

    position_id = db.create_pending_position(conn, "005930", technique="S1", signal_date="2026-08-01", order_style="fixed_limit")
    assert db.count_open_positions(conn) == 0  # pending_entry는 미체결이라 카운트 안 됨

    db.confirm_position_fill(conn, position_id, fill_price=70000, fill_quantity=10,
                              stop_price=65100, target_price=74900, max_hold_trading_days=4)
    db.record_buy(conn, "035420", buy_price=200000)  # 수동매매도 슬롯 공유

    assert db.count_open_positions(conn) == 2
