"""下週行事曆的資料源：官方排程（BLS／Fed）＋台股法說會（MOPS）＋美股財報（Nasdaq）。

純解析函式 + 薄網路 wrapper（同 sources/ 其他模組的分工）。

**日期一律取自官方排程，不用「可計算規則」推算。** 實測 2026 年 BLS 官方排程，
「非農＝每月第一個週五」12 個月裡會錯 4 個月：2025/12 參考月發布在 1/09（第二個
週五）、2026/01 發布在 2/11（**週三**）、2026/04 發布在 5/08（第二個週五）、
2026/06 發布在 7/02（週四，撞美國國慶連假）。一條 33% 會錯的規則比沒有規則更糟——
它會安靜地產生一個看起來完全正常的錯日期，而讀者沒有任何辦法發現。
"""
from __future__ import annotations

import json
import re
import time

import httpx

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

BLS_URLS = {
    "cpi": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "empsit": "https://www.bls.gov/schedule/news_release/empsit.htm",
}
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
# 公開資訊觀測站的法人說明會。**必須用 mopsov 這個主機**——mops.twse.com.tw 會回
# 「FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED」（實測），mopsov 才通，
# 與 t21sc03 月營收歷史走的是同一台。
MOPS_CONF_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html or "")).strip()


def _cells(row_html: str) -> list:
    return [_text(c) for c in _CELL.findall(row_html)]


# ---------------------------------------------------------------- BLS 官方排程

# 「Aug. 12, 2026」「May 12, 2026」——May 本身就三個字母，官方不加句點。
_BLS_DATE = re.compile(r"([A-Z][a-z]{2})\.?\s+(\d{1,2}),\s*(\d{4})")


def parse_bls_schedule(html: str) -> list:
    """BLS 的 Schedule of Releases 表格 → [{date: ISO, ref: 參考月}]。

    每列三格：參考月／發布日／發布時間。發布時間一律 08:30 ET，會隨美國日光節約
    時間在台北時間 20:30 與 21:30 之間跳動，所以**只取日期不取時間**——寫死一個
    時差會在換季那兩週安靜地錯一小時（同 intl 那條 session_closed 的教訓）。
    """
    out = []
    for row in _ROW.findall(html or ""):
        cells = _cells(row)
        if len(cells) < 2:
            continue
        m = _BLS_DATE.search(cells[1])
        if not m:
            continue
        mon = _MONTHS.get(m.group(1).lower())
        if not mon:
            continue
        out.append({"date": f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}",
                    "ref": cells[0]})
    return out


def fetch_bls_schedule(kind: str, timeout: float = 25.0) -> list:
    url = BLS_URLS[kind]
    r = httpx.get(url, headers=_UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return parse_bls_schedule(r.text)


# ---------------------------------------------------------------- Fed FOMC

# **月份與日期必須在同一個 match 內成對擷取。** 寫成「兩份 findall 再 zip」時，
# 實跑 2026 年真實頁面得到 January 27-28／April 17-18／July 28-29／October 16-17
# ——只有第一筆是對的，其餘都是交叉配對出來的假日期，而且看起來完全正常。
_FOMC_PAIR = re.compile(
    r'fomc-meeting__month[^>]*>\s*<strong>(.*?)</strong>.*?'
    r'fomc-meeting__date[^>]*>(.*?)</div>', re.S)
_FOMC_DAYS = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})|(\d{1,2})")


def parse_fomc_calendar(html: str, year: int) -> list:
    """Fed 的 Meeting calendars 頁 → 該年度 [{date: 決議日 ISO, projections: bool}]。

    一頁含多個年度區塊，**要先切出指定年度那一段**再解析，否則會拿到別年的日期。
    官方寫「27-28」代表兩天會期，利率決議在**第二天**收盤後公布，所以取後面那個。
    標註 `*` 的場次附經濟預測摘要（SEP）與記者會。
    """
    head = f"{year} FOMC Meetings"
    i = (html or "").find(head)
    if i < 0:
        return []
    j = html.find("FOMC Meetings", i + len(head))
    seg = html[i:j if j > i else len(html)]

    out = []
    for raw_month, raw_days in _FOMC_PAIR.findall(seg):
        mon = _MONTHS.get(_text(raw_month)[:3].lower())
        if not mon:
            continue
        days = _text(raw_days)
        m = _FOMC_DAYS.search(days)
        if not m:
            continue
        day = int(m.group(2) or m.group(3) or m.group(1))
        out.append({"date": f"{year}-{mon:02d}-{day:02d}",
                    "projections": "*" in days})
    return out


def fetch_fomc_calendar(year: int, timeout: float = 25.0) -> list:
    r = httpx.get(FOMC_URL, headers=_UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return parse_fomc_calendar(r.text, year)


# ---------------------------------------------------------------- 台股法說會

_CODE4 = re.compile(r"^\d{4}$")
_ROC_DATE = re.compile(r"^(\d{2,3})/(\d{1,2})/(\d{1,2})$")


def parse_mops_conferences(html: str) -> list:
    """MOPS 法人說明會列表 → [{date: ISO, code, name, time}]。

    表頭與合計列混在同一張表裡（同 t21sc03 月營收那支的既有處理），
    所以只收「第一格是 4 碼數字」的列。日期是民國年（115/09/11）。
    """
    out = []
    for row in _ROW.findall(html or ""):
        cells = _cells(row)
        if len(cells) < 4 or not _CODE4.match(cells[0]):
            continue
        m = _ROC_DATE.match(cells[2])
        if not m:
            continue
        year = int(m.group(1)) + 1911
        out.append({"date": f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                    "code": cells[0], "name": cells[1], "time": cells[3]})
    return out


def fetch_tw_conferences(roc_year: int, month: int, market: str = "sii",
                         timeout: float = 30.0) -> list:
    payload = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
               "TYPEK": market, "year": str(roc_year), "month": f"{month:02d}"}
    r = httpx.post(MOPS_CONF_URL, data=payload, headers=_UA,
                   timeout=timeout, verify=False)
    r.raise_for_status()
    return parse_mops_conferences(r.content.decode("utf-8", "replace"))


