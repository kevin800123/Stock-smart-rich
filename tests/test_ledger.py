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

    # 貼近真實資料：stock_ohlc 的代號**不帶後綴**（官方日線就是 "2330"），交易日曆來自
    # market_daily。這條測試原本把日線寫成 "2330.TW" 才通過，正好把「CSV 訊號帶後綴、日線
    # 不帶」這個真 bug 蓋住——production 上 7,576 筆 filtered_picks 因此一筆報酬都沒回填。
    for i in range(7):
        ds = (date.today() + timedelta(days=i)).isoformat()
        close_price = 1000.0 if i < 5 else (1050.0 if i == 5 else 1060.0)
        upsert_market_daily(conn, {"date": ds, "taiex": 20000.0})
        conn.execute(
            "INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
            (ds, "2330", 1000.0, 1000.0, 1000.0, close_price)
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


def test_signals_performance_reports_every_ledger_source(tmp_path, monkeypatch):
    """三個來源都要出現在績效讀出口，**self_screen 不可漏**。

    它漏掉時的症狀是完全無聲的：`record_self_screen_signals` 照常每個交易日寫一筆、
    `update_ledger_returns` 不分來源照常回填報酬，只有這個唯一的讀出口看不到它——
    於是「自算到底有沒有比 CSV 那套準」永遠拿不出證據，而前瞻報酬**不能事後回補**。

    同時鎖住「有訊號但還沒到期」與「根本沒在記」要分得出來：剛接上的來源必然三個
    橫幅都 count=0，只看 count 會把正常的等待期誤讀成故障（同全站「查無資料 vs
    抓取失敗」的分法）。
    """
    db_file = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)
    # self_screen：已成熟（有 ret5）；cup_handle：有訊號但未到期；filtered_picks：完全沒記
    conn.execute("INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price, ret5)"
                 " VALUES (?,?,?,?,?,?)", ("2026-09-09", "2330", "台積電", "self_screen", 1000.0, 5.0))
    conn.execute("INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price)"
                 " VALUES (?,?,?,?,?)", ("2026-09-10", "2317", "鴻海", "cup_handle", 200.0))
    conn.commit()

    body = TestClient(create_app()).get("/api/signals/performance").json()
    perf = body["performance"]

    # 舊版把來源寫死成 ("filtered_picks", "cup_handle")，這一行就是會紅的那一行
    assert "self_screen" in perf
    assert perf["self_screen"]["ret5"] == {"win_rate": 100.0, "avg_ret": 5.0, "count": 1}
    assert perf["self_screen"]["signals"] == 1
    assert perf["self_screen"]["since"] == "2026-09-09"

    # 「等待到期」與「沒在記」不可混為一談：兩者 count 都是 0，要靠 signals 分辨
    assert perf["cup_handle"]["ret5"]["count"] == 0 and perf["cup_handle"]["signals"] == 1
    assert perf["filtered_picks"]["ret5"]["count"] == 0 and perf["filtered_picks"]["signals"] == 0
    assert perf["cup_handle"]["since"] == "2026-09-10"
    assert perf["filtered_picks"]["since"] is None

    # 樣本不足的門檻由後端給，前端不得自己寫死一份（同 bands/Elliott 的規矩）
    assert body["min_sample"] > 0
    assert body["sources"] == ["filtered_picks", "self_screen", "cup_handle"]


def test_record_self_screen_signals_uses_the_given_signal_date(tmp_path, monkeypatch):
    """提早計算時 market_daily 可能還沒有今天那一列（它是 21:00 的每日更新才建的）。

    這時若照舊用 market_daily 最新日期當訊號日，**今天的名單會被記在昨天、進場價用昨天收盤**
    ——那是拿未來資訊回填過去，前瞻報酬會被灌水，而且補不回來。所以呼叫端要能明確指定。"""
    from stocks_power_rich import ledger, selfcheck
    from stocks_power_rich.db import upsert_market_daily

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": "2026-09-30", "taiex": 20000.0})     # 今天那列還沒建
    for ds, px in (("2026-09-30", 900.0), ("2026-10-01", 1000.0)):
        conn.execute("INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?,?,?,?,?,?)",
                     (ds, "2330", 1.0, 1.0, 1.0, px))
    conn.commit()
    monkeypatch.setattr(selfcheck, "build_self_screen",
                        lambda *a, **k: {"rows": [{"code": "2330", "name": "台積電", "vals": {}}]})

    ledger.record_self_screen_signals(conn, {"2330": {}}, 50, 9, signal_date="2026-10-01")
    rows = conn.execute("SELECT signal_date, entry_ref_price FROM signal_ledger").fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-01", 1000.0)]


