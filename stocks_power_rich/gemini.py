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


def summarize_market(market_row: dict, api_key: str) -> dict:
    prompt = (
        "你是台股分析師，依以下大盤數據用繁體中文三句話講盤勢與法人動向，"
        "提到加權指數、三大法人、散戶多空比若有的話：\n"
        + json.dumps(market_row, ensure_ascii=False)
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
