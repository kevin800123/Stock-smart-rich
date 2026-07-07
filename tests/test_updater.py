from datetime import date, timedelta

from stocks_power_rich import updater
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily


def test_run_update_collects_and_tolerates_failure(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.twse, "fetch_taiex", lambda: {"taiex": 23000.0, "taiex_chg": 50.0, "date": "2026-06-23"})
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0, "inst_trust": 2.0, "inst_dealer": 3.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 1000.0, "margin_chg": 10.0, "short_balance": 200.0, "short_chg": 5.0})
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date", lambda date=None: {
        "tx_price": 23010.0, "tx_chg": 40.0, "fut_inst_net": 600,
        "retail_ls_mtx": -0.2, "retail_ls_tmf": -0.1, "tx_foreign_oi": -76502, "retail_oi_mtx": -600,
    })

    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {"week_date": None, "data": {}})

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(updater.intl, "fetch_intl_indices", lambda tickers: boom())

    # 過舊的 ai_cache 應在每日更新時被清掉；近期的保留
    from stocks_power_rich.db import set_ai_cache
    set_ai_cache(conn, "sectors:2025-01-02", {"old": True})
    conn.execute("UPDATE ai_cache SET created_at='2025-01-02T21:00:00' WHERE cache_key='sectors:2025-01-02'")
    set_ai_cache(conn, "sectors:recent", {"new": True})
    conn.commit()

    result = updater.run_update(conn, intl_tickers={"sox": "^SOX"})
    assert "twse_taiex" in result["success"]
    assert any(f["source"] == "intl" for f in result["failed"])
    row = conn.execute("select taiex, retail_ls_mtx from market_daily").fetchone()
    assert row[0] == 23000.0 and row[1] == -0.2
    keys = {r[0] for r in conn.execute("SELECT cache_key FROM ai_cache").fetchall()}
    assert "sectors:2025-01-02" not in keys and "sectors:recent" in keys  # >120 天清除


def test_refresh_recent_corrects_inst_and_fills_margin(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    # 近期一筆：三大法人是「錯置/初值」、融資為空（白天更新所致）
    ds = (date.today() - timedelta(days=1)).isoformat()
    upsert_market_daily(conn, {"date": ds, "taiex": 100.0, "inst_foreign": -405.1})
    monkeypatch.setattr(updater.twse, "fetch_institutional",
                        lambda date=None: {"inst_foreign": -1431.89, "inst_trust": 83.95, "inst_dealer": -707.34})
    monkeypatch.setattr(updater.twse, "fetch_margin",
                        lambda date=None: {"margin_balance": 9999.0, "margin_chg": 5.0,
                                           "short_balance": 200.0, "short_chg": -1.0})
    healed = updater._refresh_recent(conn)
    assert ds in healed
    r = conn.execute("SELECT inst_foreign, margin_balance FROM market_daily WHERE date=?", (ds,)).fetchone()
    assert r[0] == -1431.89  # 三大法人被定稿值覆蓋校正
    assert r[1] == 9999.0    # 融資回補


def test_backfill_chips_fills_recent_null_futures(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    ds = (date.today() - timedelta(days=2)).isoformat()
    upsert_market_daily(conn, {"date": ds, "taiex": 100.0})  # 期貨籌碼全空
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date",
                        lambda d=None: {"retail_ls_mtx": 0.3, "retail_ls_tmf": 0.4,
                                        "tx_foreign_oi": -1000, "retail_oi_mtx": 500, "tx_price": 18000.0})
    filled = updater._backfill_chips(conn)
    assert ds in filled
    r = conn.execute("SELECT retail_ls_mtx, tx_foreign_oi FROM market_daily WHERE date=?", (ds,)).fetchone()
    assert r[0] == 0.3 and r[1] == -1000


def test_accumulate_custody_stores_new_week_then_skips(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    wk = date.today().isoformat()
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": wk,
        "data": {"2330": {"big1000_pct": 85.1, "big400_pct": 87.8, "big_holders": 1482},
                 "2317": {"big1000_pct": 50.0, "big400_pct": 55.0, "big_holders": 900}},
    })
    assert updater._accumulate_custody(conn) == wk  # 新週 → 全市場入庫
    n = conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=?", (wk,)).fetchone()[0]
    assert n == 2
    assert updater._accumulate_custody(conn) is None  # 本週已有 → 跳過


def test_backfill_ohlc_otc_floor_circuit_breaker(tmp_path, monkeypatch):
    """上櫃到官方歷史底線（一直回空）→ 連續失敗熔斷，配額讓給上市續補；
    上市達標且同輪上市有成功抓取 → otc_exhausted=True 且 done=True（不再無限重試）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    calls = {"tw": 0, "otc": 0}

    def tw_fetch(d=None):
        calls["tw"] += 1
        return {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}}

    def otc_fetch(d=None):
        calls["otc"] += 1
        return {}  # 模擬 TPEx 歷史底線：永遠抓不到

    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc", tw_fetch)
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc", otc_fetch)
    r = updater.backfill_ohlc(conn, target=20, max_fetch=120)
    assert r["twse_days"] == 20 and r["otc_days"] == 0
    assert r["otc_exhausted"] is True and r["done"] is True     # 底線＝完成，不會卡死
    assert calls["otc"] == 20                                    # 熔斷後不再浪費請求
    assert calls["tw"] == 20                                     # 配額全讓給上市


def test_backfill_ohlc_survives_multiday_holiday_gap(tmp_path, monkeypatch):
    """連續假期(如農曆春節封關 5~6 個工作日)兩市場同時休市 → 不可誤判成歷史底線卡死；
    斷路器閾值需高於假期長度，才能穿越假期繼續往更舊的日期補（回歸測試：曾在此卡死）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    # 以「距今第 N 個交易日」模擬一段連續 6 個工作日的假期（兩市場同時休市）
    holiday_start, holiday_len = 20, 6

    def make_fetch(payload):
        counter = {"n": -1}

        def fetch(d=None):
            counter["n"] += 1
            if holiday_start <= counter["n"] < holiday_start + holiday_len:
                return {}  # 假期：真的休市，兩邊都回空
            return payload
        return fetch

    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc",
                        make_fetch({"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}}))
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc",
                        make_fetch({"8069": {"open": 40.0, "high": 41.0, "low": 39.0, "close": 40.5}}))
    r = updater.backfill_ohlc(conn, target=40, max_fetch=200)
    # 假期前後都要補到，證明穿越了假期而非卡死在假期邊界
    assert r["twse_days"] == 40 and r["otc_days"] == 40
    assert r["done"] is True
