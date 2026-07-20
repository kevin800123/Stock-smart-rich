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


_MAX_DOD_JUMP = 0.35   # 台股個股單日漲跌幅上限 ±10%，日對日收盤跳動 >35% 必為壞值（0/半值/資料錯）


def _sanitize_series(dates: list, candles: list, volumes: list) -> tuple:
    """丟棄明顯壞列，避免 MA/波浪被污染：任一 OHLC 非正、high<low、或收盤對「前一筆有效
    收盤」跳動 >35%（yfinance/官方源偶發 0 或半值時會出現）。回傳過濾後的三個並列陣列。"""
    out_d, out_c, out_v = [], [], []
    last = None
    for i, c in enumerate(candles):
        o, cl, lo, hi = c
        if None in c or o <= 0 or cl <= 0 or lo <= 0 or hi <= 0 or hi < lo:
            continue
        if last is not None and last > 0 and abs(cl / last - 1) > _MAX_DOD_JUMP:
            continue
        out_d.append(dates[i]); out_c.append(c); out_v.append(volumes[i]); last = cl
    return out_d, out_c, out_v


def _pack_candles(dates: list, candles: list, volumes: list) -> dict:
    """並列陣列 → 組 K 線輸出（含各門檻波浪）。呼叫端須先自行清洗（週/月聚合後不宜再套
    日對日跳動門檻，因整週漲跌可能合理 >35%）。"""
    closes = [c[1] for c in candles]
    waves = {}
    if len(closes) >= 6:
        for pct_int in range(2, 16):
            waves[str(pct_int)] = elliott.elliott_waves(closes, pct_int / 100.0)
    return {"dates": dates, "candles": candles, "volumes": volumes, "waves": waves}


def _df_to_candles(df, interval: str = "1d") -> dict:
    dates = [_fmt_dt(d, interval) for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(getattr(r, "Volume", 0) or 0) for r in df.itertuples()]
    return _pack_candles(*_sanitize_series(dates, candles, volumes))


def _history(code: str, period: str, interval: str, tries: int = 3):
    """抓 yfinance 歷史；雲端 IP 常被 Yahoo 偶發限流，故重試數次，全失敗回空 df。"""
    import time

    import pandas as pd

    for i in range(tries):
        try:
            df = yf.Ticker(code).history(period=period, interval=interval)
            if df is not None and not df.empty:
                return df
        except Exception:  # noqa: BLE001 — 限流/錯誤 → 重試
            pass
        if i < tries - 1:
            time.sleep(0.8)
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
        return {"code": code, "dates": [], "candles": [], "volumes": [], "waves": {}}
    return {"code": code, **_df_to_candles(df, interval)}


def fetch_index_kline(symbol: str, interval: str = "1d") -> dict:
    """大盤指數 K 線（目前支援 taiex=^TWII）。"""
    ticker = INDEX_TICKERS.get(symbol)
    empty = {"symbol": symbol, "dates": [], "candles": [], "volumes": [], "waves": {}}
    if not ticker:
        return empty
    period = INTERVAL_PERIOD.get(interval, "6mo")
    df = _history(ticker, period, interval)
    if df is None or df.empty:
        return empty
    return {"symbol": symbol, **_df_to_candles(df, interval)}


def ohlc_candles(rows: list, interval: str = "1d") -> dict:
    """通用 OHLC 組 K 線：rows 含 date/open/high/low/close(/volume)；週/月以 pandas 聚合。"""
    import pandas as pd

    recs = [{"date": r["date"], "o": r["open"], "h": r["high"], "l": r["low"],
             "c": r["close"], "v": r.get("volume") or 0}
            for r in rows if r.get("close") is not None]
    if not recs:
        return {"dates": [], "candles": [], "volumes": [], "waves": {}}

    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    # 先在日線層級清洗壞列（0/半值），避免污染 MA/波浪與週月聚合的 min/max
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in df.itertuples()]
    volumes = [float(r.v) for r in df.itertuples()]
    dates, candles, volumes = _sanitize_series(dates, candles, volumes)

    if interval in ("1wk", "1mo"):
        clean = pd.DataFrame({"date": pd.to_datetime(dates),
                              "o": [c[0] for c in candles], "c": [c[1] for c in candles],
                              "l": [c[2] for c in candles], "h": [c[3] for c in candles],
                              "v": volumes}).set_index("date")
        rule = "W" if interval == "1wk" else "ME"
        clean = clean.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
        dates = [d.strftime("%Y-%m-%d") for d in clean.index]
        candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in clean.itertuples()]
        volumes = [float(r.v) for r in clean.itertuples()]
    return _pack_candles(dates, candles, volumes)
