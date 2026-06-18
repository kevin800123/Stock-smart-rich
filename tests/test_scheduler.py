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

