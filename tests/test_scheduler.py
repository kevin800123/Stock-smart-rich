from stocks_power_rich.scheduler import parse_schedule_time, build_trigger_kwargs


def test_parse_time():
    assert parse_schedule_time("15:30") == (15, 30)


def test_build_trigger_kwargs():
    assert build_trigger_kwargs("09:05") == {"hour": 9, "minute": 5}
