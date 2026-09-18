import datetime as _dt

from fastapi.testclient import TestClient

from stocks_power_rich.db import get_connection, init_db, get_ssf_dates, get_ssf_rows
from stocks_power_rich.api import helpers as H
from stocks_power_rich.main import create_app
from stocks_power_rich.sources import taifex_ssf as ssf


def _db(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    return conn


def _fake_rows(date, n=1300):
    """n 列一般月合約的一般列（root 依序循環，每檔都湊得出 contract=root+"F"）。

    **依真實介面塑形，不是隨手編**：`summarize_ssf_day` 只從 `session=="一般" and
    contract==root+"F"` 的列取價（見 `sources/taifex_ssf.py`），root 取 `contract[:2]`。
    若合約代碼不落在這個形狀（例如原本誤用 `f"X{i:03d}F"[:4]` 切出 "X000" 這種 4 碼
    代號，其 `root+"F"`＝"X0F" 恆不等於它自己），`summarize_ssf_day` 會對任何一天的
    輸入都回傳空列表——不是抓取失敗，是這個測試替身沒有依照真實資料形狀來偽造。
    """
    out = []
    for i in range(n):
        root = f"{chr(65 + i % 26)}{(i // 26) % 10}"
        out.append({"date": date, "contract": root + "F", "month": "202610",
                    "session": "一般", "is_spread": False, "open": 1.0, "high": 2.0,
                    "low": 0.5, "close": 1.5, "chg": 0.1, "chg_pct": 7.1,
                    "volume": 10 + i, "settlement": 1.5, "oi": 100})
    return out


def test_refresh_writes_the_day_and_prunes(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    # 用 e（查詢區間的結束日＝目標日）而非 s（回看起點）餵資料：
    # refresh_ssf_daily 內部把 rows 依自己的 "date" 欄位分組寫入，若餵成 s 那天的日期，
    # 寫進 DB 的會是回看起點而不是目標日，get_ssf_dates 斷言就對不起來。
    monkeypatch.setattr(ssf, "fetch_ssf_daily",
                        lambda s, e: _fake_rows(e.replace("/", "-")))
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["date"] == "2026-09-17"
    assert res["roots"] > 0 and res["margin_ok"] is True
    assert get_ssf_dates(conn) == ["2026-09-17"]


def test_refresh_skips_weekends(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily",
                        lambda s, e: (_ for _ in ()).throw(AssertionError("週末不該連外")))
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {})
    assert H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 19))["skipped"] == "weekend"   # 週六


def test_refresh_reports_data_not_ready_when_the_fetcher_guards_reject(tmp_path, monkeypatch):
    """早上跑時抓取器會因『一般列不足』回空——那不是錯誤，是資料還沒發佈。"""
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: [])
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "x"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["skipped"] == "data_not_ready"
    assert get_ssf_dates(conn) == []      # 絕不寫進半套


def test_refresh_still_updates_the_margin_table_when_quotes_are_not_ready(tmp_path, monkeypatch):
    """保證金與行情無關；行情沒到齊不該連帶讓保證金整天不更新。"""
    conn = _db(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: [])

    def _margin(c):
        called["n"] += 1
        return {"stock_updated": "2026/09/15"}
    monkeypatch.setattr(H, "_ssf_margin_table", _margin)
    H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert called["n"] == 1


