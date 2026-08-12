import threading
import time
from datetime import date, timedelta

from stocks_power_rich import updater
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily


def test_run_update_collects_and_tolerates_failure(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.stock_flow, "update_day", lambda conn, D: {
        "TWSE": {"quotes": {}, "margin": {}}, "TPEx": {"quotes": {}, "margin": {}}})
    monkeypatch.setattr(updater.twse, "fetch_taiex", lambda: {"taiex": 23000.0, "taiex_chg": 50.0, "date": "2026-06-23"})
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0, "inst_trust": 2.0, "inst_dealer": 3.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 1000.0, "margin_chg": 10.0, "short_balance": 200.0, "short_chg": 5.0})
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date", lambda date=None: {
        "tx_price": 23010.0, "tx_chg": 40.0, "fut_inst_net": 600,
        "retail_ls_mtx": -0.2, "retail_ls_tmf": -0.1, "tx_foreign_oi": -76502, "retail_oi_mtx": -600,
    })

    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {"week_date": None, "data": {}})
    monkeypatch.setattr(updater.revenue, "fetch_twse_revenue", lambda: {})
    monkeypatch.setattr(updater.revenue, "fetch_otc_revenue", lambda: {})

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


def _stub_taiex_month(monkeypatch, per_month=20):
    """fetch_taiex_history(anchor) → 該錨點所在月份的每日指數（每月 per_month 個交易日）。"""
    def fake(anchor=None):
        a = anchor or date.today()
        out = []
        for d in range(1, per_month + 1):
            try:
                iso = a.replace(day=d).isoformat()
            except ValueError:
                break
            out.append({"date": iso, "taiex": 100.0 + d, "taiex_chg": 1.0, "turnover": 5000.0})
        return out
    monkeypatch.setattr(updater.twse, "fetch_taiex_history", fake)


def test_backfill_history_anchors_derive_from_days_not_hardcoded_three_months(tmp_path, monkeypatch):
    """月度錨點數必須由 days 推得。

    原本錨點迴圈寫死 `for _ in range(3)`，days 只當過濾條件用——所以 days=180 實際上
    只補到 3 個月前，且因為端點還把 days 夾在 60，傳 180 等於什麼都沒多補（實測
    backfilled_days=41 卻一列都沒新增）。這條測試鎖住「窗口真的會跟著 days 變寬」。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _stub_taiex_month(monkeypatch)
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 9.0})

    wide = updater.backfill_history(conn, days=150, cap=500)
    cutoff = (date.today() - timedelta(days=150)).isoformat()
    rows = [r[0] for r in conn.execute(
        "SELECT date FROM market_daily ORDER BY date").fetchall()]
    assert rows, "應建出歷史列"
    # 最舊一列必須早於「3 個月前」，證明錨點不再寫死 3 個月
    three_months_ago = (date.today() - timedelta(days=95)).isoformat()
    assert rows[0] < three_months_ago, f"最舊列 {rows[0]} 未超過 3 個月，錨點仍被寫死"
    assert all(r >= cutoff for r in rows), "不得補到 cutoff 之外"
    assert wide["backfilled_days"] > 0 and wide["remaining"] == 0


def test_backfill_history_caps_per_call_and_reports_remaining(tmp_path, monkeypatch):
    """每天要打兩支 TWSE 請求（法人＋融資券），所以每次呼叫只處理 cap 天並回報 remaining，
    比照 chips/margin 回補的慣例（重複呼叫直到 remaining 為 0），不要一次打幾百個請求。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _stub_taiex_month(monkeypatch)
    calls = {"n": 0}

    def inst(date=None):
        calls["n"] += 1
        return {"inst_foreign": 1.0}
    monkeypatch.setattr(updater.twse, "fetch_institutional", inst)
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 9.0})

    first = updater.backfill_history(conn, days=90, cap=5)
    assert first["backfilled_days"] == 5
    assert first["remaining"] > 0
    assert calls["n"] == 5, "只該對 cap 天發請求"

    # 第二次呼叫要接著補，不從頭重打已完成的日期
    before = calls["n"]
    second = updater.backfill_history(conn, days=90, cap=5)
    assert second["backfilled_days"] == 5
    assert second["remaining"] == first["remaining"] - 5
    assert calls["n"] - before == 5, "已完成的日期不該重打請求"


