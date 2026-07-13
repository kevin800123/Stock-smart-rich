import pytest
from datetime import date, timedelta
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot
from stocks_power_rich.ledger import record_daily_signals, update_ledger_returns
from stocks_power_rich.main import create_app
from fastapi.testclient import TestClient

def test_ledger_flow_and_api(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    today_str = date.today().isoformat()
    insert_chip_snapshot(conn, today_str, [{
        "code": "2330.TW",
        "name": "台積電",
        "w55": 1.0,
        "big_holder_ratio": 2.0,
        "rev_yoy": 10.0,
        "est_profit": 5.0,
        "close": 1000.0,
        "lan_value": 80.0
    }])
    conn.commit()
    
    record_daily_signals(conn)
    
    rows = conn.execute("SELECT * FROM signal_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["code"] == "2330.TW"
    assert rows[0]["source"] == "filtered_picks"
    assert rows[0]["entry_ref_price"] == 1000.0
    assert rows[0]["ret5"] is None

    for i in range(7):
        ds = (date.today() + timedelta(days=i)).isoformat()
        close_price = 1000.0 if i < 5 else (1050.0 if i == 5 else 1060.0)
        conn.execute(
            "INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
            (ds, "2330.TW", 1000.0, 1000.0, 1000.0, close_price)
        )
    conn.commit()

    update_ledger_returns(conn)
    
    updated_rows = conn.execute("SELECT ret5, ret10, ret20 FROM signal_ledger").fetchall()
    assert len(updated_rows) == 1
    assert updated_rows[0]["ret5"] == 5.0
    assert updated_rows[0]["ret10"] is None

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/signals/performance")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["performance"]["filtered_picks"]["ret5"]["count"] == 1
    assert body["performance"]["filtered_picks"]["ret5"]["win_rate"] == 100.0
    assert body["performance"]["filtered_picks"]["ret5"]["avg_ret"] == 5.0
    assert body["performance"]["filtered_picks"]["ret10"]["count"] == 0
