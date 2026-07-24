import threading
import time
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

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(updater.intl, "fetch_intl_history", boom)

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


def test_backfill_ohlc_progress_persists_across_separate_calls(tmp_path, monkeypatch):
    """回歸測試：曾發生「單次呼叫時間預算不足以撐到熔斷門檻」時，游標/失敗計數若不持久化，
    每次獨立呼叫都從今天重新掃、在同一批日期打轉，連續多次呼叫進度永遠掛零。

    模擬：每次呼叫只給極小 max_fetch（如同官方伺服器慢、單次呼叫只夠試幾天），
    連續呼叫 15 次，驗證天數單調不減、最終達標或觸發熔斷（而非停在同一數字不動）。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc",
                        lambda d=None: {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}})
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc", lambda d=None: {})  # 上櫃永遠失敗（模擬底線）

    progress = []
    r = None
    for _ in range(15):
        r = updater.backfill_ohlc(conn, target=30, max_fetch=3)
        progress.append(r["twse_days"])
    assert progress == sorted(progress) and progress[-1] > progress[0]  # 持續前進，非卡死
    assert r["twse_days"] >= 30                    # 上市最終達標
    assert r["otc_exhausted"] is True               # 上櫃失敗次數跨呼叫累積，終究觸發熔斷
    assert r["done"] is True


def test_backfill_ohlc_hard_deadline_abandons_hung_fetch(tmp_path, monkeypatch):
    """回歸（2026-07-07 事故）：單一對外請求超過來源自身 httpx timeout 仍掛死
    （DNS/TLS 等階段不受 httpx timeout 涵蓋）→ 回補鎖不釋放、整個服務卡死需人工重啟。
    加硬性截止後：逾時視同該日抓不到（計入該市場失敗），另一市場照常補、不再卡死。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater, "_FETCH_DEADLINE", 0.05)

    def hung(d=None):
        threading.Event().wait(2)   # 模擬掛死（不受上面 time.sleep 打樁影響）
        return {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}}

    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc", hung)
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc",
                        lambda d=None: {"8069": {"open": 40.0, "high": 41.0, "low": 39.0, "close": 40.5}})
    start = time.monotonic()
    r = updater.backfill_ohlc(conn, target=3, max_fetch=8)
    assert time.monotonic() - start < 5                    # 不會傻等掛死的請求
    assert r["otc_days"] == 3 and r["twse_days"] == 0      # 掛死市場視同失敗、另一市場照補


def test_reset_ohlc_progress_clears_state_and_unsticks(tmp_path, monkeypatch):
    """回歸情境：兩市場都被判定熔斷（真假難辨）後，reset 應清掉游標/失敗計數/熔斷旗標，
    讓下次呼叫重新給機會判定——且不影響已經存好的 OHLC 資料本身。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc", lambda d=None: {})   # 先讓兩邊都熔斷
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc", lambda d=None: {})
    r = updater.backfill_ohlc(conn, target=10, max_fetch=100)
    assert r["twse_exhausted"] is True and r["otc_exhausted"] is True and r["added"] == 20

    # 熔斷後再打一次：兩邊都已標記，理應完全不再嘗試任何日期（added 應為 0）
    r_stuck = updater.backfill_ohlc(conn, target=10, max_fetch=100)
    assert r_stuck["added"] == 0 and r_stuck["twse_days"] == r["twse_days"]

    # 重置後改回會成功的來源，應該能重新前進（不受舊熔斷旗標卡住）
    updater.reset_ohlc_progress(conn)
    monkeypatch.setattr(updater.twse, "fetch_stock_ohlc",
                        lambda d=None: {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}})
    monkeypatch.setattr(updater.tpex, "fetch_otc_ohlc",
                        lambda d=None: {"8069": {"open": 40.0, "high": 41.0, "low": 39.0, "close": 40.5}})
    r2 = updater.backfill_ohlc(conn, target=10, max_fetch=100)
    assert r2["twse_days"] == 10 and r2["otc_days"] == 10
    assert r2["twse_exhausted"] is False and r2["otc_exhausted"] is False and r2["done"] is True


def test_heal_margin_maintenance_fills_days_that_had_no_margin_value_yet(tmp_path, monkeypatch):
    """維持率的自癒：margin_value 由 _refresh_recent 事後補上，維持率必須跟著補算。

    原本維持率只在當次 run 算一次，21:00 前跑的那些 run 因 margin_value 未公布而整段
    跳過，之後再也不會重算——依賴補好了、被依賴的沒補，導致 45 天只有 7 天有值。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    today = date.today()
    d1, d2 = (today - timedelta(days=i) for i in (2, 1))
    upsert_market_daily(conn, {"date": d1.isoformat(), "margin_value": 5800.0})   # 待補
    upsert_market_daily(conn, {"date": d2.isoformat(), "taiex": 23000.0})          # 無 margin_value

    monkeypatch.setattr(updater, "_compute_margin_maintenance",
                        lambda D, mv: {"margin_maintenance": 175.5, "margin_mv": 100.0,
                                       "short_mv": 2.0})
    monkeypatch.setattr(updater, "_compute_otc_margin_maintenance", lambda D: {})
    filled = updater._heal_margin_maintenance(conn, days=7)

    assert d1.isoformat() in filled
    got = {r[0]: r for r in conn.execute(
        "SELECT date, margin_maintenance, margin_mv FROM market_daily ORDER BY date")}
    assert got[d1.isoformat()][1] == 175.5 and got[d1.isoformat()][2] == 100.0  # 補上比率與分子
    assert got[d2.isoformat()][1] is None      # 沒有 margin_value 就不硬算


