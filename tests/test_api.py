import os

from fastapi.testclient import TestClient

from stocks_power_rich.main import create_app
from tests.conftest import HEADER, ROW_2330


def test_dashboard_and_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)

    assert client.get("/api/dashboard").status_code == 200

    content = (
        "符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n"
    ).encode("big5")
    r = client.post("/api/csv/upload", files={"file": ("a.csv", content, "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["picks"][0]["code"] == "2330.TW"

    # 重整後仍可從最新快照取得篩選榜
    d = client.get("/api/analysis/daily").json()
    assert d["snap_date"] == "2026-06-15"
    assert d["picks"][0]["code"] == "2330.TW"


def test_data_is_stale_logic():
    from stocks_power_rich.main import data_is_stale

    # 平日(週三=2)且資料停在前一交易日 → 官方當日盤後尚未釋出
    assert data_is_stale("2026-06-23", "2026-06-24", 2) is True
    # 已是當日 → 不提示
    assert data_is_stale("2026-06-24", "2026-06-24", 2) is False
    # 週六(5)：資料停在週五屬正常 → 不提示
    assert data_is_stale("2026-06-26", "2026-06-27", 5) is False
    # 尚無任何資料 → 不提示
    assert data_is_stale(None, "2026-06-24", 2) is False


def test_dashboard_includes_today_and_stale_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    body = client.get("/api/dashboard").json()
    assert "today" in body and "data_stale" in body
    assert body["data_stale"] is False  # 無資料時不標延遲


def test_sectors_endpoint_sorted_by_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "航運", "close": 1.0, "chg_pct": -1.0},
        {"name": "半導體", "close": 1.0, "chg_pct": 2.0},
        {"name": "金融保險", "close": 1.0, "chg_pct": 0.5},
    ])
    monkeypatch.setattr(twse, "fetch_sector_turnover", lambda date=None: {"半導體": 88, "航運": 9})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 2_000_000_000},
        "2454": {"sector": "半導體", "name": "聯發科", "shares": 1_000_000_000},
        "2603": {"sector": "航運", "name": "長榮", "shares": 500_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 1000.0, "chg_pct": 2.0},
        "2454": {"name": "聯發科", "close": 500.0, "chg_pct": 1.0},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": -1.0}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors").json()
    assert r["date"] == "2026-06-26"  # 預設用最新大盤日期
    assert [s["name"] for s in r["sectors"]] == ["半導體", "金融保險", "航運"]  # 漲幅大→小
    by = {s["name"]: s for s in r["sectors"]}
    assert by["半導體"]["turnover"] == 88          # 成交值（備援面積）
    assert by["半導體"]["mcap"] == 25000.0         # (20億股×1000 + 10億股×500)/1e8 = 25000 億
    assert by["航運"]["mcap"] == 1000.0
    assert by["金融保險"]["mcap"] is None and by["金融保險"]["turnover"] is None  # 無數據→不進熱力圖


def test_sector_stocks_lists_constituents_by_mcap(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 25_930_000_000},
        "2454": {"sector": "半導體", "name": "聯發科", "shares": 1_600_000_000},
        "6789": {"sector": "半導體", "name": "采鈺", "shares": None},   # 無股數→市值 None 排最後
        "2603": {"sector": "航運", "name": "長榮", "shares": 2_100_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 2505.0, "chg_pct": 3.94},
        "2454": {"name": "聯發科", "close": 1000.0, "chg_pct": -1.0},
        "6789": {"name": "采鈺", "close": 300.0, "chg_pct": 4.29},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": 5.0}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors/半導體/stocks").json()
    assert r["sector"] == "半導體" and r["date"] == "2026-06-26" and r["count"] == 3
    assert [s["code"] for s in r["stocks"]] == ["2330", "2454", "6789"]  # 市值大→小，無股數者最後
    assert r["stocks"][0]["mcap"] == round(25_930_000_000 * 2505.0 / 1e8, 1)  # 億
    assert "2603" not in [s["code"] for s in r["stocks"]]  # 不含他類股


