from stocks_power_rich.scheduler import parse_schedule_time, build_trigger_kwargs, start_scheduler


def test_parse_time():
    assert parse_schedule_time("15:30") == (15, 30)


def test_build_trigger_kwargs():
    assert build_trigger_kwargs("09:05") == {"hour": 9, "minute": 5}


def test_start_scheduler_registers_daily_job():
    sched = start_scheduler(lambda: None, "15:30")
    try:
        job = sched.get_job("daily_update")
        assert job is not None
        assert sched.running is True
    finally:
        sched.shutdown(wait=False)


def test_create_app_enables_scheduler(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.main import create_app

    app = create_app(enable_scheduler=True)
    try:
        assert app.state.scheduler.get_job("daily_update") is not None
    finally:
        app.state.scheduler.shutdown(wait=False)


def test_create_app_with_line_token_registers_all_jobs(tmp_path, monkeypatch):
    """設 LINE_CHANNEL_ACCESS_TOKEN 才會觸發 line_brief/intraday_watch 的註冊分支——
    這是雲端生產設定（Zeabur 皆設此變數）；曾因 job 函式定義順序問題整個 app 起不來。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy-token")
    from stocks_power_rich.main import create_app

    app = create_app(enable_scheduler=True)
    try:
        ids = {j.id for j in app.state.scheduler.get_jobs()}
        assert ids == {"daily_update", "line_brief", "intraday_watch", "weekly_line"}
        # 籌碼週報固定週六 17:00（Asia/Taipei，scheduler 時區已設）
        wk = app.state.scheduler.get_job("weekly_line")
        fields = {f.name: str(f) for f in wk.trigger.fields}
        assert fields["day_of_week"] == "sat"
        assert fields["hour"] == "17" and fields["minute"] == "0"
    finally:
        app.state.scheduler.shutdown(wait=False)

