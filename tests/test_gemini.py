from stocks_power_rich import gemini


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
