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
