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


def test_index_kline_tx_from_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-16", "tx_open": 45600, "tx_high": 45900, "tx_low": 45550, "tx_price": 45772})
    upsert_market_daily(c, {"date": "2026-06-17", "tx_open": 45772, "tx_high": 45850, "tx_low": 45700, "tx_price": 45809})

    app = create_app()
    client = TestClient(app)
    out = client.get("/api/index/kline?symbol=tx&interval=1d").json()
    assert out["dates"] == ["2026-06-16", "2026-06-17"]
    assert out["candles"][0] == [45600.0, 45772.0, 45550.0, 45900.0]