def test_refresh_reports_data_not_ready_when_D_itself_is_missing_even_if_prior_days_came_back(
        tmp_path, monkeypatch):
    """review I2：排程抓的是多日重疊區間，前幾個交易日幾乎必定回得來，即使今天
    (D) 還沒發佈。舊行為在這種情況下會把「早幾天有寫」誤報成「今天成功」——
    run_job 記到的狀態與 note 讓每一個時段看起來都成功，`/api/health` 的
    `jobs.ssf_daily` 因此永遠分不出哪個時段才是 D 真正到齊的那一次
    （見 probe_review2.py 的重現）。D 不在這次抓到的日期裡時，即使前面幾天的
    列已經被寫回 DB（COALESCE 讓重寫安全），也要老實回報 data_not_ready，並列出
    真的更新了哪些日期（`refreshed_prior`），而不是謊稱『date』那天處理成功。
    """
    conn = _db(tmp_path)
    prior_rows = _fake_rows("2026-09-15") + _fake_rows("2026-09-16")
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: prior_rows)
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["skipped"] == "data_not_ready"
    assert res["date"] == "2026-09-17"
    assert res["refreshed_prior"] == ["2026-09-15", "2026-09-16"]
    assert set(get_ssf_dates(conn)) == {"2026-09-15", "2026-09-16"}   # 前幾天確實有寫
    assert "2026-09-17" not in get_ssf_dates(conn)                    # 但 D 沒被寫進去


def test_refresh_records_ready_at_the_first_time_D_is_written_and_keeps_reporting_it(
        tmp_path, monkeypatch):
    """D 第一次真的寫入時要記下時間戳（`ssf_ready:{D}`），之後同一天的
    already_done 回應要繼續帶著這個時間戳——`/api/health` 的
    `jobs.ssf_daily.note.ready_at` 才能回答『D 是哪個時段第一次到齊』。
    """
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: _fake_rows(e.replace("/", "-")))
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    monkeypatch.setattr(H, "_now", lambda: _dt.datetime(2026, 9, 17, 18, 15, 3))
    res1 = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res1["date"] == "2026-09-17"
    assert res1["ready_at"] == "2026-09-17T18:15:03"

    # 之後（例如 20:15 那次）同一天再呼叫：已經 ready，不該再重抓，但要繼續回報
    # 第一次成功的時間，不是這一次呼叫的時間。
    monkeypatch.setattr(ssf, "fetch_ssf_daily",
                        lambda s, e: (_ for _ in ()).throw(AssertionError("已 ready 不該再連外")))
    monkeypatch.setattr(H, "_now", lambda: _dt.datetime(2026, 9, 17, 20, 15, 7))
    res2 = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res2["skipped"] == "already_done"
    assert res2["ready_at"] == "2026-09-17T18:15:03"     # 仍是第一次成功的時間，不是這次


def test_refresh_already_done_means_D_was_actually_written_not_merely_a_row_existing(
        tmp_path, monkeypatch):
    """`already_done` 的定義是『`ssf_ready:{D}` 這個 marker 存在』，不是『`ssf_daily`
    裡剛好已經有 D 這個列』——例如 `/api/ssf/backfill` 可能先把 D 的列寫進去，
    這時排程不該把它誤判成『自己已經確認過 D 到齊』而略過重新確認。
    """
    conn = _db(tmp_path)
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    bulk_upsert_ssf_daily(conn, ssf.summarize_ssf_days(_fake_rows("2026-09-17")))
    assert "2026-09-17" in get_ssf_dates(conn)     # D 已經有列，但沒有 ready marker

    called = {"n": 0}

    def fake_fetch(s, e):
        called["n"] += 1
        return _fake_rows(e.replace("/", "-"))
    monkeypatch.setattr(ssf, "fetch_ssf_daily", fake_fetch)
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert called["n"] == 1              # 沒有 ready marker，仍然要重新確認
    assert res["date"] == "2026-09-17"
    assert "ready_at" in res


def test_job_schedule_registers_ssf_on_weekday_evenings():
    """時段刻意避開現有全部排程：17:00 news／17:30、18:30、19:30 self_screen_early／
    21:00 daily_update／21:10 news／21:30 osfut／21:40 picks_new_daily。"""
    from stocks_power_rich.config import Config
    cfg = Config(telegram_token="t", telegram_chat_id="c", line_token="l",
                 weekly_push_time="17:00")     # 同 tests/test_job_runs.py::_cfg
    by_id = {s["id"]: s for s in H.job_schedule(cfg, "21:00")}
    assert by_id["ssf_daily"]["dow"] == "mon-fri"
    assert by_id["ssf_daily"]["hour"] == "17,18,20"
    assert by_id["ssf_daily"]["minute"] == "15"
    taken = {(s["hour"], s["minute"]) for k, s in by_id.items() if k != "ssf_daily"}
    assert ("17,18,20", "15") not in taken     # 不與既有時段同分鐘


