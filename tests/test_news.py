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


# 這 20 筆是 2026-08-03 21:xx 從株探實抓的標題（照原順序）。當天推播的
# 「🇯🇵 日股與外匯」區塊混進泰森食品、ISM、NY 連銀三則美股新聞，就是因為
# 這條線路在日本收盤後由美股編制台主導——20 則裡有 9 則是美股。
_KABUTAN_REAL = [
    "ＮＹ外為：円底堅い、日米さらなる協調介入も躊躇せず",
    "タイソン・フーズ、決算受け時間外で下落＝米国株個別",
    "ホライズン３．ａｉ社、２億５０００万ドルを調達",
    "日経225先物：3日22時＝1220円安、6万2610円",
    "このあと７月調査分のＩＳＭ製造業景気指数　前回からやや上昇が見込まれる",
    "ダウ先物は大幅続伸　トランプ大統領の攻撃計画撤回で原油下落　半導体は軟調＝米国株",
    "ブリストルが時間外で上昇＝米国株個別",
    "スパーナスとインディビアが時間外で上昇＝米国株個別",
    "サークル・インターネット、時間外で５％安＝米国株個別",
    "ストラテジスト、今後は「クオリティ株」が相場をけん引と指摘",
    "ダウ先物は続伸　半導体は時間外で鈍い動き＝米国株",
    "ＮＹ連銀総裁、現在の金融政策は適切な水準",
    "ビットコイン、６万２０００ドル台に下落　リスク回避後退も独自の売り材料",
    "今夜の海外イベント・スケジュール(3日)",
    "本日の【上場来高値更新】 高速、たけびしなど16銘柄",
    "明日の経済スケジュール ─ マネタリーベースなど",
    "【明日の好悪材料】を開示情報でチェック！ (8月3日発表分)",
    "欧州為替：ドル・円は157円付近、クロス円は失速",
    "本日の【株主優待】情報 (3日 発表分)",
    "★本日の【イチオシ決算】 ＦＵＪＩ、塩野義、商船三井 (8月3日)",
]


def _items(titles):
    return [{"title": t, "url": "https://kabutan.jp/x", "source": "株探"} for t in titles]


def test_jp_sort_puts_us_desk_copy_behind_japanese_news():
    """實測那天的前 6 則有 3 則是美股；排序後前 6 必須全是日本／外匯稿。"""
    from stocks_power_rich.sources.news import sort_jp_domestic_first

    top6 = [it["title"] for it in sort_jp_domestic_first(_items(_KABUTAN_REAL))[:6]]
    for bad in ("タイソン", "ＩＳＭ", "ＮＹ連銀", "ホライズン"):
        assert not any(bad in t for t in top6), bad
    assert any("日経225先物" in t for t in top6)
    assert any("上場来高値" in t for t in top6)
    assert any("円" in t for t in top6)          # 外匯稿留在這一區（標題就是「日股與外匯」）


def test_jp_sort_is_stable_so_recency_survives_within_a_tier():
    """pick_top 已排好新到舊，分層不可打亂同層內的順序。"""
    from stocks_power_rich.sources.news import sort_jp_domestic_first

    out = [it["title"] for it in sort_jp_domestic_first(_items(_KABUTAN_REAL))]
    jp = [t for t in out if t in _KABUTAN_REAL]
    order = [_KABUTAN_REAL.index(t) for t in jp if _KABUTAN_REAL.index(t) in
             (0, 3, 14, 15, 16, 17, 18, 19)]
    assert order == sorted(order)


def test_jp_sort_never_drops_items_only_reorders():
    """排序不是過濾：日本假日可能湊不滿 6 則，全球總經要能遞補，
    不可交出一個只有 3 則的區塊。"""
    from stocks_power_rich.sources.news import sort_jp_domestic_first

    src = _items(_KABUTAN_REAL)
    out = sort_jp_domestic_first(src)
    assert sorted(t["title"] for t in out) == sorted(t["title"] for t in src)

    # 極端情形：整批都是美股稿 → 仍回傳全部，讓上層有東西可用
    only_us = _items([t for t in _KABUTAN_REAL if "米国株" in t])
    assert len(sort_jp_domestic_first(only_us)) == len(only_us)


def test_jp_relevance_tiers_use_kabutan_own_conventions():
    from stocks_power_rich.sources.news import jp_relevance

    assert jp_relevance({"title": "ブリストルが時間外で上昇＝米国株個別"}) == 0
    assert jp_relevance({"title": "今夜の海外イベント・スケジュール(3日)"}) == 0
    assert jp_relevance({"title": "本日の【株主優待】情報"}) == 2
    assert jp_relevance({"title": "欧州為替：ドル・円は157円付近"}) == 2
    assert jp_relevance({"title": "ビットコイン、６万２０００ドル台に下落"}) == 1
