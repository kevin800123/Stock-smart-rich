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


_SLOT_MUST = {
    # 每日財經專案踩過兩次的坑：必含主題放在被引用的檔案裡會被模型忽略，
    # 連續兩次下午場完全跳過台股。改成放在 prompt 最前面、每次都送。
    "morning": "本次摘要開頭第一則務必是「美股前一夜收盤」相關（標普／那斯達克／道瓊／費半），"
               "即使亞股或台股新聞更聳動，也要先講美股收盤。",
    "afternoon": "本次摘要開頭第一則務必是「台股當日收盤」相關——加權指數點數與漲跌、"
                 "成交量、法人動向擇要提及，即使歐美新聞更聳動，台股這則也不可省略。",
    "evening": "本次摘要開頭第一則務必是「美股盤前或當晚即將公布的重要數據／財報」相關，"
               "即使有更聳動的國際新聞，也要先講這則。",
}


def _summarize_news_legacy(payload: dict, api_key: str) -> dict:
    """每日財經新聞 → 繁中摘要。payload＝{slot, snapshot(盤面), markets:{tw,us,jp:[{title,url,source}]}}。

    設計依據每日財經專案（C:\\...\\每日財經）用血換來的三條規則：
    1) 盤面數字由程式抓、原封不動照抄——他們實測模型把台股 −3.79% 寫成 +3.76%，方向全反。
    2) 必含主題放 prompt 最前面（見 _SLOT_MUST），放在附件裡會被忽略。
    3) 只能引用給定清單裡的新聞——清單本身已經是白名單過濾過的結果，
       模型物理上拿不到清單外的來源，比對方案的「prompt 裡寫白名單」更可靠
       （他們實測 6 次有 1 次還是引用了名單外網域）。
    """
    slot = payload.get("slot", "afternoon")
    must = _SLOT_MUST.get(slot, _SLOT_MUST["afternoon"])
    prompt = (
        f"你是財經新聞編輯，為台灣讀者用繁體中文（台灣用語，不得出現任何簡體字）"
        f"統整以下財經新聞為一則精簡摘要，直接顯示在 Telegram，純文字、不用 Markdown 符號。\n\n"
        f"⚠️ {must}\n\n"
        "【盤面】以下數字已由程式從官方來源取得，原封不動照抄，不得重新計算、"
        "不得自行檢索、不得改動任何一個數字：\n"
        + json.dumps(payload.get("snapshot", {}), ensure_ascii=False) + "\n\n"
        "【新聞】只能從下面清單裡的標題挑選、統整，嚴禁引用清單以外的事件或數字："
        "台股、美股、日股各挑 3～5 則有代表性的整合成摘要，每則用一句話講清楚"
        "「發生什麼事」與「影響」；日股標題若為日文請翻成繁體中文再統整：\n"
        + json.dumps(payload.get("markets", {}), ensure_ascii=False) + "\n\n"
        "【格式】依序輸出「📈 盤面」「🌟 台股」「🌟 美股」「🌟 日股」「💡 短評」五段，"
        "盤面段落把上方數字整理成條列；短評 2～3 句總結今日／今晚重點。"
        "禁止任何投資勸誘用語（如「建議買進」「目標價」「建議關注」），"
        "最後另起一行標「⚠️ 非投資建議」。"
    )
    return _run(prompt, api_key)


def summarize_news(payload: dict, api_key: str) -> dict:
    """Turn the freshly fetched market headlines into a concise, sourced daily brief."""
    slot = payload.get("slot", "afternoon")
    must = _SLOT_MUST.get(slot, _SLOT_MUST["afternoon"])
    report_date = payload.get("report_date") or payload.get("snapshot", {}).get("日期") or "最新資料日"
    prompt = (
        "你是專業、客觀的財經編輯。請只根據下方『系統剛取得的市場快照與新聞清單』，"
        "以繁體中文完成一份三分鐘內可讀完的每日財經重點速覽。\n\n"
        "資料邊界與正確性：\n"
        "- 新聞清單是本系統剛抓取的最新來源；僅能以其中的標題、來源與市場快照作為事實依據。\n"
        "- 不得補造未提供的數字、漲跌、引述、政策內容或事件細節；來源不足時，明確寫『資料待後續驗證』。\n"
        "- 避免八卦、傳言、炒作語氣與具體買賣建議；分析須中立，並以條件式語氣說明可能影響。\n"
        "- 從清單挑選 3 至 5 項最有市場影響力、彼此不重複的頭條，優先涵蓋全球總經、主要股市、匯率／加密資產、"
        "地緣政治或重要政策；若清單不足，不要硬湊。\n"
        f"- 當前時段的閱讀重點：{must}\n\n"
        "請嚴格輸出下列 Markdown 結構，不要使用程式碼區塊、表格或開場白：\n"
        f"### 📅 {report_date} 每日財經重點速覽\n"
        "#### 一、 🌟 今日市場總覽（Top Stories）\n"
        "* **[新聞標題]**\n"
        "  * **事件摘要**：只說明來源可支持的核心事件。\n"
        "  * **市場影響**：說明可能影響的市場、資產或產業；未有量化依據時不寫精確數字。\n"
        "  * **後續指標**：列出應持續觀察的數據、政策、財報或價格反應。\n"
        "（重複上述格式共 3 至 5 則）\n\n"
        "#### 二、 📊 關鍵總經數據與焦點\n"
        "* 以 3 至 5 點列出近期值得追蹤的經濟數據、央行動態、產業風向或風險事件；沒有確定日期時，勿自行杜撰發布日期。\n\n"
        "#### 三、 💡 專業分析師短評\n"
        "- 寫 1 至 2 段宏觀觀察，連結台股、亞股或全球風險情緒，但不可給出交易指令。\n"
        "- **非投資建議**：市場有風險，資訊僅供研究參考。\n\n"
        "市場快照：\n"
        + json.dumps(payload.get("snapshot", {}), ensure_ascii=False)
        + "\n\n新聞清單：\n"
        + json.dumps(payload.get("markets", {}), ensure_ascii=False)
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
