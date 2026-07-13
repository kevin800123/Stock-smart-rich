import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient

from stocks_power_rich.main import create_app
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot, bulk_upsert_custody, get_setting, set_setting


def test_health_endpoint_calculation(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    # Insert mock records
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    upsert_market_daily(conn, {"date": yesterday_str, "taiex": 23000.0})
    insert_chip_snapshot(conn, yesterday_str, [{"code": "2330", "name": "台積電", "close": 1000.0}])
    conn.execute("INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                 (yesterday_str, "2330", 1000.0, 1010.0, 990.0, 1000.0))
    bulk_upsert_custody(conn, yesterday_str, {"2330": {"big1000_pct": 80.0, "big400_pct": 85.0, "big_holders": 10}})
    conn.commit()

    app = create_app()
    client = TestClient(app)

    # Test the API
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["market_daily"]["latest"] == yesterday_str
    assert body["market_daily"]["lag_days"] == 1
    assert body["chip_snapshot"]["latest"] == yesterday_str
    assert body["stock_ohlc"]["latest"] == yesterday_str
    assert body["custody_dist"]["latest_week"] == yesterday_str


def test_health_endpoint_not_ok_when_stale(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t2.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    # Insert old record
    old_date = (date.today() - timedelta(days=15)).isoformat()
    upsert_market_daily(conn, {"date": old_date, "taiex": 23000.0})
    conn.commit()

    app = create_app()
    client = TestClient(app)

    r = client.get("/api/health")
    body = r.json()
    assert body["ok"] is False  # Too old and other tables are empty


def test_alert_deduplication_logic(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t3.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    from stocks_power_rich.config import load_config
    cfg = load_config()

    sent_messages = []
    def mock_broadcast_text(token, text):
        sent_messages.append(text)
        return {"ok": True}

    from stocks_power_rich import line_push
    monkeypatch.setattr(line_push, "broadcast_text", mock_broadcast_text)

    # Import the app to get the inner function _check_update_result_and_alert
    from stocks_power_rich import updater
    
    # Run 1: failure alert
    failed_result = {
        "date": (date.today() - timedelta(days=2)).isoformat(),
        "success": ["twse_taiex"],
        "failed": [{"source": "twse", "name": "twse_inst", "error": "timeout error"}]
    }
    
    monkeypatch.setattr(updater, "run_update", lambda conn, tickers: failed_result)
    
    captured_jobs = {}
    def mock_start_scheduler(job_func, schedule_time):
        captured_jobs["scheduled_job"] = job_func
        class DummyScheduler:
            def add_job(self, func, *args, **kwargs):
                captured_jobs[kwargs.get("id", "dummy")] = func
        return DummyScheduler()

    import stocks_power_rich.scheduler
    monkeypatch.setattr(stocks_power_rich.scheduler, "start_scheduler", mock_start_scheduler)
    
    app = create_app(enable_scheduler=True)
    try:
        assert "scheduled_job" in captured_jobs
        job = captured_jobs["scheduled_job"]

        job()

        assert len(sent_messages) == 1
        assert "資料更新警告" in sent_messages[0]
        assert "twse_inst" in sent_messages[0]

        job()
        assert len(sent_messages) == 1  # Still 1!

        different_failed_result = {
            "date": (date.today() - timedelta(days=2)).isoformat(),
            "success": ["twse_taiex"],
            "failed": [{"source": "taifex", "name": "taifex_chips", "error": "connection failed"}]
        }
        monkeypatch.setattr(updater, "run_update", lambda conn, tickers: different_failed_result)

        job()
        assert len(sent_messages) == 2  # Sent again!
    finally:
        if getattr(app.state, "scheduler", None):
            app.state.scheduler.shutdown(wait=False)