def test_ssf_backfill_endpoint_writes_rows_and_stays_within_a_month(tmp_path, monkeypatch):
    """`/api/ssf/backfill`：驗證真的寫進資料並回報筆數，且每一次實際打給
    `fetch_ssf_daily` 的區間都要在官方硬性限制（不可超過一個月，超過只會拿到 200
    的 HTML 警告頁而非資料）之內。刻意帶一個遠超合理範圍的 days，確認迴圈不會失控——
    這正是 clamp（Fix 2）要擋下的形狀。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    calls = []

    def fake_fetch(s, e):
        calls.append((s, e))
        return _fake_rows(e.replace("/", "-"))

    monkeypatch.setattr(ssf, "fetch_ssf_daily", fake_fetch)
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/ssf/backfill?days=999999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wrote"] > 0
    assert body["dates"] > 0
    assert calls                 # 真的呼叫了 fetch_ssf_daily
    assert len(calls) < 50       # 迴圈確實有界會結束——不是因為它剛好夠快，是因為 days 被 clamp 了
    for s, e in calls:
        sd = _dt.datetime.strptime(s, "%Y/%m/%d").date()
        ed = _dt.datetime.strptime(e, "%Y/%m/%d").date()
        assert (ed - sd).days <= 30


def test_refresh_groups_multi_date_response_by_date_before_summarizing(tmp_path, monkeypatch):
    """`fetch_ssf_daily` 一次回應本來就常橫跨多天（`refresh_ssf_daily` 自己也會一併重抓
    前 2 個交易日）。`summarize_ssf_day` 用『該 root 第一次出現』的列決定日期與主力月價格，
    所以呼叫端**必須先依日期分組**再逐日呼叫——若整批一次丟給它，兩天的資料會被壓成
    同一天，且哪天的價格留下來純屬巧合（見 CLAUDE.md 對這支函式的說明）。

    兩個日期用同一批 root、但收盤價明顯不同（1.5 vs 9.9），才分得出「有沒有被誰蓋掉」。
    """
    conn = _db(tmp_path)

    def two_date_rows(s, e):
        day1 = _fake_rows("2026-09-16")
        day2 = _fake_rows("2026-09-17")
        for r in day2:
            r["close"] = 9.9
            r["chg_pct"] = -3.3
        return day1 + day2

    monkeypatch.setattr(ssf, "fetch_ssf_daily", two_date_rows)
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["date"] == "2026-09-17"
    assert res["days"] == 2
    assert set(get_ssf_dates(conn)) == {"2026-09-16", "2026-09-17"}
    rows16 = {r["root"]: r for r in get_ssf_rows(conn, ["2026-09-16"])}
    rows17 = {r["root"]: r for r in get_ssf_rows(conn, ["2026-09-17"])}
    assert rows16 and rows17
    common = set(rows16) & set(rows17)
    assert common
    sample = next(iter(common))
    assert rows16[sample]["close"] == 1.5
    assert rows17[sample]["close"] == 9.9


import os, tempfile


def _client(monkeypatch, tmp_path):
    os.environ["SPR_DB_PATH"] = str(tmp_path / "api.sqlite")
    from fastapi.testclient import TestClient
    from stocks_power_rich.main import create_app
    return TestClient(create_app(enable_scheduler=False))


_CONTRACTS = {
    "CD": {"code": "2330", "stock_name": "台積電", "name": "台積電",
           "multiplier": 2000, "is_etf": False, "is_mini": False},
    "QF": {"code": "2330", "stock_name": "台積電", "name": "小型台積電",
           "multiplier": 100, "is_etf": False, "is_mini": True},
    "NY": {"code": "0050", "stock_name": "元大台灣50", "name": "元大台灣50",
           "multiplier": 10000, "is_etf": True, "is_mini": False},
}
_MARGIN = {
    "stock_updated": "2026/09/15", "etf_updated": "2026/08/12", "index_updated": "2026/08/12",
    "stock": {"CDF": {"code": "2330", "name": "台積電期貨", "tier": "級距1",
                      "clearing_pct": 10.0, "maintenance_pct": 10.35, "initial_pct": 13.50},
              "QFF": {"code": "2330", "name": "小型台積電期貨", "tier": "級距1",
                      "clearing_pct": 10.0, "maintenance_pct": 10.35, "initial_pct": 13.50}},
    "etf": {"NYF": {"code": "0050", "name": "元大台灣50ETF期貨",
                    "clearing": 64000, "maintenance": 67000, "initial": 87000}},
    "index": {"微型臺指期貨": {"clearing": 25950, "maintenance": 26900, "initial": 35050},
              "臺股期貨": {"clearing": 519000, "maintenance": 538000, "initial": 701000}},
}


def test_self_screen_rows_carry_the_stock_futures_margin(monkeypatch, tmp_path):
    """自算選股表的參考欄：有沒有股期、1 口要多少錢。"""
    from stocks_power_rich.api import admin as A
    monkeypatch.setattr(A, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(A, "_ssf_margin_table", lambda c: _MARGIN)
    rows = [{"code": "2330", "mu_value": 90}, {"code": "6488", "mu_value": 80}]
    out = A._attach_ssf_margin(get_connection(str(tmp_path / "x.sqlite")), rows,
                               settlements={"CD": 2432.0, "QF": 2432.0})
    assert out[0]["ssf"] is True and out[0]["ssf_margin"] == 656640   # 取標準約，非小型
    assert out[1]["ssf"] is False and out[1]["ssf_margin"] is None


def test_attaching_ssf_margin_never_changes_the_screening_result(monkeypatch, tmp_path):
    """它是參考欄：不進篩選、不進計分，只多兩個鍵。"""
    from stocks_power_rich.api import admin as A
    monkeypatch.setattr(A, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(A, "_ssf_margin_table", lambda c: _MARGIN)
    rows = [{"code": "2330", "mu_value": 90, "mu_score": 12}]
    before = dict(rows[0])
    out = A._attach_ssf_margin(get_connection(str(tmp_path / "y.sqlite")), rows,
                               settlements={"CD": 2432.0})
    assert len(out) == 1
    assert {k: v for k, v in out[0].items() if k not in ("ssf", "ssf_margin")} == before


def _seed_margin(monkeypatch, tmp_path):
    from stocks_power_rich.api import market as M
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_ssf_margin_table", lambda c: _MARGIN)
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "CD", "main_month": "202610", "settlement": 2432.0,
         "close": 2433.0, "chg_pct": 1.33, "volume": 8192, "oi": 25045, "main_volume": 6027,
         "open": None, "high": None, "low": None, "chg": None},
        {"date": "2026-09-17", "root": "QF", "main_month": "202610", "settlement": 2432.0,
         "close": 2434.0, "chg_pct": 1.37, "volume": 36062, "oi": 53174, "main_volume": 26032,
         "open": None, "high": None, "low": None, "chg": None},
        {"date": "2026-09-17", "root": "NY", "main_month": "202610", "settlement": 108.3,
         "close": 108.3, "chg_pct": 0.5, "volume": 100, "oi": 500, "main_volume": 100,
         "open": None, "high": None, "low": None, "chg": None},
    ])


def test_margin_endpoint_computes_stock_futures_from_settlement(monkeypatch, tmp_path):
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    assert d["price_date"] == "2026-09-17"
    assert d["stock_updated"] == "2026/09/15"
    by_root = {r["root"]: r for r in d["rows"]}
    # 原始保證金
    assert by_root["CD"]["initial"] == 656640      # 2432 × 2000 × 13.50%
    assert by_root["QF"]["initial"] == 32832       # 2432 × 100 × 13.50%
    # 維持保證金
    assert by_root["CD"]["maintenance"] == 503424  # 2432 × 2000 × 10.35%
    assert by_root["QF"]["maintenance"] == 25171   # 2432 × 100 × 10.35% → 25171.2 四捨五入


def test_margin_endpoint_uses_the_published_amount_for_etf_futures(monkeypatch, tmp_path):
    """ETF 期貨公布固定金額，不可套價格×比例（108.3 × 10000 × 比例 會完全不同）。"""
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    ny = {r["root"]: r for r in d["rows"]}["NY"]
    assert ny["initial"] == 87000 and ny["kind"] == "etf"
    assert ny["initial_pct"] is None
    # ETF 期貨的維持保證金也來自公布的固定金額
    assert ny["maintenance"] == 67000


def test_margin_endpoint_includes_tmf(monkeypatch, tmp_path):
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    tmf = [x for x in d["index"] if "微型" in x["name"]]
    assert tmf and tmf[0]["initial"] == 35050
    # 指數期貨的維持保證金來自公布的固定金額
    assert tmf[0]["maintenance"] == 26900


def test_margin_endpoint_builds_the_by_stock_index_on_the_server(monkeypatch, tmp_path):
    """個股頁要用股票代號反查。索引在後端組，前端不得自己掃 320 列組第二份。"""
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    assert {x["root"] for x in d["by_stock"]["2330"]} == {"CD", "QF"}


def _seed_overview(monkeypatch, tmp_path):
    from stocks_power_rich.api import market as M
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {"2330": {"close": 2425.0}})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    # 每個 root 每天只有一個月份（一列即代表全部），故 oi_total 與 oi 同值——不是湊數，
    # 這正是「該 root 當天只有一個 root+F 月份」時 oi_total 該有的樣子。
    rows = []
    for day, oi_cd in (("2026-09-16", 24000), ("2026-09-17", 25045)):
        rows += [
            {"date": day, "root": "CD", "main_month": "202610", "open": 2425.0,
             "high": 2453.0, "low": 2410.0, "close": 2433.0, "chg": 32.0, "chg_pct": 1.33,
             "settlement": 2432.0, "oi": oi_cd, "oi_total": oi_cd,
             "volume": 8192, "main_volume": 6027},
            {"date": day, "root": "QF", "main_month": "202610", "open": 2423.0,
             "high": 2453.0, "low": 2412.0, "close": 2434.0, "chg": 33.0, "chg_pct": -2.5,
             "settlement": 2432.0, "oi": 53174, "oi_total": 53174,
             "volume": 36062, "main_volume": 26032},
        ]
    bulk_upsert_ssf_daily(conn, rows)


def test_overview_ranks_hot_by_official_lot_count(monkeypatch, tmp_path):
    """官方 STFTop10 口徑：依口數排，小型合約自成一檔（規模不同，副標要註明）。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] == "2026-09-17"
    assert [x["root"] for x in d["hot"]] == ["QF", "CD"]     # 36062 > 8192
    assert d["hot"][0]["name"] == "小型台積電"


