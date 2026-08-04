"""Gemini 統整：CSV 籌碼洞察與大盤盤勢摘要。無金鑰或呼叫失敗時自動降級。"""
import json

MODEL = "gemini-2.5-flash"


def genai_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _run(prompt: str, api_key: str) -> dict:
    if not api_key:
        return {"enabled": False, "text": "（未啟用 AI 摘要：未設定 GEMINI_API_KEY）"}
    try:
        client = genai_client(api_key)
        resp = client.models.generate_content(model=MODEL, contents=prompt)
        return {"enabled": True, "text": resp.text}
    except Exception as e:  # noqa: BLE001 — 失敗即降級，不影響數據功能
        return {"enabled": False, "text": f"（AI 摘要失敗：{e}）"}


def summarize_market(data: dict, api_key: str) -> dict:
    """大盤盤後解讀。data 可為單列，或 {latest, trend(近數日), sectors(領漲/領跌)}。"""
    prompt = (
        "你是台股資深籌碼／期貨分析師。依下方 JSON 用繁體中文做盤後解讀；"
        "欄位名已含單位、衍生指標（連買賣天數/OI增減/多空比%）已算好，直接引用、嚴禁自行換算單位。\n"
        "【輸出格式（務必遵守）】純文字（直接顯示在 LINE 與網頁），嚴禁任何 Markdown 符號（**、#、表格、```）。"
        "全文恰好 7 行，依序為「國際、大盤、法人、期貨、情緒、族群、結論」，"
        "每個面向『恰好一行』、以「• 面向：」開頭；同面向多個數據整合在同一行，嚴禁拆成多行。"
        "大盤行須包含量能判讀（成交金額與量能較均量%，說出價量結構的含義）。\n"
        "【解讀要求】每行＝「關鍵數據＋判讀」：只挑該面向最重要的 1~3 個數字，"
        "並用明確判讀詞（如 承壓/背離/獨撐/誘多/縮手/回溫/急凍/止穩）講出含義，禁止只複述數字、"
        "禁止空泛形容詞與免責套話。各行判讀須前後一致；結論的多空傾向必須由前面幾行的證據支撐，"
        "若指數上漲卻判偏空（或相反），必須點出關鍵理由。\n"
        "【判讀基準】VIX<17 平穩、17~25 升溫、>25 恐慌；外資台指淨空逾 5 萬口屬重空部位；"
        "散戶多空比正=偏多、負=偏空，散戶與外資期貨方向相反即為背離（散戶偏多+外資重空→慎防誘多）；"
        "同向連 3 天以上才稱連買/連賣；費半與台股半導體高度連動，可與領漲跌類股相互印證。"
        "價量關係：價漲量增=多方動能健全、價漲量縮=追價意願不足、價跌量增=賣壓沉重、價跌量縮=賣壓收斂；"
        "量能較均量 ±20% 以上才稱顯著放大/萎縮，±10% 內稱量能持平。\n"
        "最後另起一行標『（數據解讀，非投資建議）』。\n\n"
        + json.dumps(data, ensure_ascii=False)
    )
    return _run(prompt, api_key)


def slim_markets(markets: dict) -> dict:
    """丟掉餵給模型的新聞網址，只留 title/source。

    模型從來不需要網址：它是綜合改寫、無法逐條回溯，推播底部的「延伸閱讀」是
    `api/news.py::build_reading_links` 在 Python 端從同一份 markets 取的，跟 prompt 無關。
    而 Google 新聞的 `CBMi…` 轉址網址實測每則 250～350 字元（base64 protobuf），
    60 則、又在 summarize_news 與 summarize_news_push **各帶一次**，實測占了
    整個輸入的 **~55%**（每場 15,813 → 7,083 est tokens）。純浪費且會吃掉月額度。
    回新的 dict，不可就地改動呼叫端的 markets（`build_reading_links` 還要用網址）。
    """
    return {m: [{"title": it.get("title"), "source": it.get("source")} for it in (items or [])]
            for m, items in (markets or {}).items()}