def test_sectors_picks_cross_groups_by_sector(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-26", [
        {"code": "2330", "name": "台積電", "industry": "上市半導體", "sub_industry": "晶圓代工",
         "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80},
        {"code": "3008", "name": "大立光", "industry": "上市光電", "sub_industry": "光學鏡片",
         "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 60},
    ])
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": -3.41},
        {"name": "光電", "close": 1.0, "chg_pct": -7.37},
    ])
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors/picks").json()
    assert r["date"] == "2026-06-26"
    assert [g["sector"] for g in r["groups"]] == ["半導體", "光電"]  # 族群強→弱
    assert r["groups"][0]["stocks"][0]["code"] == "2330"


def test_watchlist_add_track_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    base = {"name": "台積電", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}
    insert_chip_snapshot(c, "2026-06-25", [{"code": "2330.TW", "close": 100, **base}])
    insert_chip_snapshot(c, "2026-06-26", [{"code": "2330.TW", "close": 110, **base}])
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/watchlist", json={"code": "2330"}).json()
    s = r["stocks"][0]
    assert s["code"] == "2330.TW" and s["in_latest"] is True and s["times"] == 2
    assert s["entry_date"] == "2026-06-25" and s["ret_pct"] == 10.0  # 100→110
    assert s["name"] == "台積電"                                     # 股名取自快照
    assert s["chip"]["lan_value"] == 80 and s["chip"]["close"] == 110  # 最新快照籌碼欄位
    assert client.delete("/api/watchlist/2330.TW").json()["stocks"] == []


def test_line_test_endpoint_composes_and_broadcasts(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-123")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 47018.99, "taiex_chg": 893.08,
                            "inst_foreign": 323.76, "margin_balance": 9414925.0})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": 3.21}])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    sent = {}

    def fake_broadcast(token, text):
        sent["token"], sent["text"] = token, text
        return {"ok": True, "status": 200}

    monkeypatch.setattr(line_push, "broadcast_text", fake_broadcast)
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/line/test").json()
    assert r["ok"] is True
    assert sent["token"] == "tok-123"
    assert "加權指數 47,018.99" in sent["text"] and "外資　+323.8" in sent["text"]
    assert "融資 9,414,925張" in sent["text"]        # 測試端點推完整版
    assert "🔥 半導體　　 +3.21%" in sent["text"]    # 新版型：每行一項＋全形空白對齊
    # settings 只回報狀態，不洩漏 token
    s = client.get("/api/settings").json()
    assert s["line_configured"] is True and "tok" not in str(s)


def test_options_sentiment_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import taifex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-29", "taiex": 1.0})
    monkeypatch.setattr(taifex, "fetch_put_call_ratio", lambda: {"date": "2026-06-26", "pc_oi_ratio": 128.74, "pc_vol_ratio": 90.03})
    monkeypatch.setattr(taifex, "fetch_large_traders", lambda: {"date": "2026-06-26", "top10_specific_net": -11967, "top5_specific_net": 1044})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/options-sentiment").json()
    assert r["pcr"]["pc_oi_ratio"] == 128.74
    assert r["large"]["top10_specific_net"] == -11967


