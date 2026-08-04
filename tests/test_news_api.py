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
    # telegram_text 現在是組裝結果：AI 文字（已跳脫）＋ 底部延伸閱讀連結，
    # 不再是 Gemini 原樣輸出（盤面／過濾／連結／跳脫都在 compose_push_message 做）。
    assert "AI 摘要" in body["summary"]
    assert "延伸閱讀" in body["telegram_text"]
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


def test_all_telegram_slots_request_six_items_per_market():
    from stocks_power_rich.api.news import _PUSH_PLAN

    for _, (_, plan) in _PUSH_PLAN.items():
        assert dict(plan) == {"tw": 6, "jp": 6, "us": 6}


def test_news_test_endpoint_uses_telegram_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import telegram_push
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *args, **kwargs: ([], False))
    brief = ("#### 🇹🇼 台股｜6 則精選\n"
             "* 🔥 **台股收盤上漲**\n"
             "  * 🔢 **關鍵數據**：加權指數上漲 100 點。\n")
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *args, **kwargs: {"enabled": True, "text": brief})
    sent = {}
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda token, chat_id, text: sent.update(token=token, chat_id=chat_id, text=text)
                        or {"ok": True})
    client = TestClient(create_app())

    response = client.post("/api/news/test")
    assert response.status_code == 200
    assert response.json()["push"]["ok"] is True
    # 送出的是組裝後的訊息（程式標題＋AI 內文），不是 Gemini 原樣輸出
    assert "非投資建議" in sent["text"]
    # 標題日期由程式產生並跳脫（MarkdownV2 的 - 必須跳脫，否則整則會被退件）
    date = response.json()["news"]["date"]
    assert sent["text"].splitlines()[0].endswith(telegram_push.escape_mdv2(date))


# ===== 推播組裝（純函式；不打網路、不發 Telegram） =====

def test_render_snapshot_block_uses_program_numbers_not_llm():
    """盤面必須由程式輸出。先前推播沒有這一段，模型就把指數揉進散文
    （「加權指數收漲266點至43386點」），等於繞過本專案最重要的那條防線。"""
    from stocks_power_rich.api.news import render_snapshot_block

    snap = {"日期": "2026-08-03", "加權指數": 43386.0, "加權漲跌": 266.0,
            "成交金額(億)": 4102.0, "台指期": 43400.0,
            "那斯達克100/費半等國際指標若有": {"sox": 5123.0, "vix": 17.2}}
    out = render_snapshot_block(snap, "2026-08-03")
    assert out.startswith("📈 盤面")
    assert "43,386" in out and "▲266" in out
    assert "+0.62%" in out          # 266/(43386-266) → 由 Python 算，不交給 LLM
    assert "43,400" in out and "4,102 億" in out
    assert "費半 5,123" in out and "VIX 17.20" in out
    assert "截至" not in out        # 日期相同就不標


def test_render_snapshot_block_marks_stale_date_and_never_mixes_value_with_no_data():
    """早上 07:00 那場拿到的必然是前一交易日收盤 → 標「截至 MM-DD」。
    缺值一律寫「—」，不可出現「43,386（無資料）」這種自相矛盾格式。"""
    from stocks_power_rich.api.news import render_snapshot_block

    out = render_snapshot_block({"日期": "2026-08-01", "加權指數": 43386.0,
                                 "加權漲跌": None, "成交金額(億)": None, "台指期": None},
                                "2026-08-03")
    assert "截至 08-01" in out
    assert "• 台指期　—" in out and "• 成交　—" in out
    assert "無資料" not in out
    for line in out.splitlines():
        assert not (any(ch.isdigit() for ch in line) and "—" in line)


def test_render_snapshot_block_empty_snapshot_returns_empty():
    from stocks_power_rich.api.news import render_snapshot_block
    assert render_snapshot_block({}, "2026-08-03") == ""


def test_advice_filter_blocks_the_line_that_actually_shipped():
    """實際推播出去過的句子，必須被擋掉。"""
    from stocks_power_rich.api.news import is_advice_line, strip_advice_lines

    shipped = "• 法人分析台股8月可能在39000至45000點區間震盪，建議在半年線附近謹慎布局。"
    assert is_advice_line(shipped)
    text, dropped = strip_advice_lines("🇹🇼 台股｜重點掃描\n" + shipped + "\n• 正常敘述一句。")
    assert dropped == 1
    assert "謹慎布局" not in text and "正常敘述一句" in text


def test_advice_filter_does_not_over_fire_on_plain_news():
    """「金管會建議業者強化風控」是新聞事實、不是對讀者的勸誘，不可誤殺；
    企業「布局」也不是投資建議。"""
    from stocks_power_rich.api.news import is_advice_line

    for ok in ("• 金管會建議業者強化風控。",
               "• 台積電加速布局先進製程，資本支出上修。",
               "• 加權指數收在 43,386 點，成交量放大。"):
        assert not is_advice_line(ok), ok
    for bad in ("• 分析師給出目標價 1200 元。",
                "• 建議逢低承接半導體權值股。",
                "• 建議減碼電子股。"):
        assert is_advice_line(bad), bad


