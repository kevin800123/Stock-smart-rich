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
        "你是台股資深籌碼／期貨分析師。依下方 JSON 用繁體中文做盤後解讀。\n"
        "【輸出格式（務必遵守）】結果會直接顯示在 LINE 訊息與網頁：只能輸出純文字，"
        "嚴禁任何 Markdown 符號（**、#、表格、```）。必須是條列，每一點『自成一行』並以「• 」開頭，"
        "點與點之間換行；嚴禁寫成連續段落。專業精簡、每行力求 40 字內、務必引用具體數字。依序輸出這幾點：\n"
        "• 國際：費半/日經/韓股/黃金/美元兌日圓/比特幣的漲跌，點出對台股氛圍的影響（有資料才寫）\n"
        "• 大盤：加權指數收盤與當日強弱\n"
        "• 現貨法人：外資／投信／自營買賣超（點出近日是連買或連賣）\n"
        "• 期貨籌碼：外資台指淨未平倉（淨多/淨空）、散戶多空比，研判主力與散戶是否背離\n"
        "• 情緒：VIX、融資增減（有資料才寫）\n"
        "• 族群：具體點名領漲與領跌類股及其 %\n"
        "• 結論：盤勢傾向（偏多／偏空／中性）＋一句數據理由\n"
        "禁止空泛形容詞與免責套話；最後另起一行標『（數據解讀，非投資建議）』。\n\n"
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
