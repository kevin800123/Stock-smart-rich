"""K 線資料：個股 OHLC（yfinance）、大盤指數 OHLC（yfinance ^TWII）、
台指期 OHLC（由每日 market_daily 快照累積/聚合）。

candles 每筆順序為 [open, close, low, high]（ECharts candlestick 規格）。
"""
import yfinance as yf

from .. import elliott

# 指數代碼對應
INDEX_TICKERS = {"taiex": "^TWII"}
# interval → 抓取期間（1h 為小時K，受 yfinance 期間限制取近一月）
INTERVAL_PERIOD = {"1h": "1mo", "1d": "6mo", "1wk": "2y", "1mo": "5y"}


def _fmt_dt(d, interval: str) -> str:
    return d.strftime("%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d")


def _df_to_candles(df, interval: str = "1d") -> dict:
    dates = [_fmt_dt(d, interval) for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(getattr(r, "Volume", 0) or 0) for r in df.itertuples()]
    closes = [c[1] for c in candles]
    waves = elliott.elliott_waves(closes) if len(closes) >= 6 else []
    return {"dates": dates, "candles": candles, "volumes": volumes, "waves": waves}


def _history(code: str, period: str, interval: str):
    try:
        return yf.Ticker(code).history(period=period, interval=interval)
    except Exception:  # noqa: BLE001 — 抓不到視為空
        import pandas as pd

        return pd.DataFrame()


def fetch_kline(code: str, period: str = "1y", interval: str = "1d") -> dict:
    df = _history(code, period, interval)
    # 上櫃/興櫃股 .TW 查不到 → 改試 .TWO（CSV 一律給 .TW）
    if (df is None or df.empty) and code.endswith(".TW"):
        alt = code[:-3] + ".TWO"
        alt_df = _history(alt, period, interval)
        if alt_df is not None and not alt_df.empty:
            code, df = alt, alt_df
    if df is None or df.empty:
        return {"code": code, "dates": [], "candles": [], "volumes": [], "waves": []}
    return {"code": code, **_df_to_candles(df, interval)}


def fetch_index_kline(symbol: str, interval: str = "1d") -> dict:
    """大盤指數 K 線（目前支援 taiex=^TWII）。"""
    ticker = INDEX_TICKERS.get(symbol)
    empty = {"symbol": symbol, "dates": [], "candles": [], "volumes": [], "waves": []}
    if not ticker:
        return empty
    period = INTERVAL_PERIOD.get(interval, "6mo")
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    if df.empty:
        return empty
    return {"symbol": symbol, **_df_to_candles(df, interval)}


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
        return {"symbol": "tx", "dates": [], "candles": [], "volumes": [], "waves": []}

    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")

    if interval in ("1wk", "1mo"):
        rule = "W" if interval == "1wk" else "ME"
        df = df.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()

    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in df.itertuples()]
    closes = [c[1] for c in candles]
    waves = elliott.elliott_waves(closes) if len(closes) >= 6 else []
    return {"symbol": "tx", "dates": dates, "candles": candles, "volumes": [0.0] * len(dates), "waves": waves}