def summarize_news(payload: dict, api_key: str) -> dict:
    """Create a translated 15-story brief with five fact-backed items per market."""
    slot = payload.get("slot", "afternoon")
    must = {
        "morning": "先交代美股收盤、日股開盤與台股盤前外溢影響。",
        "midday": "先交代台股與日股盤中結構、量能、匯率與午盤風險。",
        "afternoon": "先交代台股收盤、法人與台指期，並連結日美股外部風險。",
        "evening": "先交代日股收盤、美股盤前、利率與全球風險事件。",
    }.get(slot, "先交代台股、日股與美股的最新市場連動。")
    report_date = payload.get("report_date") or payload.get("snapshot", {}).get("日期") or "最新資料日"
    prompt = (
        "你是專業、客觀的財經編輯。僅能依下方系統剛取得的市場快照與新聞清單寫作；"
        "不得補造數字、漲跌、引述、政策內容或事件細節。若來源沒有可驗證數據，必須寫『來源未提供可驗證數據』。"
        "不寫八卦、傳言、炒作或買賣建議。\n\n"
        "輸出為繁體中文。日股新聞必須先翻譯成自然、精確的繁體中文，再輸出；保留日本公司名稱、股票代碼與數字的原貌。"
        "日股只可取自 jp 新聞清單（其正常來源為株探）；不可用其他市場或想像內容補足。\n\n"
        "本報告的每個市場都要恰好 6 則新聞：🇹🇼 台股 6 則、🇯🇵 日股 6 則、🇺🇸 美股 6 則。"
        "每則僅能使用對應市場清單的內容；若該市場少於 6 則，列出全部可用項目並註明來源不足，絕不可湊數。"
        "標題不超過 28 個中文字；事件、關鍵數據、影響、關注各一行且不超過 48 個中文字。\n"
        f"不同時段的閱讀焦點：{must}\n\n"
        "請嚴格輸出下列 Markdown 與 icon 結構，不要使用程式碼區塊、表格、開場白或其他標題：\n"
        f"### 📅 {report_date} 每日財經重點速覽\n"
        "#### 🇹🇼 台股｜6 則精選\n"
        "* 🔥 **[台股新聞標題]**\n"
        "  * 🧾 **事件**：來源可支持的核心事實。\n"
        "  * 🔢 **關鍵數據**：僅填市場快照或標題中可驗證的數字；沒有則寫『來源未提供可驗證數據』。\n"
        "  * 📈 **影響**：可能影響的市場、產業或情緒。\n"
        "  * 👀 **關注**：後續數據、政策、財報或價格反應。\n"
        "（共 6 則）\n\n"
        "#### 🇯🇵 日股｜6 則精選（株探翻譯）\n"
        "（沿用完全相同的 6 則格式；每個日文標題先翻成繁體中文）\n\n"
        "#### 🇺🇸 美股｜6 則精選\n"
        "（沿用完全相同的 6 則格式）\n\n"
        "#### 📊 今日宏觀焦點\n"
        "* 🗓️ 列 3 點近期值得追蹤的經濟數據、央行、匯率、加密資產或地緣風險；沒有確定日期不可杜撰。\n\n"
        "#### 💡 客觀短評\n"
        "* 🧭 用 2 點連結台股、日股與美股的風險情緒，不得給交易指令。\n"
        "* ⚠️ **非投資建議**：市場有風險，資訊僅供研究參考。\n\n"
        "市場快照：\n"
        + json.dumps(payload.get("snapshot", {}), ensure_ascii=False)
        + "\n\n新聞清單：\n"
        + json.dumps(slim_markets(payload.get("markets", {})), ensure_ascii=False)
    )
    return _run(prompt, api_key)


