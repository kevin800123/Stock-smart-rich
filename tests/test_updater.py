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


def _hist_row(ym):
    return {"name": "X", "industry": "", "year_month": ym, "report_date": ym + "-10",
            "revenue": 100.0, "revenue_prev_month": 90.0, "revenue_last_year": 80.0,
            "mom_pct": 11.1, "yoy_pct": 25.0, "revenue_accum": 500.0,
            "revenue_accum_last_year": 400.0, "accum_yoy_pct": 25.0, "note": None}


def test_prev_calendar_month_year_boundary():
    from datetime import date as _d
    assert updater._prev_calendar_month(_d(2026, 8, 23)) == (2026, 7)
    assert updater._prev_calendar_month(_d(2026, 1, 5)) == (2025, 12)


def test_backfill_monthly_revenue_history_walks_months_both_markets(tmp_path, monkeypatch):
    """回補 N 個月：由 anchor 往回逐月抓上市＋上櫃，換算民國年月，逐月落地。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    calls = []

    def fake(roc_year, month, market):
        calls.append((roc_year, month, market))
        ym = f"{roc_year + 1911:04d}-{month:02d}"
        return {"2330" if market == "twse" else "1240": _hist_row(ym)}

    monkeypatch.setattr(updater.revenue, "fetch_monthly_revenue_history", fake)
    out = updater.backfill_monthly_revenue_history(conn, months=3, anchor=(2026, 7))

    # 民國 115 年、月份 7→6→5，每月兩市場各一次
    assert [(y, m) for (y, m, _mk) in calls] == [(115, 7), (115, 7), (115, 6), (115, 6), (115, 5), (115, 5)]
    assert {mk for (_y, _m, mk) in calls} == {"twse", "otc"}
    yms = {r[0] for r in conn.execute("SELECT DISTINCT year_month FROM stock_revenue_monthly")}
    assert yms == {"2026-07", "2026-06", "2026-05"}
    assert out["months"] == 3
    assert out["filled"] == 6  # 3 月 × 2 市場 × 1 檔


def test_backfill_report_until_plateau_stops_when_remaining_stops_dropping(tmp_path, monkeypatch):
    """完整報表回補在 Zeabur 上同步跑會 502（重、逐批打 mopsfin），改由背景執行緒跑到底。
    這支純迴圈負責「一直呼叫 backfill_report_financials 直到 remaining 不再下降」，可測。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    # (filled, remaining)：40 之後連續兩輪沒下降才算到底（patience=2，不在第一個平輪就停——
    # 報表端點退化造成的整輪回空是暫時性的，見 until_plateau docstring）
    seq = [(150, 70), (90, 40), (0, 40)]
    calls = {"i": 0}

    def fake(c, anchor_year=None, anchor_season=None, max_batches=4, batch_size=30):
        f, r = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return {"filled": f, "remaining": r, "universe": 1977}

    monkeypatch.setattr(updater, "backfill_report_financials", fake)
    prog = {}
    out = updater.backfill_report_financials_until_plateau(conn, chunk_batches=6, batch_size=30,
                                                           max_rounds=25, patience=2, progress=prog)
    assert out["remaining"] == 40
    assert out["filled"] == 240        # 150+90+0+0（第 4 輪也跑了，才湊滿兩個平輪）
    assert out["rounds"] == 4          # 70→40 兩個進展輪 + 40,40 兩個平輪
    assert prog["remaining"] == 40 and prog["done"] is True   # progress 供端點即時讀


def test_report_pending_codes_requires_enough_quarters_not_just_presence(tmp_path):
    """迴歸（財報分顯示 —）：早期 sync 只抓到 capex 2 季就中斷，舊版「有 capex 指標就算完成」
    把半殘代號判完成、游標跳過、永遠補不到 lan_score 需要的 8 季。改成季數不足即 pending。"""
    from stocks_power_rich.db import bulk_upsert_financials
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    q = ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2", "2025Q1", "2024Q4", "2024Q3"]
    for ind in ("pretax_income", "opex", "income_tax"):           # 損益 3 項各 4 季（=深度）
        for code in ("1101", "2330"):
            bulk_upsert_financials(conn, ind, {code: {x: 1.0 for x in q[:4]}})
    bulk_upsert_financials(conn, "capex", {"2330": {x: 1.0 for x in q}})       # 2330 capex 8 季（齊）
    bulk_upsert_financials(conn, "capex", {"1101": {x: 1.0 for x in q[:2]}})   # 1101 capex 只 2 季（半殘）

    pending = updater._report_pending_codes(conn, ["1101", "2330"])
    assert "1101" in pending        # capex 不足 8 季 → 仍 pending（會被重抓補齊）
    assert "2330" not in pending    # 齊全 → 完成


