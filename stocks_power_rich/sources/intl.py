"""國際指數抓取：費半(^SOX)、日經(^N225)、KOSPI(^KS11)、黃金(GC=F)、日圓(JPY=X 美元兌日圓)、比特幣(BTC-USD)。

來源 yfinance；回傳每個 key 的最新值與相對前一日漲跌百分比。
雲端（資料中心 IP）yfinance 偶發限流／單一代碼整欄 NaN，故批次抓後對缺漏代碼重試補抓。
"""
import time

import yfinance as yf


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
