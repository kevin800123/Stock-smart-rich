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
        "每個面向『恰好一行』、以「• 面向：」開頭；同面向多個數據整合在同一行，嚴禁拆成多行。\n"
        "【解讀要求】每行＝「關鍵數據＋判讀」：只挑該面向最重要的 1~3 個數字，"
        "並用明確判讀詞（如 承壓/背離/獨撐/誘多/縮手/回溫/急凍/止穩）講出含義，禁止只複述數字、"
        "禁止空泛形容詞與免責套話。各行判讀須前後一致；結論的多空傾向必須由前面幾行的證據支撐，"
        "若指數上漲卻判偏空（或相反），必須點出關鍵理由。\n"
        "【判讀基準】VIX<17 平穩、17~25 升溫、>25 恐慌；外資台指淨空逾 5 萬口屬重空部位；"
        "散戶多空比正=偏多、負=偏空，散戶與外資期貨方向相反即為背離（散戶偏多+外資重空→慎防誘多）；"
        "同向連 3 天以上才稱連買/連賣；費半與台股半導體高度連動，可與領漲跌類股相互印證。\n"
        "最後另起一行標『（數據解讀，非投資建議）』。\n\n"
        + json.dumps(data, ensure_ascii=False)
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