def test_inst_ranking_sorts_and_filters_etf(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-29", "taiex": 1.0})
    table = {
        "2330": {"name": "台積電", "foreign": -5000, "trust": 1, "dealer": 1, "total": -4000},
        "2317": {"name": "鴻海", "foreign": 8000, "trust": 1, "dealer": 1, "total": 9000},
        "00677U": {"name": "期富邦VIX", "foreign": 99999, "trust": 1, "dealer": 1, "total": 99999},
    }
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/inst-ranking?who=foreign&top=5").json()
    assert r["date"] == "2026-06-29"
    assert "00677U" not in [x["code"] for x in r["buy"]]  # ETF 濾掉
    assert r["buy"][0]["code"] == "2317"   # 外資買超最大
    assert r["sell"][0]["code"] == "2330"  # 外資賣超最大

    # 金額單位：張 × 收盤價 ÷ 1e5 = 億
    monkeypatch.setattr(twse, "fetch_close_prices", lambda date=None: {"2330": 1000.0, "2317": 100.0})
    rv = client.get("/api/inst-ranking?who=foreign&unit=value&top=5").json()
    assert rv["unit"] == "value"
    buy = {x["code"]: x["net"] for x in rv["buy"]}
    assert buy["2317"] == round(8000 * 100.0 / 1e5, 2)   # 8.0 億
    sell = {x["code"]: x["net"] for x in rv["sell"]}
    assert sell["2330"] == round(-5000 * 1000.0 / 1e5, 2)  # -50.0 億


def test_stock_custody_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import tdcc

    monkeypatch.setattr(tdcc, "fetch_custody_distribution", lambda: {
        "week_date": "2026-06-26",
        "data": {"2330": {"big1000_pct": 70.0, "big400_pct": 73.0, "big_holders": 30}},
    })
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/custody").json()
    assert r["week"] == "2026-06-26"
    assert r["current"]["big1000_pct"] == 70.0
    assert len(r["trend"]) == 1 and r["trend"][0]["big1000_pct"] == 70.0  # 已累積入庫


def test_stock_chips_per_day_series(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-25", "taiex": 1.0})
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 1.0})
    table = {"2330": {"foreign": 5000, "trust": 2000, "dealer": -1000, "total": 6000}}
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/chips?days=10").json()
    assert r["code"] == "2330"
    assert r["dates"] == ["2026-06-25", "2026-06-26"]
    assert r["foreign"] == [5000, 5000] and r["total"] == [6000, 6000]


