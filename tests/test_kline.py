import pandas as pd

from stocks_power_rich.sources import kline


def test_fetch_kline_echarts_shape(monkeypatch):
    def fake_history(self, period="1y"):
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        return pd.DataFrame(
            {"Open": [10, 11], "High": [12, 13], "Low": [9, 10], "Close": [11, 12], "Volume": [100, 200]},
            index=idx,
        )

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_kline("2330.TW", period="1mo")
    assert out["dates"] == ["2026-06-12", "2026-06-13"]
    # ECharts candlestick 順序：[open, close, low, high]
    assert out["candles"][0] == [10.0, 11.0, 9.0, 12.0]
    assert out["volumes"] == [100.0, 200.0]
