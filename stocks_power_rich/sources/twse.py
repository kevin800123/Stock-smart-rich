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
# 直連（RWD）端點：當日盤後（約 14:00 後）即可取得，openapi 鏡像則常延遲到晚間/隔日
FMTQIK_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
MARGIN_RWD = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
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


def _rwd_row_getter(fields: list, row: list):
    idx = {name: i for i, name in enumerate(fields or [])}

    def g(name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    return g


def parse_taiex_rwd(payload: dict) -> dict:
    """直連 FMTQIK（{fields, data}）→ 取最新一列的加權指數、漲跌點數、資料日期。"""
    data = payload.get("data") or []
    if not data:
        return {"taiex": None, "taiex_chg": None, "date": None}
    g = _rwd_row_getter(payload.get("fields"), data[-1])
    return {
        "taiex": _f(g("發行量加權股價指數")),
        "taiex_chg": _f(g("漲跌點數")),
        "date": _roc_to_iso(g("日期")),
    }


def parse_margin_rwd(payload: dict) -> dict:
    """直連 MI_MARGN（selectType=MS）信用交易統計表 → 大盤融資/融券餘額（張）與日增減。"""
    tables = payload.get("tables") or []
    table = tables[0] if tables else {}
    fields = table.get("fields") or []
    res = {"margin_balance": None, "margin_chg": None, "short_balance": None, "short_chg": None}
    for row in table.get("data") or []:
        item = str(row[0]) if row else ""
        g = _rwd_row_getter(fields, row)
        today, prev = _f(g("今日餘額")), _f(g("前日餘額"))
        bal = round(today) if today is not None else None
        chg = round(today - prev) if (today is not None and prev is not None) else None
        if "融資" in item and "單位" in item:
            res["margin_balance"], res["margin_chg"] = bal, chg
        elif "融券" in item and "單位" in item:
            res["short_balance"], res["short_chg"] = bal, chg
    return res


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
    """直連 FMTQIK 取最新加權指數（當日盤後即有；回傳含資料日期作為全列錨點）。"""
    for back in (0, 35):  # 跨月初容錯：當月無資料就回看上一個月
        day = datetime.date.today() - datetime.timedelta(days=back)
        try:
            j = httpx.get(FMTQIK_RWD, params={"date": day.strftime("%Y%m%d"), "response": "json"},
                          timeout=20, follow_redirects=True).json()
            out = parse_taiex_rwd(j)
            if out["taiex"] is not None:
                return out
        except Exception:  # noqa: BLE001 — 試下一個月份
            pass
    return {"taiex": None, "taiex_chg": None, "date": None}


def fetch_margin(date: datetime.date | None = None) -> dict:
    """直連 MI_MARGN 取指定日（預設今天）大盤融資融券（張）。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(MARGIN_RWD,
                      params={"date": day.strftime("%Y%m%d"), "selectType": "MS", "response": "json"},
                      timeout=20, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("tables"):
            return parse_margin_rwd(j)
    except Exception:  # noqa: BLE001
        pass
    return {"margin_balance": None, "margin_chg": None, "short_balance": None, "short_chg": None}


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
