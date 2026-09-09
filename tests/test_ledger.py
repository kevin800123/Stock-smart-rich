import pytest
from datetime import date, timedelta
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot
from stocks_power_rich.ledger import record_daily_signals, update_ledger_returns
from stocks_power_rich.main import create_app
from fastapi.testclient import TestClient

def test_record_self_screen_signals_writes_forward_test_rows(tmp_path, monkeypatch):
    """自算選股的 picks 也要進 signal_ledger（source='self_screen'）才有前瞻績效可查。

    前瞻資料**不能回補**（回補就有存活者偏誤），所以晚一天接、歷史就永遠少一天——這是唯一
    「拖越久損失越大」的一項。

    **signal_date 改用市場最新交易日（market_daily），不再是 CSV 快照日。** 原本跟著
    `MAX(snap_date)` 走是為了與 filtered_picks 落在同一天好對照，但那讓它跟著 CSV 一起凍
    ——使用者已決定停止每日上傳，實測也出現過 CSV 停在 08-28 而市場資料已到 09-04。
    自算這條線零 CSV 依賴，訊號日沒有理由由上傳頻率決定。**這條測試是刻意更新的**，
    不是為了讓程式通過。

    entry_ref_price 取「signal_date 當天(或之前最近)的收盤」，**不可**拿 stock_ohlc 的最新
    收盤——用最新收盤等於進場價領先訊號日，報酬會灌水。"""
    from stocks_power_rich import ledger, selfcheck
    from stocks_power_rich.db import upsert_market_daily

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": "2026-09-04", "taiex": 20000.0})
    # CSV 停在更早的日期（或根本沒有）都不該影響訊號日——這正是要脫離的依賴
    insert_chip_snapshot(conn, "2026-08-28", [{"code": "2330.TW", "name": "台積電"}])
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
    assert rows[0]["signal_date"] == "2026-09-04"     # 市場最新交易日，不是 CSV 的 08-28
    assert rows[0]["source"] == "self_screen"
    assert rows[0]["entry_ref_price"] == 1000.0       # 訊號日收盤，不是後來的 1200

    ledger.record_self_screen_signals(conn, {"2330": {}}, 50, 9)   # 重跑不重複寫
    assert conn.execute("SELECT COUNT(*) FROM signal_ledger").fetchone()[0] == 1


def test_record_self_screen_signals_reuses_the_precomputed_payload(tmp_path, monkeypatch):
    """每日排程本來就要算一份 compute_self_screen 存快取，ledger 再算一次是純粹浪費
    （全市場約 2,000 檔的季報/月營收/集保/法人/OHLC）。帶了 precomputed 就不重算。"""
    from stocks_power_rich import ledger, selfcheck
    from stocks_power_rich.db import upsert_market_daily

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": "2026-09-04", "taiex": 20000.0})
    conn.execute("INSERT INTO stock_ohlc (date, code, close) VALUES ('2026-09-04','2330',1000.0)")
    conn.commit()

    seen = {}

    def fake_build(c, date, universe, vmin, smin, conds=None, precomputed=None):
        seen["precomputed"] = precomputed
        return {"rows": [{"code": "2330", "name": "台積電", "vals": {}}]}

    monkeypatch.setattr(selfcheck, "build_self_screen", fake_build)
    sentinel = {"date": "2026-09-04", "rows": [], "heatmap": [], "coverage": {}}
    ledger.record_self_screen_signals(conn, {"2330": {}}, 50, 9, precomputed=sentinel)
    assert seen["precomputed"] is sentinel


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