def test_backfill_history_fills_index_without_extra_requests(tmp_path, monkeypatch):
    """指數/成交金額來自月度批次抓取，不花逐日請求——即使 cap 用完，當窗口內每一列都該
    建好並帶有 taiex（否則對照圖的 K 線窗格會缺列）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _stub_taiex_month(monkeypatch)
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 9.0})

    res = updater.backfill_history(conn, days=90, cap=1)
    assert res["backfilled_days"] == 1 and res["remaining"] > 0
    n_rows, n_taiex, n_inst = conn.execute(
        "SELECT COUNT(*), COUNT(taiex), COUNT(inst_foreign) FROM market_daily").fetchone()
    assert n_rows == n_taiex > 1, "窗口內每一列都要建好並帶指數"
    assert n_inst == 1, "法人只補了 cap 天"


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


def test_refresh_monthly_revenue_stores_both_markets(tmp_path, monkeypatch):
    """兩個市場各自失敗互不影響（同 stock_source_coverage 的既有精神），且回傳兩市場共入了幾檔。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.revenue, "fetch_twse_revenue", lambda: {
        "2330": {"name": "台積電", "industry": "半導體業", "year_month": "2026-07",
                 "report_date": "2026-08-11", "revenue": 100.0, "revenue_prev_month": 90.0,
                 "revenue_last_year": 80.0, "mom_pct": 11.1, "yoy_pct": 25.0,
                 "revenue_accum": 500.0, "revenue_accum_last_year": 400.0,
                 "accum_yoy_pct": 25.0, "note": None},
    })
    monkeypatch.setattr(updater.revenue, "fetch_otc_revenue", lambda: {
        "1240": {"name": "茂生農經", "industry": "農業科技", "year_month": "2026-07",
                 "report_date": "2026-08-11", "revenue": 10.0, "revenue_prev_month": 9.0,
                 "revenue_last_year": 8.0, "mom_pct": 11.1, "yoy_pct": 13.2,
                 "revenue_accum": 50.0, "revenue_accum_last_year": 40.0,
                 "accum_yoy_pct": 13.2, "note": None},
    })
    counts = updater._refresh_monthly_revenue(conn)
    assert counts == {"TWSE": 1, "TPEx": 1}
    twse_row = conn.execute(
        "SELECT market, yoy_pct FROM stock_revenue_monthly WHERE code='2330'").fetchone()
    assert tuple(twse_row) == ("TWSE", 25.0)
    otc_row = conn.execute(
        "SELECT market, yoy_pct FROM stock_revenue_monthly WHERE code='1240'").fetchone()
    assert tuple(otc_row) == ("TPEx", 13.2)


