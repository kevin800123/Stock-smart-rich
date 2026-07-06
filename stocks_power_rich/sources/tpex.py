"""櫃買中心（TPEx）上櫃個股三大法人買賣超（帶日期）。

回傳格式對齊 twse.parse_t86：{代號: {name, foreign, trust, dealer, total}}（單位：張）。
欄位為固定位置：0 代號、1 名稱、4 外資買賣超股數(不含外資自營商)、13 投信、16 自營商(合計)、
末欄 三大法人買賣超股數合計。
"""
import datetime

import httpx

DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
OTC_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"  # 上櫃公司基本資料
DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"  # 上櫃盤後每日行情


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


def parse_otc_names(records: list) -> dict:
    """上櫃公司基本資料 mopsfin_t187ap03_O → {代號: 公司簡稱}。"""
    out: dict[str, str] = {}
    for r in records or []:
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        name = str(r.get("CompanyAbbreviation", "")).strip()
        if code and name:
            out[code] = name
    return out


def fetch_otc_names() -> dict:
    """上櫃公司 {代號: 簡稱}。近乎靜態，呼叫端宜快取。查無回空 dict。"""
    try:
        j = httpx.get(OTC_COMPANY_URL, timeout=25,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        return parse_otc_names(j)
    except Exception:  # noqa: BLE001
        return {}


def parse_otc_quotes(payload: dict) -> dict:
    """dailyQuotes 上櫃盤後行情 → {代號: {name, close, chg_pct}}。

    欄位固定位置：0 代號、1 名稱、2 收盤、3 漲跌（帶號價差，元）；以昨收回推漲跌%。
    """
    out: dict[str, dict] = {}
    for t in payload.get("tables") or []:
        for r in t.get("data") or []:
            if not r or len(r) < 4:
                continue
            code = str(r[0]).strip()
            close, diff = _f(r[2]), _f(r[3])
            if not code or close is None or diff is None:
                continue
            prev = close - diff
            out[code] = {"name": str(r[1]).strip(), "close": close,
                         "chg_pct": round(diff / prev * 100, 2) if prev else 0.0}
    return out


def parse_otc_ohlc(payload: dict) -> dict:
    """dailyQuotes 上櫃盤後行情 → {代號: {open,high,low,close}}。

    位置欄位：0 代號、2 收盤、4 開盤、5 最高、6 最低。只取 4 位數普通股（排除 ETF 00xx）。
    """
    out: dict[str, dict] = {}
    for t in payload.get("tables") or []:
        for r in t.get("data") or []:
            if not r or len(r) < 7:
                continue
            code = str(r[0]).strip()
            if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
                continue
            c, o, h, l = _f(r[2]), _f(r[4]), _f(r[5]), _f(r[6])
            if None not in (o, h, l, c):
                out[code] = {"open": o, "high": h, "low": l, "close": c}
    return out


def fetch_otc_ohlc(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyQuotes 取指定日全上櫃個股 OHLC（型態選股用）。查無回空。"""
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_ohlc(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_otc_quotes(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyQuotes 取指定日（預設今天）全上櫃個股收盤與漲跌%。查無回空。"""
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_quotes(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


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
