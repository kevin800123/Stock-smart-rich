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


def test_fetch_kline_falls_back_to_two(monkeypatch):
    # 上櫃股 .TW 查不到 → 自動改試 .TWO
    def fake_history(self, period="1y", interval="1d"):
        if self.ticker.endswith(".TWO"):
            idx = pd.to_datetime(["2026-06-12"])
            return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [100]}, index=idx)
        return pd.DataFrame()  # .TW 空

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_kline("6174.TW")
    assert out["code"] == "6174.TWO"
    assert out["candles"][0] == [10.0, 11.0, 9.0, 12.0]


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


def test_ohlc_candles_daily():
    rows = [
        {"date": "2026-06-16", "open": 45600, "high": 45900, "low": 45550, "close": 45772, "volume": 100},
        {"date": "2026-06-17", "open": 45772, "high": 45850, "low": 45700, "close": 45809, "volume": 200},
    ]
    out = kline.ohlc_candles(rows, interval="1d")
    assert out["dates"] == ["2026-06-16", "2026-06-17"]
    assert out["candles"][0] == [45600.0, 45772.0, 45550.0, 45900.0]
    assert out["volumes"] == [100.0, 200.0]


def test_ohlc_candles_drops_bad_rows():
    # 壞值列（yfinance/官方源偶發 0 或半值）：台股單日最多 ±10%，日對日跳動 >35% 必為錯誤 → 丟棄
    rows = [
        {"date": "2026-02-09", "open": 1800, "high": 1820, "low": 1790, "close": 1810, "volume": 100},
        {"date": "2026-02-10", "open": 950, "high": 960, "low": 940, "close": 950, "volume": 100},   # 半值壞列
        {"date": "2026-02-11", "open": 1815, "high": 1840, "low": 1810, "close": 1830, "volume": 100},
        {"date": "2026-02-12", "open": 1830, "high": 1850, "low": 0, "close": 1840, "volume": 100},   # low<=0 壞列
        {"date": "2026-02-13", "open": 1840, "high": 1860, "low": 1830, "close": 1850, "volume": 100},
    ]
    out = kline.ohlc_candles(rows, interval="1d")
    assert out["dates"] == ["2026-02-09", "2026-02-11", "2026-02-13"]   # 兩壞列被丟
    assert all(c[1] > 1000 for c in out["candles"])                    # 沒有半值殘留


def test_ohlc_candles_weekly_resample():
    # 同一週兩天 → 週線聚合成一根（open第一天、close最後天、high最大、low最小）
    rows = [
        {"date": "2026-06-15", "open": 100, "high": 120, "low": 95, "close": 110, "volume": 1},
        {"date": "2026-06-16", "open": 110, "high": 130, "low": 90, "close": 125, "volume": 2},
    ]
    out = kline.ohlc_candles(rows, interval="1wk")
    assert len(out["candles"]) == 1
    assert out["candles"][0] == [100.0, 125.0, 90.0, 130.0]


def test_kline_waves_precomputed(monkeypatch):
    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime([f"2026-06-{10+i}" for i in range(12)])
        # Create an upward and downward zigzag pattern to trigger wave labeling
        closes = [100, 110, 105, 120, 115, 130, 125, 140, 130, 150, 140, 160]
        return pd.DataFrame(
            {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [100]*12},
            index=idx,
        )

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_kline("2330.TW", period="1mo")
    assert isinstance(out["waves"], dict)
    # Check that keys from "2" to "15" are present
    for i in range(2, 16):
        assert str(i) in out["waves"]
        assert isinstance(out["waves"][str(i)], list)
