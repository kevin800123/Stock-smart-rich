import pandas as pd

from stocks_power_rich.sources import intl



def test_parse_tv_scan_maps_by_ticker_and_skips_missing_close():
    # TradingView scanner 回應：每列 d=[close, change_pct, change_abs]，依 "s"(代碼) 對映
    payload = {"totalCount": 3, "data": [
        {"s": "COMEX:GC1!", "d": [4058.7, 0.22, 8.9]},
        {"s": "NASDAQ:NVDA", "d": [207.13, -0.78, -1.63]},
        {"s": "FX:EURUSD", "d": [None, None, None]},   # 缺 close → 跳過，不入表
    ]}
    out = intl.parse_tv_scan(payload)
    assert out["COMEX:GC1!"] == {"value": 4058.7, "chg": 8.9, "chg_pct": 0.22}
    assert out["NASDAQ:NVDA"]["value"] == 207.13 and out["NASDAQ:NVDA"]["chg"] == -1.63
    assert "FX:EURUSD" not in out
    assert intl.parse_tv_scan({}) == {}
    assert intl.parse_tv_scan({"data": None}) == {}


def test_fetch_futures_monitor_groups(monkeypatch):
    # 一次 POST TradingView scanner，回應依代碼對回五大分類
    def fake_post(url, json=None, **kw):
        tickers = json["symbols"]["tickers"]
        data = [{"s": t, "d": [110.0, 10.0, 10.0]} for t in tickers]

        class _R:
            status_code = 200
            def json(self):  # noqa: N802
                return {"totalCount": len(data), "data": data}
        return _R()

    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl.httpx, "post", fake_post)
    cats = intl.fetch_futures_monitor()
    assert [g["category"] for g in cats] == ["指數期貨", "能源金屬", "農產品", "外匯", "美股"]
    gold = next(it for g in cats if g["category"] == "能源金屬"
                for it in g["items"] if it["name"] == "黃金")
    assert gold["value"] == 110.0 and gold["chg"] == 10.0 and gold["chg_pct"] == 10.0
    assert len(next(g for g in cats if g["category"] == "美股")["items"]) == 14


def test_fetch_futures_monitor_returns_empty_groups_on_http_error(monkeypatch):
    # 端點掛掉（非 200 或連線失敗）→ 五個分類、每組 0 檔；呼叫端據此判定不寫快取
    def boom(url, json=None, **kw):
        raise RuntimeError("blocked")

    monkeypatch.setattr(intl.time, "sleep", lambda s: None)
    monkeypatch.setattr(intl.httpx, "post", boom)
    cats = intl.fetch_futures_monitor()
    assert [g["category"] for g in cats] == ["指數期貨", "能源金屬", "農產品", "外匯", "美股"]
    assert all(g["items"] == [] for g in cats)



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


def test_parse_tv_scan_dated_converts_epoch_to_local_session_date():
    # KOSPI 收盤在台北傍晚更新前已結束，用回傳的 time 欄位反查是否真的落在該場次日期，
    # 不是像 sox 那樣可能抓到盤中即時價。1784851200 UTC = 2026-07-24 00:00 UTC，
    # 換成 Asia/Seoul(+9) 仍是 2026-07-24。
    payload = {"data": [{"s": "TVC:KOSPI", "d": [6690.63, -5.72, -406.27, 1784851200]}]}
    out = intl.parse_tv_scan_dated(payload)
    assert out == {"date": "2026-07-24", "value": 6690.63, "chg_pct": -5.72}


def test_parse_tv_scan_dated_missing_fields_returns_none():
    assert intl.parse_tv_scan_dated({"data": []}) is None
    assert intl.parse_tv_scan_dated({}) is None
    # 缺 time 欄位（只有 3 個元素）→ 無法判定場次日期，不可硬猜
    assert intl.parse_tv_scan_dated({"data": [{"s": "TVC:KOSPI", "d": [6690.63, -5.72, -406.27]}]}) is None
    assert intl.parse_tv_scan_dated({"data": [{"s": "TVC:KOSPI", "d": [None, None, None, 1784851200]}]}) is None


def test_fetch_kospi_dated_wraps_scanner(monkeypatch):
    def fake_post(url, json=None, **kw):
        assert json["symbols"]["tickers"] == ["TVC:KOSPI"]

        class _R:
            status_code = 200
            def json(self):  # noqa: N802
                return {"data": [{"s": "TVC:KOSPI", "d": [6690.63, -5.72, -406.27, 1784851200]}]}
        return _R()

    monkeypatch.setattr(intl.httpx, "post", fake_post)
    out = intl.fetch_kospi_dated()
    assert out == {"date": "2026-07-24", "value": 6690.63, "chg_pct": -5.72}


def test_fetch_kospi_dated_failure_returns_none(monkeypatch):
    def boom(url, json=None, **kw):
        raise RuntimeError("blocked")

    monkeypatch.setattr(intl.httpx, "post", boom)
    assert intl.fetch_kospi_dated() is None


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


