"""Nasdaq 官方公開歷史指數 API——費半（^SOX／PHLX Semiconductor Index）專用歷史備援。

yfinance 從 Zeabur 出站 IP 整段被 429 擋死（見 intl.py 開頭說明），FRED 沒有 SOX 的
series（見 fred.py 開頭說明），sox 因此只有「今天」那一格靠 TradingView 帶日期快照
補上（intl.TV_DATED），歷史序列在雲端一直是 NULL。api.nasdaq.com 是完全不同的基礎
設施（非 Yahoo），不需要金鑰，只要求瀏覽器類 User-Agent 與 Accept header；實測單次
請求即可回傳任意區間的日線 OHLC（148 個交易日一次到位，不必分批）。
"""
from datetime import date, timedelta

import httpx

_URL = "https://api.nasdaq.com/api/quote/SOX/historical"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"}


def parse_sox_history(rows: list) -> dict:
    """Nasdaq API 的 `data.tradesTable.rows` → {iso_date: close}。

    "date" 是 MM/DD/YYYY；"close" 帶千分位逗號（"12,179.26"）且缺值時是 "N/A"/"--"，
    兩者都要濾掉而非當成 0。單筆壞資料只跳過那一筆，不讓整批解析失敗。
    """
    out: dict[str, float] = {}
    for r in rows or []:
        d, c = r.get("date"), r.get("close")
        if not d or not c or c in ("N/A", "--"):
            continue
        try:
            mm, dd, yyyy = d.split("/")
            out[f"{yyyy}-{mm}-{dd}"] = float(str(c).replace(",", ""))
        except (ValueError, AttributeError):
            continue
    return out


def fetch_sox_history(days: int = 120) -> dict:
    """近 days 天費半日線收盤 → {iso_date: close}。非 200／例外一律回空 dict
    （呼叫端維持 NULL 等下次回補，備援失敗不影響其他來源）。"""
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    try:
        r = httpx.get(_URL, headers=_HEADERS, timeout=15,
                      params={"assetclass": "index", "fromdate": start,
                              "todate": end, "limit": 9999})
        if r.status_code != 200:
            return {}
        rows = ((r.json().get("data") or {}).get("tradesTable") or {}).get("rows") or []
        return parse_sox_history(rows)
    except Exception:  # noqa: BLE001
        return {}
