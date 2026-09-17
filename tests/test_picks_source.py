"""選股名單來源：族群交叉選股與杯柄 ⭐ 優先用自算選股快取，CSV 較新才退回 CSV。

**為什麼要有這份測試**：原本的四條測試（sectors picks／杯柄端點／LINE 杯柄段／盤中只盯交集）
全部只種 CSV，而正式站自算快取每個交易日都在更新——實際走的幾乎一定是快取那條。只跑舊測試
等於只驗了正式站幾乎不走的退路。所以這裡每條都種一檔「只在快取、不在 CSV」的股（或反過來），
斷言看得出名單真的是從哪一份來的。
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from stocks_power_rich import db, line_push, selfcheck
from stocks_power_rich.api import helpers
from stocks_power_rich.main import create_app

# 過得了 screen_pass 全部 7 條件、預設門檻（木率>50、木質>9）的一組自算值
PASS = {"rev_yoy": 10.0, "w55": 1, "big_holder_ratio": 1.0, "holder_drop_ratio": -1.0,
        "trust_3d": 0, "foreign_3d": 0, "lan_score": 12, "est_profit": 2.0, "mu_score": 15,
        "mu_value": 99.0, "margin_3d": 0}
FAIL = {**PASS, "w55": 0}
CSV_PICK = {"w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


def _cache(c, day, rows):
    selfcheck.save_precomputed(c, {
        "date": day, "heatmap": [], "coverage": {},
        "rows": [{"code": code, "name": name, "sector": "細分類", "vals": vals}
                 for code, name, vals in rows]})


def _csv(c, day, codes):
    db.insert_chip_snapshot(c, day, [{"code": f"{code}.TW", "name": code, **CSV_PICK} for code in codes])


# ---------- 判定規則 ----------

def test_no_list_at_all(conn):
    p = helpers.active_picks(conn)
    assert p["source"] is None and p["codes"] == set() and p["date"] is None


def test_csv_only_falls_back_to_csv_with_bare_codes(conn):
    _csv(conn, "2026-09-10", ["2330"])
    p = helpers.active_picks(conn)
    assert (p["source"], p["label"], p["date"]) == ("csv", "籌碼/基本", "2026-09-10")
    assert p["codes"] == {"2330"}                     # .TW 後綴一律去掉


def test_cache_wins_on_same_day_and_when_newer(conn):
    _csv(conn, "2026-09-10", ["2330"])
    _cache(conn, "2026-09-10", [("9999", "只在快取", PASS)])
    p = helpers.active_picks(conn)
    assert (p["source"], p["label"]) == ("self_screen", "自算籌碼/基本")
    assert p["codes"] == {"9999"}                     # 不是 CSV 的 2330：真的讀了快取
    _cache(conn, "2026-09-11", [("9999", "只在快取", PASS)])
    assert helpers.active_picks(conn)["date"] == "2026-09-11"


def test_newer_csv_beats_stale_cache(conn):
    """快取可能連續好幾天沒更新（data_not_ready／partial_universe），不可蓋掉較新的 CSV。"""
    _cache(conn, "2026-09-08", [("9999", "只在快取", PASS)])
    _csv(conn, "2026-09-10", ["2330"])
    p = helpers.active_picks(conn)
    assert (p["source"], p["date"], p["codes"]) == ("csv", "2026-09-10", {"2330"})


def test_cache_with_zero_picks_is_still_the_list(conn):
    """名單是空的不代表沒有名單：自算今天沒選到股，不可偷偷改用別份。"""
    _csv(conn, "2026-09-10", ["2330"])
    _cache(conn, "2026-09-10", [("9999", "沒過門檻", FAIL)])
    p = helpers.active_picks(conn)
    assert p["source"] == "self_screen" and p["codes"] == set()


def test_thresholds_come_from_settings(conn):
    _cache(conn, "2026-09-10", [("9999", "木率99", PASS)])
    db.set_setting(conn, "screen_mu_value_min", "120")
    assert helpers.active_picks(conn)["codes"] == set()


def test_broken_cache_is_logged_and_falls_back_to_csv(conn, caplog):
    _csv(conn, "2026-09-10", ["2330"])
    selfcheck.save_precomputed(conn, {"date": "2026-09-10", "heatmap": [], "coverage": {},
                                      "rows": [{"code": "9999", "name": "壞列"}]})   # 沒有 vals
    p = helpers.active_picks(conn)
    assert p["source"] == "csv" and p["codes"] == {"2330"}
    assert any("active_picks" in r.getMessage() for r in caplog.records)


def test_official_agri_tech_sector_joins_other_like_csv():
    from stocks_power_rich import analysis
    assert analysis.industry_to_sector("農業科技") == "其他"       # 官方寫法（無「業」）
    assert analysis.industry_to_sector("上櫃農業科技業") == "其他"  # CSV 寫法照舊
    assert analysis.industry_to_sector("文化創意") == "其他"


# ---------- 族群交叉選股 ----------

def test_sectors_picks_groups_self_screen_by_official_sector(conn, monkeypatch):
    from stocks_power_rich.api import market
    from stocks_power_rich.sources import twse
    _csv(conn, "2026-09-09", ["1101"])                # 較舊的 CSV：不該被用到
    _cache(conn, "2026-09-10", [("2330", "台積電", PASS), ("6488", "環球晶", PASS),
                                ("7777", "農科股", PASS), ("9999", "查無類股", PASS),
                                ("3008", "沒入選", FAIL)])
    monkeypatch.setattr(market, "_industry_map", lambda c: {"2330": {"sector": "半導體", "name": "台積電"},
                                                            "3008": {"sector": "光電", "name": "大立光"}})
    monkeypatch.setattr(market, "_otc_industry", lambda c: {"6488": {"sector": "半導體", "name": "環球晶"},
                                                            "7777": {"sector": "農業科技", "name": "農科股"}})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": 1.5}, {"name": "其他", "close": 1.0, "chg_pct": -0.5}])
    r = TestClient(create_app()).get("/api/sectors/picks").json()
    assert (r["source"], r["label"], r["date"]) == ("self_screen", "自算籌碼/基本", "2026-09-10")
    assert r["total"] == 4 and r["unclassified"] == 1           # 9999 查不到類股：攤開，不靜默丟掉
    assert [g["sector"] for g in r["groups"]] == ["半導體", "其他"]
    assert {s["code"] for s in r["groups"][0]["stocks"]} == {"2330", "6488"}   # 上市上櫃同類股併一組
    assert r["groups"][0]["chg_pct"] == 1.5
    assert [s["code"] for s in r["groups"][1]["stocks"]] == ["7777"]


def test_sectors_picks_date_param_still_reads_that_csv_day(conn, monkeypatch):
    from stocks_power_rich.sources import twse
    db.insert_chip_snapshot(conn, "2026-09-09", [{"code": "1101.TW", "name": "台泥",
                                                  "industry": "上市水泥", **CSV_PICK}])
    _cache(conn, "2026-09-10", [("2330", "台積電", PASS)])
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    r = TestClient(create_app()).get("/api/sectors/picks?date=2026-09-09").json()
    assert (r["source"], r["date"]) == ("csv", "2026-09-09")
    assert r["groups"][0]["stocks"][0]["code"] == "1101.TW"


def test_sectors_picks_date_param_reads_csv_even_on_the_cache_day(conn, monkeypatch):
    """明講要看某天的 CSV，就不該因為同一天也有自算快取而被換掉（審查抓到的）。"""
    from stocks_power_rich.sources import twse
    db.insert_chip_snapshot(conn, "2026-09-10", [{"code": "1101.TW", "name": "台泥",
                                                  "industry": "上市水泥", **CSV_PICK}])
    _cache(conn, "2026-09-10", [("2330", "台積電", PASS)])
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    client = TestClient(create_app())
    monkeypatch.setattr("stocks_power_rich.api.market._industry_map", lambda c: {})
    monkeypatch.setattr("stocks_power_rich.api.market._otc_industry", lambda c: {})
    assert client.get("/api/sectors/picks").json()["source"] == "self_screen"
    r = client.get("/api/sectors/picks?date=2026-09-10").json()
    assert (r["source"], r["date"]) == ("csv", "2026-09-10")


def test_sectors_picks_without_any_list(conn):
    r = TestClient(create_app()).get("/api/sectors/picks").json()
    assert r["source"] is None and r["groups"] == []


# ---------- 杯柄 ⭐ ----------

def _cup_ohlc(c, codes):
    from tests.test_patterns import _make_cup_handle
    highs, lows, closes = _make_cup_handle()
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        ds = (base + timedelta(days=i)).isoformat()
        db.bulk_upsert_ohlc(c, ds, {code: {"open": cl, "high": h, "low": l, "close": cl} for code in codes})
    return ds, (base + timedelta(days=i - 1)).isoformat()


def _cup_names(monkeypatch):
    from stocks_power_rich.sources import twse, tpex
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 1},
        "8069": {"sector": "光電", "name": "元太", "shares": 1}})
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})   # 沒樁會真的去櫃買抓名稱（測試照過、看不出來）


def test_cup_endpoint_marks_picks_from_self_screen_cache(conn, monkeypatch):
    last_ds, _ = _cup_ohlc(conn, ["2330", "8069"])
    _csv(conn, "2025-03-01", ["2330"])                # 舊 CSV 裡有 2330
    _cache(conn, last_ds, [("8069", "元太", PASS), ("2330", "台積電", FAIL)])
    _cup_names(monkeypatch)
    r = TestClient(create_app()).get("/api/patterns/cup-handle").json()
    marks = {m["code"]: m["in_picks"] for m in r["stocks"]}
    assert marks == {"2330": False, "8069": True}     # 照快取、不照 CSV
    assert (r["picks_source"], r["picks_label"], r["picks_date"]) == ("self_screen", "自算籌碼/基本", last_ds)
    assert r["has_picks"] is True and r["picks_count"] == 1 and r["picks_total"] == 1


def test_empty_self_screen_list_still_counts_as_a_list(conn, monkeypatch):
    """自算今天 0 檔入選：杯柄頁仍說有名單（0 檔）、LINE 杯柄段交集後就是 0 檔，不退回全杯柄。"""
    last_ds, prev_ds = _cup_ohlc(conn, ["2330", "8069"])
    db.set_ai_cache(conn, f"cupsig:{prev_ds}", [])
    _csv(conn, "2025-03-01", ["2330"])
    _cache(conn, last_ds, [("2330", "台積電", FAIL)])
    _cup_names(monkeypatch)
    r = TestClient(create_app()).get("/api/patterns/cup-handle").json()
    assert r["has_picks"] is True and r["picks_total"] == 0 and r["picks_count"] == 0
    info = helpers._cup_push_info(conn)
    assert info["picks"] is True and info["count"] == 0 and info["new"] == []


def test_line_cup_section_uses_cache_label_and_loads_list_once(conn, monkeypatch):
    last_ds, prev_ds = _cup_ohlc(conn, ["2330", "8069"])
    db.set_ai_cache(conn, f"cupsig:{prev_ds}", [])
    _cache(conn, last_ds, [("8069", "元太", PASS)])
    _cup_names(monkeypatch)
    calls = []
    real = helpers.active_picks
    monkeypatch.setattr(helpers, "active_picks", lambda c: calls.append(1) or real(c))
    info = helpers._cup_push_info(conn)
    assert len(calls) == 1                            # 一次推播只載入一次名單
    assert info["picks"] is True and info["count"] == 1 and info["picks_label"] == "自算籌碼/基本"
    assert [s["name"] for s in info["new"]] == ["元太"]
    txt = line_push.compose_daily_brief({"date": last_ds}, [], [], cup=info)
    assert "【杯柄型態&自算籌碼/基本】符合 1 檔" in txt and "台積電" not in txt


def _intraday_setup(conn, monkeypatch, sent):
    from stocks_power_rich.sources import mis, tpex
    db.bulk_upsert_ohlc(conn, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    db.set_ai_cache(conn, "cupsig:2026-07-04", [
        {"code": "8069", "name": "元太", "resistance": 212.0},
        {"code": "2812", "name": "台中銀", "resistance": 19.8}])
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {"8069": "元太"})
    monkeypatch.setattr(mis, "fetch_mis_rank",
                        lambda tokens: {"8069": {"price": 213.5}, "2812": {"price": 19.85}})
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(str(msgs)) or {"ok": True})


def test_intraday_star_and_filter_follow_self_screen_cache(conn, monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    sent = []
    _intraday_setup(conn, monkeypatch, sent)
    _csv(conn, "2026-07-03", ["8069"])                # 較舊的 CSV 只有 8069
    _cache(conn, "2026-07-04", [("2812", "台中銀", PASS)])
    client = TestClient(create_app())
    assert client.post("/api/intraday/test?push=1").json()["hits"] == []
    r = client.post("/api/intraday/test?push=1").json()
    assert r["picks_source"] == "self_screen"
    assert "⭐台中銀" in sent[0] and "⭐元太" not in sent[0]
    assert "⭐=同時符合自算籌碼/基本選股" in sent[0]
    client.post("/api/settings", json={"intraday_picks_only": True})
    conn.execute("DELETE FROM ai_cache WHERE cache_key LIKE 'cupalerted:%' OR cache_key LIKE 'cuppending:%'")
    conn.commit()
    assert client.post("/api/intraday/test").json()["checked"] == 1
    assert [h["code"] for h in client.post("/api/intraday/test").json()["hits"]] == ["2812"]


def test_intraday_filtered_to_nothing_says_why(conn, monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    _intraday_setup(conn, monkeypatch, [])
    _cache(conn, "2026-07-04", [("5555", "別檔", PASS)])
    db.set_setting(conn, "intraday_picks_only", "1")
    r = TestClient(create_app()).post("/api/intraday/test").json()
    assert r["checked"] == 0 and r["picks_source"] == "self_screen"
    assert "自算籌碼/基本" in r["note"] and "2026-07-04" in r["note"]


def test_intraday_picks_only_with_empty_list_alerts_nothing_and_says_why(conn, monkeypatch):
    """審查實跑抓到的：自算今天 0 檔入選時，舊寫法（看名單空不空）會跳過「只警示入選股」、
    改成全部杯柄股都發 LINE。開了這個設定，名單又是空的，就該一檔都不發並說明原因。"""
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    sent = []
    _intraday_setup(conn, monkeypatch, sent)
    _csv(conn, "2026-07-04", ["2812"])
    _cache(conn, "2026-07-04", [("2812", "台中銀", FAIL)])
    db.set_setting(conn, "intraday_picks_only", "1")
    client = TestClient(create_app())
    for _ in range(2):                                # 兩輪確認也不能讓它溜過去
        r = client.post("/api/intraday/test?push=1").json()
        assert r["checked"] == 0 and r["hits"] == []
    assert sent == [] and "自算籌碼/基本" in r["note"]


def test_intraday_does_not_load_list_when_nothing_pending(conn, monkeypatch):
    db.bulk_upsert_ohlc(conn, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    monkeypatch.setattr(helpers, "active_picks", lambda c: pytest.fail("沒有待監控股時不該載入名單"))
    assert helpers._intraday_scan(conn, push=False)["checked"] == 0
