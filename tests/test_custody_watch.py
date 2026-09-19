"""週集保一公布就反映到自算選股（docs/superpowers/specs/2026-09-19-custody-watch-design.md）。"""
import json
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db, ledger, pick_push, selfcheck, telegram_push, updater
from stocks_power_rich.api import helpers
from stocks_power_rich.config import Config
from stocks_power_rich.sources import tdcc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


FULL = [f"{1000 + i}" for i in range(10)]


def _week(c, week, codes):
    db.bulk_upsert_custody(c, week, {code: {"big400_pct": 50.0, "total_holders": 100} for code in codes})


def test_complete_week_ignores_single_stock_backfill(conn):
    """逐檔回補把一檔寫進新週：那一週不算完整，最新完整週仍是上一週。"""
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])
    assert db.latest_complete_custody_week(conn) == "2026-09-11"
    assert db.custody_week_complete(conn, "2026-09-11") is True
    assert db.custody_week_complete(conn, "2026-09-18") is False
    assert db.custody_week_complete(conn, "2026-09-25") is False   # 完全沒有列


def test_accumulate_custody_is_not_blocked_by_a_partial_week(conn, monkeypatch):
    """新週只有逐檔回補的一檔時，全市場那一批仍要寫入（舊寫法會被 6 天節流與「已存在」擋一整週）。"""
    prev = (date.today() - timedelta(days=8)).isoformat()
    new = (date.today() - timedelta(days=1)).isoformat()
    _week(conn, prev, FULL)
    _week(conn, new, ["2330"])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    n = conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=?", (new,)).fetchone()[0]
    assert n == len(FULL) + 1          # 全市場 10 檔＋原本那一檔（2330 不在 FULL 裡）
    assert db.custody_week_complete(conn, new) is True


def test_accumulate_custody_records_first_fetch_time_once(conn, monkeypatch):
    old = (date.today() - timedelta(days=14)).isoformat()
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": old, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == old
    stamp = db.get_ai_cache(conn, f"custody_fetched:{old}")
    assert stamp and datetime.fromisoformat(stamp["at"])
    # 第二段要真的走到「已記過就不覆寫」那道守衛：新週 W 只有逐檔回補的 1 檔（殘缺、不擋寫入），
    # 舊週是 14 天前（不被 6 天節流擋），W 的取得時間已記過（哨兵值）。寫入照做，時間不覆寫。
    w = (date.today() - timedelta(days=7)).isoformat()
    _week(conn, w, ["2330"])
    db.set_ai_cache(conn, f"custody_fetched:{w}", {"at": "2000-01-01T00:00:00"})
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": w, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == w                       # 有寫入（不是被節流擋下）
    assert conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=?", (w,)).fetchone()[0] == len(FULL) + 1
    assert db.get_ai_cache(conn, f"custody_fetched:{w}")["at"] == "2000-01-01T00:00:00"


HEAD = "﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260918,000218,1,0,0,0.00\n20260918,0002".encode("utf-8")


def test_parse_custody_week_head_reads_date_from_second_line():
    assert tdcc.parse_custody_week_head(HEAD) == "2026-09-18"


def test_parse_custody_week_head_rejects_unexpected_format():
    assert tdcc.parse_custody_week_head(b"") is None
    assert tdcc.parse_custody_week_head("資料日期,證券代號\n".encode("utf-8")) is None      # 只有表頭
    assert tdcc.parse_custody_week_head("<html>維護中</html>\n\n".encode("utf-8")) is None


class _FakeStream:
    def __init__(self, chunks, calls):
        self.chunks, self.calls = chunks, calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("closed")

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        for ch in self.chunks:
            self.calls.append("chunk")
            yield ch


def test_peek_custody_week_stops_after_second_line_and_skips_tls_verify(monkeypatch):
    calls, seen = [], {}
    rest = [b"x" * 100] * 50                                     # 後面還有很多塊，不應該讀到
    def fake_stream(method, url, **kw):
        seen.update(kw)
        return _FakeStream([HEAD[:20], HEAD[20:]] + rest, calls)
    monkeypatch.setattr(tdcc.httpx, "stream", fake_stream)
    assert tdcc.peek_custody_week() == "2026-09-18"
    assert seen["verify"] is False and seen["params"] == {"id": "1-5"}
    assert calls.count("chunk") == 2 and calls[-1] == "closed"


def test_slot_times_support_multiple_minutes():
    spec = {"id": "x", "family": "x", "hour": "18-21", "minute": "0,30", "dow": "sat"}
    sat = date(2026, 9, 19)
    got = [t.strftime("%H:%M") for t in helpers.slot_times(spec, sat)]
    assert got == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]
    assert helpers.run_key_for(spec, datetime(2026, 9, 19, 20, 30)) == "2026-09-19:20:30"
    assert helpers.scheduled_run_key(spec, datetime(2026, 9, 19, 20, 31, 5)) == "2026-09-19:20:30"
    one = {"id": "y", "family": "y", "hour": "21", "minute": "0", "dow": None}
    assert helpers.run_key_for(one, datetime(2026, 9, 19, 21, 0)) == "2026-09-19"   # 一天一場照舊


