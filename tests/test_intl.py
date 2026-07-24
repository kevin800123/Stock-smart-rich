import pandas as pd

from stocks_power_rich.sources import intl



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



def test_parse_history_closes_skips_gaps_and_chains_chg():
    # 中間 None（休市/缺報價）不產生列，且漲跌%以「前一個有效收盤」為基準，不是前一列日期
    out = intl.parse_history_closes([
        ("2026-07-20", 100.0),
        ("2026-07-21", None),
        ("2026-07-22", 110.0),
    ])
    assert out == {
        "2026-07-20": {"value": 100.0, "chg_pct": None},
        "2026-07-22": {"value": 110.0, "chg_pct": 10.0},
    }
    assert intl.parse_history_closes([]) == {}
    assert intl.parse_history_closes([("2026-07-20", None)]) == {}


def test_pick_close_for_respects_session_availability():
    h = intl.parse_history_closes([
        ("2026-07-17", 10.0),   # 週五
        ("2026-07-20", 20.0),   # 週一
    ])
    # 亞股（same_day）：D 當日已收盤 → 取 D
    assert intl.pick_close_for(h, "2026-07-20", same_day=True)["value"] == 20.0
    # 亞股當日尚未收盤／當天休市 → None。**絕不可退取前一場**：那會把別天的收盤
    # 貼上 D 的標籤，正是本專案禁止的「walk back to another date」。
    assert intl.pick_close_for(h, "2026-07-21", same_day=True) is None
    # 美盤（非 same_day）：台北 D 晚間 21:00 時 D 的美股尚未開盤 → 取 D 之前最近一場
    assert intl.pick_close_for(h, "2026-07-20", same_day=False)["value"] == 10.0
    # 週一往前不是「D-1 曆日(週日)」而是「最近一個有交易的日子(上週五)」
    assert intl.pick_close_for(h, "2026-07-21", same_day=False)["value"] == 20.0
    # 早於所有資料 → None，不硬湊
    assert intl.pick_close_for(h, "2026-07-17", same_day=False) is None
    assert intl.pick_close_for({}, "2026-07-20", same_day=True) is None


def test_parse_chart_history_maps_timestamps_to_session_dates():
    # v8 chart 的 timestamp 是每根日 K 的開盤 epoch；轉成場次日期後與 yfinance 路徑同形狀
    payload = {"chart": {"result": [{
        "timestamp": [1784505600, 1784592000, 1784678400],   # 2026-07-20/21/22 00:00 UTC
        "indicators": {"quote": [{"close": [100.0, None, 110.0]}]},
    }]}}
    out = intl.parse_chart_history(payload)
    assert out == {
        "2026-07-20": {"value": 100.0, "chg_pct": None},
        "2026-07-22": {"value": 110.0, "chg_pct": 10.0},   # None 那天不產生列
    }
    assert intl.parse_chart_history({"chart": {"result": None}}) == {}
    assert intl.parse_chart_history({}) == {}


def test_fetch_intl_history_falls_back_to_chart_api(monkeypatch):
    """yfinance 的 cookie/crumb 握手正是機房 IP 會被擋的那段，故單一代碼失敗要有備援。"""
    class _Boom:
        def history(self, *a, **k):
            raise RuntimeError("crumb rejected")

    monkeypatch.setattr(intl.yf, "Ticker", lambda sym: _Boom())
    monkeypatch.setattr(intl, "_fetch_chart_raw", lambda sym, range_="", interval="": {
        "chart": {"result": [{
            "timestamp": [1784505600, 1784678400],
            "indicators": {"quote": [{"close": [100.0, 110.0]}]},
        }]}} if sym == "^SOX" else None)

    out = intl.fetch_intl_history({"sox": "^SOX", "vix": "^VIX"})
    assert out["sox"]["2026-07-22"] == {"value": 110.0, "chg_pct": 10.0}
    assert "vix" not in out          # 兩條路都失敗 → 該 key 缺席，呼叫端維持 NULL


def test_fetch_futures_monitor_falls_back_to_chart_api(monkeypatch):
    """yf.download 整批被擋（機房 IP 常態）時，必須逐檔退回 chart API。

    少了這層，雲端拿到的是「5 個分類、每組 0 檔」，而呼叫端會把它當有效結果快取，
    海期監控就永遠空著（見 api/helpers.py::_os_futures 的註解）。
    """
    def blocked(*a, **k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(intl.yf, "download", blocked)
    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl, "_fetch_chart_raw", lambda sym, range_="", interval="": {
        "chart": {"result": [{"indicators": {"quote": [{"close": [100.0, 110.0]}]}}]}})

    cats = intl.fetch_futures_monitor()
    assert any(g["items"] for g in cats)
    ym = next(i for g in cats if g["category"] == "指數期貨"
              for i in g["items"] if i["name"] == "小道瓊")
    assert ym["value"] == 110.0 and ym["chg"] == 10.0 and ym["chg_pct"] == 10.0


def test_parse_chart_stats_handles_thin_payloads():
    mk = lambda closes: {"chart": {"result": [{"indicators": {"quote": [{"close": closes}]}}]}}
    assert intl.parse_chart_stats(mk([100.0, 110.0])) == {"value": 110.0, "chg": 10.0, "chg_pct": 10.0}
    assert intl.parse_chart_stats(mk([110.0])) == {"value": 110.0, "chg": None, "chg_pct": None}
    assert intl.parse_chart_stats(mk([])) is None
    assert intl.parse_chart_stats({}) is None
