from stocks_power_rich.config import load_config


def test_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SPR_SCHEDULE_TIME", raising=False)
    cfg = load_config()
    assert cfg.schedule_time == "21:00"
    assert cfg.gemini_api_key == ""
    assert "^SOX" in cfg.intl_tickers.values()
    assert "^VIX" in cfg.intl_tickers.values()


def test_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("SPR_SCHEDULE_TIME", "14:00")
    cfg = load_config()
    assert cfg.gemini_api_key == "abc"
    assert cfg.schedule_time == "14:00"
