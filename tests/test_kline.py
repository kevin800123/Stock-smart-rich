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
    # **個股 K 線的量一律是「張」**：yfinance 的 Volume 是股數，這裡 /1000 正規化，
    # 才能與後備來源 stock_ohlc.volume_lots（本來就是張）同一個單位。不統一的話，
    # 本機（yfinance 通）與雲端（yfinance 被擋、走 stock_ohlc）畫出來的量會差 1000 倍。
    assert out["volumes"] == [0.1, 0.2]


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


def test_sanitize_drops_nan_rows_so_the_response_stays_json_encodable():
    """NaN 會通過所有既有守衛：它不是 None，而且所有與 NaN 的比較都回 False
    （nan<=0、hi<lo、跳動門檻全部不成立）。壞列因此一路走到 FastAPI 序列化才炸成
    `Out of range float values are not JSON compliant: nan`，整個端點 500、該股 K 線
    完全打不開。實測 yfinance 偶爾會給這種未完成的 bar（同一支股票早上好、下午 500）。"""
    import json
    from stocks_power_rich.sources.kline import _sanitize_series

    nan = float("nan")
    dates = ["2026-08-01", "2026-08-02", "2026-08-03"]
    candles = [[10.0, 10.5, 9.8, 10.6], [nan, nan, nan, nan], [10.5, 10.7, 10.4, 10.8]]
    volumes = [100, 200, 300]

    d, c, v = _sanitize_series(dates, candles, volumes)
    assert d == ["2026-08-01", "2026-08-03"]
    assert len(c) == 2 and len(v) == 2
    json.dumps({"dates": d, "candles": c, "volumes": v})   # 不可拋 ValueError


def test_sanitize_drops_a_partially_nan_row():
    """只有一欄是 NaN 也要整列丟掉——半筆 OHLC 畫不出 K 棒。"""
    from stocks_power_rich.sources.kline import _sanitize_series

    nan = float("nan")
    d, c, _ = _sanitize_series(
        ["d1", "d2"], [[10.0, 10.5, 9.8, 10.6], [10.0, nan, 9.9, 10.2]], [1, 2])
    assert d == ["d1"] and len(c) == 1


def test_merge_tail_appends_newer_official_rows_to_a_lagging_primary():
    """主來源落後官方時要補尾巴，不能只在它「完全失敗」時才用備援。

    實測 2026-08-06 08:17：yfinance ^TWII 只到 08-04，而 TWSE MI_5MINS_HIST 已有
    08-05（收盤 44611.6，與 market_daily 一致）。原本的備援是**全有全無**——主來源
    回夠多根就直接用、永遠不問官方，於是「大盤×籌碼對照」的籌碼窗格有 08-05、
    K 線卻沒有，最新一天看不到指數。
    """
    from stocks_power_rich.sources import kline
    base = {"dates": ["2026-08-03", "2026-08-04"],
            "candles": [[100.0, 110.0, 99.0, 111.0], [110.0, 120.0, 109.0, 121.0]],
            "volumes": [10.0, 20.0], "waves": {}}
    rows = [
        {"date": "2026-08-04", "open": 110.0, "high": 121.0, "low": 109.0, "close": 120.0},
        {"date": "2026-08-05", "open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},
    ]
    out = kline.merge_tail(base, rows, "1d")
    assert out["dates"] == ["2026-08-03", "2026-08-04", "2026-08-05"]
    assert out["candles"][-1] == [120.0, 130.0, 119.0, 131.0]   # [open, close, low, high]
    # 既有那天不可被重複附加（rows 也含 08-04）
    assert out["dates"].count("2026-08-04") == 1


def test_merge_tail_is_a_noop_when_primary_is_already_current():
    from stocks_power_rich.sources import kline
    base = {"dates": ["2026-08-04"], "candles": [[1.0, 2.0, 0.5, 2.5]], "volumes": [1.0], "waves": {}}
    # 官方沒有更新的日期 → 原樣返回，不做多餘的重算
    assert kline.merge_tail(base, [{"date": "2026-08-04", "open": 1.0, "high": 2.5,
                                    "low": 0.5, "close": 2.0}], "1d") is base
    assert kline.merge_tail(base, [], "1d") is base
    assert kline.merge_tail({"dates": [], "candles": []}, [{"date": "2026-08-05", "close": 1.0}], "1d")         == {"dates": [], "candles": []}


def test_merge_tail_skips_rows_without_a_close():
    """缺收盤價的列補不出 K 棒，寧可不補——半根 K 線比沒有更誤導。"""
    from stocks_power_rich.sources import kline
    base = {"dates": ["2026-08-04"], "candles": [[1.0, 2.0, 0.5, 2.5]], "volumes": [1.0], "waves": {}}
    assert kline.merge_tail(base, [{"date": "2026-08-05", "close": None}], "1d") is base


def test_fetch_kline_volume_is_lots_not_shares(monkeypatch):
    """個股 K 線量能的單位契約＝張。yfinance 給的是股數，必須 /1000。

    這條契約存在的理由是「同一張圖在兩個環境會有兩個來源」：雲端 yfinance 被擋、
    一律走 stock_ohlc（volume_lots，本來就是張），本機 yfinance 通則走 Volume（股）。
    不統一就會變成「本機看起來正常、雲端數字差 1000 倍」那類只在部署後才發現的錯。
    """
    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11],
                             "Volume": [1_234_000]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_kline("2330.TW", period="1mo")
    assert out["volumes"] == [1234.0]        # 1,234,000 股 ＝ 1,234 張