def _ready(c, day):
    for market in ("TWSE", "TPEx"):
        for source in ("quotes", "institutional"):
            db.set_stock_source_coverage(c, day, market, source, "complete", 1, None)


def _universe(monkeypatch):
    monkeypatch.setattr(helpers, "_industry_map",
                        lambda c: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1e9}})
    monkeypatch.setattr(helpers, "_otc_industry",
                        lambda c: {"8069": {"sector": "光電業", "name": "元太", "shares": 1e9}})


@pytest.mark.parametrize("now,record_signals,expect", [
    (datetime(2026, 9, 18, 21, 0), True, True),     # 訊號日當天：寫
    (datetime(2026, 9, 19, 21, 0), True, False),    # 週六重算週五：不寫（收盤後才公布的集保）
    (datetime(2026, 9, 18, 21, 0), False, False),   # custody_watch 明確不寫
])
def test_refresh_records_signals_only_on_the_signal_day(conn, monkeypatch, now, record_signals, expect):
    db.upsert_market_daily(conn, {"date": "2026-09-18", "taiex": 20000.0})
    conn.commit()
    _ready(conn, "2026-09-18")
    _universe(monkeypatch)
    calls = []
    monkeypatch.setattr(ledger, "record_self_screen_signals", lambda *a, **k: calls.append(k.get("signal_date")))
    monkeypatch.setattr(helpers, "_now", lambda: now)
    res = helpers.refresh_self_screen_cache(conn, record_signals=record_signals)
    assert res["cached"] is True and res["date"] == "2026-09-18"
    assert res["recorded"] is expect
    assert calls == (["2026-09-18"] if expect else [])


def test_custody_watch_does_nothing_when_tdcc_has_no_new_week(conn, monkeypatch):
    _week(conn, "2026-09-18", FULL)
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該被呼叫"))
    monkeypatch.setattr(updater, "_accumulate_custody", boom)
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", boom)
    assert helpers.custody_watch(conn) == {"skipped": "no_new_week", "tdcc": "2026-09-18",
                                           "local": "2026-09-18"}


def test_custody_watch_stores_new_week_and_recomputes_the_listed_day_without_ledger(conn, monkeypatch):
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])                       # 逐檔回補的殘缺週不算
    selfcheck.save_precomputed(conn, {"date": "2026-09-18", "rows": [], "heatmap": [], "coverage": {}})
    db.upsert_market_daily(conn, {"date": "2026-09-17", "taiex": 20000.0})   # 21:00 前 market_daily 只到週四
    conn.commit()
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: "2026-09-18")
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})
    seen = {}

    def fake_refresh(c, day=None, record_signals=True):
        seen.update(day=day, record_signals=record_signals)
        return {"cached": True, "date": day}
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    r = helpers.custody_watch(conn)
    assert r["week"] == "2026-09-18" and r["fetched_at"] == "2026-09-19T09:30:00"
    assert r["self_screen"] == {"cached": True, "date": "2026-09-18", "skipped": None}
    assert seen == {"day": "2026-09-18", "record_signals": False}   # 重算畫面上那一天，不是 09-17
    assert "stored_elsewhere" not in r                         # 自己寫入的，不標


def test_custody_watch_raises_when_the_head_cannot_be_read(conn, monkeypatch):
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: None)
    with pytest.raises(RuntimeError):
        helpers.custody_watch(conn)


def test_custody_watch_is_scheduled_friday_evening_and_saturday():
    cfg = Config()                                            # 不綁任何推播設定
    by_id = {s["id"]: s for s in helpers.job_schedule(cfg, "21:00")}
    fri, sat = date(2026, 9, 18), date(2026, 9, 19)
    f = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["custody_watch_fri"], fri)]
    s = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["custody_watch_sat"], sat)]
    assert (f[0], f[-1], len(f)) == ("17:00", "23:30", 14)
    assert (s[0], s[-1], len(s)) == ("08:00", "21:30", 28)
    assert helpers.slot_times(by_id["custody_watch_fri"], sat) == []
    assert by_id["custody_watch_fri"]["family"] == by_id["custody_watch_sat"]["family"] == "custody_watch"


def test_custody_watch_fails_when_full_download_stores_nothing(conn, monkeypatch):
    """TDCC 檔頭已是新週、完整下載卻沒寫入（下載 5xx、維護頁被解析成空、週別對不上）是故障：
    丟 RuntimeError 讓 run_job 記成 failed（/api/health 看得到），訊息帶 TDCC 與本地週；不呼叫自算選股。
    契約刻意改變：原本回 {"skipped": "not_stored"}，被記成 ok、看不出故障。"""
    _week(conn, "2026-09-11", FULL)
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: None)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該被呼叫"))
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", boom)
    with pytest.raises(RuntimeError, match="2026-09-18.*2026-09-11"):
        helpers.custody_watch(conn)