def test_compute_report_indicators_returns_decumulated_by_indicator_no_db(monkeypatch):
    """抽出的純函式：抓報表→反推單季→{indicator:{code:{季別:單季值}}}，不需要 DB。
    本機腳本靠它在本機算好再 POST 上 production（Zeabur 打不動報表端點）。"""
    monkeypatch.setattr(updater, "_REPORT_THROTTLE", 0)
    # 每季累計值（loss/pretax 用同一組模擬）；2026 各季累計，反推後 Q1=Q1累計、Q2=Q2-Q1…
    income_cum = {"2026Q1": 100.0, "2025Q4": 400.0, "2025Q3": 300.0, "2025Q2": 200.0, "2025Q1": 90.0}
    cash_cum = {f"{y}Q{s}": 10.0 * (4 * (2026 - y) + (5 - s)) for y in (2025, 2026) for s in (1, 2, 3, 4)}

    def fake(codes, report, year, season):
        q = f"{year}Q{season}"
        if report == "IncomeStatement":
            return (q, {c: {"稅前淨利（淨損）": income_cum[q]} for c in codes}) if q in income_cum else (q, {})
        return (q, {c: {"取得不動產、廠房及設備": -cash_cum[q]} for c in codes}) if q in cash_cum else (q, {})

    monkeypatch.setattr(updater.financials, "fetch_report", fake)
    out = updater.compute_report_indicators(["2330"], 2026, 1)
    assert "pretax_income" in out and "capex" in out
    assert out["pretax_income"]["2330"]["2026Q1"] == 100.0        # 新年度 Q1＝累計本身
    assert out["pretax_income"]["2330"]["2025Q4"] == 100.0        # 400-300
    assert isinstance(out["capex"]["2330"], dict) and out["capex"]["2330"]


def test_backfill_report_until_plateau_tolerates_transient_empty_round(tmp_path, monkeypatch):
    """迴歸（production 卡在 remaining=1947）：某一輪整輪 committed=0（報表端點退化回空）不可
    直接判 done——下一輪可能就恢復。patience=2 讓單一暫時性空輪被容忍、繼續補到真的平為止。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    seq = [(100, 80), (0, 80), (60, 50), (0, 50), (0, 50)]   # 第 2 輪空、第 3 輪恢復
    calls = {"i": 0}

    def fake(c, **k):
        f, r = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return {"filled": f, "remaining": r, "universe": 1977}

    monkeypatch.setattr(updater, "backfill_report_financials", fake)
    out = updater.backfill_report_financials_until_plateau(conn, patience=2)
    assert out["remaining"] == 50      # 沒有停在第 2 輪的 80，繼續補到 50
    assert out["filled"] == 160        # 100+0+60+0+0（含恢復輪的 60）
    assert out["rounds"] == 5


def test_backfill_report_until_plateau_respects_max_rounds(tmp_path, monkeypatch):
    """遠端一直有微小進展也不能無限跑——max_rounds 當安全上限。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    state = {"rem": 1000}

    def fake(c, **k):
        state["rem"] -= 1                # 每輪只掉 1，永遠在下降
        return {"filled": 1, "remaining": state["rem"], "universe": 1977}

    monkeypatch.setattr(updater, "backfill_report_financials", fake)
    out = updater.backfill_report_financials_until_plateau(conn, max_rounds=5)
    assert out["rounds"] == 5           # 停在上限，不是跑到 remaining=0


