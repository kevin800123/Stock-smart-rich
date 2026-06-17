import pandas as pd

from stocks_power_rich.sources import kline


def test_fetch_kline_echarts_shape(monkeypatch):
    def fake_history(self, period="1y", interval="1d"):
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


def test_fetch_index_kline_taiex(monkeypatch):
    captured = {}

    def fake_history(self, period="1y", interval="1d"):
        captured["interval"] = interval
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        return pd.DataFrame(
            {"Open": [100, 110], "High": [120, 130], "Low": [90, 100], "Close": [110, 120], "Volume": [1, 2]},
            index=idx,
        )

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_index_kline("taiex", interval="1wk")
    assert captured["interval"] == "1wk"
    assert out["candles"][0] == [100.0, 110.0, 90.0, 120.0]


def test_tx_candles_from_rows_daily():
    rows = [
        {"date": "2026-06-16", "tx_open": 45600, "tx_high": 45900, "tx_low": 45550, "tx_price": 45772},
        {"date": "2026-06-17", "tx_open": 45772, "tx_high": 45850, "tx_low": 45700, "tx_price": 45809},
    ]
    out = kline.tx_candles_from_rows(rows, interval="1d")
    assert out["dates"] == ["2026-06-16", "2026-06-17"]
    assert out["candles"][0] == [45600.0, 45772.0, 45550.0, 45900.0]


def test_tx_candles_weekly_resample():
    # 同一週兩天 → 週線聚合成一根（open第一天、close最後天、high最大、low最小）
    rows = [
        {"date": "2026-06-15", "tx_open": 100, "tx_high": 120, "tx_low": 95, "tx_price": 110},
        {"date": "2026-06-16", "tx_open": 110, "tx_high": 130, "tx_low": 90, "tx_price": 125},
    ]
    out = kline.tx_candles_from_rows(rows, interval="1wk")
    assert len(out["candles"]) == 1
    assert out["candles"][0] == [100.0, 125.0, 90.0, 130.0]