def test_custody_watch_accepts_a_week_stored_by_another_writer_at_the_same_time(conn, monkeypatch):
    """同一時間另一條路（21:00 run_update、補跑與排程重疊）先把那一週寫進去了：`_accumulate_custody`
    看到「已完整」回 None。這是正常的競爭，不是下載失敗——不可丟錯，照常重算名單並標 stored_elsewhere。"""
    _week(conn, "2026-09-11", FULL)
    _cache(conn, "2026-09-18", ["2026-09-11", "2026-09-04"])
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})

    def other_writer_won(c):
        _week(c, "2026-09-18", FULL)                           # 另一條路剛寫完整週
        return None                                           # 這一邊看到已完整，回 None
    monkeypatch.setattr(updater, "_accumulate_custody", other_writer_won)
    seen = []

    def fake_refresh(c, day=None, record_signals=True):
        seen.append((day, record_signals))
        return {"cached": True, "date": day}
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    r = helpers.custody_watch(conn)
    assert r["stored_elsewhere"] is True
    assert r["week"] == "2026-09-18" and r["fetched_at"] == "2026-09-19T09:30:00"
    assert r["self_screen"] == {"cached": True, "date": "2026-09-18", "skipped": None}
    assert seen == [("2026-09-18", False)]                     # 照常重算畫面上那一天、不寫前瞻紀錄


def test_custody_watch_without_cache_calls_refresh_with_day_and_no_signals(conn, monkeypatch):
    """無既存自算快取時：day 傳 None（由 refresh_self_screen_cache 退回最新交易日），不寫前瞻紀錄"""
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])                       # 逐檔回補的殘缺週
    db.upsert_market_daily(conn, {"date": "2026-09-17", "taiex": 20000.0})
    conn.commit()
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: "2026-09-18")
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})
    # 不設 selfscreen_cache，空快取
    seen = {}
    def fake_refresh(c, day=None, record_signals=True):
        seen.update(day=day, record_signals=record_signals)
        return {"cached": False, "date": day}
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    r = helpers.custody_watch(conn)
    assert r["week"] == "2026-09-18" and r["fetched_at"] == "2026-09-19T09:30:00"
    assert seen == {"day": None, "record_signals": False}
    assert r["self_screen"] == {"cached": False, "date": None, "skipped": None}


def test_custody_watch_retries_the_recompute_after_it_failed(conn, monkeypatch):
    """寫入成功、重算失敗之後，下一場 TDCC 仍是同一週、資料表也已有：不可回 no_new_week，要補算。
    舊寫法在這裡每一場都回 no_new_week，名單永遠停在舊集保，週報卻以為本週集保已到。"""
    _week(conn, "2026-09-04", FULL)
    _week(conn, "2026-09-11", FULL)
    _cache(conn, "2026-09-18", ["2026-09-11", "2026-09-04"])   # 週五 17:30 用舊集保算好的名單
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: _week(c, "2026-09-18", FULL) or "2026-09-18")

    def refresh_fails(c, day=None, record_signals=True):
        raise RuntimeError("重算中途失敗")
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", refresh_fails)
    with pytest.raises(RuntimeError, match="重算中途失敗"):
        helpers.custody_watch(conn)                            # 集保已寫入（已 commit），名單沒重算

    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該再下載"))
    monkeypatch.setattr(updater, "_accumulate_custody", boom)
    seen = []

    def fake_refresh(c, day=None, record_signals=True):
        seen.append((day, record_signals))
        _cache(c, day, ["2026-09-18", "2026-09-11"])          # 模擬重算成功後的快取
        return {"cached": True, "date": day}
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    r = helpers.custody_watch(conn)
    assert r == {"retried": True, "week": "2026-09-18",
                 "self_screen": {"cached": True, "date": "2026-09-18", "skipped": None}}
    assert seen == [("2026-09-18", False)]                     # 重算畫面上那一天、不寫前瞻紀錄
    # 補算成功之後名單已用上本週集保：下一場回 no_new_week，不會每 30 分鐘重算全市場
    assert helpers.custody_watch(conn) == {"skipped": "no_new_week", "tdcc": "2026-09-18",
                                           "local": "2026-09-18"}
    assert len(seen) == 1


def test_custody_watch_does_not_recompute_a_thursday_list_for_fridays_custody(conn, monkeypatch):
    """快取是週四名單時，週四本來就用不到週五那週的集保——比的是「那一天應該用的週」，
    不是資料庫最新週，否則週五 17:00~17:30 之間每 30 分鐘都會重算一次全市場。"""
    _week(conn, "2026-09-04", FULL)
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", FULL)
    _cache(conn, "2026-09-17", ["2026-09-11", "2026-09-04"])
    assert db.custody_compare_weeks(conn, "2026-09-17") == ["2026-09-11", "2026-09-04"]
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該被呼叫"))
    monkeypatch.setattr(updater, "_accumulate_custody", boom)
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", boom)
    assert helpers.custody_watch(conn) == {"skipped": "no_new_week", "tdcc": "2026-09-18",
                                           "local": "2026-09-18"}


def _cal(c, days):
    for d in days:
        db.upsert_market_daily(c, {"date": d, "taiex": 20000.0})
    c.commit()


