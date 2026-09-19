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
    `jobs.ssf_daily` 因此永遠分不出哪個時段才是 D 真正到齊的那一次。
    D 不在這次抓到的日期裡時，即使前面幾天的
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
    already_done 回應要繼續帶著這個時間戳——`/api/health` 的 `jobs.ssf_daily.note`
    才能回答『D 是哪個時段第一次到齊』（`note` 是 `run_job` 把這個 dict 字串化
    存進去的結果，不是巢狀物件，`ready_at` 要從那段字串裡讀）。
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


def _seed_trading_days(conn, dates):
    """交易日曆＝`market_daily` 有收盤指數的日子（與 `coverage.lag_trading_days` 同一個定義）。"""
    from stocks_power_rich.db import upsert_market_daily
    for d in dates:
        upsert_market_daily(conn, {"date": d, "taiex": 20000.0})


def _weekdays(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _fake_fetch_for(trading, calls, n=30):
    """依真實介面塑形：`fetch_ssf_daily(start, end)` 回傳區間內**每個有資料的交易日**的
    列，其餘日期不出現（真實擷取器的逐日守衛會丟掉沒有日盤資料的日子）。"""
    trading = set(trading)

    def fake(s, e):
        calls.append((s, e))
        sd = _dt.datetime.strptime(s, "%Y/%m/%d").date()
        ed = _dt.datetime.strptime(e, "%Y/%m/%d").date()
        rows = []
        for d in _weekdays(sd, ed):
            if d in trading:
                rows += _fake_rows(d, n=n)
        return rows
    return fake


def test_ssf_backfill_endpoint_writes_rows_and_stays_within_a_month(tmp_path, monkeypatch):
    """`/api/ssf/backfill`：驗證真的寫進資料並回報筆數，且每一次實際打給
    `fetch_ssf_daily` 的區間都要在官方硬性限制（不可超過一個月，超過只會拿到 200
    的 HTML 警告頁而非資料）之內。刻意帶一個遠超合理範圍的 days 與 max_fetch，確認
    迴圈不會失控——這正是 clamp 要擋下的形狀。

    （2026-09 起回補改成「只補缺的交易日」、交易日曆取自 market_daily，所以這裡先種好
    交易日；沒有交易日曆時端點什麼都不抓，那是另一條測試的主題。斷言一條都沒放寬。）"""
    db = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db)
    conn = get_connection(db)
    init_db(conn)
    trading = _weekdays(_dt.date(2026, 3, 1), _dt.date(2026, 9, 18))   # 遠多於 days 上限
    _seed_trading_days(conn, trading)
    monkeypatch.setattr(H, "_now", lambda: _dt.datetime(2026, 9, 19, 10, 0))
    calls = []
    monkeypatch.setattr(ssf, "fetch_ssf_daily", _fake_fetch_for(trading, calls))
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/ssf/backfill?days=999999&max_fetch=999999")
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


def _backfill_client(tmp_path, monkeypatch, today, trading, stored=(), n=30):
    db = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db)
    conn = get_connection(db)
    init_db(conn)
    _seed_trading_days(conn, trading)
    if stored:
        from stocks_power_rich.db import bulk_upsert_ssf_daily
        rows = []
        for d in stored:
            rows += _fake_rows(d, n=n)
        bulk_upsert_ssf_daily(conn, ssf.summarize_ssf_days(rows))
    monkeypatch.setattr(H, "_now", lambda: today)
    calls = []
    monkeypatch.setattr(ssf, "fetch_ssf_daily", _fake_fetch_for(trading, calls, n=n))
    return TestClient(create_app()), calls