# ---------------------------------------------------------------- 美股財報

_SESSION = {"time-pre-market": "盤前", "time-after-hours": "盤後"}


def parse_nasdaq_earnings(payload: dict, date: str) -> list:
    """Nasdaq 財報行事曆 → [{date, symbol, name, session, market_cap}]。

    回應本身不帶日期（日期是查詢參數），所以由呼叫端帶進來。
    **市值缺值的列直接丟掉**——市值是「大型權值股」這個篩選的唯一依據，
    缺了就無從判斷，寧可不列也不要讓一檔小公司混進權值股清單。
    """
    rows = ((payload or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        raw = str(r.get("marketCap") or "").replace("$", "").replace(",", "").strip()
        try:
            cap = float(raw)
        except ValueError:
            continue
        if cap <= 0:
            continue
        out.append({"date": date, "symbol": r.get("symbol") or "",
                    "name": r.get("name") or "",
                    "session": _SESSION.get(r.get("time") or "", ""),
                    "market_cap": cap})
    return out


def fetch_us_earnings(date: str, timeout: float = 25.0) -> list:
    r = httpx.get(NASDAQ_EARNINGS_URL, params={"date": date},
                  headers={**_UA, "Accept": "application/json, text/plain, */*"},
                  timeout=timeout)
    r.raise_for_status()
    return parse_nasdaq_earnings(r.json(), date)


# ---------------------------------------------------------------- 日股財報

# JPX「決算発表予定日」：每個會計月份一個 xlsx，**檔名帶更新日期會變**
# （kessan08_0904.xlsx），所以要從頁面解析連結，不可寫死。
JPX_SCHEDULE_URL = ("https://www.jpx.co.jp/listing/event-schedules/"
                    "financial-announcement/index.html")
_JPX_HOST = "https://www.jpx.co.jp"
_JPX_XLSX = re.compile(r'href="(/listing/event-schedules/[^"]+\.xlsx)"')

# TradingView 公開 scanner（與 sources/intl.py 的海期監控同一個端點，已證實從
# Zeabur 打得到）。`market_cap_basic` 實測回**美元**（Toyota 0.24 兆＝$240B），
# 所以日股沿用美股那條美元門檻，不必另立一個日圓門檻。
TV_SCAN_URL = "https://scanner.tradingview.com/global/scan"
_TV_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def parse_jpx_links(html: str) -> list:
    return [_JPX_HOST + m for m in _JPX_XLSX.findall(html or "")]


def parse_jpx_schedule(blob: bytes) -> list:
    """JPX 決算発表予定日 xlsx → [{date, code, name, kind}]。

    前 4 列是標題與基準日、第 5 列才是欄名，而欄名列自己也長得像資料列，
    所以不用「跳過前 N 列」，改成**只認「第一格日期解析得出來、第二格是 4 碼代號」**
    的列（同 t21sc03 月營收那支「只收 4 碼數字」的既有做法）。
    來源改版或下載到 HTML 錯誤頁時回空，不要炸掉整份行事曆。
    """
    import datetime as _dt
    import io

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return []

    out = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            if len(row) < 3:
                continue
            when, code = row[0], row[1]
            if not isinstance(when, (_dt.datetime, _dt.date)):
                continue
            code = str(code or "").strip()
            if not re.fullmatch(r"\d{4}", code):
                continue
            out.append({"date": when.strftime("%Y-%m-%d"), "code": code,
                        "name": str(row[2] or "").strip(),
                        "kind": str(row[7] or "").strip() if len(row) > 7 else ""})
    return out


def fetch_jp_earnings_schedule(timeout: float = 30.0) -> list:
    r = httpx.get(JPX_SCHEDULE_URL, headers=_UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    rows = []
    for url in parse_jpx_links(r.content.decode("utf-8", "replace")):
        try:
            f = httpx.get(url, headers=_UA, timeout=timeout, follow_redirects=True)
            f.raise_for_status()
            rows += parse_jpx_schedule(f.content)
        except Exception:  # noqa: BLE001 — 少一個會計月份不該讓整份消失
            pass
        time.sleep(0.3)
    return rows


def parse_tv_market_caps(payload) -> dict:
    """TradingView scanner 回應 → {代號(去掉交易所前綴): 市值美元}。缺市值的略過。"""
    out = {}
    for row in ((payload or {}).get("data") or []):
        d = row.get("d") or []
        if len(d) < 2 or not d[1]:
            continue
        sym = str(row.get("s") or "")
        out[sym.split(":")[-1]] = float(d[1])
    return out


def fetch_market_caps(tickers: list, timeout: float = 25.0) -> dict:
    """一次 POST 拿多檔市值（美元）。抓不到回空，由呼叫端決定要不要整段略過。"""
    if not tickers:
        return {}
    body = {"symbols": {"tickers": list(tickers), "query": {"types": []}},
            "columns": ["close", "market_cap_basic"]}
    r = httpx.post(TV_SCAN_URL, json=body, timeout=timeout, headers=_TV_UA)
    r.raise_for_status()
    return parse_tv_market_caps(r.json())
