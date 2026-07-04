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
MI_INDEX_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"  # 各類股指數（族群當日漲跌）
BFIAMU_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/BFIAMU"  # 各類指數日成交量值（熱力圖面積）
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"  # 個股三大法人買賣超
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


def _to_yi(v):
    """元 → 億（1 位小數）。"""
    return round(v / 1e8, 1) if v is not None else None


def parse_taiex_rwd(payload: dict) -> dict:
    """直連 FMTQIK（{fields, data}）→ 取最新一列的加權指數、漲跌點數、成交金額（億）、資料日期。"""
    data = payload.get("data") or []
    if not data:
        return {"taiex": None, "taiex_chg": None, "turnover": None, "date": None}
    g = _rwd_row_getter(payload.get("fields"), data[-1])
    return {
        "taiex": _f(g("發行量加權股價指數")),
        "taiex_chg": _f(g("漲跌點數")),
        "turnover": _to_yi(_f(g("成交金額"))),
        "date": _roc_to_iso(g("日期")),
    }


def parse_index_ohlc(records: list) -> list[dict]:
    """MI_5MINS_HIST → 加權指數每日 OHLC（供雲端 yfinance 失敗時的 K 線來源）。"""
    out = []
    for r in records or []:
        iso = _roc_to_iso(r.get("Date"))
        o, h, l, c = (_f(r.get("OpeningIndex")), _f(r.get("HighestIndex")),
                      _f(r.get("LowestIndex")), _f(r.get("ClosingIndex")))
        if iso and None not in (o, h, l, c):
            out.append({"date": iso, "open": o, "high": h, "low": l, "close": c, "volume": 0})
    return out


def fetch_index_ohlc() -> list[dict]:
    return parse_index_ohlc(_get_openapi("/exchangeReport/MI_5MINS_HIST"))


def parse_taiex_history(payload: dict) -> list[dict]:
    """FMTQIK 直連 → 整月每個交易日的加權指數與漲跌（供歷史回補）。"""
    fields = payload.get("fields")
    out = []
    for row in payload.get("data") or []:
        g = _rwd_row_getter(fields, row)
        iso = _roc_to_iso(g("日期"))
        if iso:
            out.append({"date": iso, "taiex": _f(g("發行量加權股價指數")),
                        "taiex_chg": _f(g("漲跌點數")), "turnover": _to_yi(_f(g("成交金額")))})
    return out


