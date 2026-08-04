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
             "  * 🔢 **關鍵數據**：外資買超 120 億元。\n")
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
    assert r"\(8306\)" in out        # 括號要跳脫；代號不帶單位，不加粗（見 bold 那條規則）
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
    taiex_line = next(l for l in block.splitlines() if "加權" in l)
    assert "—" not in taiex_line   # 主要讀數要有值（國際欄缺值另有「—」是刻意的）


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
        "  * 🔢 **關鍵數據**：外資買超 120 億元。\n"
    )
    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": True, "text": full_brief})
    assert not hasattr(news_api.gemini, "summarize_news_push"), "第二支 LLM 呼叫應已移除"
    client = TestClient(create_app())

    body = client.get("/api/news?slot=afternoon").json()
    text = body["telegram_text"]
    assert "台股收盤上漲" in text                      # digest 解析出的標題
    assert "外資買超 *120 億元*" in text                # 關鍵數據併進同一行，數字加粗
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
             "  * 🔢 **關鍵數據**：外資買超 120 億元。\n")
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


def test_useful_data_drops_figures_the_title_already_states():
    """全部取自 2026-08-04 的實跑輸出。新模型把「關鍵數據」寫成光禿禿的數字，
    常常只是把標題裡的數字再講一次——那是噪音，不是增量。"""
    from stocks_power_rich.api.news import useful_data

    # 冗餘：數字標題已經有了
    for title, data in [
        ("台股開盤下跌293點早盤震盪", "293.92點"),
        ("8月88家上市公司法說會接力登場", "88家"),
        ("油價大跌逾6%緩解通膨擔憂", "6％"),
        ("Sweetgreen因環孢子蟲病疫情挫逾8%", "8%"),
        ("亞馬遜市值突破3兆美元大關", "3兆美元"),
        ("摩根大通看好標普500目標8200點", "8200點"),
        ("日圓大幅貶值一度來到157.65水位", "157.65日圓"),
    ]:
        assert useful_data(title, data) == "", (title, data)

    # 增量：標題沒提過這個數字
    assert useful_data("日經指數續跌愛德萬測試單一拖累大盤", "255日圓") == "255日圓"
    assert useful_data("台積電下挫帶動台股早盤下殺", "55元、500點") == "55元、500點"

    # 佔位語與無數字的複述一律不附
    assert useful_data("台股處置新規預計下周一上路", "來源未提供可驗證數據") == ""
    assert useful_data("某標題", "市場氣氛偏向保守") == ""
    assert useful_data("某標題", "") == ""


def test_useful_data_ignores_thousands_separators():
    """標題寫 43,386 而數據寫 43386（或反之）仍算同一個數字。"""
    from stocks_power_rich.api.news import useful_data

    assert useful_data("加權指數收在 43,386 點", "43386點") == ""
    assert useful_data("加權指數收在 43386 點", "43,386點") == ""


def test_useful_data_strips_clauses_that_restate_the_snapshot_block():
    """📈 盤面 已經用程式算出來的數字講過加權／成交／台指期，內文再講一次就是重複——
    而且是「同一個數字兩個來源」，一有出入就在同一則訊息裡自相矛盾。實跑輸出過
    「…　加權指數 43386.41 點、加權漲跌 266.66 點、成交金額 8855.1 億元。」"""
    from stocks_power_rich.api.news import useful_data

    shipped = "加權指數 43386.41 點、加權漲跌 266.66 點、成交金額 8855.1 億元。"
    assert useful_data("台股開盤權值股反彈續航弱，月線反壓下再啟4萬3攻防戰", shipped) == ""

    # 混著真增量時只剃盤面那半，不整段丟
    mixed = "外資期貨空單突破 9 萬口、台指期 43230.0 點。"
    assert useful_data("處置股大改制，台股震盪引發0050買氣", mixed) == "外資期貨空單突破 9 萬口"

    # 與盤面無關的數據完全不受影響
    # 與盤面無關的數據完全不受影響（尾端的句號會一併整理掉）
    assert useful_data("某日股標題", "營業利益增長 5 割（50%）。") == "營業利益增長 5 割（50%）"


