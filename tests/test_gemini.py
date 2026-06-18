from fastapi.testclient import TestClient

from stocks_power_rich import gemini
from stocks_power_rich.main import create_app
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily


def test_market_summary_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-17", "taiex": 23000.0, "updated_at": "2026-06-17T15:00:00"})

    calls = {"n": 0}

    def fake_summarize(market_row, api_key):
        calls["n"] += 1
        return {"enabled": True, "text": "盤勢偏多"}

    monkeypatch.setattr(gemini, "summarize_market", fake_summarize)
    client = TestClient(create_app())
    first = client.get("/api/market/summary").json()
    second = client.get("/api/market/summary").json()
    assert first["text"] == "盤勢偏多" and second["text"] == "盤勢偏多"
    assert calls["n"] == 1  # 第二次走快取，不再呼叫 Gemini


def test_degrades_without_key():
    out = gemini.summarize_market({"taiex": 23000}, api_key="")
    assert out["enabled"] is False
    assert "未啟用" in out["text"]


def test_uses_model_when_key(monkeypatch):
    class FakeResp:
        text = "盤勢偏多"

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResp()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini, "genai_client", lambda key: FakeClient(key))
    out = gemini.summarize_market({"taiex": 23000}, api_key="k")
    assert out["enabled"] is True
    assert out["text"] == "盤勢偏多"
