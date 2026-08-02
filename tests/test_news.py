import pathlib
from datetime import datetime, timedelta, timezone

from stocks_power_rich.sources import news

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FIXTURE_NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_registrable_domain_handles_two_and_three_level():
    assert news.registrable_domain("www.udn.com") == "udn.com"
    assert news.registrable_domain("money.udn.com") == "udn.com"
    assert news.registrable_domain("ctee.com.tw") == "ctee.com.tw"
    assert news.registrable_domain("www.ctee.com.tw") == "ctee.com.tw"
    assert news.registrable_domain("") == ""


def test_parse_news_rss_real_fixture_strips_source_suffix_and_extracts_domain():
    xml = (FIXTURES / "google_news_tw.xml").read_bytes()
    items = news.parse_news_rss(xml)
    assert len(items) >= 50
    first = items[0]
    assert first["title"]
    assert not first["title"].rstrip().endswith(first["source"])  # 尾端來源已切掉
    assert first["url"].startswith("https://")
    assert first["domain"]
    assert first["published"]  # RFC822 pubDate 都解析得出來


def test_parse_news_rss_malformed_xml_returns_empty():
    assert news.parse_news_rss(b"<not><valid") == []


def test_parse_news_rss_skips_items_without_title_or_link():
    xml = b"""<rss><channel>
      <item><title>Only title</title></item>
      <item><link>https://x.com/a</link></item>
      <item><title>OK</title><link>https://x.com/b</link></item>
    </channel></rss>"""
    items = news.parse_news_rss(xml)
    assert len(items) == 1 and items[0]["title"] == "OK"


def test_parse_kabutan_real_fixture_extracts_time_category_and_absolute_url():
    html = (FIXTURES / "kabutan_marketnews.html").read_text(encoding="utf-8")
    items = news.parse_kabutan(html)
    assert len(items) >= 10
    first = items[0]
    assert first["source"] == "株探" and first["domain"] == "kabutan.jp"
    assert first["url"].startswith("https://kabutan.jp/")
    assert first["published"]
    assert first["category"]  # 市況／材料／特集…


def test_parse_kabutan_no_rows_returns_empty():
    assert news.parse_kabutan("<html><body>no rows here</body></html>") == []


def test_pick_top_dedups_by_normalized_title_prefix():
    now = datetime.now(timezone.utc)
    items = [
        {"title": "台積電法說會優於預期，股價大漲", "published": now.isoformat()},
        {"title": "台積電法說會優於預期！股價大漲逾5%",
         "published": (now - timedelta(minutes=5)).isoformat()},
        {"title": "聯發科新品發表", "published": now.isoformat()},
    ]
    out = news.pick_top(items, n=20)
    assert len(out) == 2  # 前兩則標題前綴相同視為同一則，只留較新的
    assert out[0]["title"].startswith("台積電法說會優於預期")


def test_pick_top_filters_by_time_window():
    now = datetime.now(timezone.utc)
    items = [
        {"title": "新的", "published": now.isoformat()},
        {"title": "太舊了", "published": (now - timedelta(hours=48)).isoformat()},
    ]
    out = news.pick_top(items, n=20, hours=36)
    assert [it["title"] for it in out] == ["新的"]


def test_pick_top_36_hour_window_covers_friday_night_to_monday_morning():
    """07:00 那場要涵蓋前一晚美股收盤；24 小時在週一早上會把週五晚間的新聞濾掉。"""
    now = datetime.now(timezone.utc)
    friday_night = now - timedelta(hours=30)
    items = [{"title": "週五美股收盤", "published": friday_night.isoformat()}]
    assert news.pick_top(items, n=20, hours=36) == items
    assert news.pick_top(items, n=20, hours=24) == []