def summarize_news_push(payload: dict, full_brief: str, api_key: str) -> dict:
    """Produce a compact Telegram-only brief from the same verified source set."""
    slot = payload.get("slot", "afternoon")
    report_date = payload.get("report_date", "最新資料日")
    plan = {
        "morning": ("🌅 07:00 盤前早報", "美股 6、日股 6、台股 6", "先寫美股收盤，再連結日股與台股盤前"),
        "midday": ("☀️ 12:00 午間財經快訊", "台股 6、日股 6、美股 6", "先寫台股與日股盤中結構"),
        "afternoon": ("🏁 17:00 收盤快訊", "台股 6、日股 6、美股 6", "先寫台股收盤與法人、期貨"),
        "evening": ("🌙 21:10 晚間全球焦點", "美股 6、日股 6、台股 6", "先寫美股盤前與日股收盤"),
    }.get(slot, ("📊 財經快訊", "台股 6、日股 6、美股 6", "優先寫市場連動"))
    prompt = (
        "你是 Telegram 財經快訊編輯。只可依下方完整報告與來源資料寫作，不得補造任何數字或事件。"
        "輸出繁體中文；日股項目必須翻譯日文株探標題，保留公司名、代號和數字。\n\n"
        "這是短訊，不是完整報告。嚴禁出現『來源未提供可驗證數據』、資料不足、系統提示、重複標題，"
        "也不得逐條重寫事件摘要。只挑選真正有資訊增量的重點；可驗證數字直接自然寫入句中，"
        "沒有新增數據價值時就不寫數字。\n\n"
        "⚠️ 盤面（加權指數點數、漲跌幅、台指期、成交金額）已由程式輸出在訊息最上方，"
        "**你不得再複述這些指數數字**；只寫新聞判讀與個股／政策／總經事件。\n"
        "⚠️ 🇯🇵 區塊只寫日本股市與匯市。株探的市場新聞線路在日本收盤後會混入美股編制台的"
        "稿件（標題常以「＝米国株」結尾），那些屬於 🇺🇸 區塊；若日股素材不足，寧可少寫一則，"
        "也不要把美股新聞掛在日股標題下。\n"
        "⚠️ 嚴禁任何投資勸誘或操作建議：不得出現「建議布局／建議買進／建議賣出／逢低承接／"
        "逢高減碼／目標價／可以進場／值得買進」這類字眼，也不得用「宜」「應」對讀者下指令。"
        "只陳述已發生的事實與其影響，不告訴讀者該怎麼做。\n\n"
        f"本次標題：{plan[0]} ｜ {report_date}\n"
        f"數量：{plan[1]}。焦點：{plan[2]}。\n"
        "使用下列純文字格式；不要 Markdown 的 **、不要編號、不要『關鍵數據』標籤、不要額外段落：\n\n"
        "🇹🇼 台股｜重點掃描\n"
        "• 外資今年前七月累計賣超 1.69 兆元，持股市值占比仍達 48.93%。\n"
        "• （……其餘各則同樣格式）\n\n"
        "🇯🇵 日股與外匯\n"
        "• （翻譯後的日股或匯市重點，格式同上）\n\n"
        "🇺🇸 美股趨勢\n"
        "• （科技、利率、財報或宏觀重點，格式同上）\n\n"
        "⚠️ 非投資建議，資訊僅供研究參考\n\n"
        "格式硬性規定（上面第一行是**格式範例**，不是要你照抄內容）：\n"
        "1. 每則就是「• 」加上一句完整的話，**一則只佔一行**，不可拆成兩行。\n"
        "2. **不得在句子前面加任何標籤或前綴**——不要寫「短標籤：」「重點：」「標題：」，"
        "也不要先寫一個名詞當標題再換行寫內容。直接寫事件本身。\n"
        "3. 標題行與日期由程式產生，你**不要**自己再輸出一次標題行。\n\n"
        "完整報告：\n"
        + (full_brief or "")
        + "\n\n市場快照與原始新聞：\n"
        + json.dumps({"snapshot": payload.get("snapshot", {}),
                      "markets": slim_markets(payload.get("markets", {}))}, ensure_ascii=False)
    )
    return _run(prompt, api_key)


def summarize_csv(daily_top: list, weekly: dict, industry: list, api_key: str) -> dict:
    prompt = (
        "你是籌碼分析師，依下列資料用繁體中文條列『本週大戶進、散戶退』的重點類股與個股，"
        "並各給一句選股理由（結合籌碼、技術W55、營收年增）：\n"
        + json.dumps(
            {"daily_top": daily_top[:15], "weekly": weekly, "industry": industry[:10]},
            ensure_ascii=False,
        )
    )
    return _run(prompt, api_key)