def test_ssf_backfill_only_fetches_missing_trading_days_and_converges(tmp_path, monkeypatch):
    """回補要像同類端點（chips/backfill、margin-maintenance/heal）一樣「重複呼叫直到
    remaining 不再下降」：只抓**缺的交易日**、新的先抓、每段不超過 14 個日曆天、已存的
    不重抓、每次最多 max_fetch 段。舊版每次都從今天往回走同一段，重打只會重抓同樣的
    資料，也沒有 remaining 可以告訴呼叫端還差多少。

    今天 2026-09-19（週六）；交易日 08-24～09-18 共 20 天；已存 09-14～09-18 → 缺 15 天。
    以 14 日曆天分段、新的先：第一段 08-31～09-11（10 天）、第二段 08-24～08-28（5 天）。"""
    trading = _weekdays(_dt.date(2026, 8, 24), _dt.date(2026, 9, 18))
    assert len(trading) == 20
    stored = [d for d in trading if d >= "2026-09-14"]
    client, calls = _backfill_client(tmp_path, monkeypatch, _dt.datetime(2026, 9, 19, 10, 0),
                                     trading, stored)

    first = client.get("/api/ssf/backfill?days=30&max_fetch=1").json()
    assert calls == [("2026/08/31", "2026/09/11")]
    assert first["fetched"] == 1
    assert first["remaining"] == 5

    second = client.get("/api/ssf/backfill?days=30&max_fetch=1").json()
    assert calls[1] == ("2026/08/24", "2026/08/28")
    assert second["remaining"] == 0
    assert second["dates"] == 20

    third = client.get("/api/ssf/backfill?days=30&max_fetch=1").json()
    assert len(calls) == 2                    # 補齊之後不再打期交所
    assert third["fetched"] == 0 and third["remaining"] == 0


def test_ssf_backfill_leaves_today_to_the_daily_job(tmp_path, monkeypatch):
    """今天交給每日排程（平日 17:15／18:15／20:15）。白天打回補時今天的日盤資料還沒
    發佈，把今天算進缺口的話 remaining 會整天卡在 1、看起來永遠「補不完」。"""
    trading = _weekdays(_dt.date(2026, 9, 14), _dt.date(2026, 9, 18))
    client, calls = _backfill_client(tmp_path, monkeypatch, _dt.datetime(2026, 9, 18, 10, 0),
                                     trading)
    body = client.get("/api/ssf/backfill?days=30").json()
    assert calls == [("2026/09/14", "2026/09/17")]
    assert body["remaining"] == 0


def test_ssf_backfill_says_so_when_there_is_no_trading_calendar(tmp_path, monkeypatch):
    """交易日曆來自 market_daily；它是空的（例如新部署還沒跑過 /api/backfill）時什麼都
    不會抓。要把原因講出來——只回 remaining=0 會讓人以為已經補齊。"""
    client, calls = _backfill_client(tmp_path, monkeypatch, _dt.datetime(2026, 9, 19, 10, 0),
                                     trading=[])
    body = client.get("/api/ssf/backfill?days=30").json()
    assert calls == []
    assert body["window_trading_days"] == 0
    assert body["fetched"] == 0
    assert "market_daily" in body["note"]