def test_overview_splits_gainers_and_losers_by_official_chg_pct(monkeypatch, tmp_path):
    """漲幅榜只收真的漲的、跌幅榜只收真的跌的——湊滿榜單的那一列會直接說謊。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["root"] for x in d["ranks"]["gainers"]] == ["CD"]
    assert [x["root"] for x in d["ranks"]["losers"]] == ["QF"]


def test_overview_candle_percentages_are_against_the_prior_settlement(monkeypatch, tmp_path):
    """參考價＝收盤−漲跌（官方漲跌是對前日結算價，不是對前日收盤）。"""
    _seed_overview(monkeypatch, tmp_path)
    cd = [x for x in _client(monkeypatch, tmp_path).get(
        "/api/ssf/overview").json()["ranks"]["volume"] if x["root"] == "CD"][0]
    assert cd["ref"] == 2401.0                                  # 2433 − 32
    assert round(cd["close_pct"], 2) == 1.33
    assert round(cd["amplitude"], 2) == round((2453 - 2410) / 2401 * 100, 2)


def test_overview_basis_is_in_ticks_and_lists_each_underlying_once(monkeypatch, tmp_path):
    """標準與小型共用同一個結算價，同一檔標的只列一列。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["code"] for x in d["basis"]] == ["2330"]
    b = d["basis"][0]
    assert b["futures"] == 2432.0 and b["spot"] == 2425.0
    assert b["ticks"] == 7          # 2425→2432，2500 以下 1 元一檔