def _cache(c, day, custody_weeks):
    """存一份名單快取，coverage.custody_weeks＝這份名單實際用的集保週（新到舊）。"""
    selfcheck.save_precomputed(c, {"date": day, "ready_at": f"{day}T17:31:00", "heatmap": [],
                                   "coverage": {"custody_weeks": list(custody_weeks)}, "rows": []})


def test_custody_is_current_by_iso_week(conn):
    """看名單快取實際用的集保週，不看集保資料表。"""
    _cal(conn, ["2026-09-17", "2026-09-18"])
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", FULL)                            # 資料表已有本週……
    _cache(conn, "2026-09-18", ["2026-09-11", "2026-09-04"])   # ……但名單還是用上一週
    assert helpers.custody_is_current(conn) == {"current": False, "week": "2026-09-11"}
    _cache(conn, "2026-09-18", ["2026-09-18", "2026-09-11"])
    assert helpers.custody_is_current(conn) == {"current": True, "week": "2026-09-18"}


def test_custody_is_current_when_friday_is_a_holiday(conn):
    """週五放假：當週最後交易日是週四、TDCC 的週日期也是週四，仍算本週已公布。"""
    _cal(conn, ["2026-09-16", "2026-09-17"])
    _cache(conn, "2026-09-17", ["2026-09-17", "2026-09-11"])
    assert helpers.custody_is_current(conn)["current"] is True


def test_custody_is_current_without_custody_weeks_in_cache(conn):
    """舊快取沒有 custody_weeks＝不知道用了哪週＝不算本週（寧可等到截止時間、照送並註明）。"""
    _cal(conn, ["2026-09-18"])
    _week(conn, "2026-09-18", FULL)
    selfcheck.save_precomputed(conn, {"date": "2026-09-18", "heatmap": [], "coverage": {}, "rows": []})
    assert helpers.custody_is_current(conn) == {"current": False, "week": None}


def _weekly_setup(c, custody_weeks, cache_weeks=None):
    """cache_weeks＝名單快取實際用的集保週（新到舊）；不給就當作名單已用上資料表最新兩週。"""
    _cal(c, ["2026-09-11", "2026-09-14", "2026-09-18"])
    for wk in custody_weeks:
        _week(c, wk, FULL)
    if cache_weeks is None:
        cache_weeks = sorted(custody_weeks, reverse=True)[:2]
    _cache(c, "2026-09-18", cache_weeks)


def _tg():
    return Config(telegram_token="t", telegram_chat_id="c")


def test_weekly_push_waits_for_new_custody_then_sends_once(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    _weekly_setup(conn, ["2026-09-04", "2026-09-11"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 0))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r == {"kind": "weekly", "skipped": "waiting_custody", "custody_week": "2026-09-11"}
    assert sent == []
    # 新集保 18:10 寫進資料表，但名單還沒重算（重算失敗／被重啟打斷／回 skipped）：仍然要等。
    # 只看資料表的舊寫法會在這裡用舊集保的名單送出、標記本週已送，之後無法更正。
    _week(conn, "2026-09-18", FULL)
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r == {"kind": "weekly", "skipped": "waiting_custody", "custody_week": "2026-09-11"}
    assert sent == []
    _cache(conn, "2026-09-18", ["2026-09-18", "2026-09-11"])   # 18:40 custody_watch 重算完名單
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 19, 0))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1 and "集保仍為" not in sent[0]
    assert "集保 09\\-11→09\\-18" in sent[0]                  # 一律附用了哪兩週（MarkdownV2 跳脫後）
    assert db.get_ai_cache(conn, "picks_weekly_sent:2026-W38")["date"] == "2026-09-18"
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 19, 30))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly") == {"kind": "weekly", "skipped": "already_sent"}
    assert len(sent) == 1