def test_backfill_monthly_revenue_history_one_month_empty_does_not_abort(tmp_path, monkeypatch):
    """某月某市場回空（改版或暫時性失敗）不中斷其餘月份。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)

    def fake(roc_year, month, market):
        if month == 6:
            return {}
        ym = f"{roc_year + 1911:04d}-{month:02d}"
        return {"2330": _hist_row(ym)}

    monkeypatch.setattr(updater.revenue, "fetch_monthly_revenue_history", fake)
    out = updater.backfill_monthly_revenue_history(conn, months=3, anchor=(2026, 7))
    yms = {r[0] for r in conn.execute("SELECT DISTINCT year_month FROM stock_revenue_monthly")}
    assert yms == {"2026-07", "2026-05"}  # 6 月兩市場皆空，略過不落地
    assert out["filled"] == 4  # (7,twse)+(7,otc?) → 這裡 otc 也走同 fake：7 月與 5 月各 2 市場


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


def test_backfill_report_financials_decumulates_and_stores_by_indicator(tmp_path, monkeypatch):
    """sub-task 2：完整報表逐季回補。IncomeStatement 一次抓 4 季（覆蓋 pretax_income/opex/
    income_tax 各自需要的深度）、CashflowStatement 一次抓 8 季（capex 需要 8 季）；累計值
    要先反推成單季才落地（同真實 TSMC 2025 全年數字驗證過的 decumulate_quarterly）。"""
    from stocks_power_rich.db import bulk_upsert_revenue, get_financial_series
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": "2026-07", "report_date": "2026-08-11", "yoy_pct": 1.0}})

    # 模擬 IncomeStatement／CashflowStatement 累計值（只用整數方便手算）。實際 fetch 深度是
    # depth+1（見 backfill_report_financials：最舊一季的單季值要靠多抓一季累計值當基準才
    # 算得出來，故這裡也多給一季墊底，讓 depth 季的 own 值全部能算出來）。
    income_cum = {"2026Q1": 100.0, "2025Q4": 400.0, "2025Q3": 280.0, "2025Q2": 150.0,
                  "2025Q1": 50.0}
    cashflow_cum = {"2026Q1": 10.0, "2025Q4": 80.0, "2025Q3": 65.0, "2025Q2": 45.0,
                    "2025Q1": 20.0, "2024Q4": 90.0, "2024Q3": 70.0, "2024Q2": 48.0,
                    "2024Q1": 30.0}
    calls = []

    def fake_fetch_report(codes, report, year, season):
        calls.append((tuple(codes), report, year, season))
        q = f"{year}Q{season}"
        if report == "IncomeStatement":
            if q not in income_cum:
                return (q, {})
            return (q, {c: {"稅前淨利（淨損）": income_cum[q], "營業費用合計": income_cum[q] / 2,
                            "所得稅費用（利益）合計": income_cum[q] / 10} for c in codes})
        if q not in cashflow_cum:
            return (q, {})
        return (q, {c: {"取得不動產、廠房及設備": -cashflow_cum[q]} for c in codes})

    monkeypatch.setattr(updater.financials, "fetch_report", fake_fetch_report)
    monkeypatch.setattr(updater, "_REPORT_THROTTLE", 0)   # 測試不等真實節流

    res = updater.backfill_report_financials(conn, anchor_year=2026, anchor_season=1,
                                              max_batches=1, batch_size=50)
    assert res["filled"] == 1

    # IncomeStatement 抓了 5 季（4 季要交付＋1 季墊底）、CashflowStatement 抓了 9 季（8+1）
    income_calls = [c for c in calls if c[1] == "IncomeStatement"]
    cash_calls = [c for c in calls if c[1] == "CashflowStatement"]
    assert len(income_calls) == 5 and len(cash_calls) == 9

    # pretax_income 反推單季：2025Q4 own = 400-280 = 120；2026Q1 own = 100（新年度重置）
    pretax = dict(get_financial_series(conn, "2330", "pretax_income"))
    assert pretax["2026Q1"] == 100.0
    assert pretax["2025Q4"] == 120.0
    assert pretax["2025Q3"] == 130.0  # 280-150

    # capex 反推單季（原始值取負號，反推後仍保留負號代表流出）
    capex = dict(get_financial_series(conn, "2330", "capex"))
    assert capex["2026Q1"] == -10.0          # Q1 own＝自己
    assert capex["2025Q4"] == -15.0          # -(80-65)
    assert capex["2025Q1"] == -20.0          # 2025Q1 own＝自己（新年度重置，不與 2024Q4 相減）
    # depth=8 要交付到 2024Q2；多抓的 2024Q1 墊底讓它的 own 值算得出來，不會因為
    # 「缺上一季基準」被過濾掉（這正是本測試要鎖住的 +1 深度 bug 修正）
    assert capex["2024Q2"] == -18.0          # -(48-30)
    # 9 筆而非 8：墊底那季（2024Q1）自己本身也算得出 own 值（season=1，own＝自己），
    # 順便可用，不是缺陷——只是「至少 8 季可用」這個承諾之外的額外資料
    assert len(capex) == 9

    # opex/income_tax 也各自落地
    opex = dict(get_financial_series(conn, "2330", "opex"))
    assert opex["2026Q1"] == 50.0            # 100/2


def test_backfill_report_financials_skips_unpublished_quarter(tmp_path, monkeypatch):
    """最新一季尚未公布（完整報表回空）→ 略過該季，不落地假資料、也不當機。"""
    from stocks_power_rich.db import bulk_upsert_revenue, get_financial_series
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": "2026-07", "report_date": "2026-08-11", "yoy_pct": 1.0}})

    def fake_fetch_report(codes, report, year, season):
        q = f"{year}Q{season}"
        if q == "2026Q2":  # 最新一季尚未公布
            return (q, {})
        return (q, {c: {"稅前淨利（淨損）": 10.0, "營業費用合計": 5.0,
                        "所得稅費用（利益）合計": 1.0, "取得不動產、廠房及設備": -3.0} for c in codes})

    monkeypatch.setattr(updater.financials, "fetch_report", fake_fetch_report)
    monkeypatch.setattr(updater, "_REPORT_THROTTLE", 0)   # 測試不等真實節流
    updater.backfill_report_financials(conn, anchor_year=2026, anchor_season=2,
                                       max_batches=1, batch_size=50)
    pretax = dict(get_financial_series(conn, "2330", "pretax_income"))
    assert "2026Q2" not in pretax  # 尚未公布那季沒進去，不是整個序列都空
    # 但抓得到的其他季仍正常入庫（anchor 往回數第 2 季＝2026Q1，本例回傳固定 10.0）
    assert pretax.get("2026Q1") == 10.0


def test_backfill_report_financials_quarter_with_data_but_missing_target_label_does_not_count(
        tmp_path, monkeypatch):
    """實測踩到的真實情況：最新一季 parsed 不是空的（有資料），但**沒有我要的那個科目**
    （現金流量表的『取得不動產、廠房及設備』——像是損益數字先出來、現金流量表科目還沒填齊
    的過渡態）。這種季度不能算進『已蒐集到 depth+1 個有用季度』，否則會讓真正有 capex
    資料的季度少一筆、最舊一季反推單季時缺基準被濾掉（實測：depth=8 只換到 7 季能用）。
    """
    from stocks_power_rich.db import bulk_upsert_revenue, get_financial_series
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": "2026-07", "report_date": "2026-08-11", "yoy_pct": 1.0}})

    # capex 需要 8 季；2026Q2 有資料但缺 capex 科目，2026Q1~2024Q1 給 9 季乾淨累計值
    # （單調遞增，模擬真實累計性質；用小整數方便手算）
    cashflow_cum = {"2026Q1": 90.0, "2025Q4": 80.0, "2025Q3": 65.0, "2025Q2": 45.0,
                    "2025Q1": 20.0, "2024Q4": 90.0, "2024Q3": 70.0, "2024Q2": 48.0,
                    "2024Q1": 30.0}

    def fake_fetch_report(codes, report, year, season):
        q = f"{year}Q{season}"
        if report == "IncomeStatement":
            return (q, {c: {"稅前淨利（淨損）": 1.0, "營業費用合計": 1.0,
                            "所得稅費用（利益）合計": 1.0} for c in codes})
        if q == "2026Q2":
            return (q, {c: {"某個不相干科目": 999.0} for c in codes})  # 有資料，但沒有 capex 科目
        if q not in cashflow_cum:
            return (q, {})
        return (q, {c: {"取得不動產、廠房及設備": -cashflow_cum[q]} for c in codes})

    monkeypatch.setattr(updater.financials, "fetch_report", fake_fetch_report)
    monkeypatch.setattr(updater, "_REPORT_THROTTLE", 0)   # 測試不等真實節流
    updater.backfill_report_financials(conn, anchor_year=2026, anchor_season=2,
                                       max_batches=1, batch_size=50)
    capex = dict(get_financial_series(conn, "2330", "capex"))
    # depth=8 一定要換到 8 季能用，「2026Q2 有資料但缺科目」不能頂替一個有效名額
    assert len(capex) >= 8
    assert capex["2024Q2"] == -18.0  # -(48-30)，最舊一季要靠多抓的 2024Q1 才反推得出


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


def test_expected_published_quarter_uses_tw_filing_deadlines():
    """財報公布截止：年報(Q4) 3/31、Q1 5/15、Q2 8/14、Q3 11/14。回「到今天最新一個應已公布的季」。"""
    eq = updater.expected_published_quarter
    assert eq(date(2026, 5, 14)) == "2025Q4"   # Q1 未到 5/15
    assert eq(date(2026, 5, 15)) == "2026Q1"   # Q1 公布日
    assert eq(date(2026, 8, 14)) == "2026Q2"
    assert eq(date(2026, 11, 14)) == "2026Q3"
    assert eq(date(2026, 12, 1)) == "2026Q3"
    assert eq(date(2026, 3, 31)) == "2025Q4"   # 年報 3/31
    assert eq(date(2026, 3, 30)) == "2025Q3"   # 年報前，最新是前年 Q3
    assert eq(date(2027, 1, 10)) == "2026Q3"