def test_overview_oi_change_needs_both_days(monkeypatch, tmp_path):
    """前一日缺列就整檔不列——缺值不可當成 0（那會捏造一筆大增）。"""
    _seed_overview(monkeypatch, tmp_path)
    # NY 只在新的一天(09-17)出現，前一天(09-16)完全沒有這一列，用來驗證
    # 「前一日缺列」那個分支真的有被走到——QF 雖然兩天都有列，但走的是
    # 「未平倉變化為零」那條過濾規則，測不到這裡要驗證的分支。
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    conn = get_connection(str(tmp_path / "api.sqlite"))
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "NY", "main_month": "202610", "settlement": 108.3,
         "close": 108.3, "chg_pct": 0.5, "volume": 100, "oi": 500, "oi_total": 500,
         "main_volume": 100},
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    ups = {x["root"]: x for x in d["oi_change"]["up"]}
    assert ups["CD"]["oi_change"] == 1045
    assert "QF" not in ups          # QF 兩天相同，不算增加（變化為零的過濾規則）
    assert "NY" not in ups          # NY 前一天缺列，不可當成 0 算出一筆假的暴增


def test_overview_oi_change_uses_the_total_not_the_rolled_main_month(monkeypatch, tmp_path):
    """主力月換月時（結算日附近常態），用「主力月自己的 OI」相減會把單純的移倉誤讀成
    未平倉大減——這裡兩天的真實總量只變動 +500，但成交量最大的月份從 202610 換成
    202611，若用主力月口徑相減會得到 −8500 並被列進「減少最多」，其副標會告訴讀者
    「部位在減少」，但那是錯的。`oi_change` 必須用 `oi_total`，該檔也不能被歸類成
    減少。數字取自 review I1 的重現腳本（`probe_review.py`）。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    from stocks_power_rich.sources import taifex_ssf as ssf_mod
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: {
        "CD": {"code": "2330", "stock_name": "台積電", "name": "台積電",
               "multiplier": 2000, "is_etf": False, "is_mini": False}})
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    day1 = ssf_mod.SSF_HEADER + "\n" + "\n".join([
        "2026/10/19,CDF,202610  ,2400,2400,2400,2400,0,0.00%,9000,2400,25000,2400,2400,2400,2400,,一般,,",
        "2026/10/19,CDF,202611  ,2405,2405,2405,2405,0,0.00%,3000,2405,3000,2405,2405,2405,2405,,一般,,",
    ]) + "\n"
    day2 = ssf_mod.SSF_HEADER + "\n" + "\n".join([
        "2026/10/20,CDF,202610  ,2410,2410,2410,2410,0,0.00%,7000,2410,12000,2410,2410,2410,2410,,一般,,",
        "2026/10/20,CDF,202611  ,2415,2415,2415,2415,0,0.00%,9000,2415,16500,2415,2415,2415,2415,,一般,,",
    ]) + "\n"
    rows = ssf_mod.summarize_ssf_days(
        ssf_mod.parse_ssf_daily_csv(day1) + ssf_mod.parse_ssf_daily_csv(day2))
    bulk_upsert_ssf_daily(conn, rows)

    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] == "2026-10-20"
    ups = {x["root"]: x for x in d["oi_change"]["up"]}
    downs = {x["root"]: x for x in d["oi_change"]["down"]}
    assert "CD" not in downs                # 不可被誤判成「減少最多」
    assert ups["CD"]["oi_change"] == 500    # 真實總量變化，不是主力月口徑的 −8500
    assert ups["CD"]["oi"] == 28500         # 顯示值＝總量，不是主力月自己的 16500


def test_overview_heatmap_leaves_a_missing_day_empty(monkeypatch, tmp_path):
    """缺的交易日是空欄，絕不拿別天的資料頂替。"""
    _seed_overview(monkeypatch, tmp_path)
    # `_seed_overview` 兩天都是同一組 CD/QF，名次不會出現任何空缺——就算實作把
    # 資料從別天橫向搬過來頂替，兩天的名次看起來還是一樣「正確」，測不出「移位」
    # 這種 bug。加一檔只在新的一天(09-17)出現、成交量比 CD/QF 都低的 NY，讓
    # 09-16 在名次 2 那格真的沒有第 3 檔可排，才驗證得出那一格是真空缺、
    # 且其他名次沒有因此被牽動（移位）。
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    conn = get_connection(str(tmp_path / "api.sqlite"))
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "NY", "main_month": "202610", "settlement": 108.3,
         "close": 108.3, "chg_pct": 0.5, "volume": 100, "oi": 500, "main_volume": 100},
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["heatmap"]["dates"] == ["2026-09-16", "2026-09-17"]
    rows = d["heatmap"]["rows"]
    assert len(rows) >= 1
    assert rows[0][0]["root"] in ("QF", "CD")
    # 名次 0、1 兩天都還是 QF／CD，沒有因為新增 NY 而移位
    assert rows[0][0]["root"] == rows[0][1]["root"]
    assert rows[1][0]["root"] == rows[1][1]["root"]
    # 名次 2：09-16 只有 2 檔、真的沒有第 3 名，必須是空缺，不可拿 09-17 的 NY 頂替
    assert rows[2][0] is None
    assert rows[2][1]["root"] == "NY"


def test_overview_basis_coverage_counts_gaps_beyond_the_cap(monkeypatch, tmp_path):
    """`no_stock_code`／`no_spot` 要看『當天全部合約』，不能被輸出上限
    (SSF_RANK_N=20) 擋住——先前把計數與 append 綁在同一個 break 之前，一旦湊滿
    20 筆就整個迴圈提早結束，排在後面（成交量較低）的合約完全不會被走訪，缺口
    計數因此永遠停在接近 0，資料越殘缺、算出來的計數反而越正常，與這兩個計數
    存在的目的相反。

    這裡 seed 20 檔『成交量最高、可正常對到現貨』的合約去撐滿輸出上限，
    再加 3 檔查無合約代號對照、3 檔有對照但查無現貨報價——這兩種都刻意排在
    成交量最低的位置，複現「上限之後的合約從未被走訪」的原始 bug 形狀。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily

    day = "2026-09-17"
    contracts, rows, quotes = {}, [], {}
    for i in range(20):                              # 20 檔有效合約，成交量最高
        root, code = f"V{i:02d}", f"9{i:03d}"
        contracts[root] = {"code": code, "name": f"股{i}", "stock_name": f"股{i}",
                           "multiplier": 2000, "is_etf": False, "is_mini": False}
        quotes[code] = {"close": 100.0 + i}
        rows.append({"date": day, "root": root, "main_month": "202610",
                     "settlement": 100.0 + i, "close": 101.0 + i, "chg_pct": 1.0,
                     "volume": 1000 - i, "oi": 500, "main_volume": 400})
    for i in range(3):                                # 查無合約代號對照，成交量最低那一批
        rows.append({"date": day, "root": f"NC{i}", "main_month": "202610",
                     "settlement": 50.0, "close": 51.0, "chg_pct": 1.0,
                     "volume": 30 - i, "oi": 100, "main_volume": 80})
    for i in range(3):                                # 有代號對照、但查無現貨報價
        root, code = f"NS{i}", f"8{i:03d}"
        contracts[root] = {"code": code, "name": f"缺現貨{i}", "stock_name": f"缺現貨{i}",
                           "multiplier": 2000, "is_etf": False, "is_mini": False}
        rows.append({"date": day, "root": root, "main_month": "202610",
                     "settlement": 60.0, "close": 61.0, "chg_pct": 1.0,
                     "volume": 20 - i, "oi": 100, "main_volume": 80})

    monkeypatch.setattr(M, "_ssf_contracts", lambda c: contracts)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: quotes)
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, rows)

    resp = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert len(resp["basis"]) == 20                   # 輸出仍受上限
    assert resp["coverage"]["roots"] == 26
    assert resp["coverage"]["no_stock_code"] == 3      # 真正的缺口總數，不是 0
    assert resp["coverage"]["no_spot"] == 3