def test_stock_chips_market_detect_skips_unpublished_day(tmp_path, monkeypatch):
    """最新日 T86 未公布（空表）時，市場判定應回看前一個有資料的日子，而非誤判成上櫃。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-25", "taiex": 1.0})
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 1.0})
    table = {"2330": {"foreign": 5000, "trust": 2000, "dealer": -1000, "total": 6000}}
    monkeypatch.setattr(twse, "fetch_t86",
                        lambda date=None: {} if date.isoformat() == "2026-06-26" else table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/chips?days=10").json()
    assert r["market"] == "twse"                 # 以 6/25（有資料）判定，不被 6/26 空表帶偏
    assert r["foreign"] == [5000, None]          # 6/26 未公布 → None，不以他日填充


def test_kline_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline

    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [100]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/kline?period=1mo")
    assert r.status_code == 200
    assert r.json()["candles"][0] == [10.0, 11.0, 9.0, 12.0]


def test_import_latest_from_folder(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    data_in = tmp_path / "data_in"
    os.makedirs(data_in)
    monkeypatch.setenv("SPR_DATA_DIR", str(data_in))
    content = (
        "符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n"
    ).encode("cp950")
    (data_in / "20260615.csv").write_bytes(content)

    app = create_app()
    client = TestClient(app)
    r = client.post("/api/csv/import-latest").json()
    assert r["count"] == 1
    assert r["picks"][0]["code"] == "2330.TW"
    assert r["file"] == "20260615.csv"


def test_import_all_loads_every_csv(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    data_in = tmp_path / "din"
    os.makedirs(data_in)
    monkeypatch.setenv("SPR_DATA_DIR", str(data_in))
    for day in ("22", "23"):
        content = (f"符合條件商品\n資料日期：2026年  6月 {day}日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n").encode("cp950")
        (data_in / f"202606{day}.csv").write_bytes(content)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/csv/import-all").json()
    assert len(r["imported"]) == 2
    assert set(r["dates"]) == {"2026-06-22", "2026-06-23"}


def test_settings_get_hides_gemini_key(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "secret-xyz")
    monkeypatch.setenv("SPR_SCHEDULE_TIME", "15:30")
    app = create_app()
    client = TestClient(app)
    s = client.get("/api/settings").json()
    assert s["gemini_configured"] is True
    assert s["schedule_time"] == "15:30"
    assert "gemini_api_key" not in s
    assert "secret-xyz" not in json.dumps(s, ensure_ascii=False)  # 金鑰絕不外洩


def test_settings_post_updates_schedule_and_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    # 以外部絕對路徑當資料根（env 為信任來源），其下子目錄才可由設定頁指定
    ext = tmp_path / "mydata"
    (ext / "sub").mkdir(parents=True)
    monkeypatch.setenv("SPR_DATA_DIR", str(ext))
    app = create_app()
    client = TestClient(app)
    # 排程時間可更新；資料夾指到白名單根之下的子目錄可接受
    r = client.post("/api/settings", json={"schedule_time": "09:05", "data_dir": str(ext / "sub")})
    assert r.json()["ok"] is True
    s = client.get("/api/settings").json()
    assert s["schedule_time"] == "09:05"
    assert s["data_dir"] == str(ext / "sub")
    # 根外任意路徑被拒
    assert client.post("/api/settings", json={"data_dir": str(tmp_path.parent)}).json()["ok"] is False


def test_export_returns_xlsx(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/analysis/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # xlsx 為 zip
    # 帶中文細產業篩選：檔名須維持 ASCII，回應仍為合法 xlsx
    r2 = client.get("/api/analysis/export?sub=晶圓")
    assert r2.status_code == 200
    assert r2.content[:2] == b"PK"


def test_summary_refresh_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich import gemini

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])

    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return {"enabled": True, "text": f"v{calls['n']}"}

    monkeypatch.setattr(gemini, "summarize_csv", fake)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/analysis/summary").json()["text"] == "v1"
    assert client.get("/api/analysis/summary").json()["text"] == "v1"  # 走快取
    assert client.get("/api/analysis/summary?refresh=1").json()["text"] == "v2"  # 強制重生
    assert calls["n"] == 2


def test_tx_kline_falls_back_to_proxy_when_no_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline, taifex

    monkeypatch.setattr(taifex, "fetch_tx_history", lambda *a, **k: [])  # 下載不到歷史

    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        return pd.DataFrame({"Open": [1, 2], "High": [3, 4], "Low": [0, 1], "Close": [2, 3], "Volume": [1, 1]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/index/kline?symbol=tx&interval=1d").json()
    assert d.get("proxy") is True          # 無歷史 → 以加權指數近似
    assert len(d["candles"]) == 2


def test_stock_kline_interval_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline

    cap = {}

    def fake_history(self, period="1y", interval="1d"):
        cap["interval"] = interval
        cap["period"] = period
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [100]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/kline?interval=1wk")
    assert r.status_code == 200
    assert cap["interval"] == "1wk"
    assert cap["period"] == "2y"


def test_snapshots_and_daily_by_date(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])
    insert_chip_snapshot(c, "2026-06-16", [{"code": "B", "sub_industry": "水泥", "w55": 1, "big_holder_ratio": 0.2, "rev_yoy": 5, "est_profit": 1, "lan_value": 30}])

    app = create_app()
    client = TestClient(app)
    snaps = client.get("/api/snapshots").json()
    assert snaps["dates"] == ["2026-06-15", "2026-06-16"]

    d = client.get("/api/analysis/daily?date=2026-06-15").json()
    assert d["snap_date"] == "2026-06-15"
    assert d["picks"][0]["code"] == "A"
    assert d["subindustry"][0]["sub_industry"] == "晶圓"


def test_stock_profile_merges_chip_and_valuation(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{
        "code": "2330.TW", "name": "台積電", "custody": 75, "big_holder_ratio": 0.8,
        "w55": 1, "rev_yoy": 30, "trust_3d": 2, "foreign_3d": 3,
    }])
    monkeypatch.setattr(twse, "fetch_valuation", lambda: [{"code": "2330.TW", "pe": 20.0, "yield": 2.0, "pb": 5.0}])

    app = create_app()
    client = TestClient(app)
    p = client.get("/api/stock/2330.TW/profile").json()
    assert p["chip"]["name"] == "台積電"
    assert p["chip"]["big_holder_ratio"] == 0.8
    assert p["valuation"]["pe"] == 20.0
    assert p["valuation"]["yield"] == 2.0


def test_index_kline_tx_from_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_tx_history

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    # tx_history 已有 ≥20 個交易日 → 走真實台指期 OHLC（非 proxy）
    rows = [{"date": f"2026-06-{d:02d}", "open": 45000 + d, "high": 45000 + d + 5,
             "low": 45000 + d - 5, "close": 45000 + d + 2, "volume": d} for d in range(1, 21)]
    upsert_tx_history(c, rows)

    app = create_app()
    client = TestClient(app)
    out = client.get("/api/index/kline?symbol=tx&interval=1d").json()
    assert not out.get("proxy")
    assert len(out["candles"]) == 20
    assert out["candles"][0] == [45001.0, 45003.0, 44996.0, 45006.0]  # 第1天 [open, close, low, high]


# ========== P0 資安：認證 / 上傳限制 / 路徑白名單 ==========

def test_check_basic_credentials():
    import base64
    from stocks_power_rich.main import _check_basic

    good = "Basic " + base64.b64encode(b"kevin:s3cret").decode()
    assert _check_basic(good, "kevin", "s3cret") is True
    assert _check_basic(good, "kevin", "wrong") is False
    assert _check_basic(good, "other", "s3cret") is False
    assert _check_basic("Bearer xxx", "kevin", "s3cret") is False   # 非 Basic
    assert _check_basic("", "kevin", "s3cret") is False             # 無標頭
    assert _check_basic("Basic !!not-base64!!", "kevin", "s3cret") is False


def test_basic_auth_gates_all_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("SPR_BASIC_USER", "kevin")
    monkeypatch.setenv("SPR_BASIC_PASS", "s3cret")
    app = create_app()
    client = TestClient(app)
    # 無帳密 → 401 且帶 WWW-Authenticate（含 API 與靜態頁）
    r = client.get("/api/dashboard")
    assert r.status_code == 401 and "Basic" in r.headers.get("WWW-Authenticate", "")
    assert client.post("/api/line/test").status_code == 401
    # 錯帳密 → 401
    assert client.get("/api/dashboard", auth=("kevin", "nope")).status_code == 401
    # 正確帳密 → 放行
    assert client.get("/api/dashboard", auth=("kevin", "s3cret")).status_code == 200


def test_no_auth_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.delenv("SPR_BASIC_USER", raising=False)
    monkeypatch.delenv("SPR_BASIC_PASS", raising=False)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/dashboard").status_code == 200   # 未設定即不啟用（本機開發無感）


def test_settings_rejects_data_dir_outside_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)
    bad = "/etc" if os.name != "nt" else "C:\Windows"
    r = client.post("/api/settings", json={"data_dir": bad})
    assert r.json().get("data_dir") != bad          # 未被採用
    assert client.get("/api/settings").json()["data_dir"] != bad
    # 合法子目錄可被接受
    sub = str(tmp_path / "Date")
    os.makedirs(sub, exist_ok=True)
    r2 = client.post("/api/settings", json={"data_dir": "Date"})
    assert r2.json()["ok"] is True


def test_upload_rejects_bad_extension_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)
    # 非白名單副檔名 → 拒絕（不寫檔、不匯入）
    bad = client.post("/api/csv/upload", files={"file": ("evil.exe", b"MZ...", "application/octet-stream")}).json()
    assert bad["count"] == 0 and bad.get("error")
    # 超過大小上限 → 拒絕
    big = client.post("/api/csv/upload",
                      files={"file": ("big.csv", b"x" * (10 * 1024 * 1024 + 5), "text/csv")}).json()
    assert big["count"] == 0 and "10" in big.get("error", "")


def test_security_headers_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    h = client.get("/api/dashboard").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in h["Content-Security-Policy"]   # ECharts CDN 放行


def test_db_backup_endpoint(tmp_path, monkeypatch):
    db = str(tmp_path / "spr.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db)
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    c = get_connection(db)
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-01", "taiex": 1.0})
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/db/backup").json()
    assert r["ok"] is True and r["file"].startswith("spr-") and r["file"].endswith(".sqlite")
    assert r["file"] in r["backups"]
    assert os.path.exists(os.path.join(str(tmp_path), "backup", r["file"]))


def test_index_kline_falls_back_to_multimonth_twse(tmp_path, monkeypatch):
    """雲端 yfinance 被擋（回空）時，加權 K 線改用證交所多月 OHLC，而非只有當月幾天。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import kline, twse
    monkeypatch.setattr(kline, "fetch_index_kline",
                        lambda symbol, interval="1d": {"symbol": symbol, "candles": [], "dates": [], "volumes": []})
    sample = [{"date": f"2026-0{m}-{d:02d}", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0}
              for m in (5, 6, 7) for d in (1, 8, 15, 22)]   # 3 個月共 12 天
    calls = {"n": 0}

    def fake_hist(months=12):
        calls["n"] += 1
        return sample

    monkeypatch.setattr(twse, "fetch_index_ohlc_history", fake_hist)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/index/kline?symbol=taiex&interval=1d").json()
    assert r["source"] == "twse" and len(r["candles"]) == 12   # 多月歷史
    # 再打一次應命中快取，不重複逐月抓取
    client.get("/api/index/kline?symbol=taiex&interval=1d")
    assert calls["n"] == 1