def test_heal_computes_otc_independently_of_tse(tmp_path, monkeypatch):
    """上櫃走櫃買自己的端點（餘額與融資金額同一支），不該被上市那邊的缺料卡住。

    兩個市場的融資成數不同（60% vs 50%），損益兩平線 166.7% vs 200%，本來就要分開判讀；
    若上櫃跟著上市一起失敗，等於少掉一個獨立訊號。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    ds = (date.today() - timedelta(days=1)).isoformat()
    upsert_market_daily(conn, {"date": ds, "taiex": 23000.0})   # 刻意沒有 margin_value

    monkeypatch.setattr(updater, "_compute_otc_margin_maintenance", lambda D: {
        "otc_margin_maintenance": 166.8, "otc_margin_mv": 3203.7, "otc_short_mv": 45.9,
        "otc_margin_value": 1927.5, "otc_margin_balance": 2365064, "otc_short_balance": 29937})
    filled = updater._heal_margin_maintenance(conn, days=7)

    assert filled == [ds]
    r = conn.execute("SELECT otc_margin_maintenance, otc_margin_value, margin_maintenance "
                     "FROM market_daily WHERE date=?", (ds,)).fetchone()
    assert r[0] == 166.8 and r[1] == 1927.5
    assert r[2] is None            # 上市仍留空，兩邊互不牽連


def test_backfill_intl_fills_only_nulls_and_respects_session_availability(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    today = date.today()
    d_prev, d = today - timedelta(days=1), today
    upsert_market_daily(conn, {"date": d_prev.isoformat(), "taiex": 23000.0})
    upsert_market_daily(conn, {"date": d.isoformat(), "sox": 9999.0})   # 既有值：不得覆寫

    tickers = {"sox": "^SOX", "n225": "^N225"}
    monkeypatch.setattr(updater.intl, "fetch_intl_history", lambda t, days=0: {
        "sox": {d_prev.isoformat(): {"value": 100.0, "chg_pct": 1.0},
                d.isoformat(): {"value": 200.0, "chg_pct": 2.0}},
        "n225": {d_prev.isoformat(): {"value": 300.0, "chg_pct": 3.0},
                 d.isoformat(): {"value": 400.0, "chg_pct": 4.0}},
    })

    filled = updater._backfill_intl(conn, tickers, days=7)

    assert filled == [d_prev.isoformat(), d.isoformat()]
    rows = {r[0]: r for r in conn.execute(
        "SELECT date, sox, sox_chg, n225 FROM market_daily ORDER BY date").fetchall()}
    # 美盤：台北 D 日晚間時 D 當日尚未開盤 → 取 D 之前那一場
    assert rows[d.isoformat()][1] == 9999.0          # 既有值原封不動
    assert rows[d_prev.isoformat()][1] is None       # d_prev 之前沒有場次 → 不硬湊
    # 亞股：D 當日已收盤 → 直接取 D
    assert rows[d.isoformat()][3] == 400.0
    assert rows[d_prev.isoformat()][3] == 300.0


def test_run_update_writes_session_aligned_intl_not_live_snapshot(tmp_path, monkeypatch):
    """每日更新的國際指數必須走場次規則，而不是「跑的當下」的報價。

    舊做法寫入 fetch_intl_indices 的即時值，導致同一個 sox 數字被寫進相鄰兩天
    （2026-07-20 與 07-21 都是 11743.85）——把別場的價格貼上資料日 D 的標籤。
    因 _backfill_intl 只填 NULL 不覆蓋，寫錯的值永遠不會被修正，故寧可留 NULL。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    D = date.today()
    prev_session = (D - timedelta(days=1)).isoformat()

    monkeypatch.setattr(updater.twse, "fetch_taiex",
                        lambda: {"taiex": 23000.0, "taiex_chg": 50.0, "date": D.isoformat()})
    for name in ("fetch_institutional", "fetch_margin"):
        monkeypatch.setattr(updater.twse, name, lambda date=None: {})
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date", lambda date=None: {})
    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution",
                        lambda: {"week_date": None, "data": {}})
    # sox 有「D 之前那一場」；n225 只有 D 之前，沒有 D 當天（亞股尚未收盤）
    monkeypatch.setattr(updater.intl, "fetch_intl_history", lambda t, days=0: {
        "sox": {prev_session: {"value": 100.0, "chg_pct": 1.0}},
        "n225": {prev_session: {"value": 300.0, "chg_pct": 3.0}},
    })

    result = updater.run_update(conn, intl_tickers={"sox": "^SOX", "n225": "^N225"})

    r = conn.execute("SELECT sox, sox_chg, n225 FROM market_daily WHERE date=?",
                     (D.isoformat(),)).fetchone()
    assert r[0] == 100.0 and r[1] == 1.0   # 美盤：D 當晚可得的是 D 之前那一場
    assert r[2] is None                    # 亞股當日還沒收 → 留 NULL，不拿別場頂替
    assert "intl" in result["success"]