def test_weekly_push_sends_at_deadline_with_stale_note(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    _weekly_setup(conn, ["2026-09-04", "2026-09-11"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1
    assert "集保仍為 09\\-11 週" in sent[0]                   # MarkdownV2 跳脫後的樣子
    # 程式只知道名單沒用上本週集保，不知道 TDCC 有沒有公布——字樣是「尚未取得」不是「尚未公布」
    assert "本週集保尚未取得" in sent[0] and "尚未公布" not in sent[0]


_TABLE_HAS_THIS_WEEK = ["2026-09-04", "2026-09-11", "2026-09-18"]
_TABLE_LACKS_THIS_WEEK = ["2026-09-04", "2026-09-11"]


@pytest.mark.parametrize("table_weeks,cache_weeks,expect,absent", [
    (_TABLE_HAS_THIS_WEEK, ["2026-09-18", "2026-09-11"], "集保 09\\-11→09\\-18", ("尚未取得", "已取得")),
    (_TABLE_HAS_THIS_WEEK, ["2026-09-18"], "集保 09\\-18", ("→",)),   # 只有一週：不畫懸空的箭頭
    # 資料表已有本週、名單還是上一週（寫入後重算失敗等）：說「已取得、名單尚未重算」，不叫人去等 TDCC
    (_TABLE_HAS_THIS_WEEK, ["2026-09-11", "2026-09-04"],
     "集保仍為 09\\-11 週（本週集保已取得，名單尚未重算）", ("尚未公布", "尚未取得")),
    (_TABLE_HAS_THIS_WEEK, [], "本週集保已取得，名單尚未重算", ("集保仍為", "尚未取得")),   # 舊快取不知道用了哪週
    # 資料表也還沒有本週：維持「尚未取得」
    (_TABLE_LACKS_THIS_WEEK, ["2026-09-11", "2026-09-04"],
     "集保仍為 09\\-11 週（本週集保尚未取得）", ("尚未公布", "已取得")),
    (_TABLE_LACKS_THIS_WEEK, [], "本週集保尚未取得", ("集保仍為", "已取得")),
])
def test_weekly_payload_always_carries_a_custody_line(conn, monkeypatch, table_weeks, cache_weeks, expect, absent):
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    _weekly_setup(conn, table_weeks, cache_weeks=cache_weeks)
    text = helpers.new_picks_push_payload(conn, "weekly")["text"]
    assert expect in text
    for a in absent:
        assert a not in text


def test_weekly_payload_says_not_obtained_when_table_is_newer_but_not_this_week(conn, monkeypatch):
    """名單落後兩週（09-04）、資料表只補到上週（09-11）：資料表比名單新，但本週集保其實還沒取得，
    不可說「已取得」。門檻是「≥ 本週週一」，不是「比名單用的週新」。"""
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    _weekly_setup(conn, ["2026-08-28", "2026-09-04", "2026-09-11"], cache_weeks=["2026-09-04", "2026-08-28"])
    text = helpers.new_picks_push_payload(conn, "weekly")["text"]
    assert "集保仍為 09\\-04 週（本週集保尚未取得）" in text and "已取得" not in text


def test_weekly_push_at_deadline_says_custody_obtained_but_list_not_recomputed(conn, monkeypatch):
    """21:30 截止：新集保已寫進資料表、名單卻沒重算（重算失敗／被重啟打斷／回 skipped）。照送，
    註記要講「本週集保已取得，名單尚未重算」——要查的是重算，不是等 TDCC。"""
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    _weekly_setup(conn, _TABLE_HAS_THIS_WEEK, cache_weeks=["2026-09-11", "2026-09-04"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1
    assert "集保仍為 09\\-11 週（本週集保已取得，名單尚未重算）" in sent[0]
    assert "本週集保尚未取得" not in sent[0]


def test_weekly_push_sends_once_when_two_threads_race(conn, tmp_path, monkeypatch):
    """啟動補跑執行緒與排程執行緒各拿不同 run_key、同時跑到週報：只能送一次。
    run_job 只擋同一個 run_key，這裡靠 _WEEKLY_PUSH_LOCK 把「讀標記 → 送出 → 寫標記」包成一段。
    sqlite 連線不能跨執行緒，所以兩條執行緒各開自己的連線到同一個 DB。"""
    import threading
    import time
    sent, guard = [], threading.Lock()

    def slow_send(tok, chat, text):
        time.sleep(0.2)                                        # 讓兩邊都有機會在「寫標記」之前讀標記
        with guard:
            sent.append(text)
        return {"ok": True, "parse_mode_used": "MarkdownV2"}
    monkeypatch.setattr(telegram_push, "send_message", slow_send)
    _weekly_setup(conn, ["2026-09-11", "2026-09-18"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 30))
    path = str(tmp_path / "t.sqlite")
    start, results, errors = threading.Barrier(2), [], []

    def worker():
        c = db.get_connection(path)
        try:
            start.wait()
            results.append(helpers.telegram_new_picks_job(c, _tg(), "weekly"))
        except Exception as e:  # noqa: BLE001 — 例外要帶回主執行緒斷言，不能在執行緒裡消失
            errors.append(e)
        finally:
            c.close()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == [] and len(results) == 2
    assert len(sent) == 1
    assert sorted(r.get("skipped", "sent") for r in results) == ["already_sent", "sent"]


def test_weekly_push_failed_send_is_retried(conn, monkeypatch):
    monkeypatch.setattr(telegram_push, "send_message", lambda *a: {"ok": False})
    _weekly_setup(conn, ["2026-09-11", "2026-09-18"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 0))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly")["sent"] is False
    assert db.get_ai_cache(conn, "picks_weekly_sent:2026-W38") is None   # 沒送成功不標記，下一場重試


def test_compose_weekly_includes_custody_note_only_when_given():
    base = dict(day="2026-09-18", week_start="2026-09-14", total=0, n_week=0, items=[], basis=None,
                top_sectors=[], ready_at=None)
    assert "集保仍為" not in pick_push.compose_weekly_new_picks(**base)
    assert "集保仍為 09\\-11 週" in pick_push.compose_weekly_new_picks(
        **base, custody_note="集保仍為 09-11 週（本週集保尚未取得）")


def test_weekly_push_is_scheduled_every_half_hour_on_saturday_evening():
    by_id = {s["id"]: s for s in helpers.job_schedule(_tg(), "21:00")}
    got = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["picks_new_weekly"], date(2026, 9, 19))]
    assert got == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]


def test_self_screen_coverage_reports_the_custody_weeks_used(conn):
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", FULL)
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})
    cov = selfcheck.compute_self_screen(conn, "2026-09-18", {})["coverage"]
    assert cov["custody_weeks"] == ["2026-09-18", "2026-09-11"]
    assert cov["custody_fetched_at"] == "2026-09-19T09:30:00"
    json.dumps(cov)                                           # 要進 ai_cache 的 TEXT 欄


