"""國際指數抓取：費半(^SOX)、日經(^N225)、KOSPI(^KS11)、黃金(GC=F)、日圓(JPY=X 美元兌日圓)、比特幣(BTC-USD)。

來源 yfinance；回傳每個 key 的最新值與相對前一日漲跌百分比。
雲端（資料中心 IP）yfinance 偶發限流／單一代碼整欄 NaN，故批次抓後對缺漏代碼重試補抓；
重試仍缺者再直連 Yahoo chart API 備援（同代碼、免 cookie/crumb 握手，機房 IP 較不易被擋）。
"""
import time
from urllib.parse import quote

import httpx
import yfinance as yf

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
_CHART_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _extract_series(df, sym):
    """從 yf.download()['Close'] 取某代碼的序列。多代碼→DataFrame(缺欄回 None 跳過)；
    單代碼→Series 直接用。避免『缺欄時 fallback 整個 DataFrame』導致 float(多列) 崩潰。"""
    if hasattr(df, "columns"):          # DataFrame：多代碼
        return df[sym].dropna() if sym in df.columns else None
    return df.dropna()                  # Series：單代碼


def _parse_series(series) -> dict | None:
    if len(series) >= 2:
        last, prev = float(series.iloc[-1]), float(series.iloc[-2])
        chg = round((last - prev) / prev * 100, 2) if prev else None
        return {"value": round(last, 2), "chg_pct": chg}
    if len(series) == 1:
        return {"value": round(float(series.iloc[-1]), 2), "chg_pct": None}
    return None