def test_refresh_monthly_revenue_one_market_failing_does_not_block_other(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(updater.revenue, "fetch_twse_revenue", boom)
    monkeypatch.setattr(updater.revenue, "fetch_otc_revenue", lambda: {
        "1240": {"name": "茂生農經", "industry": "農業科技", "year_month": "2026-07",
                 "report_date": "2026-08-11", "revenue": 10.0, "revenue_prev_month": 9.0,
                 "revenue_last_year": 8.0, "mom_pct": 11.1, "yoy_pct": 13.2,
                 "revenue_accum": 50.0, "revenue_accum_last_year": 40.0,
                 "accum_yoy_pct": 13.2, "note": None},
    })
    counts = updater._refresh_monthly_revenue(conn)
    assert counts == {"TWSE": 0, "TPEx": 1}


def test_backfill_financials_batches_pending_codes_and_is_resumable(tmp_path, monkeypatch):
    """全市場逐檔季報回補：以月營收表的代號為母體，一次處理一批、可續傳（同 ohlc/inst 回補）。
    每批對每個 RATIO_ITEMS 指標各打一次 mopsfin，已抓過的代號跳過。"""
    from stocks_power_rich.db import bulk_upsert_revenue, get_financial_series
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    # 母體＝月營收表的代號（bare code，與 mopsfin 一致）
    bulk_upsert_revenue(conn, "TWSE", {
        c: {"year_month": "2026-07", "report_date": "2026-08-11", "yoy_pct": 1.0}
        for c in ["2330", "2317", "2454"]
    })

    calls = []

    def fake_fetch(codes, indicator):
        calls.append((tuple(codes), indicator))
        # 回每個 code 一季假值，值用指標長度區分以便斷言
        return {c: {"2026Q1": float(len(indicator))} for c in codes}

    monkeypatch.setattr(updater.financials, "fetch_financial_ratio", fake_fetch)

    # batch_size=2 → 第一次只處理 2 檔（2330,2317），剩 2454
    res = updater.backfill_financials(conn, max_batches=1, batch_size=2)
    assert res["filled"] == 2 and res["remaining"] == 1
    # 每個指標各打一次（一批 8 指標）
    assert len({ind for _codes, ind in calls}) == len(updater.financials.RATIO_ITEMS)
    assert get_financial_series(conn, "2330", "roe") == [("2026Q1", 3.0)]  # len("roe")=3

    # 再呼叫一次 → 補完 2454
    res2 = updater.backfill_financials(conn, max_batches=1, batch_size=2)
    assert res2["filled"] == 1 and res2["remaining"] == 0
    assert get_financial_series(conn, "2454", "debt_ratio") == [("2026Q1", 10.0)]  # len=10

    # 全補完後再呼叫 → 無 pending、不再打 mopsfin
    calls.clear()
    res3 = updater.backfill_financials(conn, max_batches=5, batch_size=50)
    assert res3["remaining"] == 0 and res3["filled"] == 0 and calls == []


def test_backfill_financials_retries_codes_missing_some_indicators(tmp_path, monkeypatch):
    """半 populated 不能算完成：某指標暫時性回空 → 該代號缺一個指標 → 下次呼叫要再打它，
    把缺的補上（避免『有任一列就跳過』把暫時失敗永久化，同 codebase 一貫的教訓）。"""
    from stocks_power_rich.db import bulk_upsert_revenue, get_financial_series
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": "2026-07", "report_date": "2026-08-11", "yoy_pct": 1.0}})

    fail_revenue = {"on": True}

    def flaky(codes, indicator):
        if indicator == "revenue" and fail_revenue["on"]:
            return {}  # 第一輪 revenue 暫時性失敗
        return {c: {"2026Q1": 1.0} for c in codes}

    monkeypatch.setattr(updater.financials, "fetch_financial_ratio", flaky)

    res1 = updater.backfill_financials(conn, max_batches=1, batch_size=50)
    assert res1["remaining"] == 1  # 2330 缺 revenue → 仍算 pending
    assert get_financial_series(conn, "2330", "revenue") == []
    assert get_financial_series(conn, "2330", "roe") == [("2026Q1", 1.0)]

    fail_revenue["on"] = False  # 第二輪 revenue 恢復
    res2 = updater.backfill_financials(conn, max_batches=1, batch_size=50)
    assert res2["remaining"] == 0
    assert get_financial_series(conn, "2330", "revenue") == [("2026Q1", 1.0)]


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


def test_backfill_intl_empty_tickers_returns_empty_instead_of_sql_error(tmp_path, monkeypatch):
    """intl_tickers 濾完（呼叫端排除掉已頂替的 key 之後）若剛好空了，cols 會是空字串，
    讓 f"SELECT date, {cols} FROM ..." 變成語法錯誤——run_update 傳入更窄的 ticker
    集合時就踩過這個洞（被外層 try/except 吞掉，intl 整段悄悄失敗）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(), "taiex": 23000.0})
    called = []
    monkeypatch.setattr(updater.intl, "fetch_intl_history", lambda *a, **k: called.append(1) or {})
    assert updater._backfill_intl(conn, {}, days=7) == []
    assert not called


def test_backfill_intl_fred_fills_only_nulls_and_respects_session_availability(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    today = date.today()
    d_prev, d = today - timedelta(days=1), today
    upsert_market_daily(conn, {"date": d_prev.isoformat(), "taiex": 23000.0})
    upsert_market_daily(conn, {"date": d.isoformat(), "vix": 15.0})   # 既有值：不得覆寫

    def fake_fetch(series_id, start_date):
        if series_id == "VIXCLS":
            return {d_prev.isoformat(): 18.0, d.isoformat(): 19.0}
        if series_id == "NIKKEI225":
            return {d_prev.isoformat(): 30000.0, d.isoformat(): 31000.0}
        return {}

    monkeypatch.setattr(updater.fred, "fetch_fred_series", fake_fetch)

    filled = updater._backfill_intl_fred(conn, days=7)

    assert filled == [d_prev.isoformat(), d.isoformat()]
    rows = {r[0]: r for r in conn.execute(
        "SELECT date, vix, n225 FROM market_daily ORDER BY date").fetchall()}
    assert rows[d.isoformat()][1] == 15.0        # vix 既有值原封不動
    # vix 非 same_day：要找「d_prev 之前」那一場，測試資料裡沒有更早的一場 → 不硬湊
    assert rows[d_prev.isoformat()][1] is None
    # n225 是 same_day：D 當日已收盤 → 直接取 D
    assert rows[d.isoformat()][2] == 31000.0
    assert rows[d_prev.isoformat()][2] == 30000.0


def test_backfill_intl_fred_no_holes_skips_fetch(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(), "vix": 15.0, "n225": 30000.0})
    called = []
    monkeypatch.setattr(updater.fred, "fetch_fred_series",
                        lambda *a, **k: called.append(1) or {})
    assert updater._backfill_intl_fred(conn, days=7) == []
    assert not called


def test_backfill_intl_nasdaq_fills_only_nulls_and_takes_prior_session(tmp_path, monkeypatch):
    """sox 不是 same_day（美股）：要找「D 之前」最近一場，且既有值不得覆寫。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    today = date.today()
    d_prev2, d_prev, d = today - timedelta(days=2), today - timedelta(days=1), today
    upsert_market_daily(conn, {"date": d_prev2.isoformat(), "taiex": 23000.0})
    upsert_market_daily(conn, {"date": d_prev.isoformat(), "sox": 5000.0})   # 既有值：不得覆寫
    upsert_market_daily(conn, {"date": d.isoformat(), "taiex": 23100.0})

    monkeypatch.setattr(updater.nasdaq, "fetch_sox_history", lambda days: {
        d_prev2.isoformat(): 4800.0, d_prev.isoformat(): 4900.0,
    })

    filled = updater._backfill_intl_nasdaq(conn, days=7)

    assert filled == [d.isoformat()]
    rows = {r[0]: r[1] for r in conn.execute("SELECT date, sox FROM market_daily ORDER BY date").fetchall()}
    assert rows[d_prev.isoformat()] == 5000.0                # 既有值原封不動
    # d_prev2 沒有更早的一場 → 不硬湊，留 None
    assert rows[d_prev2.isoformat()] is None
    # d 取「D 之前」最近一場 = d_prev 的收盤（4900.0）
    assert rows[d.isoformat()] == 4900.0


def test_backfill_intl_nasdaq_no_holes_skips_fetch(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(), "sox": 5000.0})
    called = []
    monkeypatch.setattr(updater.nasdaq, "fetch_sox_history",
                        lambda days: called.append(1) or {})
    assert updater._backfill_intl_nasdaq(conn, days=7) == []
    assert not called