def test_refresh_groups_multi_date_response_by_date_before_summarizing(tmp_path, monkeypatch):
    """`fetch_ssf_daily` 一次回應本來就常橫跨多天（`refresh_ssf_daily` 自己也會一併重抓
    `[D-6, D]` 約 7 個日曆天的重疊區間）。`summarize_ssf_day` 用『該 root 第一次出現』的
    列決定日期與主力月價格，所以呼叫端**必須先依日期分組**再逐日呼叫——若整批一次丟給
    它，兩天的資料會被壓成同一天，且哪天的價格留下來純屬巧合（見 CLAUDE.md 對這支函式
    的說明）。

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


def _client(monkeypatch, tmp_path):
    # review M1：改用 monkeypatch.setenv，不直接改 os.environ——後者沒有 monkeypatch
    # fixture 的自動 teardown，會把這個測試的 tmp DB 路徑一路漏進同一個 pytest
    # session 裡跑在它之後、剛好沒有自己設定 SPR_DB_PATH 的其他測試。
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "api.sqlite"))
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


def _today_contracts_key():
    return f"ssf_contracts:{_dt.datetime.now().strftime('%Y-%m')}"


def _today_margin_key():
    return f"ssfmargin:v1:{_dt.datetime.now().strftime('%Y-%m-%d')}"


def _seed_ssf_cache_for_self_screen(conn):
    """review I3／Fix F：自算選股的股期參考欄改成只讀快取（`fetch=False`），不再
    透過 `_attach_ssf_margin` 的 `settlements=` 參數手動注入結算價——那個參數已經
    隨著改用共用的 `ssf_margin_index()` 一併移除。改成直接把合約表／保證金表寫進
    `ai_cache`（今天的鍵，貼近真實情境），再用 `bulk_upsert_ssf_daily` 寫入
    `ssf_daily` 供結算價查詢，同 `/api/ssf/margin` 端點測試（`_seed_margin`）的
    做法，只是這裡不必監聽 HTTP 端點、直接呼叫 `_attach_ssf_margin`。
    """
    from stocks_power_rich.db import set_ai_cache, bulk_upsert_ssf_daily
    set_ai_cache(conn, _today_contracts_key(), _CONTRACTS)
    set_ai_cache(conn, _today_margin_key(), _MARGIN)
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "CD", "main_month": "202610", "settlement": 2432.0,
         "close": 2433.0, "chg_pct": 1.33, "volume": 8192, "oi": 25045, "oi_total": 25045,
         "main_volume": 6027},
        {"date": "2026-09-17", "root": "QF", "main_month": "202610", "settlement": 2432.0,
         "close": 2434.0, "chg_pct": 1.37, "volume": 36062, "oi": 53174, "oi_total": 53174,
         "main_volume": 26032},
    ])


def test_self_screen_rows_carry_the_stock_futures_margin(tmp_path):
    """自算選股表的參考欄：有沒有股期、1 口要多少錢。"""
    from stocks_power_rich.api import admin as A
    conn = get_connection(str(tmp_path / "x.sqlite"))
    init_db(conn)
    _seed_ssf_cache_for_self_screen(conn)
    rows = [{"code": "2330", "mu_value": 90}, {"code": "6488", "mu_value": 80}]
    out = A._attach_ssf_margin(conn, rows)
    assert out[0]["ssf"] is True and out[0]["ssf_margin"] == 656640   # 取標準約，非小型
    assert out[1]["ssf"] is False and out[1]["ssf_margin"] is None


def test_attaching_ssf_margin_never_changes_the_screening_result(tmp_path):
    """它是參考欄：不進篩選、不進計分，只多兩個鍵。"""
    from stocks_power_rich.api import admin as A
    conn = get_connection(str(tmp_path / "y.sqlite"))
    init_db(conn)
    _seed_ssf_cache_for_self_screen(conn)
    rows = [{"code": "2330", "mu_value": 90, "mu_score": 12}]
    before = dict(rows[0])
    out = A._attach_ssf_margin(conn, rows)
    assert len(out) == 1
    assert {k: v for k, v in out[0].items() if k not in ("ssf", "ssf_margin")} == before


def test_attach_ssf_margin_returns_immediately_when_there_are_no_rows(monkeypatch, tmp_path):
    """review I3／Fix F：沒有入選股時（例如當天全市場自算 0 檔入選）應該立刻回傳，
    連 ai_cache 都不必查——用會 raise 的樁確認完全沒有觸碰任何一個可能連外的
    路徑（即使快取是空的，`_ssf_contracts`／`_ssf_margin_table` 的 fetch=True
    分支也可能被誤觸而連外，這裡連讀都不該讀）。
    """
    from stocks_power_rich.api import admin as A
    conn = get_connection(str(tmp_path / "z.sqlite"))
    init_db(conn)
    monkeypatch.setattr(ssf, "fetch_ssf_contract_map",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：contract map")))
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：margin table")))
    assert A._attach_ssf_margin(conn, []) == []


def test_attach_ssf_margin_makes_zero_network_calls_even_with_a_completely_empty_cache(
        monkeypatch, tmp_path):
    """review I3／Fix F：自算選股頁的股期參考欄只是純參考欄，不該讓整頁陪著 TAIFEX
    抽風——即使快取完全是空的（今天第一個打開這頁的人，也還沒有人開過股期概況頁
    暖過快取），也絕不能連外。用會 raise 的樁而非回傳空字典的樁，才能真的證明
    程式路徑不會呼叫到它們，而不是呼叫了、只是被既有的容錯路徑接住。
    """
    from stocks_power_rich.api import admin as A
    conn = get_connection(str(tmp_path / "empty.sqlite"))
    init_db(conn)
    monkeypatch.setattr(ssf, "fetch_ssf_contract_map",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：contract map")))
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：margin table")))
    rows = [{"code": "2330"}]
    A._attach_ssf_margin(conn, rows)
    assert rows[0]["ssf"] is False
    assert rows[0]["ssf_margin"] is None


def test_attach_ssf_margin_never_falls_back_to_the_mini_when_the_standard_is_unpriced(
        tmp_path):
    """review I3 重現：標準合約當天查無結算價時，舊碼的迴圈會退而求其次改用小型
    合約算出的金額（32,832，只有標準合約真正金額 656,640 的 1/20），畫面上卻沒有
    任何跡象顯示這其實是小型的數字。自算選股的參考欄只認標準合約，查不到就是
    None，絕不能安靜地換成別的合約規模。
    """
    from stocks_power_rich.api import admin as A
    from stocks_power_rich.db import set_ai_cache, bulk_upsert_ssf_daily
    conn = get_connection(str(tmp_path / "fallback.sqlite"))
    init_db(conn)
    set_ai_cache(conn, _today_contracts_key(), _CONTRACTS)
    set_ai_cache(conn, _today_margin_key(), _MARGIN)
    # 只有小型（QF）有結算價，標準（CD）當天缺列。
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "QF", "main_month": "202610", "settlement": 2432.0,
         "close": 2434.0, "chg_pct": 1.37, "volume": 36062, "oi": 53174, "oi_total": 53174,
         "main_volume": 26032},
    ])
    rows = [{"code": "2330"}]
    A._attach_ssf_margin(conn, rows)
    assert rows[0]["ssf"] is True             # 這檔標的仍有股期（有小型合約）
    assert rows[0]["ssf_margin"] is None       # 但不可退而求其次變成小型的 32,832


def test_ssf_contracts_fetch_false_never_touches_network_and_falls_back_to_a_stale_cache(
        tmp_path, monkeypatch):
    """`fetch=False`（自算選股參考欄用）只讀快取：今天的鍵沒有就退回最近一次存過
    的舊快取，絕不連外。這裡故意只種一個很久以前的月份，證明『沒有今天的』時
    不會被當成完全沒有資料而白白浪費既有的合約表。

    退回的舊快取本身也要通過合理性檢查（final review Fix 3），所以這裡塞的是一份
    達到 `MIN_PLAUSIBLE_CONTRACTS` 筆數的合約表，而不是只有 3 檔的 `_CONTRACTS`
    ——否則會被新加的守衛擋下，測不到這裡真正要驗證的『退回舊快取』行為（見下一條
    測試：筆數不足時該被擋下）。
    """
    conn = _db(tmp_path)
    from stocks_power_rich.db import set_ai_cache
    stale = {f"Z{i:03d}": {"code": f"9{i:03d}", "stock_name": f"測試股{i}",
                           "name": f"測試股{i}期貨", "multiplier": 2000,
                           "is_etf": False, "is_mini": False}
             for i in range(ssf.MIN_PLAUSIBLE_CONTRACTS)}
    set_ai_cache(conn, "ssf_contracts:2020-01", stale)
    monkeypatch.setattr(ssf, "fetch_ssf_contract_map",
                        lambda: (_ for _ in ()).throw(AssertionError("fetch=False 不該連外")))
    assert H._ssf_contracts(conn, fetch=False) == stale


def test_ssf_contracts_fetch_false_fallback_rejects_an_implausibly_small_stale_cache(
        tmp_path, monkeypatch):
    """final review Fix 3：退回的舊快取要通過跟 `fetch=True` 一樣的筆數守衛。
    `latest_ai_cache_with_prefix` 本身不做合理性判斷，若原樣放行，一份只有 3 檔
    （遠低於 `MIN_PLAUSIBLE_CONTRACTS`，八成是某次半套寫入的殘留）的舊快取，會被
    當成『有股期資料』原樣交給呼叫端，讓退路繞過「讀取端也要守衛筆數」這條規矩。
    """
    conn = _db(tmp_path)
    from stocks_power_rich.db import set_ai_cache
    set_ai_cache(conn, "ssf_contracts:2020-01", _CONTRACTS)   # 只有 3 檔，不合理
    monkeypatch.setattr(ssf, "fetch_ssf_contract_map",
                        lambda: (_ for _ in ()).throw(AssertionError("fetch=False 不該連外")))
    assert H._ssf_contracts(conn, fetch=False) == {}


def test_ssf_margin_table_fetch_false_never_touches_network_and_falls_back_to_a_stale_cache(
        tmp_path, monkeypatch):
    """同上一條，換保證金表：`fetch=False` 找不到今天的鍵時退回最近一次存過的。"""
    conn = _db(tmp_path)
    from stocks_power_rich.db import set_ai_cache
    set_ai_cache(conn, "ssfmargin:v1:2020-01-01", _MARGIN)
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table",
                        lambda: (_ for _ in ()).throw(AssertionError("fetch=False 不該連外")))
    assert H._ssf_margin_table(conn, fetch=False) == _MARGIN


def test_ssf_margin_table_fetch_false_fallback_rejects_a_stale_cache_missing_stock_updated(
        tmp_path, monkeypatch):
    """final review Fix 3：退回的舊快取一樣要有 `stock_updated`——缺這個鍵的快取
    在主要路徑（今天的鍵）本來就被判定未命中，退路不能繞過同一條規則原樣放行。
    """
    conn = _db(tmp_path)
    from stocks_power_rich.db import set_ai_cache
    set_ai_cache(conn, "ssfmargin:v1:2020-01-01", {"stock": _MARGIN["stock"]})   # 缺 stock_updated
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table",
                        lambda: (_ for _ in ()).throw(AssertionError("fetch=False 不該連外")))
    assert H._ssf_margin_table(conn, fetch=False) == {}


def test_ssf_margin_table_fetch_true_enters_cooldown_after_a_failed_fetch(tmp_path, monkeypatch):
    """TAIFEX 持續失敗時，`fetch=True`（股期概況頁／排程）不該每次呼叫都重打一次
    可能 30 秒逾時的請求——比照既有 `_osfut_cooling_down` 的做法：失敗一次後在
    冷卻期間內不再重試。這裡完全沒有任何舊快取可退，兩次呼叫都回 `{}` 是正確
    答案（見下一條測試：有舊快取可退時不該回 `{}`）。
    """
    conn = _db(tmp_path)
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        return {}
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table", fail)
    assert H._ssf_margin_table(conn, fetch=True) == {}
    assert calls["n"] == 1
    assert H._ssf_margin_table(conn, fetch=True) == {}   # 冷卻中，不重打
    assert calls["n"] == 1


def test_ssf_margin_table_fetch_true_falls_back_to_a_stale_cache_instead_of_going_blank(
        tmp_path, monkeypatch):
    """final review Fix 2：抓取失敗（或冷卻中）不該讓 `/api/ssf/margin` 與個股頁的
    保證金列整段空白——`fetch=True` 應該比照 `fetch=False` 退回『最近一次存過、且
    通過合理性檢查』的表，而不是回 `{}`。這裡種一筆『昨天』成功過的快取，模擬
    今天抓取失敗、但過去確實成功過的情況；第二次呼叫落在冷卻期間內，一樣要退回
    同一份舊表，且不該再打一次網路。
    """
    conn = _db(tmp_path)
    from stocks_power_rich.db import set_ai_cache
    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    set_ai_cache(conn, f"ssfmargin:v1:{yesterday}", _MARGIN)
    calls = {"n": 0}

    def fail():
        calls["n"] += 1
        return {}
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table", fail)
    assert H._ssf_margin_table(conn, fetch=True) == _MARGIN   # 第一次：抓取失敗，退回昨天
    assert calls["n"] == 1
    assert H._ssf_margin_table(conn, fetch=True) == _MARGIN   # 第二次：冷卻中，仍退回昨天
    assert calls["n"] == 1                                    # 不重打


def test_refresh_ssf_daily_also_warms_the_contract_map_cache(tmp_path, monkeypatch):
    """cache-only 的自算選股參考欄要有資料可讀，前提是**有人**暖過快取——`ssf_daily`
    排程已經在暖保證金表，合約對照表也要一併暖，否則從沒開過股期概況頁時，
    這欄會永遠顯示不出東西。
    """
    conn = _db(tmp_path)
    calls = {"n": 0}

    def fake_contracts(c):
        calls["n"] += 1
        return {}
    monkeypatch.setattr(H, "_ssf_contracts", fake_contracts)
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: [])
    H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert calls["n"] == 1


def test_picks_self_screen_endpoint_with_no_picks_makes_zero_taifex_calls(tmp_path, monkeypatch):
    """整條 `/api/picks/self-screen` 請求路徑最終都會呼叫 `_attach_ssf_margin`；
    候選池為空時它應該立刻回傳、連 ai_cache 都不查（見對應的單元測試），這裡從
    端點層級再驗證一次，確認 `picks_self_screen` 真的把空 rows 一路傳到底。

    需要一筆 `market_daily` 列讓 `_latest_date` 有日期可選——完全空的資料庫會讓
    `chosen` 是 None、連帶讓 `annotate_new_entries`（與這次修復無關的既有路徑）
    在算集保週期時對 `None` 呼叫 `date.fromisoformat` 而炸掉，那是另一個問題，
    不是本測試要驗證的對象。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import admin as A
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO market_daily (date, taiex) VALUES ('2026-09-17', 1.0)")
    conn.commit()
    monkeypatch.setattr(A, "_industry_map", lambda c: {})
    monkeypatch.setattr(A, "_otc_industry", lambda c: {})
    monkeypatch.setattr(ssf, "fetch_ssf_contract_map",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：contract map")))
    monkeypatch.setattr(ssf, "fetch_ssf_margin_table",
                        lambda: (_ for _ in ()).throw(AssertionError("不該連外：margin table")))
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/picks/self-screen")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def _seed_margin(monkeypatch, tmp_path):
    # `/api/ssf/margin` 端點（Fix F 之後）委派給共用的 `H.ssf_margin_index`，它內部
    # 呼叫的是 helpers 自己的 `_ssf_contracts`／`_ssf_margin_table`，不是
    # market.py 匯入的那份副本——樁在 `M` 上對它沒有作用，要樁在 `H` 上。
    monkeypatch.setattr(H, "_ssf_contracts", lambda c, fetch=True: _CONTRACTS)
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c, fetch=True: _MARGIN)
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


