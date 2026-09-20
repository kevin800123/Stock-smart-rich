"""集保股數／人均數（docs/superpowers/specs/2026-09-20-stock-combo-chart-design.md）。"""
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db
from stocks_power_rich.sources import tdcc


CSV_HEAD = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
# 級 1（散戶）、級 12～15（400 張↑，其中 15 是千張大戶）、級 17（合計，兩來源編號不同，必須排除）
CSV_BODY = (
    "20260918,2330,1,1000,500000,5.00\n"
    "20260918,2330,12,20,300000,3.00\n"
    "20260918,2330,13,10,400000,4.00\n"
    "20260918,2330,14,5,600000,6.00\n"
    "20260918,2330,15,3,8200000,82.00\n"
    "20260918,2330,17,1038,10000000,100.00\n"
)


def test_parse_custody_distribution_sums_shares_of_levels_1_to_15():
    d = tdcc.parse_custody_distribution(CSV_HEAD + CSV_BODY)
    rec = d["data"]["2330"]
    assert d["week_date"] == "2026-09-18"
    assert rec["total_holders"] == 1038            # 1000+20+10+5+3，不含第 17 級合計列
    assert rec["total_shares"] == 10000000         # 同樣不含合計列（否則會是兩倍）
    assert rec["big1000_pct"] == 82.0 and rec["big400_pct"] == 95.0


def test_parse_custody_ownership_html_also_returns_total_shares():
    rows = [("1", "1-999", "1,000", "500,000", "5.00"),
            ("15", "1,000以上", "3", "8,200,000", "82.00"),
            ("16", "合計", "1,003", "8,700,000", "87.00")]   # 智能網的合計列是第 16 級
    html = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    rec = tdcc.parse_custody_ownership_html(html)
    assert rec["total_holders"] == 1003
    assert rec["total_shares"] == 8700000          # 只加 1 與 15 兩級，剛好等於合計，但不是讀合計列
    assert rec["big1000_pct"] == 82.0


def test_aggregate_levels_without_shares_degrades_to_zero():
    """股數欄解析不出來（來源改版）時不可整筆炸掉，總股數算 0、其餘照常。"""
    rec = tdcc._aggregate_levels([("1", 100, None, 1.0), ("15", 2, None, 80.0)])
    assert rec["total_shares"] == 0 and rec["total_holders"] == 102 and rec["big1000_pct"] == 80.0


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


def test_upsert_custody_stores_holders_and_shares_and_trend_returns_avg(conn):
    db.upsert_custody(conn, "2026-09-11", "2330", {"big1000_pct": 77.0, "big400_pct": 82.0,
                                                   "big_holders": 200, "total_holders": 1000,
                                                   "total_shares": 9000000})
    db.upsert_custody(conn, "2026-09-18", "2330", {"big1000_pct": 77.9, "big400_pct": 83.0,
                                                   "big_holders": 205, "total_holders": 1250,
                                                   "total_shares": 10000000})
    trend = db.get_custody_trend(conn, "2330")
    assert [t["week"] for t in trend] == ["2026-09-11", "2026-09-18"]
    assert trend[0]["total_holders"] == 1000 and trend[0]["total_shares"] == 9000000
    assert trend[0]["avg_shares"] == 9000 and trend[1]["avg_shares"] == 8000   # 人均數下降＝籌碼分散


def test_custody_trend_avg_is_none_when_inputs_are_missing(conn):
    """算不出來就回 None，不要拿 0 或舊值頂替（同全站『算不出回 None』的慣例）。"""
    db.upsert_custody(conn, "2026-09-04", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10})          # 舊資料：沒有人數與股數
    db.upsert_custody(conn, "2026-09-11", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10, "total_holders": 0,
                                                   "total_shares": 500})        # 人數 0 不可除
    assert [t["avg_shares"] for t in db.get_custody_trend(conn, "1101")] == [None, None]


def test_custody_total_shares_column_migrates_on_an_old_table(tmp_path, monkeypatch):
    """既有部署的 custody_dist 沒有 total_shares 欄，init_db 要能就地補上且不動既有資料。"""
    path = str(tmp_path / "old.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    c.execute("CREATE TABLE custody_dist (week TEXT, code TEXT, big1000_pct REAL, "
              "big400_pct REAL, big_holders REAL, PRIMARY KEY(week, code))")
    c.execute("INSERT INTO custody_dist VALUES ('2026-09-11','2330',77.0,82.0,200)")
    c.commit()
    db.init_db(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(custody_dist)")}
    assert "total_shares" in cols and "total_holders" in cols
    assert db.get_custody_trend(c, "2330")[0]["big1000_pct"] == 77.0   # 既有列還在
