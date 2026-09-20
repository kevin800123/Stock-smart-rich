"""集保股數／人均數（docs/superpowers/specs/2026-09-20-stock-combo-chart-design.md）。"""
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db
from stocks_power_rich.sources import tdcc


CSV_HEAD = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
# 級 1（散戶）、級 12～15（400 張↑，其中 15 是千張大戶）、級 17（合計，兩來源編號不同，必須排除）
CSV_BODY = (
    "20260918,2330,1,1000,500000,5.00\n"
    "20260918,2330,12,20,300000,3.00\n"
    "20260918,2330,13,10,400000,4.00\n"
    "20260918,2330,14,5,600000,6.00\n"
    "20260918,2330,15,3,8200000,82.00\n"
    "20260918,2330,17,1038,10000000,100.00\n"
)


def test_parse_custody_distribution_sums_shares_of_levels_1_to_15():
    d = tdcc.parse_custody_distribution(CSV_HEAD + CSV_BODY)
    rec = d["data"]["2330"]
    assert d["week_date"] == "2026-09-18"
    assert rec["total_holders"] == 1038            # 1000+20+10+5+3，不含第 17 級合計列
    assert rec["total_shares"] == 10000000         # 同樣不含合計列（否則會是兩倍）
    assert rec["big1000_pct"] == 82.0 and rec["big400_pct"] == 95.0


def test_parse_custody_ownership_html_also_returns_total_shares():
    rows = [("1", "1-999", "1,000", "500,000", "5.00"),
            ("15", "1,000以上", "3", "8,200,000", "82.00"),
            ("16", "合計", "1,003", "8,700,000", "87.00")]   # 智能網的合計列是第 16 級
    html = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    rec = tdcc.parse_custody_ownership_html(html)
    assert rec["total_holders"] == 1003
    assert rec["total_shares"] == 8700000          # 只加 1 與 15 兩級，剛好等於合計，但不是讀合計列
    assert rec["big1000_pct"] == 82.0


def test_aggregate_levels_without_shares_degrades_to_zero():
    """股數欄解析不出來（來源改版）時不可整筆炸掉，總股數算 0、其餘照常。"""
    rec = tdcc._aggregate_levels([("1", 100, None, 1.0), ("15", 2, None, 80.0)])
    assert rec["total_shares"] == 0 and rec["total_holders"] == 102 and rec["big1000_pct"] == 80.0


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


def test_upsert_custody_stores_holders_and_shares_and_trend_returns_avg(conn):
    db.upsert_custody(conn, "2026-09-11", "2330", {"big1000_pct": 77.0, "big400_pct": 82.0,
                                                   "big_holders": 200, "total_holders": 1000,
                                                   "total_shares": 9000000})
    db.upsert_custody(conn, "2026-09-18", "2330", {"big1000_pct": 77.9, "big400_pct": 83.0,
                                                   "big_holders": 205, "total_holders": 1250,
                                                   "total_shares": 10000000})
    trend = db.get_custody_trend(conn, "2330")
    assert [t["week"] for t in trend] == ["2026-09-11", "2026-09-18"]
    assert trend[0]["total_holders"] == 1000 and trend[0]["total_shares"] == 9000000
    assert trend[0]["avg_shares"] == 9000 and trend[1]["avg_shares"] == 8000   # 人均數下降＝籌碼分散


def test_custody_trend_avg_is_none_when_inputs_are_missing(conn):
    """算不出來就回 None，不要拿 0 或舊值頂替（同全站『算不出回 None』的慣例）。"""
    db.upsert_custody(conn, "2026-09-04", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10})          # 舊資料：沒有人數與股數
    db.upsert_custody(conn, "2026-09-11", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10, "total_holders": 0,
                                                   "total_shares": 500})        # 人數 0 不可除
    assert [t["avg_shares"] for t in db.get_custody_trend(conn, "1101")] == [None, None]