def test_mark_lead_bullets_flags_first_item_of_each_market():
    from stocks_power_rich.api.news import mark_lead_bullets

    text = ("🇹🇼 台股｜重點掃描\n• 第一則\n• 第二則\n\n"
            "🇯🇵 日股與外匯\n• 日股第一則\n• 日股第二則")
    out = mark_lead_bullets(text)
    assert "🔥 第一則" in out and "• 第二則" in out
    assert "🔥 日股第一則" in out and "• 日股第二則" in out
    assert out.count("🔥") == 2


def test_build_reading_links_uses_short_visible_label_per_market():
    """Google 新聞網址實測 174～354 字元且解不出短網址，只能靠行內連結縮短「可見長度」。"""
    from stocks_power_rich.api.news import build_reading_links

    long_url = "https://news.google.com/rss/articles/" + "A" * 200
    markets = {"tw": [{"title": "t", "url": long_url, "source": "中央社"}],
               "jp": [{"title": "j", "url": "https://kabutan.jp/news/?b=n1", "source": "株探"}],
               "us": [{"title": "u", "url": long_url, "source": "Reuters"}]}
    out = build_reading_links(markets)
    assert "延伸閱讀" in out
    assert "[🇹🇼 中央社](" in out and "[🇯🇵 株探](" in out and "[🇺🇸 Reuters](" in out
    assert long_url in out                      # 網址完整保留、沒被跳脫破壞
    assert out.count("](") == 3                 # 每市場一條
    assert "來源" not in out                     # 刻意叫「延伸閱讀」，不宣稱是引用出處


def test_build_reading_links_empty_when_no_urls():
    from stocks_power_rich.api.news import build_reading_links
    assert build_reading_links({"tw": [], "jp": [], "us": []}) == ""


def test_compose_push_escapes_the_characters_that_broke_markdownv2():
    """實際推播含 (8306)、3.5%、48.93%。 這些 MarkdownV2 特殊字元且未跳脫，
    導致每則都 400 後退回純文字——連結與粗體因此永遠失效。"""
    from stocks_power_rich.api.news import compose_push_message

    ai = "🇯🇵 日股與外匯\n• 三菱UFJ (8306) 最終利益年增48%，創同期新高。\n• 標普500震幅達3.5%。"
    out = compose_push_message(ai, {}, {}, "afternoon", "2026-08-03")
    assert r"\(8306\)" in out
    assert "48%" in out and r"\." in out          # . 與 ( ) - 皆已跳脫
    assert "(" not in out.replace(r"\(", "")      # 沒有任何未跳脫的左括號


def test_compose_push_puts_snapshot_first_and_links_last():
    from stocks_power_rich.api.news import compose_push_message

    snap = {"日期": "2026-08-03", "加權指數": 43386.0, "加權漲跌": 266.0,
            "成交金額(億)": 4102.0, "台指期": 43400.0}
    markets = {"tw": [{"title": "t", "url": "https://x.example/a", "source": "中央社"}],
               "jp": [], "us": []}
    ai = "🇹🇼 台股｜重點掃描\n• 第一則\n• 建議逢低承接。"
    out = compose_push_message(ai, snap, markets, "afternoon", "2026-08-03")
    assert out.index("盤面") < out.index("第一則") < out.index("延伸閱讀")
    assert "逢低承接" not in out                  # 投資建議在組裝階段就被擋掉
    assert "🔥 第一則" in out


def test_snapshot_skips_todays_empty_row_and_walks_back(tmp_path, monkeypatch):
    """market_daily 當天早上就會有列，指數卻要收盤後才寫入。

    實測 2026-08-03 那列 taiex/turnover/tx_price 全是 NULL，直接取最新日期會讓整個
    盤面區塊變成三個「—」（實際跑出來過）。要往回找真的有指數的那一天，
    並由 render_snapshot_block 標「截至 MM-DD」——同 /api/inst-ranking 的處理。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta, timezone
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api.news import _snapshot_from_market_daily, render_snapshot_block

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    taipei = timezone(timedelta(hours=8))
    today = datetime.now(taipei).strftime("%Y-%m-%d")
    prev = (datetime.now(taipei) - timedelta(days=1)).strftime("%Y-%m-%d")
    upsert_market_daily(c, {"date": prev, "taiex": 43634.19, "taiex_chg": -20.65,
                            "turnover": 7476.5, "tx_price": 43907.0})
    upsert_market_daily(c, {"date": today, "inst_foreign": -100.0})   # 建了列但指數還沒進來

    snap = _snapshot_from_market_daily(c)
    assert snap["加權指數"] == 43634.19          # 不是今天那列的 None
    assert snap["日期"] == prev
    block = render_snapshot_block(snap, today)
    assert "43,634" in block and "截至" in block
    assert block.count("—") == 0


def test_snapshot_returns_empty_when_nothing_recent_has_an_index(tmp_path, monkeypatch):
    """一週內都沒有指數 → 不出盤面區塊，也不端出過期數字充當今天。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api.news import _snapshot_from_market_daily

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2020-01-02", "taiex": 12000.0})   # 遠古資料
    assert _snapshot_from_market_daily(c) == {}


