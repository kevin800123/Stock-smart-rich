"""櫃買中心（TPEx）上櫃個股三大法人買賣超（帶日期）。

回傳格式對齊 twse.parse_t86：{代號: {name, foreign, trust, dealer, total}}（單位：張）。
欄位為固定位置：0 代號、1 名稱、4 外資買賣超股數(不含外資自營商)、13 投信、16 自營商(合計)、
末欄 三大法人買賣超股數合計。
"""
import datetime

import httpx

DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_tpex_insti(payload: dict) -> dict:
    out = {}
    for t in payload.get("tables") or []:
        rows = t.get("data") or []
        if len(rows) < 20:
            continue
        for r in rows:
            if not r or len(r) < 24:
                continue

            def lots(i):
                v = _f(r[i])
                return round(v / 1000) if v is not None else None

            out[str(r[0]).strip()] = {
                "name": str(r[1]).strip(),
                "foreign": lots(4), "trust": lots(13), "dealer": lots(16),
                "total": lots(len(r) - 1),
            }
    return out


def fetch_tpex_insti(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyTrade 取指定日（預設今天）全上櫃個股三大法人買賣超。"""
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_TRADE_URL, params={"type": "Daily", "date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_tpex_insti(j)
    except Exception:  # noqa: BLE001
        pass
    return {}
