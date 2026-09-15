"""排程執行紀錄（job_runs）、job 包裝（run_job）、啟動補跑（catchup_*）。

不動 tests/test_health.py（平行分支正在改它），/api/health 的 jobs 欄位在這裡驗。
所有「現在」走 helpers._now；補跑測試把 job 函式樁成計數器，一次都不連外。
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stocks_power_rich import db
from stocks_power_rich.api import helpers
from stocks_power_rich.config import Config


def _cfg(**kw) -> Config:
    base = dict(telegram_token="t", telegram_chat_id="c", line_token="l", weekly_push_time="17:00")
    base.update(kw)
    return Config(**base)


@pytest.fixture
def c(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    conn = db.get_connection(path)
    db.init_db(conn)
    return conn


def _clock(monkeypatch, dt: datetime):
    monkeypatch.setattr(helpers, "_now", lambda: dt)


FRI_22 = datetime(2026, 9, 11, 22, 0)   # 週五 22:00
SAT_22 = datetime(2026, 9, 12, 22, 0)   # 週六 22:00


# ---------------------------------------------------------------- db 層
def test_job_runs_roundtrip_and_latest(c):
    rid = db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "2026-09-11T21:00:00")
    assert db.job_run_status(c, "daily_update", "2026-09-11") == "running"
    db.finish_job_run(c, rid, "ok", "2026-09-11T21:03:00", note="picked=3")
    assert db.job_run_status(c, "daily_update", "2026-09-11") == "ok"
    # 同一 key 再跑一次失敗 → 最近一列才算數
    rid2 = db.start_job_run(c, "daily_update", "2026-09-11", "catchup", "2026-09-11T22:00:00")
    db.finish_job_run(c, rid2, "failed", "2026-09-11T22:00:01", error="boom")
    assert db.job_run_status(c, "daily_update", "2026-09-11") == "failed"
    latest = db.latest_job_runs(c)
    assert latest["daily_update"]["status"] == "failed"
    assert latest["daily_update"]["error"] == "boom"
    assert latest["daily_update"]["trigger"] == "catchup"
    assert db.job_run_status(c, "nope", "2026-09-11") is None


def test_mark_interrupted_only_touches_running_rows(c):
    r1 = db.start_job_run(c, "a", "k", "scheduled", "t")
    r2 = db.start_job_run(c, "b", "k", "scheduled", "t")
    db.finish_job_run(c, r2, "ok", "t2")
    assert db.mark_interrupted_job_runs(c, "t3") == 1
    assert db.job_run_status(c, "a", "k") == "interrupted"
    assert db.job_run_status(c, "b", "k") == "ok"
    assert db.mark_interrupted_job_runs(c, "t4") == 0
    # prune 依 started_at
    assert db.prune_job_runs(c, "u") == 2
    assert db.latest_job_runs(c) == {}


def test_mark_interrupted_with_started_before_leaves_runs_of_this_process_alone(c):
    """只標「本程序啟動前開始的」running：新程序自己剛開始跑的那列必須原封不動。"""
    db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "2026-09-11T21:00:00")
    db.start_job_run(c, "osfut_evening", "2026-09-11", "scheduled", "2026-09-11T22:00:01")
    n = db.mark_interrupted_job_runs(c, "2026-09-11T22:00:02", started_before="2026-09-11T22:00:00")
    assert n == 1
    assert db.job_run_status(c, "daily_update", "2026-09-11") == "interrupted"
    assert db.job_run_status(c, "osfut_evening", "2026-09-11") == "running"


# ---------------------------------------------------------------- 排程規格
def test_job_schedule_depends_on_configured_channels():
    ids = {s["id"] for s in helpers.job_schedule(_cfg(), "21:00")}
    assert ids == {"daily_update", "osfut_morning", "osfut_evening", "self_screen_early",
                   "news_morning", "news_midday", "news_afternoon", "news_evening",
                   "intraday_watch", "weekly_line"}
    no_tg = {s["id"] for s in helpers.job_schedule(_cfg(telegram_chat_id=""), "21:00")}
    assert not any(i.startswith("news_") for i in no_tg)
    no_line = {s["id"] for s in helpers.job_schedule(_cfg(line_token=""), "21:00")}
    assert "intraday_watch" not in no_line and "weekly_line" not in no_line
    by_id = {s["id"]: s for s in helpers.job_schedule(_cfg(), "21:30")}
    assert (by_id["daily_update"]["hour"], by_id["daily_update"]["minute"]) == ("21", "30")
    assert by_id["intraday_watch"]["catchup"] is False


def test_slot_times_respect_day_of_week_and_multi_hours():
    by_id = {s["id"]: s for s in helpers.job_schedule(_cfg(), "21:00")}
    fri, sat = FRI_22.date(), SAT_22.date()
    assert [t.strftime("%H:%M") for t in helpers.slot_times(by_id["self_screen_early"], fri)] == \
        ["17:30", "18:30", "19:30"]
    assert helpers.slot_times(by_id["self_screen_early"], sat) == []
    assert helpers.slot_times(by_id["weekly_line"], fri) == []
    assert [t.strftime("%H:%M") for t in helpers.slot_times(by_id["weekly_line"], sat)] == ["17:00"]
    assert helpers.slot_times(by_id["intraday_watch"], fri) == []   # catchup=False
    # run_key：一天一場→日期；一天多場→日期:HH:MM
    assert helpers.run_key_for(by_id["daily_update"], datetime(2026, 9, 11, 21, 0)) == "2026-09-11"
    assert helpers.run_key_for(by_id["self_screen_early"], datetime(2026, 9, 11, 18, 30)) == \
        "2026-09-11:18:30"
    # 排程觸發時取「≤ now 的最後一場」（cron 只會晚不會早）
    assert helpers.scheduled_run_key(by_id["self_screen_early"], datetime(2026, 9, 11, 18, 30, 2)) == \
        "2026-09-11:18:30"
    assert helpers.scheduled_run_key(by_id["intraday_watch"], datetime(2026, 9, 11, 9, 35, 1)) == \
        "2026-09-11:09:35"


# ---------------------------------------------------------------- 補跑計畫
def test_catchup_plan_only_today_latest_slot_per_family(c):
    specs = helpers.job_schedule(_cfg(), "21:00")
    plan = {p["job_id"]: p for p in helpers.catchup_plan(c, specs, FRI_22)}
    # 22:00：今天所有時段都過了，每個家族只留最近的一場
    assert set(plan) == {"daily_update", "osfut_evening", "self_screen_early", "news_evening"}
    assert plan["self_screen_early"]["run_key"] == "2026-09-11:19:30"
    assert plan["daily_update"]["last_status"] is None
    # 依時段排序：21:00 daily_update 在 21:10 news_evening 之前
    order = [p["job_id"] for p in helpers.catchup_plan(c, specs, FRI_22)]
    assert order.index("daily_update") < order.index("news_evening")
    # 20:00：21:00 那場還沒到，不補
    plan_20 = {p["job_id"] for p in helpers.catchup_plan(c, specs, FRI_22.replace(hour=20))}
    assert "daily_update" not in plan_20 and "news_evening" not in plan_20
    assert "news_afternoon" in plan_20 and "osfut_morning" in plan_20


def test_catchup_plan_skips_done_and_retries_failed(c):
    specs = helpers.job_schedule(_cfg(), "21:00")
    rid = db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "ok", "t")
    rid = db.start_job_run(c, "news_evening", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "failed", "t", error="x")
    rid = db.start_job_run(c, "osfut_evening", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "partial", "t")
    plan = {p["job_id"]: p for p in helpers.catchup_plan(c, specs, FRI_22)}
    assert "daily_update" not in plan            # ok → 不補
    assert "osfut_evening" not in plan           # partial 也算跑過
    assert plan["news_evening"]["last_status"] == "failed"   # failed → 補


def test_catchup_plan_family_rule_ignores_older_missed_slot(c):
    """news_evening 成功了、news_midday 沒紀錄 → 整個新聞家族不補（使用者決定：只補最近的一場）。"""
    specs = helpers.job_schedule(_cfg(), "21:00")
    rid = db.start_job_run(c, "news_evening", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "ok", "t")
    assert not any(p["job_id"].startswith("news_") for p in helpers.catchup_plan(c, specs, FRI_22))


def test_catchup_plan_on_saturday(c):
    specs = helpers.job_schedule(_cfg(), "21:00")
    plan = {p["job_id"] for p in helpers.catchup_plan(c, specs, SAT_22)}
    assert "weekly_line" in plan and "daily_update" in plan
    assert "self_screen_early" not in plan and "news_morning" not in plan
    assert "news_evening" in plan   # 週末新聞只留 12:00／21:10，21:10 是最近一場


# ---------------------------------------------------------------- run_job
def test_run_job_records_ok_failed_partial_and_dedupes(c, monkeypatch):
    _clock(monkeypatch, FRI_22)
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return {"picked": 3}
    r = helpers.run_job("j", "2026-09-11", ok)
    assert r["status"] == "ok" and calls["n"] == 1
    assert db.latest_job_runs(c)["j"]["note"] == "{'picked': 3}"
    # 同 key 已 ok → 略過、不再呼叫（Telegram 沒有去重，靠這裡）
    r = helpers.run_job("j", "2026-09-11", ok)
    assert r["status"] == "skipped" and r["reason"] == "ok" and calls["n"] == 1

    def boom():
        raise RuntimeError("網路斷了")
    r = helpers.run_job("k", "2026-09-11", boom)
    assert r["status"] == "failed" and "RuntimeError: 網路斷了" in r["error"]
    assert db.job_run_status(c, "k", "2026-09-11") == "failed"

    r = helpers.run_job("m", "2026-09-11", lambda: {"failed_steps": ["run_update: X"], "date": "d"})
    assert r["status"] == "partial" and r["error"] == "run_update: X"
    assert db.job_run_status(c, "m", "2026-09-11") == "partial"


def test_run_job_skips_when_same_key_is_running(c, monkeypatch):
    """排程與補跑撞在同一分鐘：第二個看到 running 就讓開。"""
    _clock(monkeypatch, FRI_22)
    db.start_job_run(c, "j", "2026-09-11", "scheduled", "t")
    called = []
    r = helpers.run_job("j", "2026-09-11", lambda: called.append(1))
    assert r["status"] == "skipped" and r["reason"] == "running" and called == []


# ---------------------------------------------------------------- 啟動補跑
def _stub_jobs(*ids):
    calls = {i: 0 for i in ids}

    def mk(i):
        def f():
            calls[i] += 1
            return {"ran": i}
        return f
    return calls, {i: mk(i) for i in ids}


@pytest.mark.real_catchup
def test_catchup_runs_missed_jobs_once_and_not_again_on_restart(c, monkeypatch):
    """驗收：21:00 之後才啟動、今天 daily_update 沒紀錄 → 補跑一次；再啟動一次不重跑。"""
    _clock(monkeypatch, FRI_22)
    specs = helpers.job_schedule(_cfg(telegram_token=""), "21:00")
    calls, jobs = _stub_jobs("daily_update", "osfut_morning", "osfut_evening", "self_screen_early",
                             "intraday_watch", "weekly_line")
    r = helpers.catchup_missed_jobs(c, specs, jobs, now=FRI_22)
    assert [x["job_id"] for x in r["ran"]] == ["self_screen_early", "daily_update", "osfut_evening"]
    assert all(x["result"] == "ok" for x in r["ran"])
    assert calls["daily_update"] == 1 and calls["osfut_evening"] == 1 and calls["osfut_morning"] == 0
    assert calls["intraday_watch"] == 0 and calls["weekly_line"] == 0
    assert db.latest_job_runs(c)["daily_update"]["trigger"] == "catchup"
    # 第二次啟動：全部已 ok → 什麼都不跑
    r2 = helpers.catchup_missed_jobs(c, specs, jobs, now=FRI_22.replace(hour=23))
    assert r2["plan"] == [] and r2["ran"] == []
    assert calls["daily_update"] == 1


@pytest.mark.real_catchup
def test_catchup_treats_stale_running_as_missed(c, monkeypatch):
    """上一個程序跑到一半被重啟：那列停在 running，補跑要把它當沒跑完。"""
    _clock(monkeypatch, FRI_22)
    specs = [s for s in helpers.job_schedule(_cfg(telegram_token="", line_token=""), "21:00")
             if s["id"] == "daily_update"]
    db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "2026-09-11T21:00:00")
    calls, jobs = _stub_jobs("daily_update")
    r = helpers.catchup_missed_jobs(c, specs, jobs, now=FRI_22)
    assert r["interrupted"] == 1 and calls["daily_update"] == 1
    rows = c.execute("SELECT status FROM job_runs ORDER BY id").fetchall()
    assert [x[0] for x in rows] == ["interrupted", "ok"]


@pytest.mark.real_catchup
def test_catchup_does_not_interrupt_or_rerun_a_job_the_new_process_already_started(c, monkeypatch):
    """啟動競態：排程器先起來、某支 job 在補跑執行緒之前就開始跑（寫了一列 running）。
    補跑不能把這列當成上一個程序留下的——否則標成 interrupted 後會再跑一次（Telegram 重送）。
    上一個程序真正留下的那列（啟動前開始的）仍要標 interrupted 並補跑。"""
    boot = datetime(2026, 9, 11, 22, 0, 0)
    now = boot + timedelta(seconds=2)
    _clock(monkeypatch, now)
    specs = [s for s in helpers.job_schedule(_cfg(telegram_token="", line_token=""), "21:00")
             if s["id"] in ("daily_update", "osfut_evening")]
    db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "2026-09-11T21:00:00")      # 上一個程序
    db.start_job_run(c, "osfut_evening", "2026-09-11", "scheduled", "2026-09-11T22:00:01")    # 本程序剛開始
    calls, jobs = _stub_jobs("daily_update", "osfut_evening")
    r = helpers.catchup_missed_jobs(c, specs, jobs, now=now, booted_at=boot.isoformat(timespec="seconds"))
    assert r["interrupted"] == 1
    assert calls["daily_update"] == 1
    assert calls["osfut_evening"] == 0
    assert db.job_run_status(c, "osfut_evening", "2026-09-11") == "running"


def test_create_app_marks_stale_runs_interrupted_before_the_scheduler_starts(c, monkeypatch):
    """上一個程序留下的 running 必須在排程器啟動**之前**就標掉：排程器一起來就可能觸發
    同一支 job，那時若還看到舊的 running，run_job 會把它當「已在執行」而略過（這一場就沒了）。
    所以不能交給背景補跑執行緒去標（它比排程器晚）。"""
    _clock(monkeypatch, FRI_22)
    db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "2026-09-11T21:00:00")
    from stocks_power_rich import scheduler as _sched
    seen = {}
    real_start = _sched.start_scheduler

    def spy(*a, **kw):
        seen["status_at_start"] = db.job_run_status(c, "daily_update", "2026-09-11")
        return real_start(*a, **kw)
    monkeypatch.setattr(_sched, "start_scheduler", spy)
    import threading
    got, done = {}, threading.Event()

    def fake(conn, specs, jobs, now=None, booted_at=None):
        got["booted_at"] = booted_at
        done.set()
        return {}
    monkeypatch.setattr(helpers, "catchup_missed_jobs", fake)
    from stocks_power_rich.main import create_app
    app = create_app(enable_scheduler=True)
    try:
        assert done.wait(5)
    finally:
        app.state.scheduler.shutdown(wait=False)
    assert seen["status_at_start"] == "interrupted"
    assert got["booted_at"] == "2026-09-11T22:00:00"


@pytest.mark.real_catchup
def test_catchup_does_not_resend_news_evening_already_sent(c, monkeypatch):
    """驗收：同一天 news_evening 已成功 → 重啟不重送。"""
    _clock(monkeypatch, FRI_22)
    specs = helpers.job_schedule(_cfg(line_token=""), "21:00")
    rid = db.start_job_run(c, "news_evening", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "ok", "t")
    calls, jobs = _stub_jobs("daily_update", "osfut_morning", "osfut_evening", "self_screen_early",
                             "news_morning", "news_midday", "news_afternoon", "news_evening")
    helpers.catchup_missed_jobs(c, specs, jobs, now=FRI_22)
    assert calls["news_evening"] == 0 and calls["news_afternoon"] == 0 and calls["news_midday"] == 0
    assert calls["daily_update"] == 1


@pytest.mark.real_catchup
def test_catchup_continues_after_a_job_fails(c, monkeypatch):
    _clock(monkeypatch, FRI_22)
    specs = helpers.job_schedule(_cfg(telegram_token="", line_token=""), "21:00")
    calls, jobs = _stub_jobs("osfut_evening", "self_screen_early")

    def boom():
        raise RuntimeError("x")
    jobs["daily_update"] = boom
    r = helpers.catchup_missed_jobs(c, specs, jobs, now=FRI_22)
    by = {x["job_id"]: x["result"] for x in r["ran"]}
    assert by["daily_update"] == "failed" and by["osfut_evening"] == "ok"
    assert db.job_run_status(c, "daily_update", "2026-09-11") == "failed"


# ---------------------------------------------------------------- 接線：main.py 與 /api/health
def test_health_reports_latest_job_runs(c):
    rid = db.start_job_run(c, "daily_update", "2026-09-11", "scheduled", "t")
    db.finish_job_run(c, rid, "partial", "t2", error="run_update: Timeout")
    from stocks_power_rich.main import create_app
    body = TestClient(create_app()).get("/api/health").json()
    assert body["jobs"]["daily_update"]["status"] == "partial"
    assert body["jobs"]["daily_update"]["error"] == "run_update: Timeout"


def test_scheduler_registers_from_the_single_spec_and_wraps_with_run_job(c, monkeypatch):
    """註冊與補跑吃同一份 job_schedule；排程觸發的函式會寫 job_runs。"""
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "dummy")
    _clock(monkeypatch, FRI_22)
    from stocks_power_rich import main as _main
    monkeypatch.setattr(_main, "_os_futures", lambda refresh=False: {"has_remote": True, "updated_at": "u"})
    app = _main.create_app(enable_scheduler=True)
    try:
        ids = {j.id for j in app.state.scheduler.get_jobs()}
        assert ids == {s["id"] for s in helpers.job_schedule(_main.load_config(), "21:00")}
        app.state.scheduler.get_job("osfut_evening").func()   # 模擬 cron 觸發
    finally:
        app.state.scheduler.shutdown(wait=False)
    row = db.latest_job_runs(c)["osfut_evening"]
    assert row["status"] == "ok" and row["run_key"] == "2026-09-11" and row["trigger"] == "scheduled"


def test_startup_catchup_is_called_in_background_with_specs_and_jobs(c, monkeypatch):
    """create_app(enable_scheduler=True) 會在背景執行緒呼叫 catchup_missed_jobs（conftest 預設
    樁掉；這裡換成會記錄參數的樁，證明接線真的存在、且不阻塞啟動）。"""
    import threading
    seen = {}
    done = threading.Event()

    def fake(conn, specs, jobs, now=None, booted_at=None):
        seen["booted_at"] = booted_at
        seen["ids"] = {s["id"] for s in specs}
        seen["jobs"] = set(jobs)
        seen["thread"] = threading.current_thread().name
        done.set()
        return {}
    monkeypatch.setattr(helpers, "catchup_missed_jobs", fake)
    from stocks_power_rich.main import create_app
    app = create_app(enable_scheduler=True)
    try:
        assert done.wait(5)
    finally:
        app.state.scheduler.shutdown(wait=False)
    assert seen["thread"] == "spr-catchup"
    assert seen["ids"] <= seen["jobs"]
    assert seen["booted_at"]   # 補跑必須知道本程序何時啟動，才分得出新舊 running


def test_daily_update_on_saturday_pushes_nothing_to_line(c, monkeypatch):
    """驗收：週六晚上補跑 daily_update → 不發 LINE 資料告警、不發每日卡片（走同一支 job，
    既有守衛自動生效）。run_update 樁成「有失敗來源」，若週末守衛失效就會 broadcast。"""
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
    _clock(monkeypatch, SAT_22)
    db.upsert_market_daily(c, {"date": "2026-09-11", "taiex": 25000.0})
    from stocks_power_rich import main as _main, updater, line_push, csv_import, ledger
    from stocks_power_rich.api import market as _market, public as _public
    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda *a, **k: sent.append(("text", a)) or {"ok": True})
    monkeypatch.setattr(line_push, "broadcast_messages", lambda *a, **k: sent.append(("msgs", a)) or {"ok": True})
    monkeypatch.setattr(csv_import, "find_latest_file", lambda d: None)
    monkeypatch.setattr(updater, "run_update", lambda c_, t: {
        "date": "2026-09-11", "success": [],
        "failed": [{"source": "tpex", "name": "revenue", "error": "ConnectTimeout"}]})
    monkeypatch.setattr(_market, "market_summary_logic", lambda *a, **k: {})
    monkeypatch.setattr(_public, "summary_logic", lambda *a, **k: {})
    monkeypatch.setattr(_main, "backup_db", lambda p: None)
    monkeypatch.setattr(ledger, "record_daily_signals", lambda c_: None)
    monkeypatch.setattr(ledger, "update_ledger_returns", lambda c_: None)
    monkeypatch.setattr(_main, "_refresh_self_screen_cache", lambda c_: {"skipped": "weekend"})
    app = _main.create_app()
    r = helpers.run_job("daily_update", "2026-09-12", app.state.jobs["daily_update"], trigger="catchup")
    assert r["status"] == "ok", r
    assert sent == []
    assert db.latest_job_runs(c)["daily_update"]["note"].startswith("{'failed_steps': []")


# ---------------------------------------------------------------- 使用者追問的四點
def _wrapped_job(monkeypatch, job_id: str):
    """拿排程器實際會呼叫的那個（run_job 包過）函式。"""
    from stocks_power_rich import main as _main
    app = _main.create_app(enable_scheduler=True)
    fn = app.state.scheduler.get_job(job_id).func
    app.state.scheduler.shutdown(wait=False)
    return fn


def test_self_screen_early_1730_not_ready_does_not_block_1830(c, monkeypatch):
    """17:30 回 data_not_ready（job 正常結束，記 ok、note 帶 skipped）→ 18:30 是另一個 run_key，
    照跑並算好。三場的 run_key 是 日期:HH:MM，不是日期。"""
    from stocks_power_rich import stock_flow, selfcheck
    monkeypatch.setattr(stock_flow, "update_day", lambda c_, d: None)
    monkeypatch.setattr(selfcheck, "load_precomputed", lambda c_, d: None)
    results = iter([
        {"cached": False, "date": "2026-09-11", "skipped": "data_not_ready", "missing": ["twse/quotes"]},
        {"cached": True, "date": "2026-09-11", "picked": 7},
    ])
    calls = []

    def fake_refresh(c_, day=None):
        calls.append(day)
        return next(results)
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    job = _wrapped_job(monkeypatch, "self_screen_early")

    _clock(monkeypatch, datetime(2026, 9, 11, 17, 30, 1))
    job()
    row = db.latest_job_runs(c)["self_screen_early"]
    assert row["run_key"] == "2026-09-11:17:30" and row["status"] == "ok"
    assert "data_not_ready" in row["note"]

    _clock(monkeypatch, datetime(2026, 9, 11, 18, 30, 2))
    job()
    row = db.latest_job_runs(c)["self_screen_early"]
    assert row["run_key"] == "2026-09-11:18:30" and row["status"] == "ok"
    assert "picked" in row["note"]
    assert calls == ["2026-09-11", "2026-09-11"]        # 18:30 真的又算了一次
    assert c.execute("SELECT COUNT(*) FROM job_runs WHERE job_id='self_screen_early'").fetchone()[0] == 2


def test_intraday_watch_is_not_deduped_within_the_day(c, monkeypatch):
    """每 5 分鐘一場，run_key 用當下分鐘（日期:HH:MM），同一天連跑不會被當成重複。"""
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
    from stocks_power_rich import main as _main
    seen = []
    monkeypatch.setattr(_main, "_intraday_scan", lambda c_, push=True: seen.append(1) or {"checked": 3, "hits": []})
    job = _wrapped_job(monkeypatch, "intraday_watch")
    for hh, mm in ((9, 35), (9, 40), (9, 45)):
        _clock(monkeypatch, datetime(2026, 9, 11, hh, mm, 0))
        assert job()["status"] == "ok"
    assert len(seen) == 3
    keys = [r[0] for r in c.execute(
        "SELECT run_key FROM job_runs WHERE job_id='intraday_watch' ORDER BY id").fetchall()]
    assert keys == ["2026-09-11:09:35", "2026-09-11:09:40", "2026-09-11:09:45"]


def test_job_runs_unique_index_allows_one_running_row_per_key(c):
    """去重的根據是 DB 唯一鍵（uq_job_runs_running），不是「先查再寫」。"""
    import sqlite3
    rid = db.start_job_run(c, "j", "2026-09-11", "scheduled", "t")
    with pytest.raises(sqlite3.IntegrityError):
        db.start_job_run(c, "j", "2026-09-11", "catchup", "t")
    db.finish_job_run(c, rid, "failed", "t2", error="x")
    db.start_job_run(c, "j", "2026-09-11", "catchup", "t3")   # 不在 running 就可以再開一列


def test_run_job_concurrent_same_key_runs_exactly_once_even_if_the_precheck_is_blind(c, monkeypatch):
    """補跑執行緒與 APScheduler 同時觸發同一支 job（部署剛好在 21:00 完成）。
    把「先查」那步弄瞎（永遠回 None），兩條執行緒同時進來，仍只有一個真的跑——
    擋住的是唯一鍵，不是先查再寫。"""
    import threading
    _clock(monkeypatch, FRI_22)
    monkeypatch.setattr(helpers, "job_run_status", lambda c_, j, k: None)
    monkeypatch.setattr(helpers, "_job_lock", threading.Lock())   # 仍有鎖；下面再拿掉鎖驗一次
    gate = threading.Event()
    ran = []

    def slow_job():
        ran.append(threading.current_thread().name)
        gate.wait(5)
        return {"ok": True}
    results = {}

    def go(name):
        results[name] = helpers.run_job("daily_update", "2026-09-11", slow_job, trigger=name)
    t1 = threading.Thread(target=go, args=("scheduled",), name="t-sched")
    t2 = threading.Thread(target=go, args=("catchup",), name="t-catch")
    t1.start()
    # 等第一條真的進到 job 裡（running 列已寫入）再放第二條
    for _ in range(100):
        if ran:
            break
        t1.join(0.05)
    t2.start(); t2.join(5)
    gate.set(); t1.join(5)
    statuses = sorted(r["status"] for r in results.values())
    assert statuses == ["ok", "skipped"], results
    assert len(ran) == 1
    skipped = next(r for r in results.values() if r["status"] == "skipped")
    assert skipped["reason"] == "running"
    rows = c.execute("SELECT status FROM job_runs WHERE job_id='daily_update'").fetchall()
    assert [r[0] for r in rows] == ["ok"]


def test_run_job_concurrent_without_the_process_lock_still_runs_once(c, monkeypatch):
    """反證用：連程序內的鎖都換成不互斥的物件、先查也弄瞎，只剩唯一鍵——仍然只跑一次。"""
    import threading
    _clock(monkeypatch, FRI_22)
    monkeypatch.setattr(helpers, "job_run_status", lambda c_, j, k: None)

    class NoLock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(helpers, "_job_lock", NoLock())
    gate = threading.Event()
    n = {"ran": 0}

    def slow_job():
        n["ran"] += 1
        gate.wait(5)
        return {"ok": True}
    out = []
    ts = [threading.Thread(target=lambda: out.append(helpers.run_job("j", "k", slow_job)))
          for _ in range(2)]
    ts[0].start()
    for _ in range(100):
        if n["ran"]:
            break
        ts[0].join(0.05)
    ts[1].start(); ts[1].join(5); gate.set(); ts[0].join(5)
    assert sorted(r["status"] for r in out) == ["ok", "skipped"] and n["ran"] == 1