def test_self_screen_coverage_before_new_week_uses_previous_pair(conn):
    _week(conn, "2026-09-04", FULL)
    _week(conn, "2026-09-11", FULL)
    # 比查詢日更新的完整週（含取得時間）：覆蓋率只能看查詢日當時用得到的週（as_of 視窗）
    _week(conn, "2026-09-25", FULL)
    db.set_ai_cache(conn, "custody_fetched:2026-09-25", {"at": "2026-09-26T09:30:00"})
    cov = selfcheck.compute_self_screen(conn, "2026-09-18", {})["coverage"]
    assert "2026-09-25" not in cov["custody_weeks"]
    assert cov["custody_weeks"] == ["2026-09-11", "2026-09-04"] and cov["custody_fetched_at"] is None


# ── 新進榜的比對基準＝帳本 ∪ 使用者看過的名單（finding #4，使用者 2026-09-20 決定）──

def _ledger(c, day, codes):
    for code in codes:
        c.execute("INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price) VALUES (?,?,?,?,1)",
                  (day, code, code, "self_screen"))
    c.commit()


def _by_code(result):
    return {r["code"]: r for r in result["rows"]}


def test_new_entry_seen_on_saturday_is_not_reported_again_on_monday(conn):
    """帳本只有訊號日當天（週五 17:30、舊集保）記的名單；週六用新集保重算後 1001 才入選，週六週報
    已把它列成「本週新進」。週一若只拿帳本比，1001 會被標成「✦ 今天才進榜、上一個集保週期也沒有」，
    與週六的說法矛盾。基準改成「使用者看過的名單」之後，週一兩個標籤都不再掛。"""
    for d in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
              "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"):
        _ledger(conn, d, ["1000"])
    for wk in ("2026-09-04", "2026-09-11", "2026-09-18"):
        _week(conn, wk, FULL)
    ledger.record_shown_self_screen(conn, "2026-09-18", ["1000", "1001"])   # 週報以新集保重算後的名單送出，1001 入選

    sat = ledger.annotate_new_entries(conn, {"rows": [{"code": "1000"}, {"code": "1001"}]}, "2026-09-18")
    assert _by_code(sat)["1001"]["is_week_new"] is True                      # 週六週報照樣列本週新進
    assert sat["week_new_vs"] == {"from": "2026-09-07", "to": "2026-09-11"}

    mon = ledger.annotate_new_entries(conn, {"rows": [{"code": "1000"}, {"code": "1001"}]}, "2026-09-21")
    by = _by_code(mon)
    assert mon["new_vs"] == "2026-09-18"
    assert by["1001"]["is_new"] is False                                     # 週一不再說今天才進
    assert by["1001"]["is_week_new"] is False                                # 週六看過的週五名單在上一週期裡
    assert by["1000"]["is_new"] is False and by["1000"]["is_week_new"] is False
    assert mon["week_new_vs"] == {"from": "2026-09-14", "to": "2026-09-18"}


def test_record_shown_self_screen_unions_and_strips_suffix(conn):
    ledger.record_shown_self_screen(conn, "2026-09-18", ["1000"])
    ledger.record_shown_self_screen(conn, "2026-09-18", ["1001", "1002.TW"])   # 同一天再送一次（週五平日、週六週報）：送出過就算看過
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") == {"codes": ["1000", "1001", "1002"]}
    ledger.record_shown_self_screen(conn, "2026-09-19", [])                    # 空名單不寫：帳本 0 檔那天也沒有列
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-19") is None


def test_shown_list_without_ledger_rows_counts_as_the_previous_list(conn):
    _ledger(conn, "2026-09-16", ["1000"])
    # LIKE 'selfscreen_shown:%' 的 `_` 是萬用字元，會把這把長得像的鍵也算進來；實作用字典序範圍才擋得住。
    # 先單獨檢查它（後面真的那把鍵同一天，放一起會把它蓋掉、看不出來）。
    db.set_ai_cache(conn, "selfscreenXshown:2026-09-17", {"codes": ["9999"]})
    assert ledger.previous_self_screen_codes(conn, "2026-09-18") == ("2026-09-16", {"1000"})
    ledger.record_shown_self_screen(conn, "2026-09-17", ["2000"])
    assert ledger.previous_self_screen_codes(conn, "2026-09-18") == ("2026-09-17", {"2000"})