def test_backfill_intl_nasdaq_fetch_failure_leaves_holes_unfilled(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(), "taiex": 23000.0})
    monkeypatch.setattr(updater.nasdaq, "fetch_sox_history", lambda days: {})
    assert updater._backfill_intl_nasdaq(conn, days=7) == []


def test_backfill_intl_tv_same_day_key_only_fills_its_own_date(tmp_path, monkeypatch):
    """n225/kospi 的定義就是「該市場 D 當天的收盤」→ 只填 D == 快照場次日。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    d = date.today()
    upsert_market_daily(conn, {"date": d.isoformat(), "taiex": 23000.0})   # kospi 缺 → 是個洞

    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: {"kospi": {"date": d.isoformat(),
                                                     "value": 6690.63, "chg_pct": -5.72}})

    assert updater._backfill_intl_tv(conn, days=7) == [d.isoformat()]
    row = conn.execute("SELECT kospi, kospi_chg FROM market_daily WHERE date=?",
                       (d.isoformat(),)).fetchone()
    assert tuple(row) == (6690.63, -5.72)


def test_backfill_intl_tv_same_day_key_no_match_writes_nothing(tmp_path, monkeypatch):
    """快照解出的日期跟任何一個洞都對不上（南韓休市、或洞早補過了）→ 不硬猜、不寫入。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    d = date.today()
    upsert_market_daily(conn, {"date": d.isoformat(), "taiex": 23000.0})

    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: {"kospi": {"date": (d - timedelta(days=5)).isoformat(),
                                                     "value": 1.0, "chg_pct": 1.0}})

    assert updater._backfill_intl_tv(conn, days=7) == []
    row = conn.execute("SELECT kospi FROM market_daily WHERE date=?", (d.isoformat(),)).fetchone()
    assert row[0] is None


