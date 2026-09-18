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