def test_custody_total_shares_column_migrates_on_an_old_table(tmp_path, monkeypatch):
    """既有部署的 custody_dist 沒有 total_shares 欄，init_db 要能就地補上且不動既有資料。"""
    path = str(tmp_path / "old.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    c.execute("CREATE TABLE custody_dist (week TEXT, code TEXT, big1000_pct REAL, "
              "big400_pct REAL, big_holders REAL, PRIMARY KEY(week, code))")
    c.execute("INSERT INTO custody_dist VALUES ('2026-09-11','2330',77.0,82.0,200)")
    c.commit()
    db.init_db(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(custody_dist)")}
    assert "total_shares" in cols and "total_holders" in cols
    assert db.get_custody_trend(c, "2330")[0]["big1000_pct"] == 77.0   # 既有列還在


def _client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "api.sqlite"))
    from stocks_power_rich.main import create_app
    return TestClient(create_app(enable_scheduler=False))


def _seed_weeks(c, code, n, with_shares=True, end="2026-09-18"):
    db.init_db(c)  # 端點測試直接開連線寫入，早於任何請求觸發 deps.conn() 的 lazy init_db
    d = date.fromisoformat(end)
    for i in range(n):
        wk = (d - timedelta(days=7 * i)).isoformat()
        rec = {"big1000_pct": 70.0 + i * 0.1, "big400_pct": 80.0, "big_holders": 100,
               "total_holders": 1000 + i}
        if with_shares:
            rec["total_shares"] = 9000000
        db.upsert_custody(c, wk, code, rec)


def test_custody_endpoint_returns_avg_shares_and_week_count(monkeypatch, tmp_path):
    from stocks_power_rich.api import stock as S
    from stocks_power_rich.sources import tdcc as T
    monkeypatch.setattr(T, "fetch_custody_distribution", lambda: {"week_date": None, "data": {}})
    monkeypatch.setattr(S, "_start_custody_autofill", lambda code: False)
    client = _client(monkeypatch, tmp_path)
    c = db.get_connection(str(tmp_path / "api.sqlite"))
    _seed_weeks(c, "2330", 40)
    d = client.get("/api/stock/2330.TW/custody").json()
    assert d["weeks"] == 40 and d["backfilling"] is False
    assert d["trend"][-1]["avg_shares"] == round(9000000 / d["trend"][-1]["total_holders"])


@pytest.mark.real_custody_autofill
def test_custody_endpoint_returns_200_even_if_autofill_decision_raises(monkeypatch, tmp_path):
    """補歷史的判斷本身失敗，不能讓端點跟著死掉——trend 早就算好了，這是這個功能的承諾
    （「端點立刻回現有資料」）。反證：拿掉 stock.py 裡包住 _start_custody_autofill 呼叫的
    try/except，這條測試會變成 500。

    標 `real_custody_autofill` 是為了保險而非必要：這條測試自己把 `S._start_custody_autofill`
    換成 `_boom`，那個 setattr 發生在 conftest 的 `_no_custody_autofill` 之後、蓋過它的
    no-op 樁，所以拿掉這個標記結果不變——但標上去可以避免日後有人動這條測試時，
    不小心刪掉 `_boom` 那行卻沒發現其實一直是靠 conftest 的樁頂著。"""
    from stocks_power_rich.api import stock as S
    from stocks_power_rich.sources import tdcc as T
    monkeypatch.setattr(T, "fetch_custody_distribution", lambda: {"week_date": None, "data": {}})

    def _boom(code):
        raise RuntimeError("boom")

    monkeypatch.setattr(S, "_start_custody_autofill", _boom)
    client = _client(monkeypatch, tmp_path)
    c = db.get_connection(str(tmp_path / "api.sqlite"))
    _seed_weeks(c, "2330", 5)   # 太短 → 一定會觸發 autofill 判斷
    resp = client.get("/api/stock/2330.TW/custody")
    assert resp.status_code == 200
    d = resp.json()
    assert d["weeks"] == 5
    assert d["backfilling"] is False


