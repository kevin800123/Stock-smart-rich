"""台灣證交所資料抓取。

- 加權指數：openapi FMTQIK（含 TAIEX 時間序列）
- 三大法人買賣超：證交所 RWD BFI82U（openapi 未提供總表）
- 融資融券：openapi MI_MARGN（逐檔，加總成大盤）
- 本益比/殖利率/淨值比：openapi BWIBBU_ALL（個股基本面）

設計：純解析函式（可單元測試）＋ 薄網路包裝（整合測試）。
"""
import datetime

import httpx

OPENAPI = "https://openapi.twse.com.tw/v1"
BFI82U_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
NET_UNIT = 1e8  # 元 → 億


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _get_openapi(path: str):
    return httpx.get(f"{OPENAPI}{path}", timeout=20, follow_redirects=True).json()


# ---- 純解析函式 ----

def parse_taiex(records: list) -> dict:
    """FMTQIK 時間序列 → 最新加權指數與（以連續收盤計算、帶正負）日漲跌。"""
    if not records:
        return {"taiex": None, "taiex_chg": None}
    last = records[-1]
    close = _f(last.get("TAIEX"))
    chg = _f(last.get("Change"))
    if len(records) >= 2 and close is not None:
        prev = _f(records[-2].get("TAIEX"))
        if prev is not None:
            chg = round(close - prev, 2)
    return {"taiex": close, "taiex_chg": chg, "date": _roc_to_iso(last.get("Date"))}


def _roc_to_iso(roc) -> str | None:
    """民國日期字串（如 1150616）轉西元 ISO（2026-06-16）。"""
    s = "".join(ch for ch in str(roc or "") if ch.isdigit())
    if len(s) < 7:
        return None
    return f"{int(s[:3]) + 1911:04d}-{s[3:5]}-{s[5:7]}"


def parse_institutional(payload: dict) -> dict:
    """BFI82U → 外資/投信/自營 買賣超淨額（單位：億元）。

    以分類加總處理：外資含「外資及陸資」與「外資自營商」、自營含避險與自行買賣。
    （注意「外資及陸資(不含外資自營商)」字串內含「外資自營商」，故不可用子字串比對單列。）
    """
    foreign = trust = dealer = 0.0
    for r in payload.get("data", []):
        if not r:
            continue
        name = r[0]
        val = _f(r[3]) or 0
        if "合計" in name:
            continue
        if "外資" in name:
            foreign += val
        elif "投信" in name:
            trust += val
        elif "自營商" in name:
            dealer += val

    def to_yi(x):
        return round(x / NET_UNIT, 2)

    return {"inst_foreign": to_yi(foreign), "inst_trust": to_yi(trust), "inst_dealer": to_yi(dealer)}


def parse_margin(records: list) -> dict:
    """MI_MARGN 逐檔 → 大盤融資/融券餘額（張）與日增減。"""
    mb = my = sb = sy = 0.0
    for r in records:
        mb += _f(r.get("融資今日餘額")) or 0
        my += _f(r.get("融資前日餘額")) or 0
        sb += _f(r.get("融券今日餘額")) or 0
        sy += _f(r.get("融券前日餘額")) or 0
    return {
        "margin_balance": round(mb),
        "margin_chg": round(mb - my),
        "short_balance": round(sb),
        "short_chg": round(sb - sy),
    }


def parse_valuation(records: list) -> list[dict]:
    """BWIBBU_ALL → 個股本益比/殖利率/淨值比，代碼補 .TW。"""
    out = []
    for r in records:
        code = r.get("Code")
        if not code:
            continue
        out.append({
            "code": f"{code}.TW",
            "pe": _f(r.get("PEratio")),
            "yield": _f(r.get("DividendYield")),
            "pb": _f(r.get("PBratio")),
        })
    return out


# ---- 網路包裝 ----

def fetch_taiex() -> dict:
    return parse_taiex(_get_openapi("/exchangeReport/FMTQIK"))


def fetch_margin() -> dict:
    return parse_margin(_get_openapi("/exchangeReport/MI_MARGN"))


def fetch_valuation() -> list[dict]:
    return parse_valuation(_get_openapi("/exchangeReport/BWIBBU_ALL"))


def fetch_institutional(date: datetime.date | None = None) -> dict:
    """查最近一個有資料的交易日的三大法人買賣超（往前找最多 10 天）。"""
    day = date or datetime.date.today()
    for _ in range(10):
        ds = day.strftime("%Y%m%d")
        try:
            j = httpx.get(
                BFI82U_URL,
                params={"response": "json", "dayDate": ds, "type": "day"},
                timeout=20,
                follow_redirects=True,
            ).json()
            if j.get("stat") == "OK" and j.get("data"):
                return parse_institutional(j)
        except Exception:  # noqa: BLE001 — 試下一個交易日
            pass
        day = day - datetime.timedelta(days=1)
    return {"inst_foreign": None, "inst_trust": None, "inst_dealer": None}
