import pandas as pd

from stocks_power_rich.sources import intl


def test_fetch_intl_indices(monkeypatch):
    def fake_download(tickers, period=None, **kw):
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        data = {("Close", t): [100.0, 110.0] for t in tickers.split()}
        return pd.DataFrame(data, index=idx)

    monkeypatch.setattr(intl.yf, "download", fake_download)
    out = intl.fetch_intl_indices({"sox": "^SOX", "btc": "BTC-USD"})
    assert out["sox"]["value"] == 110.0
    assert out["sox"]["chg_pct"] == 10.0
    assert out["btc"]["value"] == 110.0


def test_fetch_intl_indices_retries_missing_ticker(monkeypatch):
    """單一代碼首抓回 NaN（雲端 yfinance 偶發）→ 重試補抓，不影響其他代碼。"""
    calls = {"n": 0}

    def fake_download(tickers, period=None, **kw):
        calls["n"] += 1
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        data = {}
        for t in tickers.split():
            # 第一輪 JPY=X 整欄 NaN，之後才有值
            bad = t == "JPY=X" and calls["n"] == 1
            data[("Close", t)] = [float("nan"), float("nan")] if bad else [100.0, 110.0]
        return pd.DataFrame(data, index=idx)

    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl.yf, "download", fake_download)
    out = intl.fetch_intl_indices({"sox": "^SOX", "jpy": "JPY=X"})
    assert out["sox"]["value"] == 110.0
    assert out["jpy"]["value"] == 110.0   # 第二輪補抓成功
    assert calls["n"] >= 2
