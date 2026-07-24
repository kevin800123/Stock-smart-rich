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


def test_osfut_jobs_register_even_without_line_token(tmp_path, monkeypatch):
    """海期監控排程（07:30／21:30）與 LINE 是否設定無關——不歸在 line_token 判斷式內，
    否則沒設 LINE 的人（本機開發常態）永遠不會有 07:30/21:30 這兩次更新，
    海期監控只能靠使用者自己按「更新報價」，等於功能形同半殘。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    from stocks_power_rich.main import create_app

    app = create_app(enable_scheduler=True)
    try:
        morning = app.state.scheduler.get_job("osfut_morning")
        evening = app.state.scheduler.get_job("osfut_evening")
        assert morning is not None and evening is not None
        mf = {f.name: str(f) for f in morning.trigger.fields}
        ef = {f.name: str(f) for f in evening.trigger.fields}
        assert mf["hour"] == "7" and mf["minute"] == "30"
        assert ef["hour"] == "21" and ef["minute"] == "30"
    finally:
        app.state.scheduler.shutdown(wait=False)


def test_create_app_with_line_token_registers_all_jobs(tmp_path, monkeypatch):
    """設 LINE_CHANNEL_ACCESS_TOKEN 才會觸發 line_brief/intraday_watch 的註冊分支——
    這是雲端生產設定（Zeabur 皆設此變數）；曾因 job 函式定義順序問題整個 app 起不來。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy-token")
    monkeypatch.setenv("SPR_WEEKLY_PUSH_TIME", "18:30")   # 週報時間可由 config 調整（固定週六）
    from stocks_power_rich.main import create_app

    app = create_app(enable_scheduler=True)
    try:
        ids = {j.id for j in app.state.scheduler.get_jobs()}
        assert ids == {"daily_update", "osfut_morning", "osfut_evening",
                       "line_brief", "intraday_watch", "weekly_line"}
        # 籌碼週報固定週六，時間讀 cfg.weekly_push_time（Asia/Taipei，scheduler 時區已設）
        wk = app.state.scheduler.get_job("weekly_line")
        fields = {f.name: str(f) for f in wk.trigger.fields}
        assert fields["day_of_week"] == "sat"
        assert fields["hour"] == "18" and fields["minute"] == "30"
    finally:
        app.state.scheduler.shutdown(wait=False)

