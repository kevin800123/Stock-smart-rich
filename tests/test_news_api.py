from fastapi.testclient import TestClient


def test_news_endpoint_caches_enabled_summary_and_hides_telegram_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-secret")
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    fetches, summaries = [], []

    def fake_fetch(market, n=20, now=None):
        fetches.append(market)
        return ([{"title": f"{market} title", "url": "https://example.com/news",
                  "source": "Example", "category": None}], market == "jp")

    def fake_summary(payload, api_key):
        summaries.append((payload, api_key))
        return {"enabled": True, "text": "AI 摘要"}

    monkeypatch.setattr(news_api.news, "fetch_market_news", fake_fetch)
    monkeypatch.setattr(news_api.gemini, "summarize_news", fake_summary)
    client = TestClient(create_app())

    first = client.get("/api/news?slot=afternoon")
    assert first.status_code == 200
    body = first.json()
    assert body["summary"] == "AI 摘要"
    assert body["fallback"] == {"tw": False, "us": False, "jp": True}
    assert body["markets"]["tw"][0]["title"] == "tw title"
    assert fetches == ["tw", "us", "jp"] and len(summaries) == 1

    # 同日期、同時段要走快取，不可重抓新聞／重扣 Gemini。
    assert client.get("/api/news?slot=afternoon").json()["summary"] == "AI 摘要"
    assert fetches == ["tw", "us", "jp"] and len(summaries) == 1

    settings = client.get("/api/settings").json()
    assert settings["telegram_configured"] is True
    assert "secret" not in str(settings)


def test_news_test_endpoint_uses_telegram_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import telegram_push
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *args, **kwargs: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *args, **kwargs: {"enabled": True, "text": "test summary"})
    sent = {}
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda token, chat_id, text: sent.update(token=token, chat_id=chat_id, text=text)
                        or {"ok": True})
    client = TestClient(create_app())

    response = client.post("/api/news/test")
    assert response.status_code == 200
    assert response.json()["push"]["ok"] is True
    assert sent["text"] == "test summary"