def parse_chart_payload(payload) -> dict | None:
    """從 Yahoo v8 chart JSON 取最後兩個有效收盤（中間常夾 null），回 {value, chg_pct}。"""
    try:
        closes = payload["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return None
    vals = [v for v in (closes or []) if v is not None]
    if not vals:
        return None
    last = float(vals[-1])
    chg = round((last - vals[-2]) / vals[-2] * 100, 2) if len(vals) >= 2 and vals[-2] else None
    return {"value": round(last, 2), "chg_pct": chg}


def _fetch_chart_raw(sym: str, range_: str = "5d", interval: str = "1d") -> dict | None:
    """直連 chart API 抓單一代碼原始 payload（^ 等符號需編碼進路徑）。失敗回 None。"""
    try:
        r = httpx.get(_CHART_URL + quote(sym, safe=""),
                      params={"range": range_, "interval": interval},
                      timeout=15, headers=_CHART_UA)
        if r.status_code == 200:
            return r.json()
    except Exception:  # noqa: BLE001 — 備援失敗不影響其他代碼
        pass
    return None


def _fetch_chart(sym: str) -> dict | None:
    """直連 chart API 抓單一代碼日線收盤。失敗回 None、維持缺值。"""
    payload = _fetch_chart_raw(sym)
    return parse_chart_payload(payload) if payload else None


def parse_chart_quote(payload) -> dict | None:
    """v8 chart meta → 準即時報價 {value, chg, chg_pct, time}。

    meta 直接帶 regularMarketPrice / chartPreviousClose / regularMarketTime（epoch 秒），
    不需讀 K 棒陣列；time 轉台北時間 HH:MM。缺價回 None。
    """
    from datetime import datetime, timezone, timedelta
    try:
        meta = payload["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        return None
    price, prev = meta.get("regularMarketPrice"), meta.get("chartPreviousClose")
    if price is None:
        return None
    out = {"value": round(float(price), 4), "chg": None, "chg_pct": None, "time": None}
    if prev:
        out["chg"] = round(float(price) - float(prev), 4)
        out["chg_pct"] = round((float(price) - float(prev)) / float(prev) * 100, 2)
    ts = meta.get("regularMarketTime")
    if ts:
        out["time"] = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%H:%M")
    return out


def fetch_futures_live() -> list[dict]:
    """海期準即時：逐檔 chart meta 抓盤中價（1m），輸出與 fetch_futures_monitor 同形狀
    （items 多 time 欄）。單檔失敗跳過——呼叫端以日線值補缺。期貨延遲約 10 分（CME 規定）。"""
    out = []
    for cat, items in OS_FUTURES:
        rows = []
        for name, t in items:
            payload = _fetch_chart_raw(t, range_="1d", interval="1m")
            q = parse_chart_quote(payload) if payload else None
            if q is not None:
                rows.append({"name": name, **q})
        out.append({"category": cat, "items": rows})
    return out


def fetch_intl_indices(tickers: dict, tries: int = 3) -> dict:
    out: dict = {}
    remaining = dict(tickers)
    for attempt in range(tries):
        if not remaining:
            break
        if attempt:
            time.sleep(1.0)
        try:
            # threads=False：yfinance 預設用 multitasking 開執行緒下載，從伺服器 threadpool
            # 工作緒呼叫時會靜默失敗回空；關掉改單緒循序，任何情境都可靠。
            df = yf.download(" ".join(remaining.values()), period="5d",
                             progress=False, threads=False)["Close"]
        except Exception:  # noqa: BLE001 — 整批失敗就重試
            continue
        for key, sym in list(remaining.items()):
            series = _extract_series(df, sym)
            parsed = _parse_series(series) if series is not None else None
            if parsed is not None:
                out[key] = parsed
                remaining.pop(key)
    # yfinance 重試後仍缺的代碼 → 直連 chart API 逐檔備援
    for key, sym in list(remaining.items()):
        parsed = _fetch_chart(sym)
        if parsed is not None:
            out[key] = parsed
            remaining.pop(key)
    return out


# ===== 歷史回補 =====
# fetch_intl_indices 只給「當下最新值」，沒有任何機制把歷史補回來。後果有二：
#   ① 新加入的代碼（vix 2026-06-25、jpy 07-02、twd 07-14）只能從加入當天往後長；
#   ② yfinance 偶發失敗那天就永久留空（_refresh_recent 只治三大法人與融資券）。
# 以下三個純函數把「某代碼的歷史收盤」對齊到台股資料日 D，供 updater 回補缺值。

# 台北 D 日晚間檢視時，哪些代碼「D 當日的收盤」已經產生。
# 亞股約 14:00 收盤 → 已有 D 當日值；其餘（美股指數 04:00 才收、24 小時商品尚未結算）
# 當下最新的完整場次是 D 之前那一場。
INTL_SAME_DAY = {"n225", "kospi"}


def parse_history_closes(rows) -> dict:
    """[(session_date, close|None)] → {session_date: {value, chg_pct}}。

    close 為 None（休市/缺報價）的日子不產生列；漲跌% 以「前一個有效收盤」為基準，
    不是前一列日期——中間隔幾天沒報價時，用日期相減會算出錯誤的基準。
    """
    out, prev = {}, None
    for ds, close in rows:
        if close is None:
            continue
        v = round(float(close), 2)
        out[ds] = {"value": v, "chg_pct": round((v - prev) / prev * 100, 2) if prev else None}
        prev = v
    return out


def pick_close_for(history: dict, ds: str, same_day: bool) -> dict | None:
    """取台股資料日 ds 當晚可得的最新收盤；無資料回 None（不硬湊、不往未來取）。

    same_day=False 時取「ds 之前最近一個有交易的日子」而非「ds 減一個曆日」——
    週一往前應落在上週五，用曆日相減會落在沒有場次的週日。
    """
    cands = [d for d in history if (d <= ds if same_day else d < ds)]
    return history[max(cands)] if cands else None


def fetch_intl_history(tickers: dict, days: int = 120) -> dict:
    """批次抓各代碼近 days 天日線收盤 → {key: {session_date: {value, chg_pct}}}。

    單一代碼失敗只是該 key 缺席，不影響其餘（與 fetch_intl_indices 的容錯一致）。
    """
    out = {}
    for key, sym in tickers.items():
        try:
            h = yf.Ticker(sym).history(period=f"{max(days, 5)}d")["Close"]
        except Exception:  # noqa: BLE001 — 單一代碼失敗略過，呼叫端維持缺值
            continue
        rows = [(str(idx)[:10], None if v != v else v) for idx, v in h.items()]  # v!=v → NaN
        if rows:
            out[key] = parse_history_closes(rows)
    return out


# 海期監控：五大分類 × (顯示名, yfinance 代碼)。中國A50 無穩定代碼故不列。
OS_FUTURES: list[tuple[str, list[tuple[str, str]]]] = [
    ("指數期貨", [("小道瓊", "YM=F"), ("小那斯達克", "NQ=F"), ("小S&P500", "ES=F"),
                  ("小羅素", "RTY=F"), ("日經", "^N225"), ("恆生", "^HSI"), ("法蘭克福", "^GDAXI")]),
    ("能源金屬", [("輕原油", "CL=F"), ("天然氣", "NG=F"), ("高級銅", "HG=F"),
                  ("白銀", "SI=F"), ("黃金", "GC=F"), ("白金", "PL=F")]),
    ("農產品", [("黃豆", "ZS=F"), ("小麥", "ZW=F"), ("玉米", "ZC=F"), ("咖啡", "KC=F"),
                ("11號糖", "SB=F"), ("可可", "CC=F"), ("黃豆油", "ZL=F")]),
    ("外匯", [("美元指數", "DX-Y.NYB"), ("澳幣", "AUDUSD=X"), ("英鎊", "GBPUSD=X"),
              ("加幣", "USDCAD=X"), ("歐元", "EURUSD=X"), ("日圓", "JPY=X"), ("瑞朗", "USDCHF=X")]),
    ("美股", [("輝達", "NVDA"), ("蘋果", "AAPL"), ("Alphabet", "GOOGL"), ("微軟", "MSFT"),
              ("亞馬遜", "AMZN"), ("META", "META"), ("特斯拉", "TSLA"), ("台積電ADR", "TSM"),
              ("博通", "AVGO"), ("甲骨文", "ORCL"), ("美光", "MU"), ("英特爾", "INTC"),
              ("美超微", "AMD"), ("Palantir", "PLTR")]),
]


def _series_stats(series) -> dict | None:
    if len(series) >= 2:
        last, prev = float(series.iloc[-1]), float(series.iloc[-2])
        return {"value": round(last, 4), "chg": round(last - prev, 4),
                "chg_pct": round((last - prev) / prev * 100, 2) if prev else None}
    if len(series) == 1:
        return {"value": round(float(series.iloc[-1]), 4), "chg": None, "chg_pct": None}
    return None


def fetch_futures_monitor(tries: int = 3) -> list[dict]:
    """一次批次抓海期五大分類的報價（延遲/收盤），回 [{category, items:[{name,value,chg,chg_pct}]}]。

    與 fetch_intl_indices 同機制（單次批次下載，雲端可跑）；抓不到的代碼略過不顯示。
    """
    remaining = {t for _, items in OS_FUTURES for _, t in items}
    stats: dict[str, dict] = {}
    for attempt in range(tries):
        if not remaining:
            break
        if attempt:
            time.sleep(1.0)
        try:
            df = yf.download(" ".join(remaining), period="5d",
                             progress=False, threads=False)["Close"]  # 見 fetch_intl_indices 說明
        except Exception:  # noqa: BLE001
            continue
        for t in list(remaining):
            series = _extract_series(df, t)
            st = _series_stats(series) if series is not None else None
            if st is not None:
                stats[t] = st
                remaining.discard(t)
    out = []
    for cat, items in OS_FUTURES:
        rows = [{"name": name, **stats[t]} for name, t in items if t in stats]
        out.append({"category": cat, "items": rows})
    return out