def test_backfill_intl_tv_lagging_key_fills_rows_after_the_session_only(tmp_path, monkeypatch):
    """sox/vix 是「台北 D 日晚間可得的最近一場」→ 填所有 D > S 的洞，D == S 的不填。

    D == S 那格要的是 S 的**前**一場，快照給不了；填下去就是把 S 自己的收盤
    貼上 S 那天的標籤（早了一場），而且「只填 NULL 不覆蓋」會讓它永遠留著。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    d = date.today()
    s = (d - timedelta(days=1)).isoformat()
    for ds in (s, d.isoformat()):
        upsert_market_daily(conn, {"date": ds, "taiex": 23000.0})

    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: {"sox": {"date": s, "value": 12179.26, "chg_pct": 6.55}})

    assert updater._backfill_intl_tv(conn, days=7) == [d.isoformat()]
    assert conn.execute("SELECT sox FROM market_daily WHERE date=?", (s,)).fetchone()[0] is None
    assert conn.execute("SELECT sox, sox_chg FROM market_daily WHERE date=?",
                        (d.isoformat(),)).fetchone()[0] == 12179.26


def test_backfill_intl_tv_never_overwrites_an_existing_value(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    d = date.today()
    s = (d - timedelta(days=1)).isoformat()
    upsert_market_daily(conn, {"date": s, "taiex": 23000.0})
    upsert_market_daily(conn, {"date": d.isoformat(), "sox": 999.0, "kospi": 111.0})

    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: {"sox": {"date": s, "value": 12179.26, "chg_pct": 6.55},
                                           "kospi": {"date": d.isoformat(), "value": 1.0, "chg_pct": 1.0}})

    updater._backfill_intl_tv(conn, days=7)
    assert conn.execute("SELECT sox, kospi FROM market_daily WHERE date=?",
                        (d.isoformat(),)).fetchone()[0] == 999.0


def test_backfill_intl_tv_no_holes_skips_fetch(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(),
                               "sox": 1.0, "vix": 2.0, "n225": 3.0, "kospi": 4.0})
    called = []
    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: called.append(keys) or {})
    assert updater._backfill_intl_tv(conn, days=7) == []
    assert not called


def test_backfill_intl_tv_only_asks_for_keys_that_have_holes(tmp_path, monkeypatch):
    """沒缺的 key 不必進請求——少一欄就少一次「拿盤中價覆蓋」的機會。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": date.today().isoformat(),
                               "sox": 1.0, "vix": 2.0, "n225": 3.0})
    asked = []
    monkeypatch.setattr(updater.intl, "fetch_dated_closes",
                        lambda keys=None: asked.append(list(keys)) or {})
    updater._backfill_intl_tv(conn, days=7)
    assert asked == [["kospi"]]