def test_margin_endpoint_excludes_option_rows_from_the_index_list(monkeypatch, tmp_path):
    """指數保證金 CSV 裡混著選擇權列（實檔就有「臺指選擇權風險保證金(A)值」，
    `parse_index_margining_csv` 不會預先濾掉它）。選擇權不是期貨、沒有「1 口原始
    保證金」可言，不可出現在試算表的指數列裡。"""
    _seed_margin(monkeypatch, tmp_path)
    with_option = {**_MARGIN, "index": {
        **_MARGIN["index"],
        "臺指選擇權風險保證金(A)值": {"clearing": 138000, "maintenance": 143000, "initial": 187000}}}
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c, fetch=True: with_option)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    names = [x["name"] for x in d["index"]]
    assert not any("選擇權" in n for n in names)
    assert any("微型" in n for n in names)          # 期貨列照常在


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


def test_overview_basis_uses_the_standard_contracts_price_and_label_even_when_mini_out_trades_it(
        monkeypatch, tmp_path):
    """review #2／Fix D：去重原本保留成交量較高的合約——2330 的小型合約經常量比
    標準合約大，會讓這一列的結算價、主力月與標籤全部來自小型合約（標成「小型
    台積電」）。但期現價差是**標的**的屬性，標準與小型指的是同一檔股票，不該
    因為哪個合約成交量比較大而換人代表。改成一律從標準合約（`is_mini=False`）
    取結算價與主力月，標籤固定用合約表既有的 `stock_name`。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {"2330": {"close": 2400.0}})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "CD", "main_month": "202610", "settlement": 2410.0,
         "close": 2410.0, "chg_pct": 1.0, "volume": 3000, "oi": 100, "oi_total": 100,
         "main_volume": 3000},
        {"date": "2026-09-17", "root": "QF", "main_month": "202611", "settlement": 2450.0,
         "close": 2450.0, "chg_pct": 1.0, "volume": 50000, "oi": 200, "oi_total": 200,
         "main_volume": 50000},
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["code"] for x in d["basis"]] == ["2330"]
    row = d["basis"][0]
    assert row["name"] == "台積電"          # stock_name，不是小型合約的「小型台積電」
    assert row["root"] == "CD"              # 標準合約的 root
    assert row["futures"] == 2410.0         # 標準合約的結算價，不是量大的小型 2450.0
    assert row["main_month"] == "202610"    # 標準合約的主力月，不是小型的 202611


def test_overview_basis_ranks_underlyings_by_combined_volume_not_a_single_contract(
        monkeypatch, tmp_path):
    """排名要看『同一檔標的所有合約加總的成交量』，不是『成交量最高的那一個合約』。

    A 的標準合約(3000)單獨看比 B(3050)少，但 A 還有一個小型合約(100)，兩者相加
    (3100) 超過 B——舊排序法直接照 `today_rows`（單一合約、逐列排序）走訪，
    B 因單一合約量較大而先出現、代表它的 code 先被記入 `seen`，排名變成
    [B, A]；用合計成交量排名應該是 [A, B]，兩種答案不同才能驗證真的用了合計。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    contracts = {
        "AS": {"code": "1111", "stock_name": "A股", "name": "A股",
               "multiplier": 2000, "is_etf": False, "is_mini": False},
        "AM": {"code": "1111", "stock_name": "A股", "name": "小型A股",
               "multiplier": 100, "is_etf": False, "is_mini": True},
        "BS": {"code": "2222", "stock_name": "B股", "name": "B股",
               "multiplier": 2000, "is_etf": False, "is_mini": False},
    }
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: contracts)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {
        "1111": {"close": 100.0}, "2222": {"close": 200.0}})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "AS", "main_month": "202610", "settlement": 101.0,
         "close": 101.0, "chg_pct": 1.0, "volume": 3000, "oi": 10, "oi_total": 10,
         "main_volume": 3000},
        {"date": "2026-09-17", "root": "AM", "main_month": "202610", "settlement": 101.0,
         "close": 101.0, "chg_pct": 1.0, "volume": 100, "oi": 10, "oi_total": 10,
         "main_volume": 100},
        {"date": "2026-09-17", "root": "BS", "main_month": "202610", "settlement": 201.0,
         "close": 201.0, "chg_pct": 1.0, "volume": 3050, "oi": 10, "oi_total": 10,
         "main_volume": 3050},
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["code"] for x in d["basis"]] == ["1111", "2222"]


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
    減少。數字取自 review I1 實測換月當天真實官方回應算出的重現案例，不是隨手編的。
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