@pytest.mark.parametrize("weeks,with_shares,expect", [
    (40, True, False),    # 夠長又有股數 → 不補
    (5, True, True),      # 太短 → 補
    (40, False, True),    # 夠長但整片沒有股數（舊資料）→ 補，否則人均數永遠算不出來
])
def test_autofill_decision(conn, weeks, with_shares, expect):
    from stocks_power_rich.api import stock as S
    _seed_weeks(conn, "2330", weeks, with_shares=with_shares)
    assert S._should_autofill(db.get_custody_trend(conn, "2330")) is expect


@pytest.mark.real_custody_autofill
def test_autofill_runs_once_per_code_per_day(conn, monkeypatch):
    """同一天同一檔只補一次——手動連查很多檔時不可以連打 TDCC 智能網。

    標 `real_custody_autofill`：這條測試呼叫的是真正的 `S._start_custody_autofill`
    （驗證節流邏輯本身），conftest 的 `_no_custody_autofill` 預設會把它樁成永遠
    回傳 `False` 的 no-op——不退出的話這裡的斷言（第一次 True、第二次 False、
    `started` 長度 1）就失去意義。`S.threading.Thread` 另外被樁掉讓背景工作本體
    （會呼叫 tdcc）不會真的執行，所以退出這一層不會連外。"""
    from stocks_power_rich.api import stock as S
    monkeypatch.setattr(S, "_custody_autofill", set())   # 給乾淨的集合，不留到下一條測試
    started = []
    # 測試替身要跟著真實介面走：production 呼叫 threading.Thread 時 target/args/daemon/name
    # 全是關鍵字引數，這裡用 **kw 全接住，不是反過來假設 production 只會傳 target。
    monkeypatch.setattr(S.threading, "Thread",
                        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw.get("name"))})())
    monkeypatch.setattr(S, "conn", lambda: conn)
    assert S._start_custody_autofill("2330") is True
    assert S._start_custody_autofill("2330") is False
    assert len(started) == 1