def test_pick_top_keeps_undated_items_as_filler_not_dropped():
    now = datetime.now(timezone.utc)
    items = [
        {"title": "有時間", "published": now.isoformat()},
        {"title": "沒時間", "published": None},
    ]
    out = news.pick_top(items, n=20)
    assert {it["title"] for it in out} == {"有時間", "沒時間"}
    assert out[0]["title"] == "有時間"  # 有時間戳的排在前面


def test_pick_top_respects_limit():
    now = datetime.now(timezone.utc)
    items = [{"title": f"新聞{i}", "published": now.isoformat()} for i in range(30)]
    assert len(news.pick_top(items, n=20)) == 20


def test_fetch_google_news_filters_to_whitelist_and_caps(monkeypatch):
    xml = (FIXTURES / "google_news_tw.xml").read_bytes()

    class FakeResp:
        status_code = 200
        content = xml

    monkeypatch.setattr(news.httpx, "get", lambda *a, **k: FakeResp())
    items = news.fetch_google_news("tw", n=20, now=FIXTURE_NOW)
    assert items
    assert all(it["domain"] in news.WHITELIST for it in items)
    assert len(items) <= 20


def test_fetch_google_news_unknown_market_returns_empty():
    assert news.fetch_google_news("xx", n=20) == []


def test_fetch_google_news_network_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("nope")
    monkeypatch.setattr(news.httpx, "get", boom)
    assert news.fetch_google_news("tw", n=20) == []


def test_fetch_kabutan_sleeps_between_pages_per_robots_crawl_delay(monkeypatch):
    html = (FIXTURES / "kabutan_marketnews.html").read_text(encoding="utf-8")
    calls = []

    class FakeResp:
        status_code = 200
        text = html

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResp()

    slept = []
    monkeypatch.setattr(news.httpx, "get", fake_get)
    monkeypatch.setattr(news.time, "sleep", lambda s: slept.append(s))
    items = news.fetch_kabutan(n=20, now=FIXTURE_NOW)
    assert len(calls) == len(news._KABUTAN_PAGES)
    assert slept == [news._KABUTAN_CRAWL_DELAY] * (len(news._KABUTAN_PAGES) - 1)
    assert items


def test_fetch_market_news_kabutan_success_no_fallback(monkeypatch):
    html = (FIXTURES / "kabutan_marketnews.html").read_text(encoding="utf-8")

    class FakeResp:
        status_code = 200
        text = html

    monkeypatch.setattr(news.httpx, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    items, fallback = news.fetch_market_news("jp", n=20, now=FIXTURE_NOW)
    assert items and fallback is False
    assert all(it["domain"] == "kabutan.jp" for it in items)


def test_fetch_market_news_kabutan_empty_falls_back_to_google_news(monkeypatch):
    """株探版面改版導致一列都解析不到時，要退回 Google 新聞而不是整段開天窗，
    但呼叫端必須看得出 fallback=True（不能悄悄換來源）。"""
    xml = (FIXTURES / "google_news_tw.xml").read_bytes()

    class KabutanResp:
        status_code = 200
        text = "<html>版面改版，抓不到任何一列</html>"

    class GoogleResp:
        status_code = 200
        content = xml

    def fake_get(url, **kwargs):
        if "kabutan.jp" in url:
            return KabutanResp()
        return GoogleResp()

    monkeypatch.setattr(news.httpx, "get", fake_get)
    monkeypatch.setattr(news.time, "sleep", lambda s: None)
    items, fallback = news.fetch_market_news("jp", n=20, now=FIXTURE_NOW)
    assert fallback is True
    assert items


def test_fetch_market_news_tw_us_never_fallback(monkeypatch):
    xml = (FIXTURES / "google_news_tw.xml").read_bytes()

    class FakeResp:
        status_code = 200
        content = xml

    monkeypatch.setattr(news.httpx, "get", lambda *a, **k: FakeResp())
    for market in ("tw", "us"):
        items, fallback = news.fetch_market_news(market, n=20, now=FIXTURE_NOW)
        assert fallback is False
