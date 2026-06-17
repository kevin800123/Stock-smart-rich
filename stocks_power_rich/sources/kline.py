"""K 線資料：個股 OHLC（yfinance）、大盤指數 OHLC（yfinance ^TWII）、
台指期 OHLC（由每日 market_daily 快照累積/聚合）。

candles 每筆順序為 [open, close, low, high]（ECharts candlestick 規格）。
"""
import yfinance as yf

# 指數代碼對應
INDEX_TICKERS = {"taiex": "^TWII"}
# interval → 抓取期間
INTERVAL_PERIOD = {"1d": "6mo", "1wk": "2y", "1mo": "5y"}


def _df_to_candles(df) -> dict:
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(getattr(r, "Volume", 0) or 0) for r in df.itertuples()]
    return {"dates": dates, "candles": candles, "volumes": volumes}


def fetch_kline(code: str, period: str = "1y", interval: str = "1d") -> dict:
    df = yf.Ticker(code).history(period=period, interval=interval)
    if df.empty:
        return {"code": code, "dates": [], "candles": [], "volumes": []}
    return {"code": code, **_df_to_candles(df)}


def fetch_index_kline(symbol: str, interval: str = "1d") -> dict:
    """大盤指數 K 線（目前支援 taiex=^TWII）。"""
    ticker = INDEX_TICKERS.get(symbol)
    if not ticker:
        return {"symbol": symbol, "dates": [], "candles": [], "volumes": []}
    period = INTERVAL_PERIOD.get(interval, "6mo")
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    if df.empty:
        return {"symbol": symbol, "dates": [], "candles": [], "volumes": []}
    return {"symbol": symbol, **_df_to_candles(df)}


def tx_candles_from_rows(market_rows: list, interval: str = "1d") -> dict:
    """以 market_daily 累積的台指期 OHLC 組 K 線；週/月線以 pandas 聚合。"""
    import pandas as pd

    recs = []
    for r in market_rows:
        close = r.get("tx_price")
        if close is None:
            continue
        recs.append({
            "date": r["date"],
            "o": r.get("tx_open") if r.get("tx_open") is not None else close,
            "h": r.get("tx_high") if r.get("tx_high") is not None else close,
            "l": r.get("tx_low") if r.get("tx_low") is not None else close,
            "c": close,
        })
    if not recs:
        return {"symbol": "tx", "dates": [], "candles": [], "volumes": []}

    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")

    if interval in ("1wk", "1mo"):
        rule = "W" if interval == "1wk" else "ME"
        df = df.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()

    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in df.itertuples()]
    return {"symbol": "tx", "dates": dates, "candles": candles, "volumes": [0.0] * len(dates)}