def test_overview_basis_counts_a_missing_standard_contract_instead_of_skipping_silently(
        monkeypatch, tmp_path):
    """final review Fix 4：一檔標的當天出現的合約全部是小型（沒有標準合約）理論上
    不會發生——每個掛牌標的都有一個標準合約——但『不會發生』不代表『不必被看見』。
    這裡刻意只給一檔小型合約、不給對應的標準合約，驗證它被排除在期現價差表之外的
    同時，缺口有被算進一個看得到的 coverage 計數器，而不是悄悄 continue 掉。
    """
    from stocks_power_rich.api import market as M
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    contracts = {
        "ZM": {"code": "3333", "stock_name": "Z股", "name": "小型Z股",
               "multiplier": 100, "is_etf": False, "is_mini": True},
    }
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: contracts)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {"3333": {"close": 100.0}})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "ZM", "main_month": "202610", "settlement": 101.0,
         "close": 101.0, "chg_pct": 1.0, "volume": 500, "oi": 10, "oi_total": 10,
         "main_volume": 500},
    ])
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["basis"] == []                            # 沒有標準合約，這檔不列進期現價差
    assert d["coverage"]["no_std_contract"] == 1        # 但缺口要看得見，不能悄悄消失


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
    """沒有任何資料時，coverage 仍要帶滿六個鍵——有資料時的路徑永遠會給
    roots/no_stock_code/no_spot/no_std_contract/lag_trading_days，前端一律讀這幾個
    鍵，空資料庫若少了任何一個會直接 KeyError。

    **契約變更（review I6／Fix E）**：新增 `lag_trading_days`，斷言字典跟著補上
    這個鍵（值 0——沒有 SSF 資料日可比較，沒有落後可言，同 stored_days 的 0 是
    同一種「查無資料」的預設值，不是「資料是最新的」那種 0）。

    **契約變更（final review Fix 4）**：新增 `no_std_contract`（一檔標的當天全部
    合約都缺標準合約時的計數），空資料庫時同樣回 0。
    """
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] is None
    assert d["coverage"] == {"stored_days": 0, "roots": 0, "no_stock_code": 0,
                             "no_spot": 0, "no_std_contract": 0, "lag_trading_days": 0}