def test_run_update_writes_session_aligned_intl_not_live_snapshot(tmp_path, monkeypatch):
    """每日更新的國際指數必須走場次規則，而不是「跑的當下」的報價。

    舊做法寫入 fetch_intl_indices 的即時值，導致同一個 sox 數字被寫進相鄰兩天
    （2026-07-20 與 07-21 都是 11743.85）——把別場的價格貼上資料日 D 的標籤。
    因 _backfill_intl 只填 NULL 不覆蓋，寫錯的值永遠不會被修正，故寧可留 NULL。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.stock_flow, "update_day", lambda conn, D: {
        "TWSE": {"quotes": {}, "margin": {}}, "TPEx": {"quotes": {}, "margin": {}}})
    D = date.today()
    prev_session2 = (D - timedelta(days=2)).isoformat()
    prev_session = (D - timedelta(days=1)).isoformat()

    monkeypatch.setattr(updater.twse, "fetch_taiex",
                        lambda: {"taiex": 23000.0, "taiex_chg": 50.0, "date": D.isoformat()})
    for name in ("fetch_institutional", "fetch_margin"):
        monkeypatch.setattr(updater.twse, name, lambda date=None: {})
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date", lambda date=None: {})
    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution",
                        lambda: {"week_date": None, "data": {}})
    monkeypatch.setattr(updater.revenue, "fetch_twse_revenue", lambda: {})
    monkeypatch.setattr(updater.revenue, "fetch_otc_revenue", lambda: {})
    # sox 走 Nasdaq（見 _backfill_intl_nasdaq）：有「D 之前那一場」。n225 走 FRED
    # （見 _backfill_intl_fred），只有 D 之前，沒有 D 當天（亞股尚未收盤）。
    # kospi 走 TradingView 帶日期快照，這裡模擬抓不到。
    # chg_pct 是 _backfill_intl_nasdaq 自己用 parse_history_closes 從收盤序列推算的
    # （不像舊版 fetch_intl_history 直接吐現成的 chg_pct），故給兩天讓它有前值可算。
    monkeypatch.setattr(updater.nasdaq, "fetch_sox_history", lambda days=0: {
        prev_session2: 100.0, prev_session: 105.0,
    })
    monkeypatch.setattr(updater.fred, "fetch_fred_series", lambda series_id, start_date: (
        {prev_session: 300.0} if series_id == "NIKKEI225" else {}
    ))
    monkeypatch.setattr(updater.intl, "fetch_dated_closes", lambda keys=None: {})

    result = updater.run_update(conn, intl_tickers={"sox": "^SOX", "n225": "^N225"})

    r = conn.execute("SELECT sox, sox_chg, n225 FROM market_daily WHERE date=?",
                     (D.isoformat(),)).fetchone()
    assert r[0] == 105.0 and r[1] == 5.0   # 美盤：D 當晚可得的是 D 之前那一場
    assert r[2] is None                    # 亞股當日還沒收 → 留 NULL，不拿別場頂替
    assert "intl" in result["success"]
