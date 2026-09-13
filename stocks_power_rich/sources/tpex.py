"""櫃買中心（TPEx）上櫃個股三大法人買賣超（帶日期）。

回傳格式對齊 twse.parse_t86：{代號: {name, foreign, trust, dealer, total}}（單位：張）。
欄位為固定位置：0 代號、1 名稱、4 外資買賣超股數(不含外資自營商)、13 投信、16 自營商(合計)、
末欄 三大法人買賣超股數合計。
"""
import datetime
import json
import time

import httpx

DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
OTC_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"  # 上櫃公司基本資料
DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"  # 上櫃盤後每日行情
OTC_MARGIN_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"  # 上櫃融資融券餘額



# 連續幾次「一個 byte 都沒拿到」才放棄。有進度的斷線不算——只要還在前進就繼續接。
RESUME_MAX_STALLS = 3
# 總請求數的硬上限，防止伺服器每次只吐極少量時無限拖下去。
RESUME_MAX_REQUESTS = 20


def get_resumable(url: str, *, timeout: float = 30, transport=None, sleep=time.sleep) -> bytes:
    """抓櫃買的靜態 JSON 檔，**傳到一半被切斷就從斷點續傳**。

    **為什麼需要**：2026-09-12／13 兩晚 21:00 的月營收告警，實測是櫃買 openapi 回 200 且
    宣告 `Content-Length: 496010`，卻在傳到 65～212 KB 時把連線切斷——本機 curl exit 56、
    httpx `RemoteProtocolError`，Zeabur(Linux) 上則是 `ReadError: [Errno 104]`。
    換 User-Agent、換 Accept-Encoding 都一樣斷；約 10 分鐘後自行恢復；同時段上市 openapi 正常。
    **單純重試救不了**：那段時間每一次都斷。但伺服器宣告了 `Accept-Ranges: bytes` 與 `ETag`
    （它是 nginx 送的靜態檔），所以斷在哪就從哪接著要剩下的部分。

    三條不能省的規則：
    - **`If-Range` 帶 ETag**：下載途中檔案若被重新產生，伺服器會回 200 整份新檔，此時必須
      從頭來過。否則會得到「舊檔前半＋新檔後半」——那種拼接的 JSON 仍可能解析得過，
      而且內容看起來完全正常。
    - **回 200 而非 206 就從頭來過**（伺服器不理 Range 時同理），不可把整份接在前半段後面。
    - **`Accept-Encoding: identity`**：Range 的位移是「傳輸中的位元組」，若經 gzip 壓縮，
      位移就對不上解壓後的內容。

    放棄的條件是**連續 `RESUME_MAX_STALLS` 次沒有任何進度**，不是總次數——有進度的斷線
    代表還在前進。放棄時把最後一個例外原樣往上拋，呼叫端的告警才看得到原因。
    **HTTP 錯誤碼（如 503）不重試**：那不是斷線，重試無濟於事。

    verify=False：www.tpex.org.tw 憑證缺 Subject Key Identifier（見 fetch_otc_names）。
    """
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    buf = bytearray()
    total = etag = None
    stalls = 0
    last_exc = None
    with httpx.Client(verify=False, timeout=timeout, transport=transport,
                      follow_redirects=True) as client:
        for attempt in range(RESUME_MAX_REQUESTS):
            h = dict(headers)
            resuming = bool(buf) and total is not None
            if resuming:
                h["Range"] = f"bytes={len(buf)}-"
                if etag:
                    h["If-Range"] = etag
            before = len(buf)
            try:
                with client.stream("GET", url, headers=h) as r:
                    r.raise_for_status()
                    if resuming and not _continues_at(r, len(buf)):
                        buf.clear()           # 伺服器回整份（不理 Range 或檔案已換）→ 從頭來過
                        total = None
                    if not buf:
                        cl = r.headers.get("content-length")
                        total = int(cl) if cl and cl.isdigit() else None
                        etag = r.headers.get("etag")
                        before = 0
                    for chunk in r.iter_raw():
                        buf.extend(chunk)
                if total is None or len(buf) >= total:
                    return bytes(buf)
            except httpx.TransportError as e:   # 斷線／逾時／連不上；HTTPStatusError 不在此列
                last_exc = e
            if total is None:
                buf.clear()                   # 不知道總長就無從續傳，下一次從頭抓
            if len(buf) > before:
                stalls = 0
                continue                      # 有進度：立刻接著要，不必等
            stalls += 1
            if stalls >= RESUME_MAX_STALLS:
                break
            sleep(min(2 ** (stalls - 1), 4))
    if last_exc is not None:
        raise last_exc
    raise httpx.RemoteProtocolError(f"只取得 {len(buf)}/{total} bytes 就放棄")