def test_digest_ignores_colon_inside_the_bold_label():
    """模型會寫 `**關鍵數據：**`（冒號在粗體裡）也會寫 `**關鍵數據**：`。
    不正規化就會把「關鍵數據：」當成一則新聞標題，推播多出一行空的「• 關鍵數據：」
    （實跑輸出過）。"""
    from stocks_power_rich.api.news import telegram_digest

    brief = ("#### 🇯🇵 日股｜6 則精選\n"
             "* 🔥 **Lasertec 漲勢凌厲**\n"
             "  * 🔢 **關鍵數據：** 日經225指數 63754.9 點。\n"
             "* 🔥 **JUKI 攻上漲停板**\n"
             "  * 🔢 **關鍵數據**：營業利益增逾 5 成。\n")
    out = telegram_digest(brief, "afternoon", "2026-08-04")

    assert "• 關鍵數據" not in out and "🔥 關鍵數據" not in out
    assert "Lasertec" in out and "JUKI" in out
    assert len([ln for ln in out.splitlines() if ln.startswith(("•", "🔥"))]) == 2


def test_ai_failure_starts_a_cooldown_so_page_loads_dont_retry_every_time(tmp_path, monkeypatch):
    """免費層一天只有 20 次。不快取失敗是對的（免得把失敗永久化），但單獨存在就會
    變成重試風暴——每次進頁面都重打一次。失敗後要靜置一段時間。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    calls = {"n": 0}

    def failing(*a, **k):
        calls["n"] += 1
        return {"enabled": False, "text": "（AI 摘要失敗：429 RESOURCE_EXHAUSTED.）"}

    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news", failing)
    client = TestClient(create_app())

    for _ in range(4):
        body = client.get("/api/news?slot=afternoon").json()
        assert "429" not in body["telegram_text"]      # 錯誤原文仍不可進推播正文
    assert calls["n"] == 1, "冷卻期內不該一直重打 Gemini"

    # 使用者按「更新摘要」是明確意圖，可以穿透冷卻
    client.get("/api/news?slot=afternoon&refresh=1")
    assert calls["n"] == 2


def test_successful_ai_calls_are_counted_for_the_free_tier_ceiling(tmp_path, monkeypatch):
    """撞到每日 20 次上限前完全沒有跡象可查（實際發生過），所以要能看到今天用了幾次。
    被 429 擋掉的請求本來就沒算進當日配額，因此只計成功的。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    brief = "#### 🇹🇼 台股｜6 則精選\n* 🔥 **台股收盤上漲**\n"
    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": True, "text": brief})
    client = TestClient(create_app())

    assert client.get("/api/settings").json()["gemini_calls_today"] == 0
    client.get("/api/news?slot=afternoon")
    assert client.get("/api/settings").json()["gemini_calls_today"] == 1
    client.get("/api/news?slot=afternoon")            # 走快取，不該再計一次
    assert client.get("/api/settings").json()["gemini_calls_today"] == 1


