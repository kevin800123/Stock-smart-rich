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
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors").json()
    assert r["date"] == "2026-06-26"  # 預設用最新大盤日期
    assert [s["name"] for s in r["sectors"]] == ["半導體", "金融保險", "航運"]  # 漲幅大→小


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
    app = create_app()
    client = TestClient(app)
    assert client.post("/api/settings", json={"schedule_time": "09:05", "data_dir": "D:/mydata"}).status_code == 200
    s = client.get("/api/settings").json()
    assert s["schedule_time"] == "09:05"
    assert s["data_dir"] == "D:/mydata"


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