def _continues_at(r, offset: int) -> bool:
    """206 且 Content-Range 恰好從 offset 開始，才算是接續上一段。"""
    if r.status_code != 206:
        return False
    cr = r.headers.get("content-range", "")        # "bytes 30-99/100"
    try:
        return int(cr.split()[1].split("-")[0]) == offset
    except (IndexError, ValueError):
        return False

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
    """上櫃公司 {代號: 簡稱}。近乎靜態，呼叫端宜快取。查無回空 dict。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    try:
        # 同一組 openapi 靜態檔，2026-09-13 21:13 實測也被切在一半 → 走斷線續傳
        return parse_otc_names(json.loads(get_resumable(OTC_COMPANY_URL, timeout=25)))
    except Exception:  # noqa: BLE001
        return {}


# 上櫃產業代碼→類股名：與上市 t187ap03_L 幾乎共用同一套碼（實測比對），僅多兩碼專屬上櫃。
from .twse import _INDUSTRY_CODE as _TSE_INDUSTRY_CODE  # noqa: E402
_OTC_INDUSTRY_CODE = {**_TSE_INDUSTRY_CODE, "32": "文化創意", "33": "農業科技"}


def parse_otc_industry(records: list) -> dict:
    """上櫃公司基本資料 t187ap03_O → {代號: {sector, name, shares}}（熱力圖用）。

    shares＝已發行股數(IssueShares)，×收盤價≒市值。產業代碼未知者略過。
    """
    out: dict[str, dict] = {}
    for r in records or []:
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        sec = _OTC_INDUSTRY_CODE.get(str(r.get("SecuritiesIndustryCode", "")).strip())
        if code and sec:
            out[code] = {"sector": sec, "name": str(r.get("CompanyAbbreviation", "")).strip(),
                         "shares": _f(r.get("IssueShares"))}
    return out


def fetch_otc_industry() -> dict:
    """上櫃公司 {代號: {sector, name, shares}}。近乎靜態，呼叫端宜快取。查無回空。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    try:
        # 同一組 openapi 靜態檔，2026-09-13 21:13 實測也被切在一半 → 走斷線續傳
        return parse_otc_industry(json.loads(get_resumable(OTC_COMPANY_URL, timeout=25)))
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