def test_news_test_endpoint_accepts_an_explicit_slot(tmp_path, monkeypatch):
    """要補發某一場（例如中午那場沒發成）時得指定 slot，否則只能等到那個時段才測得到。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import telegram_push
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    brief = "#### 🇹🇼 台股｜6 則精選\n* 🔥 **台股收盤上漲**\n"
    monkeypatch.setattr(news_api.news, "fetch_market_news", lambda *a, **k: ([], False))
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": True, "text": brief})
    sent = {}
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda token, chat_id, text: sent.update(text=text) or {"ok": True})
    client = TestClient(create_app())

    body = client.post("/api/news/test?slot=midday").json()
    assert body["news"]["slot"] == "midday"
    assert sent["text"].startswith("☀️ 12:00 午間財經快訊")


def test_snapshot_shows_nikkei_even_when_missing_and_adds_change_pct(tmp_path, monkeypatch):
    """使用者回報「少了日股的大盤、漲跌%」。日經 NULL 時整欄消失，讀者只會以為
    「今天沒這欄」而不會去補資料——本站三個市場是台／美／日，日股缺席本身就是資訊。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api.news import _snapshot_from_market_daily, render_snapshot_block

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=8))
    d0 = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    d1 = datetime.now(tz).strftime("%Y-%m-%d")
    upsert_market_daily(c, {"date": d0, "taiex": 43000.0, "sox": 11000.0,
                            "n225": 63000.0, "kospi": 6300.0, "vix": 16.0})
    # 今天 n225 缺、kospi 也缺 → 日經仍要出現（寫「—」），費半要有漲跌%
    upsert_market_daily(c, {"date": d1, "taiex": 43386.0, "taiex_chg": 266.0,
                            "sox": 11311.0, "vix": 15.99})

    snap = _snapshot_from_market_daily(c)
    assert snap["國際前值"]["sox"] == 11000.0
    out = render_snapshot_block(snap, d1)

    assert "日經 —" in out, "日經缺值也要出現，不可整欄消失"
    assert "費半 11,311" in out and "▲2.83%" in out      # (11311-11000)/11000
    assert "韓股" not in out                              # 脈絡欄位缺值就不佔版面


def test_push_body_emphasises_numbers_bold_and_keywords_underline():
    """Telegram 端與網頁同一套分工：數字粗體、關鍵詞底線（兩個不同的軸）。"""
    from stocks_power_rich.api.news import compose_push_message

    body = "🇹🇼 台股｜重點掃描\n🔥 台積電拆股 252 億元，處置新制 8 月 10 日上路"
    out = compose_push_message(body, {}, {}, "afternoon", "2026-08-04")

    assert "*252 億元*" in out          # 數字粗體，單位一起
    assert "__拆股__" in out
    assert "__處置新制__" in out     # 相鄰關鍵詞併成一段，避免 ____ 破壞解析
    assert "*8 月*" in out and "*10 日*" in out
    # 哨兵字元不可外洩到訊息裡
    for ch in ("\x01", "\x02", "\x03", "\x04"):
        assert ch not in out


def test_push_emphasis_survives_markdownv2_escaping():
    """`1.09` 的小數點會被跳脫成 `1\.09`。強調必須在跳脫**前**標記、跳脫**後**才換符號，
    否則正則得處理跳脫過的形式，難寫又容易漏。"""
    from stocks_power_rich.api.news import compose_push_message

    body = "🇺🇸 美股｜重點掃描\n🔥 標普漲 1.09% (創新高)，聯準會降息預期不變"
    out = compose_push_message(body, {}, {}, "afternoon", "2026-08-04")

    assert "*1\.09%*" in out          # 粗體包住已跳脫的小數
    assert "__創新高__" in out and "__降息__" in out
    # 括號等特殊字元仍然要跳脫，否則整則會被 Telegram 退件
    assert "\(" in out and "\)" in out


def test_push_emphasis_leaves_headers_and_disclaimer_alone():
    """段落標題與免責聲明不強調——全部都粗體等於都沒粗體。"""
    from stocks_power_rich.api.news import emphasize_push_body

    out = emphasize_push_body("🇹🇼 台股｜重點掃描\n• 外資買超 252 億\n⚠️ 非投資建議，資訊僅供研究參考")
    lines = out.splitlines()
    assert "\x01" not in lines[0] and "\x03" not in lines[0]
    assert "\x01" in lines[1]
    assert "\x01" not in lines[2] and "\x03" not in lines[2]


def test_adjacent_keywords_merge_into_one_span():
    """「聯準會降息」中間沒有分隔字，各自包起來會產生 `____`——那會讓 Telegram 認不出
    配對，整則退回純文字，而且是**無聲**的（訊息照送、只是格式全失效）。"""
    from stocks_power_rich.api.news import compose_push_message

    out = compose_push_message("🇯🇵 日股｜重點掃描\n🔥 豐田上修財測並宣布拆股", {}, {},
                               "afternoon", "2026-08-04")
    assert "____" not in out
    assert "__上修財測__" in out      # 相鄰的兩個關鍵詞併成一段
    assert out.count("__") % 2 == 0, "底線符號必須成對"


