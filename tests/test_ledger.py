import pytest
from datetime import date, timedelta
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot
from stocks_power_rich.ledger import record_daily_signals, update_ledger_returns
from stocks_power_rich.main import create_app
from fastapi.testclient import TestClient

def test_record_self_screen_signals_writes_forward_test_rows(tmp_path, monkeypatch):
    """自算選股的 picks 也要進 signal_ledger（source='self_screen'）才有前瞻績效可查。

    前瞻資料**不能回補**（回補就有存活者偏誤），所以晚一天接、歷史就永遠少一天——這是唯一
    「拖越久損失越大」的一項。與 filtered_picks 共用同一個 signal_date（最新 CSV 日），
    兩個來源才能直接對照誰比較準。

    entry_ref_price 取「signal_date 當天(或之前最近)的收盤」，**不可**拿 stock_ohlc 的最新
    收盤——signal_date 可能是較舊的 CSV 日，用最新收盤等於進場價領先訊號日，報酬會灌水。"""
    from stocks_power_rich import ledger, selfcheck

    db_file = str(tmp_path / "t.sqlite")
    conn = get_connection(db_file)
    init_db(conn)
    insert_chip_snapshot(conn, "2026-09-04", [{"code": "2330.TW", "name": "台積電"}])
    for ds, px in (("2026-09-03", 900.0), ("2026-09-04", 1000.0), ("2026-09-05", 1200.0)):
        conn.execute("INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?,?,?,?,?,?)",
                     (ds, "2330", 1.0, 1.0, 1.0, px))
    conn.commit()

    monkeypatch.setattr(selfcheck, "build_self_screen",
                        lambda *a, **k: {"rows": [{"code": "2330", "name": "台積電", "vals": {}}]})
    ledger.record_self_screen_signals(conn, {"2330": {}}, 50, 9)

    rows = conn.execute(
        "SELECT signal_date, code, source, entry_ref_price FROM signal_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0]["signal_date"] == "2026-09-04"     # 與 filtered_picks 同一天，才好對照
    assert rows[0]["source"] == "self_screen"
    assert rows[0]["entry_ref_price"] == 1000.0       # 訊號日收盤，不是後來的 1200

    ledger.record_self_screen_signals(conn, {"2330": {}}, 50, 9)   # 重跑不重複寫
    assert conn.execute("SELECT COUNT(*) FROM signal_ledger").fetchone()[0] == 1


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