def parse_otc_turnover(payload: dict) -> dict:
    """dailyQuotes 上櫃盤後行情 → {代號: {vol: 張, amount: 元}}。

    位置欄位：0 代號、8 成交股數、9 成交金額(元)。只取 4 位數普通股（排除 ETF 00xx）。
    """
    out: dict[str, dict] = {}
    for t in payload.get("tables") or []:
        for r in t.get("data") or []:
            if not r or len(r) < 10:
                continue
            code = str(r[0]).strip()
            if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
                continue
            shares, amount = _f(r[8]), _f(r[9])
            if shares is None or amount is None:
                continue
            out[code] = {"vol": int(shares // 1000), "amount": amount}
    return out


def parse_otc_daily(payload: dict) -> dict:
    """Parse one dailyQuotes payload into OHLC plus normalized volume and amount."""
    ohlc = parse_otc_ohlc(payload)
    turnover = parse_otc_turnover(payload)
    return {
        code: {
            **row,
            "volume_lots": turnover.get(code, {}).get("vol"),
            "amount_twd": turnover.get(code, {}).get("amount"),
        }
        for code, row in ohlc.items()
    }


def fetch_otc_daily(date: datetime.date | None = None) -> dict:
    """Fetch dailyQuotes once and share it across price/volume parsing."""
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_daily(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_otc_turnover(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyQuotes 取指定日全上櫃個股成交量額。當日盤後才發布，查無回空。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_turnover(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_otc_ohlc(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyQuotes 取指定日全上櫃個股 OHLC（型態選股用）。查無回空。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_ohlc(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_otc_quotes(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyQuotes 取指定日（預設今天）全上櫃個股收盤與漲跌%。查無回空。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_QUOTES_URL, params={"date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_otc_quotes(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def parse_otc_margin(payload: dict) -> dict:
    """櫃買 margin/balance → 上櫃融資融券。

    逐檔在 tables[0].data，市場合計在 tables[0].summary 的兩列（「合計(張)」與
    「融資金(仟元)」），兩列都以「今日餘額」欄（與 fields 同位置）為準。
    融資金額換成「億」以對齊 twse.parse_margin_rwd 的單位。
    餘額為 0 的檔不入表——後續要拿去乘收盤價加總，留著只是白費迴圈。
    """
    out = {"balance": None, "short_balance": None, "value": None, "margin": {}, "short": {}}
    tables = payload.get("tables") or []
    t = tables[0] if tables else {}
    fields = t.get("fields") or []
    idx = {name: i for i, name in enumerate(fields)}
    ic, im, isr = idx.get("代號"), idx.get("資餘額"), idx.get("券餘額")
    if ic is not None:
        for row in t.get("data") or []:
            code = str(row[ic]).strip()
            if not code:
                continue
            if im is not None and (v := _f(row[im])) is not None:
                out["margin"][code] = v
            if isr is not None and (v := _f(row[isr])) is not None:
                out["short"][code] = v
    for row in t.get("summary") or []:
        label = "".join(str(x) for x in row[:2])
        if im is None:
            break
        today = _f(row[im]) if im < len(row) else None
        if "融資金" in label:                       # 仟元 → 億
            out["value"] = round(today * 1000 / 1e8, 1) if today is not None else None
        elif "合計" in label:
            out["balance"] = round(today) if today is not None else None
            if isr is not None and isr < len(row):
                s = _f(row[isr])
                out["short_balance"] = round(s) if s is not None else None
    return out


def fetch_otc_margin(date: datetime.date | None = None) -> dict:
    """直連櫃買 margin/balance 取指定日上櫃融資融券餘額。查無回空殼。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(OTC_MARGIN_URL, params={"date": ds, "type": "Daily", "response": "json"},
                      timeout=30, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("tables"):
            return parse_otc_margin(j)
    except Exception:  # noqa: BLE001
        pass
    return {"balance": None, "short_balance": None, "value": None, "margin": {}, "short": {}}


def fetch_tpex_insti(date: datetime.date | None = None) -> dict:
    """直連櫃買 dailyTrade 取指定日（預設今天）全上櫃個股三大法人買賣超。

    verify=False：櫃買憑證缺 Subject Key Identifier，與 TDCC 同一個毛病。
    """
    day = date or datetime.date.today()
    ds = f"{day.year}/{day.month:02d}/{day.day:02d}"
    try:
        j = httpx.get(DAILY_TRADE_URL, params={"type": "Daily", "date": ds, "response": "json"},
                      timeout=25, follow_redirects=True, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        if j.get("stat") == "ok" and j.get("tables"):
            return parse_tpex_insti(j)
    except Exception:  # noqa: BLE001
        pass
    return {}