def test_intl_change_pct_only_compares_against_the_immediately_previous_row(tmp_path, monkeypatch):
    """實跑輸出過「韓股 6,359 ▲13.68%」——本日值對上 5 天前的最近非空值，那是跨多日的
    累計，掛在盤面上會被當成今天的漲跌。日漲跌的定義就是「對前一個交易日」，
    那天沒值就是算不出來，寧可不顯示。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta, timezone
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api.news import _snapshot_from_market_daily, render_snapshot_block

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    tz = timezone(timedelta(hours=8))
    d = [(datetime.now(tz) - timedelta(days=n)).strftime("%Y-%m-%d") for n in (5, 1, 0)]
    upsert_market_daily(c, {"date": d[0], "taiex": 1.0, "kospi": 5593.57})
    upsert_market_daily(c, {"date": d[1], "taiex": 1.0, "sox": 11000.0})   # kospi 這天沒值
    upsert_market_daily(c, {"date": d[2], "taiex": 43000.0, "sox": 11311.0, "kospi": 6359.0})

    snap = _snapshot_from_market_daily(c)
    assert "kospi" not in snap["國際前值"], "前一列沒有韓股 → 不可跳過它去抓 5 天前的"
    out = render_snapshot_block(snap, d[2])
    assert "13.68%" not in out
    assert "韓股 6,359" in out          # 數值照顯示，只是沒有漲跌%
    assert "▲2.83%" in out              # 費半前一列有值，照算


def test_intl_change_pct_hides_a_rounded_zero(tmp_path, monkeypatch):
    """國際指數是「上一個交易時段收盤」，隔日常常還沒更新而與前一列同值。
    這時的 ▬0.00% 是資料尚未換日的假象，不是「今天沒漲跌」。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta, timezone
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api.news import _snapshot_from_market_daily, render_snapshot_block

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    tz = timezone(timedelta(hours=8))
    d0 = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    d1 = datetime.now(tz).strftime("%Y-%m-%d")
    upsert_market_daily(c, {"date": d0, "taiex": 1.0, "sox": 11311.08, "vix": 15.99})
    upsert_market_daily(c, {"date": d1, "taiex": 43000.0, "sox": 11311.08, "vix": 15.99})

    out = render_snapshot_block(_snapshot_from_market_daily(c), d1)
    assert "0.00%" not in out and "▬" not in out
    assert "費半 11,311" in out


def test_push_bold_is_reserved_for_numbers_that_carry_a_unit():
    """實跑輸出過 `H*2*O Retailing`、`標普 *500* 指數`——名稱與代號裡的數字不是數據，
    粗體之後反而讓人找不到真正的數字。帶單位才是「這是量測值」的可靠訊號。"""
    from stocks_power_rich.api.news import compose_push_message

    body = ("🇯🇵 日股｜重點掃描\n"
            "🔥 H2O Retailing 股價創新高，標普 500 指數上看 8200 點，漲 237 日圓")
    out = compose_push_message(body, {}, {}, "afternoon", "2026-08-04")

    assert "H2O" in out and "*2*" not in out          # 名稱裡的數字不動
    assert "標普 500 指數" in out and "*500*" not in out
    assert "*8200 點*" in out and "*237 日圓*" in out  # 帶單位的量測值才粗體


def test_keyword_list_excludes_routine_vocabulary():
    """使用者回報「底線都劃在不關鍵的文字」。問題不是標太多（實測 18 條裡最高的
    「財報」也只有 11%），而是這些詞在財經句子裡無所不在——它們描述每天都在發生的事，
    不是這一則的重點。只收「改變狀態」的詞。"""
    from stocks_power_rich.api.news import _TG_KEY

    routine = ["財報", "配息", "法說", "外資", "投信", "法人", "買超", "賣超",
               "空單", "融資", "利率", "通膨", "上路", "營收", "除息"]
    for w in routine:
        assert not _TG_KEY.search(w), f"{w} 是例行活動的名稱，不該當關鍵詞"

    state_change = ["處置", "新制", "鬆綁", "併購", "增資", "拆股", "財測",
                    "上修", "下修", "創新高", "漲停", "降息", "關稅"]
    for w in state_change:
        assert _TG_KEY.search(w), f"{w} 會改變接下來的狀態，應該標"