def test_fetch_index_kline_volume_is_not_divided(monkeypatch):
    """指數 K 線**不套**個股那條 /1000：大盤量能另有口徑，跟著除會默默改掉既有圖的刻度。"""
    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11],
                             "Volume": [1_234_000]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_index_kline("taiex")
    assert out["volumes"] == [1_234_000.0]


def test_sanitize_series_does_not_drop_the_whole_tail_after_one_bad_bar():
    """一根壞值不可以把後面整條序列連鎖丟光。

    實測踩到（3022）：資料庫其實有到 2026-09-04 的完整日線，圖表卻停在 41.7、停在四月。
    原因是 41.7 這根壞值相對前一根只跌 30%（低於 35% 門檻）因而**被接受**，之後每一根
    真實價格（~60→95.9）相對 41.7 都跳 >35% 全被拒絕；而被拒時 `last` 不更新，於是
    永遠追不回來——一根壞值吃掉五個月的真實資料。

    正確行為：孤立壞值只該丟掉它自己，序列要能回到真實水位。
    """
    dates = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
             "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12"]      # 相鄰交易日
    good = [60.0, 60.5, 61.0, 20.0, 60.8, 61.2, 62.0, 63.0, 64.0]         # 第 4 根是壞值
    candles = [[p, p, p * 0.99, p * 1.01] for p in good]
    volumes = [100.0] * len(good)
    d, c, v = kline._sanitize_series(dates, candles, volumes)
    closes = [x[1] for x in c]
    assert 20.0 not in closes            # 超標的孤立壞值要被丟掉
    assert len(closes) == 8              # 其餘 8 根全留著——修好前這裡只會剩 4 根
    assert closes[-1] == 64.0            # 尾端必須存活（先前整段消失，圖表就停在壞值那天）
    assert len(d) == len(c) == len(v)    # 三個陣列仍等長


def test_sanitize_series_keeps_within_threshold_outliers_on_purpose():
    """低於門檻的異常值**刻意保留**——不把門檻收緊是有理由的。

    除權息造成的合理大跌可能超過 10%（原始價未還原時），收太緊會把真實走勢當壞值刪掉。
    所以 -31.6% 這種仍會留在圖上；真正要防的是「一根壞值吃掉後面整段」，那個已由
    just_rejected 護欄解決。這條測試把這個取捨釘住，避免日後有人「順手」把門檻改嚴。
    """
    dates = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
    candles = [[p, p, p * 0.99, p * 1.01] for p in (60.0, 41.7, 60.8, 61.2)]
    _, c, _ = kline._sanitize_series(dates, candles, [1.0] * 4)
    closes = [x[1] for x in c]
    assert 41.7 in closes          # 低於門檻 → 保留（不因除權息式的大跌而誤刪）
    assert closes[-1] == 61.2      # **序列在一根之內就回到真實水位**，不會再往後連鎖
    # 已知且接受的代價：異常值「之後那一根」仍會被丟掉一根（護欄要下一根才生效）。
    # 要完全不丟就得往前看一根，複雜度不划算——重點是不再吃掉整個尾巴。
    assert 60.8 not in closes


def test_sanitize_series_allows_large_moves_across_a_data_gap():
    """序列有洞時，跨洞的大幅變動是合理的，不該套「單日跳動」門檻。

    stock_ohlc 的覆蓋度取決於回補跑到哪，中間有洞是常態；跨越數月的價格變動本來就可能
    遠超過 35%，把它當壞值丟掉會讓「補完資料」反而看不到東西。
    """
    dates = ["2026-01-05", "2026-01-06", "2026-06-01"]   # 第 3 筆隔了快 5 個月
    good = [60.0, 60.5, 95.9]                             # 跨洞 +58%，是真實走勢
    candles = [[p, p, p * 0.99, p * 1.01] for p in good]
    d, c, _ = kline._sanitize_series(dates, candles, [1.0] * 3)
    assert [x[1] for x in c] == [60.0, 60.5, 95.9]        # 跨洞那筆要留著
