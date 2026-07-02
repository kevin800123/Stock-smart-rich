"""國際指數抓取：費半(^SOX)、日經(^N225)、KOSPI(^KS11)、黃金(GC=F)、日圓(JPY=X 美元兌日圓)、比特幣(BTC-USD)。

來源 yfinance；回傳每個 key 的最新值與相對前一日漲跌百分比。
雲端（資料中心 IP）yfinance 偶發限流／單一代碼整欄 NaN，故批次抓後對缺漏代碼重試補抓。
"""
import time

import yfinance as yf


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
            df = yf.download(" ".join(remaining.values()), period="5d", progress=False)["Close"]
        except Exception:  # noqa: BLE001 — 整批失敗就重試
            continue
        for key, sym in list(remaining.items()):
            series = (df[sym] if sym in df else df).dropna()
            parsed = _parse_series(series)
            if parsed is not None:
                out[key] = parsed
                remaining.pop(key)
    return out
