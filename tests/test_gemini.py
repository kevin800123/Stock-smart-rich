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


def test_news_summary_prompt_requires_structured_sourced_brief(monkeypatch):
    captured = {}

    def fake_run(prompt, api_key):
        captured["prompt"] = prompt
        return {"enabled": True, "text": "ok"}

    monkeypatch.setattr(gemini, "_run", fake_run)
    out = gemini.summarize_news(
        {
            "slot": "afternoon",
            "report_date": "2026-08-03",
            "snapshot": {"taiex": 24000},
            "markets": {"tw": [{"title": "台股標題", "source": "Example"}]},
        },
        api_key="k",
    )

    assert out["text"] == "ok"
    assert "2026-08-03 每日財經重點速覽" in captured["prompt"]
    assert "🇹🇼 台股｜5 則精選" in captured["prompt"]
    assert "🇺🇸 美股｜5 則精選" in captured["prompt"] and "🇯🇵 日股｜5 則精選" in captured["prompt"]
    assert "每個市場都要恰好 5 則新聞" in captured["prompt"]
    assert "株探翻譯" in captured["prompt"] and "日股新聞必須先翻譯" in captured["prompt"]
    assert "🧾 **事件**" in captured["prompt"] and "🔢 **關鍵數據**" in captured["prompt"]
    assert "不得補造" in captured["prompt"] and "非投資建議" in captured["prompt"]


def test_news_push_prompt_uses_compact_midday_template(monkeypatch):
    captured = {}

    def fake_run(prompt, api_key):
        captured["prompt"] = prompt
        return {"enabled": True, "text": "ok"}

    monkeypatch.setattr(gemini, "_run", fake_run)
    out = gemini.summarize_news_push(
        {"slot": "midday", "report_date": "2026-08-03", "snapshot": {}, "markets": {}},
        full_brief="完整報告",
        api_key="k",
    )

    assert out["text"] == "ok"
    assert "台股 6、日股 6、美股 6" in captured["prompt"]
    assert "來源未提供可驗證數據" in captured["prompt"]
    assert "日股項目必須翻譯日文株探標題" in captured["prompt"]


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
