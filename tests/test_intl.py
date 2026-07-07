import pandas as pd

from stocks_power_rich.sources import intl


def _chart_payload(closes):
    return {"chart": {"result": [{"indicators": {"quote": [{"close": closes}]}}], "error": None}}


def test_parse_chart_payload():
    # 中間有 null（Yahoo 常見）→ 取最後兩個有效收盤算漲跌%
    p = intl.parse_chart_payload(_chart_payload([100.0, None, 110.0]))
    assert p == {"value": 110.0, "chg_pct": 10.0}
    assert intl.parse_chart_payload(_chart_payload([110.0])) == {"value": 110.0, "chg_pct": None}
    assert intl.parse_chart_payload(_chart_payload([])) is None
    assert intl.parse_chart_payload({"chart": {"result": None}}) is None
    assert intl.parse_chart_payload({}) is None


def test_fetch_intl_indices_chart_fallback(monkeypatch):
    """yfinance 整批一直失敗（機房 IP 被限流）→ 直連 Yahoo chart API 逐檔備援補抓。"""
    def bad_download(*a, **kw):
        raise RuntimeError("rate limited")

    class _Resp:
        status_code = 200

        def __init__(self, sym):
            self._sym = sym

        def json(self):
            return _chart_payload([100.0, 123.0])

    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return _Resp(url)

    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl.yf, "download", bad_download)
    monkeypatch.setattr(intl.httpx, "get", fake_get)
    out = intl.fetch_intl_indices({"sox": "^SOX", "btc": "BTC-USD"})
    assert out["sox"] == {"value": 123.0, "chg_pct": 23.0}
    assert out["btc"]["value"] == 123.0
    assert any("%5ESOX" in u for u in urls)   # ^ 需 URL 編碼進路徑


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


def test_fetch_futures_monitor_groups(monkeypatch):
    def fake_download(tickers, period=None, **kw):
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        data = {("Close", t): [100.0, 110.0] for t in tickers.split()}
        return pd.DataFrame(data, index=idx)

    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl.yf, "download", fake_download)
    cats = intl.fetch_futures_monitor()
    assert [g["category"] for g in cats] == ["指數期貨", "能源金屬", "農產品", "外匯", "美股"]
    gold = next(it for g in cats if g["category"] == "能源金屬"
                for it in g["items"] if it["name"] == "黃金")
    assert gold["value"] == 110.0 and gold["chg"] == 10.0 and gold["chg_pct"] == 10.0
    assert len(next(g for g in cats if g["category"] == "美股")["items"]) == 14


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
