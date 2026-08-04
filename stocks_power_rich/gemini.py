"""Gemini 統整：CSV 籌碼洞察與大盤盤勢摘要。無金鑰或呼叫失敗時自動降級。"""
import json

# 2026-08 從 gemini-2.5-flash 換過來：2.5-flash 已「不再開放給新使用者」，換到新的
# Google 專案後直接 404（`models.list()` 還列得出來，但 generateContent 就是不給用），
# 所以**不能靠 list 判斷可用性**。刻意釘明確版本而不是用 `gemini-flash-latest` 別名：
# 釘版本壞掉是 404、吵、看得見、好修（就像這次）；別名則會在某天無聲換模型，
# 輸出格式與品質跟著漂移而沒有任何錯誤——與本專案「寧可大聲壞掉，也不要安靜地錯」
# 的一貫取捨一致（同 資料日 D／快取守衛 那幾條）。
# 免費層的請求上限是 **每日、每專案、每模型**（`GenerateRequestsPerDayPerProjectPerModel`），
# 3.6-flash 實測只有 **20 次/天**。本專案正常一天只用 ~6 次（4 場新聞＋1 次盤勢＋零星），
# 20 次原本夠用，但幾乎沒有容錯——換模型時做幾輪比較測試就會把當天額度吃光。
# 換到 3.5-flash 取得獨立額度；它也是唯一同時接受 thinking_budget=0 與 thinking_level 的，
# 對設定比較寬容。**額度是分模型計的，所以要壓測請換一個不在正式路徑上的模型。**
MODEL = "gemini-3.5-flash"


def genai_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def _thinking_config(thinking: bool):
    """thinking=False → `thinking_level="minimal"`（關閉思考）。

    **Gemini 3.x 不吃 `thinking_budget=0`**，會直接 400 INVALID_ARGUMENT（實測
    3.6-flash／flash-latest／3.5-flash-lite 皆然，只有 3.5-flash 還收）。改用
    `thinking_level="minimal"`，實測 3.6-flash 與 3.5-flash 都回 `thoughts=None`。
    （`"off"` 不是合法值。）

    gemini-2.5-flash **預設開啟動態思考**，而思考 token 是以「輸出」計價（實測牌價
    輸出約為輸入的 8 倍），所以它往往才是帳單的大頭，而不是我們一直在減的輸入。
    新聞那三支是**機械性工作**（讀標題 → 照格式吐條列 → 翻譯），推理密度低，
    關掉思考省下的錢遠多於品質損失；`summarize_market` 例外——它要判斷背離／誘多
    這類跨指標的因果，且一天只跑一次，成本可以忽略，所以保留思考。
    """
    if thinking:
        return None
    from google.genai import types

    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="minimal"))


def _run(prompt: str, api_key: str, *, thinking: bool = True) -> dict:
    if not api_key:
        return {"enabled": False, "text": "（未啟用 AI 摘要：未設定 GEMINI_API_KEY）"}
    try:
        client = genai_client(api_key)
        resp = client.models.generate_content(
            model=MODEL, contents=prompt, config=_thinking_config(thinking))
        _log_usage(resp, thinking)
        return {"enabled": True, "text": resp.text}
    except Exception as e:  # noqa: BLE001 — 失敗即降級，不影響數據功能
        return {"enabled": False, "text": f"（AI 摘要失敗：{e}）"}


def _log_usage(resp, thinking: bool) -> None:
    """把每次呼叫的 token 用量印到 stdout（Zeabur 會收）。

    這次「月額度用盡」是**完全無聲**地發生的——撞上限前沒有任何跡象可查。
    一行用量日誌不影響功能，但下次可以直接從日誌回推是誰在燒額度。
    """
    try:
        u = resp.usage_metadata
        print(f"[gemini] thinking={'on' if thinking else 'off'} "
              f"in={u.prompt_token_count} out={u.candidates_token_count} "
              f"thoughts={getattr(u, 'thoughts_token_count', None)} "
              f"total={u.total_token_count}", flush=True)
    except Exception:  # noqa: BLE001 — 記帳失敗不能影響摘要本身
        pass


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
    return _run(prompt, api_key, thinking=False)


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
