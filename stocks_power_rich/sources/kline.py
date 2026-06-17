"""個股日 K 線：用 yfinance 抓 OHLC 歷史，轉成 ECharts 蠟燭圖資料結構。

candles 每筆順序為 [open, close, low, high]（ECharts candlestick 規格）。
"""
import yfinance as yf


def fetch_kline(code: str, period: str = "1y") -> dict:
    df = yf.Ticker(code).history(period=period)
    if df.empty:
        return {"code": code, "dates": [], "candles": [], "volumes": []}
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(r.Volume) for r in df.itertuples()]
    return {"code": code, "dates": dates, "candles": candles, "volumes": volumes}