def fetch_taiex_history(date: datetime.date | None = None) -> list[dict]:
    """直連 FMTQIK 取「date 所在整月」的每日加權指數（預設今天）。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(FMTQIK_RWD, params={"date": day.strftime("%Y%m%d"), "response": "json"},
                      timeout=20, follow_redirects=True).json()
        if j.get("stat") == "OK":
            return parse_taiex_history(j)
    except Exception:  # noqa: BLE001
        pass
    return []


def parse_margin_rwd(payload: dict) -> dict:
    """直連 MI_MARGN（selectType=MS）信用交易統計表 → 大盤融資/融券餘額（張）、融資金額（億）與日增減。"""
    tables = payload.get("tables") or []
    table = tables[0] if tables else {}
    fields = table.get("fields") or []
    res = {"margin_balance": None, "margin_chg": None, "short_balance": None, "short_chg": None,
           "margin_value": None, "margin_value_chg": None}
    for row in table.get("data") or []:
        item = str(row[0]) if row else ""
        g = _rwd_row_getter(fields, row)
        today, prev = _f(g("今日餘額")), _f(g("前日餘額"))
        bal = round(today) if today is not None else None
        chg = round(today - prev) if (today is not None and prev is not None) else None
        if "融資金額" in item:  # 仟元 → 億
            res["margin_value"] = round(today / 1e5, 1) if today is not None else None
            res["margin_value_chg"] = (round((today - prev) / 1e5, 1)
                                       if (today is not None and prev is not None) else None)
        elif "融資" in item and "單位" in item:
            res["margin_balance"], res["margin_chg"] = bal, chg
        elif "融券" in item and "單位" in item:
            res["short_balance"], res["short_chg"] = bal, chg
    return res


def parse_sector_indices(payload: dict) -> list[dict]:
    """MI_INDEX(type=IND) 價格指數表 → 各產業類股當日漲跌 [{name, close, chg_pct}]。

    只取名稱以「類指數」結尾的產業族群（排除加權、台灣50 等大盤型指數），名稱去掉後綴。
    漲跌方向以「漲跌(+/-)」欄顏色為準（綠跌紅漲），套用到百分比絕對值，兼容%帶號或未帶號。
    """
    tables = payload.get("tables") or []
    table = tables[0] if tables else {}
    fields = table.get("fields") or []
    idx = {n: i for i, n in enumerate(fields)}
    ni, ci, di, pi = idx.get("指數"), idx.get("收盤指數"), idx.get("漲跌(+/-)"), idx.get("漲跌百分比(%)")
    out = []
    for row in table.get("data") or []:
        if not row or ni is None or ni >= len(row):
            continue
        name = str(row[ni] or "").strip()
        if not name.endswith("類指數"):
            continue
        pct = _f(row[pi]) if pi is not None and pi < len(row) else None
        if pct is not None:
            dcell = str(row[di]) if di is not None and di < len(row) else ""
            sign = -1 if "green" in dcell else (1 if "red" in dcell else (1 if pct >= 0 else -1))
            pct = round(sign * abs(pct), 2)
        out.append({
            "name": name[:-3],
            "close": _f(row[ci]) if ci is not None and ci < len(row) else None,
            "chg_pct": pct,
        })
    return out


def norm_sector_name(name: str) -> str:
    """類股名正規化：去掉「類指數」後綴與尾端「業」，讓 BFIAMU 與價格指數的名稱對齊。

    例：「航運業類指數」→「航運」，對齊 parse_sector_indices 的「航運」。
    """
    n = str(name or "").strip()
    if n.endswith("類指數"):
        n = n[:-3]
    if n.endswith("業"):
        n = n[:-1]
    return n


# 上市公司「產業別」代碼 → 官方類股名（對齊 fetch_sector_indices 的類股命名）。
# 資料源 t187ap03_L 的產業別是代碼而非名稱。存託憑證(91)/管理股票等不對應類股。
_INDUSTRY_CODE = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠",
    "12": "汽車", "14": "建材營造", "15": "航運", "16": "觀光餐旅", "17": "金融保險",
    "18": "貿易百貨", "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體", "25": "電腦及週邊設備", "26": "光電", "27": "通信網路",
    "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}


def parse_listed_industry(records: list) -> dict:
    """上市公司基本資料 t187ap03_L → {代號: {sector, name, shares}}。

    shares＝已發行普通股數（×收盤價≒市值）。產業別代碼未知（存託憑證等）略過。
    """
    out: dict[str, dict] = {}
    for r in records or []:
        code = str(r.get("公司代號", "")).strip()
        sec = _INDUSTRY_CODE.get(str(r.get("產業別", "")).strip())
        if code and sec:
            out[code] = {"sector": sec, "name": str(r.get("公司簡稱", "")).strip(),
                         "shares": _f(r.get("已發行普通股數或TDR原股發行股數"))}
    return out


def parse_stock_quotes(payload: dict) -> dict:
    """MI_INDEX(type=ALL) 每日收盤行情(全部) → {代號: {name, close, chg_pct}}。

    漲跌方向以「漲跌(+/-)」欄顏色為準（red 漲/green 跌/其餘平盤），漲跌價差為絕對值，
    以昨收(收盤−帶號價差)換算漲跌百分比。無收盤或無價差的列略過。
    """
    out: dict[str, dict] = {}
    for tb in payload.get("tables") or []:
        fields = tb.get("fields") or []
        if "證券代號" not in fields or "漲跌價差" not in fields:
            continue
        idx = {n: i for i, n in enumerate(fields)}
        ci, ni, cl, di, gi = (idx.get("證券代號"), idx.get("證券名稱"), idx.get("收盤價"),
                              idx.get("漲跌(+/-)"), idx.get("漲跌價差"))
        for row in tb.get("data") or []:
            if ci is None or ci >= len(row):
                continue
            code = str(row[ci]).strip()
            close = _f(row[cl]) if cl is not None and cl < len(row) else None
            diff = _f(row[gi]) if gi is not None and gi < len(row) else None
            if not code or close is None or diff is None:
                continue
            dcell = str(row[di]) if di is not None and di < len(row) else ""
            sign = -1 if "green" in dcell else (1 if "red" in dcell else 0)
            prev = close - sign * abs(diff)
            pct = round(sign * abs(diff) / prev * 100, 2) if prev else 0.0
            out[code] = {
                "name": str(row[ni]).strip() if ni is not None and ni < len(row) else "",
                "close": close, "chg_pct": pct,
            }
    return out


def parse_sector_turnover(payload: dict) -> dict:
    """BFIAMU 各類指數日成交量值 → {正規化類股名: 成交金額(元)}。作為熱力圖的面積。"""
    out: dict[str, int] = {}
    for r in payload.get("data") or []:
        if not r or len(r) < 3:
            continue
        name = norm_sector_name(r[0])
        val = _f(r[2])  # 成交金額（元）
        if name and val is not None:
            out[name] = int(val)
    return out


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
    """直連 FMTQIK 取最新加權指數（當日盤後即有；回傳含資料日期作為全列錨點）。

    月初本月尚無資料時，回看「上月最後一天」而非往回固定天數（避免跳過整個上月）。
    """
    today = datetime.date.today()
    anchors = [today, today.replace(day=1) - datetime.timedelta(days=1)]  # 本月、上月最後一天
    for day in anchors:
        try:
            j = httpx.get(FMTQIK_RWD, params={"date": day.strftime("%Y%m%d"), "response": "json"},
                          timeout=20, follow_redirects=True).json()
            out = parse_taiex_rwd(j)
            if out["taiex"] is not None:
                return out
        except Exception:  # noqa: BLE001 — 試上一個月份
            pass
    return {"taiex": None, "taiex_chg": None, "turnover": None, "date": None}


def parse_close_prices(payload: dict) -> dict:
    """MI_INDEX(type=ALLBUT0999) 個股表 → {代號: 收盤價}。"""
    out = {}
    for t in payload.get("tables") or []:
        idx = {n: i for i, n in enumerate(t.get("fields") or [])}
        ci, pi = idx.get("證券代號"), idx.get("收盤價")
        if ci is None or pi is None:
            continue
        for r in t.get("data") or []:
            if ci < len(r) and pi < len(r):
                code = str(r[ci]).strip()
                price = _f(r[pi])
                if code and price is not None:
                    out[code] = price
    return out


def fetch_close_prices(date: datetime.date | None = None) -> dict:
    """直連 MI_INDEX(ALLBUT0999) 取指定日（預設今天）全市場個股收盤價。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(MI_INDEX_RWD, params={"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
                      timeout=25, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("tables"):
            return parse_close_prices(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def parse_t86(payload: dict) -> dict:
    """T86 個股三大法人買賣超 → {代號: {foreign, trust, dealer, total}}（單位：張，股數/1000）。"""
    fields = payload.get("fields") or []
    idx = {n: i for i, n in enumerate(fields)}
    ni = idx.get("證券名稱")
    fi = idx.get("外陸資買賣超股數(不含外資自營商)")
    ti = idx.get("投信買賣超股數")
    di = idx.get("自營商買賣超股數")
    ai = idx.get("三大法人買賣超股數")
    out = {}
    for r in payload.get("data") or []:
        if not r:
            continue

        def lots(i):
            v = _f(r[i]) if i is not None and i < len(r) else None
            return round(v / 1000) if v is not None else None

        name = str(r[ni]).strip() if ni is not None and ni < len(r) else ""
        out[str(r[0]).strip()] = {"name": name, "foreign": lots(fi), "trust": lots(ti), "dealer": lots(di), "total": lots(ai)}
    return out


def fetch_t86(date: datetime.date | None = None) -> dict:
    """直連 T86 取指定日（預設今天）全市場個股三大法人買賣超。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(T86_URL, params={"date": day.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"},
                      timeout=25, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("data"):
            return parse_t86(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_sector_indices(date: datetime.date | None = None) -> list[dict]:
    """直連 MI_INDEX(type=IND) 取指定日（預設今天）各產業類股當日漲跌。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(MI_INDEX_RWD,
                      params={"date": day.strftime("%Y%m%d"), "type": "IND", "response": "json"},
                      timeout=20, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("tables"):
            return parse_sector_indices(j)
    except Exception:  # noqa: BLE001
        pass
    return []


def fetch_sector_turnover(date: datetime.date | None = None) -> dict:
    """直連 BFIAMU 取指定日各類股成交金額（元），{正規化名: 金額}。查無回空 dict。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(BFIAMU_RWD,
                      params={"date": day.strftime("%Y%m%d"), "response": "json"},
                      timeout=20, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("data"):
            return parse_sector_turnover(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


def fetch_listed_industry() -> dict:
    """上市公司基本資料 → {證券代號: 官方類股名}。屬靜態資料，呼叫端宜快取。"""
    try:
        return parse_listed_industry(_get_openapi("/opendata/t187ap03_L"))
    except Exception:  # noqa: BLE001
        return {}


def fetch_stock_quotes(date: datetime.date | None = None) -> dict:
    """直連 MI_INDEX(type=ALL) 取指定日全上市個股 {代號: {name, close, chg_pct}}。查無回空。"""
    day = date or datetime.date.today()
    try:
        j = httpx.get(MI_INDEX_RWD,
                      params={"date": day.strftime("%Y%m%d"), "type": "ALL", "response": "json"},
                      timeout=25, follow_redirects=True).json()
        if j.get("stat") == "OK" and j.get("tables"):
            return parse_stock_quotes(j)
    except Exception:  # noqa: BLE001
        pass
    return {}


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
    """查「指定日」（預設今天）的三大法人買賣超（單位：億）。

    僅查該日，不回退到其他交易日：該日尚未公布就回傳 None，避免把他日資料誤標成當日
    （日期錯置會造成「今日＝昨日」的假象）。缺的日期由 updater 的近期回補在資料公布後補正。
    """
    day = date or datetime.date.today()
    try:
        j = httpx.get(
            BFI82U_URL,
            params={"response": "json", "dayDate": day.strftime("%Y%m%d"), "type": "day"},
            timeout=20,
            follow_redirects=True,
        ).json()
        if j.get("stat") == "OK" and j.get("data"):
            return parse_institutional(j)
    except Exception:  # noqa: BLE001
        pass
    return {"inst_foreign": None, "inst_trust": None, "inst_dealer": None}