def test_overview_reports_trading_day_lag_not_calendar_days(monkeypatch, tmp_path):
    """freshness 徽章要看『交易日』落後幾天，不是日曆天——跨週末不是真正的落後。
    這裡跟總覽 `renderFreshness` 不是同一招：`renderFreshness` 的文案本身仍是
    日曆天，只有底色吃後端已經考慮週末的 `data_stale`（見 CLAUDE.md「資料新鮮度
    徽章」一節）；股期頁沒有 `data_stale` 可借，所以文字與顏色都直接吃交易日落差。

    SSF 資料只到 2026-09-17（週四）；market_daily 之後有 09-18（週五）與 09-22
    （週一，中間的 09-19/20 週末與 09-21 那個「假日」都刻意不建列，模擬真實的
    交易日曆只在真的開盤那天才有列）兩個有加權指數的交易日——落後天數要是 2，
    不能把跳過的週末／假日也算進去。
    """
    _seed_overview(monkeypatch, tmp_path)
    conn = get_connection(str(tmp_path / "api.sqlite"))
    for d in ("2026-09-18", "2026-09-22"):
        conn.execute("INSERT INTO market_daily (date, taiex) VALUES (?, 1.0)", (d,))
    conn.commit()
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] == "2026-09-17"
    assert d["coverage"]["lag_trading_days"] == 2