def _ledger_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)
    return conn


def _put_close(conn, ds, code, close):
    conn.execute("INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?,?,?,?,?,?)",
                 (ds, code, close, close, close, close))


def test_update_ledger_returns_matches_suffixed_codes_to_bare_ohlc_codes(tmp_path, monkeypatch):
    """CSV 匯入的 filtered_picks 代號帶 .TW／.TWO，stock_ohlc 的官方日線代號不帶後綴。
    舊寫法直接 code=? 比對，一筆都對不到，production 上 7,576 筆訊號的報酬因此永遠是空的。"""
    conn = _ledger_db(tmp_path, monkeypatch)
    days = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"]
    for ds in days:
        upsert_market_daily(conn, {"date": ds, "taiex": 20000.0})
    ins = "INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price) VALUES (?,?,?,?,?)"
    conn.execute(ins, ("2026-08-03", "2330.TW", "台積電", "filtered_picks", 100.0))
    conn.execute(ins, ("2026-08-03", "6488.TWO", "環球晶", "filtered_picks", 200.0))
    for ds in days:
        _put_close(conn, ds, "2330", 110.0)
        _put_close(conn, ds, "6488", 180.0)
    conn.commit()

    update_ledger_returns(conn)
    got = {r["code"]: r["ret5"] for r in conn.execute("SELECT code, ret5 FROM signal_ledger")}
    assert got == {"2330.TW": 10.0, "6488.TWO": -10.0}


def test_update_ledger_returns_counts_trading_days_from_the_market_calendar(tmp_path, monkeypatch):
    """「5 日報酬」＝訊號日之後第 5 個**交易日**的收盤，交易日曆取 market_daily（有加權指數的日子）。
    舊寫法數的是「該檔在 stock_ohlc 裡的第 5 筆」——日線缺一天就安靜地量成第 6、7 天。
    那一天剛好缺收盤就先留空，之後補到資料再算，不拿別天的價格頂替。"""
    conn = _ledger_db(tmp_path, monkeypatch)
    cal = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
           "2026-08-10", "2026-08-11"]
    for ds in cal:
        upsert_market_daily(conn, {"date": ds, "taiex": 20000.0})
    upsert_market_daily(conn, {"date": "2026-08-12"})          # 當天早上的列：還沒有指數，不算交易日
    ins = "INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price) VALUES (?,?,?,?,?)"
    conn.execute(ins, ("2026-08-03", "2330", "x", "cup_handle", 100.0))   # 第 5 個交易日＝08-10
    conn.execute(ins, ("2026-08-04", "2317", "y", "self_screen", 100.0))  # 第 5 個交易日＝08-11，那天缺價
    closes = {"2026-08-03": 100, "2026-08-04": 101, "2026-08-06": 103,    # 2330 缺 08-05
              "2026-08-07": 104, "2026-08-10": 120, "2026-08-11": 130}
    for ds, px in closes.items():
        _put_close(conn, ds, "2330", float(px))
    for ds in ["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10"]:
        _put_close(conn, ds, "2317", 150.0)                    # 2317 缺 08-11
    conn.commit()

    update_ledger_returns(conn)
    got = {r["code"]: r["ret5"] for r in conn.execute("SELECT code, ret5 FROM signal_ledger")}
    assert got["2330"] == 20.0      # 08-10 的 120，不是缺一天後錯位的 08-11（130）
    assert got["2317"] is None      # 第 5 個交易日缺收盤：留空，不拿 08-10 頂替

    _put_close(conn, "2026-08-11", "2317", 110.0)             # 之後補到資料
    conn.commit()
    update_ledger_returns(conn)
    assert conn.execute("SELECT ret5 FROM signal_ledger WHERE code='2317'").fetchone()[0] == 10.0