@pytest.mark.real_custody_autofill
def test_start_custody_autofill_is_atomic_under_concurrent_calls(tmp_path, monkeypatch):
    """check-then-act 節流必須是原子的：兩個幾乎同時呼叫 _start_custody_autofill("2330")
    的執行緒，只能有一個真的起跑（_autofill_guard 包住整段檢查＋標記＋起執行緒）。

    反證（拿掉 _autofill_guard 後手動驗證，見任務報告）：兩個真執行緒同時通過
    「還沒補過」的檢查，各自把 started 加一筆，這條測試會變紅（started 長度 2）。

    標 `real_custody_autofill`：這條測試呼叫真正的 `S._start_custody_autofill`
    驗證原子性，退出 conftest 的 `_no_custody_autofill` no-op 樁才能測到實際邏輯。
    `S.threading.Thread` 同樣被樁掉，背景工作本體不會真的執行，退出這一層不會連外。
    """
    import threading as real_threading

    from stocks_power_rich.api import stock as S

    # S.threading 就是這個 threading 模組本身（同一個物件），monkeypatch 它的 Thread
    # 會連測試自己驅動的兩條執行緒都一起被換掉——所以要先留一份真的 Thread 類別，
    # 測試驅動用真的、production 呼叫的那個用假的。
    RealThread = real_threading.Thread

    path = str(tmp_path / "concurrent.sqlite")
    db.init_db(db.get_connection(path))
    # 每次呼叫 conn() 都開一條新連線——sqlite 連線不能跨執行緒共用，兩個真執行緒
    # 各自呼叫 S.conn() 時，開連線這個動作本身就發生在呼叫端自己的執行緒裡。
    monkeypatch.setattr(S, "conn", lambda: db.get_connection(path))
    monkeypatch.setattr(S, "_custody_autofill", set())
    started = []
    monkeypatch.setattr(S.threading, "Thread",
                        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw.get("name"))})())

    barrier = real_threading.Barrier(2)
    results = []

    def call():
        barrier.wait()   # 讓兩個執行緒盡量同時進入 _start_custody_autofill
        results.append(S._start_custody_autofill("2330"))

    t1 = RealThread(target=call)
    t2 = RealThread(target=call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [False, True]
    assert len(started) == 1


def test_custody_autofill_job_refetches_weeks_missing_total_shares(conn, monkeypatch):
    """final review I1：_should_autofill 的第二個條件是「有股數的週數 < 已存週數的一半」，
    但舊版 `want` 只濾掉 `have_weeks`（已存在的週），已有列但缺 total_shares 的週永遠
    不會被排進 want——這條件因此是永久 no-op，avg_shares 永遠算不出來。

    反證：把 want 的算法改回舊版（只濾 have_weeks，拿掉「或缺股數」那個條件），這條測試
    會轉紅——`captured["weeks"]` 會是空list（2026-09-11 已經 have，被濾掉），
    avg_shares 也補不到 9000。
    """
    from stocks_power_rich.api import stock as S
    from stocks_power_rich.sources import tdcc as T

    # 種一週「已有列但缺股數」——模擬舊資料，或 total_shares 欄位剛加不久還沒補到。
    db.upsert_custody(conn, "2026-09-11", "2330", {"big1000_pct": 70.0, "big400_pct": 80.0,
                                                    "big_holders": 100, "total_holders": 1000})
    monkeypatch.setattr(T, "fetch_custody_weeks", lambda: ["20260911"])

    captured = {}

    def _fake_history(code, weeks=None, max_weeks=60):
        captured["weeks"] = weeks
        return {"2026-09-11": {"big1000_pct": 71.0, "big400_pct": 81.0, "big_holders": 101,
                                "total_holders": 1000, "total_shares": 9000000}}

    monkeypatch.setattr(T, "fetch_custody_history", _fake_history)
    monkeypatch.setattr(S, "get_connection", lambda path: conn)
    monkeypatch.setattr(S, "_custody_autofill", set())

    S._custody_autofill_job("2330")

    assert captured["weeks"] == ["20260911"]      # 缺股數的既有週被排進 want，真的重抓了
    trend = db.get_custody_trend(conn, "2330")
    assert trend[0]["total_shares"] == 9000000 and trend[0]["avg_shares"] == 9000


def test_autofill_job_clears_the_daily_mark_when_lock_is_busy(conn, monkeypatch):
    """final review I2：`_start_custody_autofill` 在**還沒確認搶得到鎖**之前就把
    `custodyauto:{code}:{date}` 節流鍵寫進 ai_cache；若背景執行緒搶不到鎖（`_custody_lock`
    被另一檔佔用），舊版直接 log 後 return，那把節流鍵留著不動——這檔股票當天再也不會被
    排進佇列，即使它其實從沒真的補到任何資料。

    反證：拿掉 `_custody_autofill_job` 裡「搶不到鎖時刪除節流鍵」那段 DELETE，這條測試
    會轉紅——鍵仍然存在，`db.get_ai_cache(conn, key)` 不會是 None。
    """
    from stocks_power_rich.api import stock as S

    monkeypatch.setattr(S, "get_connection", lambda path: conn)
    monkeypatch.setattr(S, "_custody_autofill", set())
    key = S._autofill_cache_key("2330")
    db.set_ai_cache(conn, key, {"at": "2026-09-20T12:00:00"})   # 模擬 _start_custody_autofill 已標記過

    assert S._custody_lock.acquire(blocking=False)   # 模擬另一檔正握著鎖在跑
    try:
        S._custody_autofill_job("2330")              # 這裡應該搶不到鎖、提早 return
    finally:
        S._custody_lock.release()

    assert db.get_ai_cache(conn, key) is None