def test_keyword_never_marks_a_substring_of_a_longer_word():
    """實跑輸出過「油價與美債殖利率下行」——`利率` 被標進「殖利率」裡面。
    每個關鍵詞都要確認不是別的常見詞的一部分。"""
    from stocks_power_rich.api.news import compose_push_message, _TG_KEY

    body = "🇺🇸 美股｜重點掃描\n🔥 油價與美債殖利率下行支撐CPO與算力股"
    out = compose_push_message(body, {}, {}, "afternoon", "2026-08-04")
    assert "__" not in out, "殖利率不該被切開"

    # 其餘每個詞也不可出現在這些常見長詞裡
    for longer in ("殖利率", "匯率", "獲利率", "毛利率", "周轉率", "營益率"):
        assert not _TG_KEY.search(longer), longer


def test_ai_failure_still_pushes_real_headlines_not_just_an_apology(tmp_path, monkeypatch):
    """實際收到過的 21:10 推播只有盤面加一句「AI 摘要暫時無法使用」，內容整段消失——
    但那時三個市場的新聞**都已經抓回來了**，等於白白丟掉 60 則標題。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import news as news_api
    from stocks_power_rich.main import create_app

    def fake_fetch(market, n=20, now=None):
        return ([{"title": f"{market} 頭條{i}", "url": f"https://x/{market}{i}",
                  "source": "來源"} for i in range(1, 9)], False)

    monkeypatch.setattr(news_api.news, "fetch_market_news", fake_fetch)
    monkeypatch.setattr(news_api.gemini, "summarize_news",
                        lambda *a, **k: {"enabled": False, "text": "（AI 摘要暫停：已用完今日免費額度）"})
    client = TestClient(create_app())

    text = client.get("/api/news?slot=evening").json()["telegram_text"]
    assert "標題快覽" in text
    assert "us 頭條1" in text and "jp 頭條1" in text and "tw 頭條1" in text
    assert "🔥" in text                      # 首則仍標重點
    assert "非投資建議" in text
    # 每市場只取 6 則，不是把 8 則全倒出來
    assert "頭條7" not in text and "頭條8" not in text


def test_headline_fallback_still_filters_investment_advice():
    """標題是媒體原文、沒有經過 AI 改寫，出現「逢低布局」這類字眼的機率反而更高，
    所以那道過濾在這條路徑上更重要。"""
    from stocks_power_rich.api.news import headline_digest, strip_advice_lines

    markets = {"tw": [{"title": "台股逢低布局清單曝光"}, {"title": "證交所公布處置新制"}],
               "jp": [], "us": []}
    out = headline_digest(markets, "afternoon")
    assert out.count("• ") == 2
    filtered, dropped = strip_advice_lines(out)
    assert dropped == 1 and "逢低布局" not in filtered and "處置新制" in filtered


def test_headline_fallback_degrades_to_the_note_when_no_news_either():
    """連新聞都沒抓到時才回到那句說明——這時是真的無話可說。"""
    from stocks_power_rich.api.news import headline_digest, _AI_UNAVAILABLE_NOTE

    assert headline_digest({"tw": [], "jp": [], "us": []}, "evening") == _AI_UNAVAILABLE_NOTE


def test_headline_fallback_follows_the_slot_market_order():
    """晚間場先美股、收盤場先台股——與 telegram_digest 同一套 _PUSH_PLAN 順序。"""
    from stocks_power_rich.api.news import headline_digest

    mk = {m: [{"title": f"{m}頭條"}] for m in ("tw", "us", "jp")}
    evening = headline_digest(mk, "evening")
    afternoon = headline_digest(mk, "afternoon")
    assert evening.index("us頭條") < evening.index("tw頭條")
    assert afternoon.index("tw頭條") < afternoon.index("us頭條")