def test_refresh_does_not_record_the_shown_list(conn, monkeypatch):
    """「看過的名單」只在推播送出成功時記（fix wave 2 #A）；重算快取不記——週報送出之後的週末重算帶進來
    的新進股沒有人收到過，在這裡記的話週一不再是新進、永遠不會被播出。
    契約刻意改變：91a531b 時這條斷言的是「重算會寫 selfscreen_shown」。
    樁 build_self_screen 讓入選代號確定是 2330（有東西可記），並確認只被呼叫一次。"""
    db.upsert_market_daily(conn, {"date": "2026-09-18", "taiex": 20000.0})
    conn.commit()
    _ready(conn, "2026-09-18")
    _universe(monkeypatch)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("record_signals=False 不該碰帳本"))
    monkeypatch.setattr(ledger, "record_self_screen_signals", boom)
    calls = []

    def fake_build(*a, **k):
        calls.append(1)
        return {"rows": [{"code": "2330"}]}
    monkeypatch.setattr(selfcheck, "build_self_screen", fake_build)
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 10, 0))   # 週六重算週五名單
    res = helpers.refresh_self_screen_cache(conn, day="2026-09-18", record_signals=False)
    assert res["cached"] is True and res["recorded"] is False and res["picked"] == 1
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") is None
    assert len(calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM signal_ledger").fetchone()[0] == 0


# ── 「看過的名單」＝推播實際送出成功的整份名單（fix wave 2 #A）──

_PASS = {"rev_yoy": 10.0, "w55": 1, "big_holder_ratio": 1.0, "holder_drop_ratio": -1.0,
         "trust_3d": 0, "foreign_3d": 0, "lan_score": 12, "est_profit": 2.0, "mu_score": 15,
         "mu_value": 300.0, "margin_3d": 0}                    # 7 條件全過、木率木質高於預設門檻


def _list_rows(codes):
    return [{"code": code, "name": code, "sector": "x", "vals": dict(_PASS)} for code in codes]


def _cache_list(c, day, custody_weeks, codes):
    """名單快取：coverage.custody_weeks＝實際用的集保週，rows＝這些代號都會入選。"""
    selfcheck.save_precomputed(c, {"date": day, "ready_at": f"{day}T17:31:00", "heatmap": [],
                                   "coverage": {"custody_weeks": list(custody_weeks)},
                                   "rows": _list_rows(codes)})


_WEEKDAYS = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
             "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]


def _ok_send(sent):
    return lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"}


# kind、送出時間、名單快取用的集保週（weekly 要本週集保已用上才會在 21:30 前送）
_SEND_CASES = [
    ("daily", datetime(2026, 9, 18, 21, 40), ["2026-09-11", "2026-09-04"]),
    ("weekly", datetime(2026, 9, 19, 19, 0), ["2026-09-18", "2026-09-11"]),
]


@pytest.mark.parametrize("kind,now,cache_weeks", _SEND_CASES)
def test_successful_push_records_the_whole_list_as_shown(conn, monkeypatch, kind, now, cache_weeks):
    """送出成功後記的是**整份**入選名單（1000 不是新進也要記），鍵是名單日期；daily 與 weekly 都記。"""
    _cal(conn, _WEEKDAYS)
    for wk in ("2026-09-04", "2026-09-11", "2026-09-18"):
        _week(conn, wk, FULL)
    _ledger(conn, "2026-09-17", ["1000"])
    _cache_list(conn, "2026-09-18", cache_weeks, ["1000", "1001", "1002"])
    sent = []
    monkeypatch.setattr(telegram_push, "send_message", _ok_send(sent))
    monkeypatch.setattr(helpers, "_now", lambda: now)
    r = helpers.telegram_new_picks_job(conn, _tg(), kind)
    assert r["sent"] is True and len(sent) == 1
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") == {"codes": ["1000", "1001", "1002"]}


@pytest.mark.parametrize("kind,now,cache_weeks", _SEND_CASES)
def test_failed_push_does_not_record_the_list_as_shown(conn, monkeypatch, kind, now, cache_weeks):
    """送出失敗＝沒有人收到，不算看過（名單非空，所以不是「空名單不寫」那條規則擋掉的）。"""
    _cal(conn, _WEEKDAYS)
    for wk in ("2026-09-04", "2026-09-11", "2026-09-18"):
        _week(conn, wk, FULL)
    _cache_list(conn, "2026-09-18", cache_weeks, ["1000", "1001"])
    monkeypatch.setattr(telegram_push, "send_message", lambda *a: {"ok": False})
    monkeypatch.setattr(helpers, "_now", lambda: now)
    assert helpers.telegram_new_picks_job(conn, _tg(), kind)["sent"] is False
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") is None


def test_preview_endpoint_does_not_record_the_list_as_shown(conn, monkeypatch):
    """預覽端點只組內容、不送，也就不記看過的名單（否則開一次預覽，週一的新進就少掉了）。"""
    from fastapi.testclient import TestClient
    from stocks_power_rich.main import create_app
    _cal(conn, _WEEKDAYS)
    for wk in ("2026-09-04", "2026-09-11", "2026-09-18"):
        _week(conn, wk, FULL)
    _cache_list(conn, "2026-09-18", ["2026-09-18", "2026-09-11"], ["1000", "1001"])
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("預覽不應該送出"))
    monkeypatch.setattr(telegram_push, "send_message", boom)
    client = TestClient(create_app())
    for kind in ("daily", "weekly"):
        body = client.get(f"/api/picks/new-push-preview?kind={kind}&force=1").json()
        assert body["ok"] is True and body["text"] and body["codes"] == ["1000", "1001"]
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") is None