def test_news_never_leaks_raw_gemini_error_when_main_call_fails(tmp_path, monkeypatch):
    """實測發生過：Gemini 503 時 news_logic 把 gemini._run 的例外字串
    （含原始 error dict）直接送進 Telegram 正文。summarize_news 失敗時必須
    改用友善提示，不可把 "AI 摘要失敗：503 UNAVAILABLE. {'error': {...}}" 這種
    內部訊息透出去。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    raw_error = ("（AI 摘要失敗：503 UNAVAILABLE. {'error': {'code': 503, 'message': "
                 "'This model is currently experiencing high demand.', 'status': 'UNAVAILABLE'}})")
    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": False, "text": raw_error})
    client = TestClient(create_app())

    body = client.get("/api/news?slot=afternoon").json()
    assert "UNAVAILABLE" not in body["telegram_text"]
    assert "503" not in body["telegram_text"]
    assert "error" not in body["telegram_text"]
    assert "暫時無法使用" in body["telegram_text"]
    assert body["enabled"] is False


def test_push_text_is_digested_from_full_brief_without_a_second_llm_call(tmp_path, monkeypatch):
    """推播條列改由 Python 從完整版壓出來，全程只呼叫一次 Gemini。

    先前是「Gemini 寫完整版 → 再叫 Gemini 壓縮成推播版」，同一份素材付兩次錢。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    full_brief = (
        "#### 🇹🇼 台股｜6 則精選\n"
        "* 🔥 **台股收盤上漲**\n"
        "  * 🔢 **關鍵數據**：加權指數上漲 100 點。\n"
    )
    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": True, "text": full_brief})
    assert not hasattr(news_api.gemini, "summarize_news_push"), "第二支 LLM 呼叫應已移除"
    client = TestClient(create_app())

    body = client.get("/api/news?slot=afternoon").json()
    text = body["telegram_text"]
    assert "台股收盤上漲" in text                      # digest 解析出的標題
    assert "加權指數上漲 100 點" in text                # 關鍵數據併進同一行
    assert "🔥 台股收盤上漲" in text                    # 首則標 🔥（靠 • 開頭才認得出來）
    assert "####" not in text and "**" not in text      # 不可把 markdown 原樣倒出去
    assert body["enabled"] is True


def test_digest_output_still_feeds_the_advice_filter():
    """安全性回歸防線：digest 必須吐 `• ` 條列，因為 strip_advice_lines 與
    mark_lead_bullets 都只認 `•`／`🔥` 開頭。舊版吐「1. 標題」＋下一行「   🔢 數據」，
    若直接沿用，投資建議過濾器會**整個失效**而且沒有任何錯誤。"""
    from stocks_power_rich.api.news import telegram_digest, strip_advice_lines

    brief = ("#### 🇹🇼 台股｜6 則精選\n"
             "* 🔥 **分析師建議逢低承接半導體權值股**\n"
             "  * 🔢 **關鍵數據**：目標價 1200 元。\n"
             "* 🔥 **台股收盤上漲**\n"
             "  * 🔢 **關鍵數據**：加權指數上漲 100 點。\n")
    digest = telegram_digest(brief, "afternoon", "2026-08-04")

    for line in digest.splitlines():
        if "台股收盤上漲" in line or "逢低承接" in line:
            assert line.startswith("• "), f"必須是條列才過得了濾網: {line!r}"

    filtered, dropped = strip_advice_lines(digest)
    assert dropped == 1
    assert "逢低承接" not in filtered and "目標價" not in filtered
    assert "台股收盤上漲" in filtered


def test_digest_never_dumps_raw_markdown_when_parsing_fails():
    """完整版格式改版而解析不到任何一則時，不可 return summary——那會把整份
    markdown（#### 與 ** 都在）倒進 Telegram。"""
    from stocks_power_rich.api.news import telegram_digest

    out = telegram_digest("### 標題\n這份格式完全不同\n**粗體**", "afternoon", "2026-08-04")
    assert "####" not in out and "**" not in out
    assert "這份格式完全不同" not in out
    assert "每日財經新聞頁" in out


def test_digest_keeps_slot_specific_market_order():
    """早報先美股、收盤先台股——_PUSH_PLAN 的順序必須保留。"""
    from stocks_power_rich.api.news import telegram_digest

    brief = ""
    for flag, name in (("🇹🇼", "台股"), ("🇺🇸", "美股"), ("🇯🇵", "日股")):
        brief += f"#### {flag} {name}｜6 則精選\n* 🔥 **{name}第一則**\n"

    morning = telegram_digest(brief, "morning", "2026-08-04")
    afternoon = telegram_digest(brief, "afternoon", "2026-08-04")
    assert morning.index("美股第一則") < morning.index("台股第一則")
    assert afternoon.index("台股第一則") < afternoon.index("美股第一則")
