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
    new = date.today().isoformat()
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    stamp = db.get_ai_cache(conn, f"custody_fetched:{new}")
    assert stamp and datetime.fromisoformat(stamp["at"])
    db.set_ai_cache(conn, f"custody_fetched:{new}", {"at": "2000-01-01T00:00:00"})
    assert updater._accumulate_custody(conn) is None                    # 同一週：節流擋下
    assert db.get_ai_cache(conn, f"custody_fetched:{new}")["at"] == "2000-01-01T00:00:00"


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


def test_custody_watch_when_accumulate_custody_returns_none(conn, monkeypatch):
    """集保一週都沒更新時：記下 TDCC 與本地日期、標 not_stored、不呼叫自算選股。"""
    _week(conn, "2026-09-11", FULL)
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: None)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該被呼叫"))
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", boom)
    assert helpers.custody_watch(conn) == {"skipped": "not_stored", "tdcc": "2026-09-18",
                                           "local": "2026-09-11"}


def test_custody_watch_without_cache_calls_refresh_with_day_and_no_signals(conn, monkeypatch):
    """無既存自算快取時：重算的是集保新週所對應的交易日，不寫前瞻紀錄。"""
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


def _cal(c, days):
    for d in days:
        db.upsert_market_daily(c, {"date": d, "taiex": 20000.0})
    c.commit()


def test_custody_is_current_by_iso_week(conn):
    _cal(conn, ["2026-09-17", "2026-09-18"])
    _week(conn, "2026-09-11", FULL)
    assert helpers.custody_is_current(conn) == {"current": False, "week": "2026-09-11"}
    _week(conn, "2026-09-18", FULL)
    assert helpers.custody_is_current(conn) == {"current": True, "week": "2026-09-18"}


def test_custody_is_current_when_friday_is_a_holiday(conn):
    """週五放假：當週最後交易日是週四、TDCC 的週日期也是週四，仍算本週已公布。"""
    _cal(conn, ["2026-09-16", "2026-09-17"])
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-17", FULL)
    assert helpers.custody_is_current(conn)["current"] is True


def _weekly_setup(c, custody_weeks):
    _cal(c, ["2026-09-11", "2026-09-14", "2026-09-18"])
    for wk in custody_weeks:
        _week(c, wk, FULL)
    selfcheck.save_precomputed(c, {"date": "2026-09-18", "ready_at": "2026-09-18T17:31:00",
                                   "heatmap": [], "coverage": {}, "rows": []})


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
    _week(conn, "2026-09-18", FULL)                            # 新集保 18:10 進來
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1 and "集保仍為" not in sent[0]
    assert db.get_ai_cache(conn, "picks_weekly_sent:2026-W38")["date"] == "2026-09-18"
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 19, 0))
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
        **base, custody_note="集保仍為 09-11 週（本週尚未公布）")


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
    cov = selfcheck.compute_self_screen(conn, "2026-09-18", {})["coverage"]
    assert cov["custody_weeks"] == ["2026-09-11", "2026-09-04"] and cov["custody_fetched_at"] is None