def test_index_movers_point_contribution(tmp_path, monkeypatch):
    """權值股貢獻點數：依市值加權算各股對大盤的點數貢獻，正規化到實際指數漲跌。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-03", "taiex": 1000.0, "taiex_chg": 20.0})  # 昨指數 980
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 100},
        "2317": {"sector": "電子零組件", "name": "鴻海", "shares": 50}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 110.0, "chg_pct": 10.0},   # 昨100, 市值+1000
        "2317": {"name": "鴻海", "close": 90.0, "chg_pct": -10.0}})    # 昨100, 市值-500
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/index-movers?top=10").json()
    assert r["index"] == 1000.0 and r["index_chg"] == 20.0
    mv = {m["code"]: m for m in r["movers"]}
    assert [m["code"] for m in r["movers"]] == ["2330", "2317"]     # 依貢獻絕對值排序
    assert mv["2330"]["contribution"] == 40.0 and mv["2317"]["contribution"] == -20.0
    assert round(sum(m["contribution"] for m in r["movers"]), 2) == 20.0  # 合計＝實際指數漲跌


def test_ohlc_backfill_stores_trading_days(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import twse
    monkeypatch.setattr(twse, "fetch_stock_ohlc",
                        lambda date=None: {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/ohlc/backfill?days=60&max_fetch=5").json()
    assert r["added"] == 5 and r["stored_days"] == 5 and r["done"] is False


def test_cup_handle_screen_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import twse
    from tests.test_patterns import _make_cup_handle

    from stocks_power_rich.db import insert_chip_snapshot
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        ds = (base + timedelta(days=i)).isoformat()
        bulk_upsert_ohlc(c, ds, {"2330": {"open": cl, "high": h, "low": l, "close": cl}})
    # 2330 同時進「籌碼/基本選股」榜（W55∧大戶∧營收∧EPS），供交集標記
    insert_chip_snapshot(c, "2025-03-01", [{"code": "2330.TW", "name": "台積電", "w55": 1,
                         "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}])
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/patterns/cup-handle").json()
    assert r["count"] == 1
    m = r["stocks"][0]
    assert m["code"] == "2330" and m["name"] == "台積電"
    assert m["right_price"] == 90.0 and m["resistance"] == 90.0
    assert m["left_date"] and m["right_date"]        # 畫線用日期
    assert m["in_picks"] is True and r["has_picks"] is True and r["picks_count"] == 1  # 交集標記
    # 個股 OHLC 端點供畫線
    o = client.get("/api/stock/2330/ohlc?bars=400").json()
    assert len(o["candles"]) == 400 and o["candles"][0][0] is not None


def test_settings_nav_order_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/settings").json()["nav_order"] is None       # 預設未設
    order = ["overview", "cup", "picks", "settings"]
    client.post("/api/settings", json={"nav_order": order})
    assert client.get("/api/settings").json()["nav_order"] == order
    # 非字母 slug 被濾掉（防注入），仍存有效項
    client.post("/api/settings", json={"nav_order": ["overview", "../evil", "cup"]})
    assert client.get("/api/settings").json()["nav_order"] == ["overview", "cup"]
