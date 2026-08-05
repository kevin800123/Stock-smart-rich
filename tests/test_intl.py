from datetime import datetime, timezone

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


def test_session_closed_uses_market_local_time_and_settle_buffer():
    # 美股 16:00 ET 收盤＋30 分緩衝 → 2026-08-04 那場在 20:30 UTC 才算收完
    ny = ("America/New_York", (16, 0))
    just_before = datetime(2026, 8, 4, 20, 29, tzinfo=timezone.utc)
    just_after = datetime(2026, 8, 4, 20, 30, tzinfo=timezone.utc)
    assert not intl.session_closed("2026-08-04", *ny, now=just_before)
    assert intl.session_closed("2026-08-04", *ny, now=just_after)
    # 冬令時間同一個時鐘時刻會晚一小時（EST=UTC-5）→ 不可寫死時差
    assert not intl.session_closed("2026-01-05", *ny,
                                   now=datetime(2026, 1, 5, 21, 29, tzinfo=timezone.utc))
    assert intl.session_closed("2026-01-05", *ny,
                               now=datetime(2026, 1, 5, 21, 30, tzinfo=timezone.utc))


def test_parse_tv_dated_rejects_sessions_that_have_not_closed_yet():
    """`time` 是 bar 的**開盤**時間戳，所以「日期解得出來」不等於「那一場收完了」。

    實測 09:05 台北打 scanner，NI225／KOSPI 回的就是當天進行中的盤中值（bar 開盤
    09:00 當地）；若照舊只看日期就寫入，等於把盤中價貼上收盤的標籤，而且因為
    「只填 NULL 不覆蓋」永遠不會被修正。
    """
    specs = {k: intl.TV_DATED[k] for k in ("sox", "n225")}
    payload = {"data": [
        # SOX：bar 開盤 2026-08-04 09:30 NY（13:30 UTC），該場 16:00 ET 收
        {"s": "TVC:SOX", "d": [12179.26, 6.55, 748.6, 1785850200]},
        # NI225：bar 開盤 2026-08-05 09:00 JST（前一日 00:00 UTC），該場 15:00 JST 收
        {"s": "TVC:NI225", "d": [65935.6, 3.09, 1977.0, 1785888000]},
    ]}
    # 2026-08-05 01:05 UTC ＝ 台北 09:05：美股 08-04 那場早收完，日本 08-05 那場才剛開盤
    now = datetime(2026, 8, 5, 1, 5, tzinfo=timezone.utc)
    out = intl.parse_tv_dated(payload, specs, now=now)
    assert out == {"sox": {"date": "2026-08-04", "value": 12179.26, "chg_pct": 6.55}}

    # 日本收盤後（15:30 JST＝06:30 UTC）才收得到 n225
    later = datetime(2026, 8, 5, 6, 30, tzinfo=timezone.utc)
    assert intl.parse_tv_dated(payload, specs, now=later)["n225"] == {
        "date": "2026-08-05", "value": 65935.6, "chg_pct": 3.09}


def test_parse_tv_dated_skips_rows_it_cannot_judge():
    specs = {"kospi": intl.TV_DATED["kospi"]}
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    assert intl.parse_tv_dated({"data": []}, specs, now=now) == {}
    assert intl.parse_tv_dated({}, specs, now=now) == {}
    # 缺 time（只有 3 欄）→ 判不出場次日期，不可硬猜
    assert intl.parse_tv_dated(
        {"data": [{"s": "TVC:KOSPI", "d": [6690.63, -5.72, -406.27]}]}, specs, now=now) == {}
    assert intl.parse_tv_dated(
        {"data": [{"s": "TVC:KOSPI", "d": [None, None, None, 1784851200]}]}, specs, now=now) == {}
    # 不在 specs 裡的代碼一律忽略
    assert intl.parse_tv_dated(
        {"data": [{"s": "NASDAQ:NVDA", "d": [207.1, 1.0, 2.0, 1784851200]}]}, specs, now=now) == {}


def test_fetch_dated_closes_requests_only_the_wanted_tickers(monkeypatch):
    seen = {}

    def fake_post(url, json=None, **kw):
        seen["tickers"] = json["symbols"]["tickers"]
        seen["columns"] = json["columns"]

        class _R:
            status_code = 200
            def json(self):  # noqa: N802
                return {"data": [{"s": "TVC:KOSPI", "d": [6690.63, -5.72, -406.27, 1784851200]}]}
        return _R()

    monkeypatch.setattr(intl.httpx, "post", fake_post)
    out = intl.fetch_dated_closes(["kospi"])
    assert seen["tickers"] == ["TVC:KOSPI"] and "time" in seen["columns"]
    # 1784851200 UTC ＝ 2026-07-24 09:00 KST，那一場早已收完（測試當下已是 2026-08）
    assert out["kospi"] == {"date": "2026-07-24", "value": 6690.63, "chg_pct": -5.72}


def test_fetch_dated_closes_failure_returns_empty(monkeypatch):
    def boom(url, json=None, **kw):
        raise RuntimeError("blocked")

    monkeypatch.setattr(intl.httpx, "post", boom)
    assert intl.fetch_dated_closes() == {}
    assert intl.fetch_dated_closes([]) == {}     # 沒有要補的 key → 連請求都不發


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