def test_overview_lag_is_zero_when_ssf_data_is_current(monkeypatch, tmp_path):
    """market_daily 沒有比 SSF 資料日更晚的交易日時，落後天數是 0（資料是最新的）。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["coverage"]["lag_trading_days"] == 0


# ── _ssf_missing_spans：純函式，直接測（端點測試只間接走到它）─────────────────────
def test_ssf_missing_spans_edges():
    from stocks_power_rich.api.admin import _ssf_missing_spans
    assert _ssf_missing_spans([]) == []
    # 單一天自成一段（頭尾同一天）
    assert _ssf_missing_spans(["2026-09-18"]) == [("2026-09-18", "2026-09-18")]
    # 新到舊的連續交易日併成一段，回傳 (最舊, 最新)
    assert _ssf_missing_spans(["2026-09-18", "2026-09-17", "2026-09-16"]) == [
        ("2026-09-16", "2026-09-18")]


def test_ssf_missing_spans_caps_each_span_at_14_calendar_days_inclusive():
    from stocks_power_rich.api.admin import _ssf_missing_spans
    # 頭尾差 13 天＝含頭尾 14 個日曆天，仍是同一段
    assert _ssf_missing_spans(["2026-09-18", "2026-09-05"]) == [("2026-09-05", "2026-09-18")]
    # 差 14 天＝15 個日曆天，超過上限，要切成兩段（新的那段在前）
    assert _ssf_missing_spans(["2026-09-18", "2026-09-04"]) == [
        ("2026-09-18", "2026-09-18"), ("2026-09-04", "2026-09-04")]
    # 上限量的是「這一段的最新日」到候選日，不是相鄰兩天的間隔：
    # 09-18→09-10→09-04 相鄰間隔都 <14，但 09-04 距這段最新日 09-18 已 14 天
    assert _ssf_missing_spans(["2026-09-18", "2026-09-10", "2026-09-04"]) == [
        ("2026-09-10", "2026-09-18"), ("2026-09-04", "2026-09-04")]


def test_ssf_missing_spans_respects_custom_max_days():
    from stocks_power_rich.api.admin import _ssf_missing_spans
    days = ["2026-09-18", "2026-09-17", "2026-09-16", "2026-09-15"]
    assert _ssf_missing_spans(days, max_days=2) == [
        ("2026-09-17", "2026-09-18"), ("2026-09-15", "2026-09-16")]