def test_list_recomputed_after_the_weekly_push_is_still_new_on_monday(conn, monkeypatch):
    """審查者的情境：帳本 09-07～09-18 只有 1000；週六 21:30 本週集保還沒進來，週報以舊名單 [1000]
    截止送出；週日 21:00 的每日更新抓到新集保、重算讓 1001 入選並寫入快取。週報之後沒有任何推播
    送出過 1001，週一它必須仍是 NEW 與 WEEK NEW（會被播出）。91a531b 在重算時就記「看過」，1001 在週一
    兩個標籤都掉了、Telegram 永遠不會播出它。"""
    _cal(conn, _WEEKDAYS)
    for d in _WEEKDAYS:
        _ledger(conn, d, ["1000"])
    for wk in ("2026-09-04", "2026-09-11"):
        _week(conn, wk, FULL)
    _cache_list(conn, "2026-09-18", ["2026-09-11", "2026-09-04"], ["1000"])
    sent = []
    monkeypatch.setattr(telegram_push, "send_message", _ok_send(sent))
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly")["sent"] is True
    assert db.get_ai_cache(conn, "selfscreen_shown:2026-09-18") == {"codes": ["1000"]}

    # 週日 21:00：每日更新抓到 09-18 週集保，重算週五名單，1001 入選（走真的 refresh_self_screen_cache）
    _week(conn, "2026-09-18", FULL)
    _ready(conn, "2026-09-18")
    _universe(monkeypatch)
    recomputed = {"date": "2026-09-18", "heatmap": [], "rows": _list_rows(["1000", "1001"]),
                  "coverage": {"custody_weeks": ["2026-09-18", "2026-09-11"]}}
    monkeypatch.setattr(selfcheck, "compute_self_screen", lambda c, day, universe: dict(recomputed))
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 20, 21, 0))
    res = helpers.refresh_self_screen_cache(conn)
    assert res["cached"] is True and res["date"] == "2026-09-18" and res["picked"] == 2
    assert res["recorded"] is False                            # 週日不補寫週五的前瞻紀錄

    mon = ledger.annotate_new_entries(conn, {"rows": [{"code": "1000"}, {"code": "1001"}]}, "2026-09-21")
    by = _by_code(mon)
    assert mon["new_vs"] == "2026-09-18"
    assert by["1001"]["is_new"] is True                        # 沒有人收到過：週一仍是今天才進
    assert by["1001"]["is_week_new"] is True                   # 上一個集保週期送出過的名單都沒有它
    assert by["1000"]["is_new"] is False and by["1000"]["is_week_new"] is False


def test_list_sent_by_the_weekly_push_is_not_new_again_on_monday(conn, monkeypatch):
    """週報以重算後的名單 [1000, 1001] 送出：1001 週六已被列成本週新進，週一兩個標籤都不再掛
    （finding #4 要的行為，改成「送出才記」之後照舊成立）。"""
    _cal(conn, _WEEKDAYS)
    for d in _WEEKDAYS:
        _ledger(conn, d, ["1000"])
    for wk in ("2026-09-04", "2026-09-11", "2026-09-18"):
        _week(conn, wk, FULL)
    _cache_list(conn, "2026-09-18", ["2026-09-18", "2026-09-11"], ["1000", "1001"])
    sent = []
    monkeypatch.setattr(telegram_push, "send_message", _ok_send(sent))
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 19, 0))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly")["sent"] is True
    assert "1001" in sent[0]                                   # 週六確實以本週新進播出

    mon = ledger.annotate_new_entries(conn, {"rows": [{"code": "1000"}, {"code": "1001"}]}, "2026-09-21")
    by = _by_code(mon)
    assert mon["new_vs"] == "2026-09-18"
    assert by["1001"]["is_new"] is False
    assert by["1001"]["is_week_new"] is False


def test_same_day_shown_list_is_not_its_own_baseline(conn):
    """fix wave 2 #B：21:40 推播送出後當天就有 selfscreen_shown:{當天}。當晚再看同一天（網頁、或同一天
    再組一次推播），比對基準必須是**前一份**名單，當天入選股仍是 NEW；含當天的話會一檔新進都沒有。"""
    _ledger(conn, "2026-09-17", ["1000"])
    ledger.record_shown_self_screen(conn, "2026-09-18", ["1000", "1001"])   # 當天 21:40 已送出
    res = ledger.annotate_new_entries(conn, {"rows": [{"code": "1000"}, {"code": "1001"}]}, "2026-09-18")
    assert res["new_vs"] == "2026-09-17"
    assert _by_code(res)["1001"]["is_new"] is True
    assert _by_code(res)["1000"]["is_new"] is False