def test_overview_stored_days_reports_the_true_total_not_the_heatmap_window(monkeypatch, tmp_path):
    """『已存 N 個交易日』要回報 ssf_daily 實際存了幾天，不能被熱力圖固定的 10 日
    視窗夾住——那個視窗（`dates`／`SSF_HEATMAP_DAYS`）只是熱力圖的軸寬，`len()`
    永遠 <=10，回補是否落後（例如只存了 3 天）就永遠看不出來，而這正是這個欄位
    存在的唯一理由。這裡存 15 個交易日：斷言 `coverage.stored_days`==15（真實總數）
    但熱力圖軸 `dates` 仍固定在 10（視窗不變，只有計數變）。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    rows = [
        {"date": f"2026-08-{i + 1:02d}", "root": "CD", "main_month": "202610",
         "settlement": 2432.0, "close": 2433.0, "chg_pct": 1.33,
         "volume": 8192, "oi": 25045, "main_volume": 6027}
        for i in range(15)                              # 15 個交易日 > 熱力圖視窗（10）
    ]
    bulk_upsert_ssf_daily(conn, rows)

    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["coverage"]["stored_days"] == 15           # 真實總數，不是被視窗夾住的 10
    assert len(d["dates"]) == 10                        # 熱力圖軸仍固定 10 天，不受影響


def test_overview_cell_volume_is_none_not_zero_when_missing(monkeypatch, tmp_path):
    """缺量能要回 None，不可誤植為 0——0 是『零成交』這個事實，跟『查無資料』是
    兩件不同的事（同一個 dict 裡 oi/chg/close 缺值本來就是回 None，volume 不該是
    唯一的例外）。缺量的合約在排序上退到最後（排序鍵仍用 `or 0` 頂替名次，只
    影響順序、不影響這裡顯示的值），順便驗證 None 不會讓排序整個炸掉。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "CD", "main_month": "202610", "settlement": 2432.0,
         "close": 2433.0, "chg_pct": 1.33, "volume": 8192, "oi": 25045, "main_volume": 6027},
        {"date": "2026-09-17", "root": "QF", "main_month": "202610", "settlement": 2432.0,
         "close": 2434.0, "chg_pct": 1.37, "oi": 53174, "main_volume": 26032},   # 缺 volume
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    by_root = {x["root"]: x for x in d["hot"]}
    assert by_root["QF"]["volume"] is None       # 缺量回 None，不是 0
    assert by_root["CD"]["volume"] == 8192
    assert [x["root"] for x in d["hot"]] == ["CD", "QF"]   # 缺量退到最後，排序沒炸


def test_overview_empty_payload_has_the_same_coverage_keys_as_the_populated_one(monkeypatch, tmp_path):
    """沒有任何資料時，coverage 仍要帶滿四個鍵——有資料時的路徑永遠會給
    roots/no_stock_code/no_spot，前端一律讀這幾個鍵，空資料庫若只給
    stored_days 會直接 KeyError。"""
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] is None
    assert d["coverage"] == {"stored_days": 0, "roots": 0, "no_stock_code": 0, "no_spot": 0}
