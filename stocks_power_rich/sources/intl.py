"""國際指數抓取：費半(^SOX)、日經(^N225)、KOSPI(^KS11)、黃金(GC=F)、比特幣(BTC-USD)。

來源 yfinance；回傳每個 key 的最新值與相對前一日漲跌百分比。
"""
import yfinance as yf


def fetch_intl_indices(tickers: dict) -> dict:
    symbols = " ".join(tickers.values())
    df = yf.download(symbols, period="5d", progress=False)["Close"]
    out = {}
    for key, sym in tickers.items():
        series = df[sym].dropna() if sym in df else df.dropna()
        if len(series) >= 2:
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            chg = round((last - prev) / prev * 100, 2) if prev else None
            out[key] = {"value": round(last, 2), "chg_pct": chg}
        elif len(series) == 1:
            out[key] = {"value": round(float(series.iloc[-1]), 2), "chg_pct": None}
    return out
