import pytest

from stocks_power_rich.db import (
    get_connection,
    init_db,
    upsert_market_daily,
    insert_chip_snapshot,
    get_snapshot_dates,
    get_snapshot,
    get_ai_cache,
    set_ai_cache,
    upsert_tx_history,
    get_tx_history,
    weekly_amounts,
)


def _seed_amounts(conn, rows):
    """rows＝[(code, date, amount_twd)]，只寫成交額（high/low 留空、不影響 weekly_amounts）。"""
    for code, d, amt in rows:
        conn.execute("INSERT INTO stock_ohlc(code,date,close,amount_twd) VALUES(?,?,?,?)",
                     (code, d, 100.0, amt))
    conn.commit()


# 2026-08-17 是週一（ISO 週起點）；08-10 是前一週的週一 → 兩段各自成一個完整 ISO 週、且相鄰。
def test_weekly_amounts_sums_this_and_prev_week(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed_amounts(conn, [
        ("2330", "2026-08-17", 100), ("2330", "2026-08-18", 200), ("2330", "2026-08-19", 300),
        ("2330", "2026-08-10", 100), ("2330", "2026-08-11", 100), ("2330", "2026-08-12", 100),
        ("1101", "2026-08-17", 50),   # 只有本週、上週無 → prev 0
    ])
    wk = weekly_amounts(conn, "2026-08-19")
    assert wk["2330"]["this"] == 600
    assert wk["2330"]["prev"] == 300
    assert wk["2330"]["days"] == 3
    assert wk["1101"]["this"] == 50
    assert wk["1101"]["prev"] == 0


def test_weekly_amounts_uses_equal_trading_days_not_full_week(tmp_path):
    """WoW 要同期比：本週只到週二(2 個交易日)時，上週也只取前 2 個交易日，
    不是拿半週去比完整上週（否則週初 WoW 恆為負、誤導）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed_amounts(conn, [
        ("2330", "2026-08-17", 100), ("2330", "2026-08-18", 200),   # 本週只到週二
        ("2330", "2026-08-10", 100), ("2330", "2026-08-11", 100), ("2330", "2026-08-12", 100),
    ])
    wk = weekly_amounts(conn, "2026-08-18")
    assert wk["2330"]["days"] == 2
    assert wk["2330"]["this"] == 300     # 100+200
    assert wk["2330"]["prev"] == 200     # 上週前 2 個交易日 100+100（不含 08-12 的 100）


def test_weekly_amounts_empty_when_no_amount_data(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    assert weekly_amounts(conn, "2026-08-19") == {}


def test_tx_history_roundtrip(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_tx_history(conn, [{"date": "2026-06-16", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9}])
    upsert_tx_history(conn, [{"date": "2026-06-16", "open": 1, "high": 3, "low": 0.5, "close": 2.0, "volume": 9}])  # 覆蓋
    got = get_tx_history(conn)
    assert len(got) == 1 and got[0]["close"] == 2.0


def test_ai_cache_roundtrip(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    assert get_ai_cache(conn, "market:2026-06-17") is None
    set_ai_cache(conn, "market:2026-06-17", {"enabled": True, "text": "盤勢偏多"})
    got = get_ai_cache(conn, "market:2026-06-17")
    assert got == {"enabled": True, "text": "盤勢偏多"}


def test_market_daily_upsert(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    upsert_market_daily(conn, {"date": "2026-06-15", "taiex": 23000.0, "sox": 5000.0})
    upsert_market_daily(conn, {"date": "2026-06-15", "taiex": 23100.0})  # 同日覆蓋
    row = conn.execute(
        "select taiex, sox from market_daily where date=?", ("2026-06-15",)
    ).fetchone()
    assert row[0] == 23100.0 and row[1] == 5000.0


def test_chip_snapshot_roundtrip(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    rows = [
        {
            "code": "2330.TW",
            "name": "台積電",
            "big_holder_ratio": 0.5,
            "holder_drop_ratio": -0.2,
            "industry": "上市半導體",
            "raw_json": "{}",
        }
    ]
    insert_chip_snapshot(conn, "2026-06-15", rows)
    assert get_snapshot_dates(conn) == ["2026-06-15"]
    got = get_snapshot(conn, "2026-06-15")
    assert got[0]["code"] == "2330.TW" and got[0]["big_holder_ratio"] == 0.5


def test_backup_db_creates_rotates_and_is_readable(tmp_path):
    import glob
    import os
    from stocks_power_rich.db import backup_db

    db = str(tmp_path / "spr.sqlite")
    c = get_connection(db)
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-01", "taiex": 47000.0})

    # 連續 9 天備份，輪替後只保留最近 7 份
    days = [f"202601{d:02d}" for d in range(1, 10)]
    for stamp in days:
        p = backup_db(db, keep=7, stamp=stamp)
        assert p and os.path.exists(p)
    files = sorted(glob.glob(str(tmp_path / "backup" / "spr-*.sqlite")))
    assert len(files) == 7                                   # 只留 7 份
    assert files[0].endswith("spr-20260103.sqlite")         # 最舊兩份被刪
    assert files[-1].endswith("spr-20260109.sqlite")

    # 備份檔可獨立開啟且含原資料
    bc = get_connection(files[-1])
    assert bc.execute("SELECT taiex FROM market_daily").fetchone()[0] == 47000.0


def test_backup_db_missing_source_returns_none(tmp_path):
    from stocks_power_rich.db import backup_db
    assert backup_db(str(tmp_path / "nope.sqlite")) is None


def test_upsert_market_daily_key_only_row_is_a_noop_not_a_crash(tmp_path):
    """只帶 date 的列：DO UPDATE SET 會是空字串 → 舊版丟 sqlite3.OperationalError。

    正確語意是「沒有這天就建、有了就別動」——尤其不可把既有欄位洗成 NULL，
    因為 _refresh_recent/_backfill_* 都是先確保列存在、再逐步補欄位。
    """
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)

    upsert_market_daily(conn, {"date": "2026-07-24"})              # 建列
    upsert_market_daily(conn, {"date": "2026-07-24"})              # 重複呼叫不得炸
    assert conn.execute("SELECT COUNT(*) FROM market_daily").fetchone()[0] == 1

    upsert_market_daily(conn, {"date": "2026-07-24", "taiex": 23000.0})
    upsert_market_daily(conn, {"date": "2026-07-24"})              # 只帶 key → 不得洗掉 taiex
    assert conn.execute(
        "SELECT taiex FROM market_daily WHERE date='2026-07-24'").fetchone()[0] == 23000.0


def test_upsert_market_daily_without_date_fails_loudly(tmp_path):
    """market_daily 以 date 為鍵，沒有 date 是呼叫端的錯——要給看得懂的訊息，
    而不是 sqlite 的 'incomplete input'。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    with pytest.raises(ValueError, match="date"):
        upsert_market_daily(conn, {"taiex": 23000.0})


def test_insert_chip_snapshot_key_only_row_is_a_noop_not_a_crash(tmp_path):
    """同一個坑的另一半：只帶 code 的籌碼列，DO UPDATE SET 同樣會是空字串。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    insert_chip_snapshot(conn, "2026-07-24", [{"code": "2330"}])
    insert_chip_snapshot(conn, "2026-07-24", [{"code": "2330", "name": "台積電"}])
    insert_chip_snapshot(conn, "2026-07-24", [{"code": "2330"}])   # 只帶 key → 不得洗掉 name
    rows = get_snapshot(conn, "2026-07-24")
    assert len(rows) == 1 and rows[0]["name"] == "台積電"


def test_stock_flow_schema_migrates_old_ohlc_and_preserves_partial_updates(tmp_path):
    from stocks_power_rich.db import bulk_upsert_ohlc, bulk_upsert_stock_flow

    conn = get_connection(str(tmp_path / "t.sqlite"))
    conn.execute("CREATE TABLE stock_ohlc (date TEXT, code TEXT, open REAL, high REAL, "
                 "low REAL, close REAL, PRIMARY KEY(date, code))")
    init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_ohlc)")}
    assert {"volume_lots", "amount_twd"} <= columns

    bulk_upsert_ohlc(conn, "2026-08-07", {
        "2330": {"open": 10, "high": 11, "low": 9, "close": 10.5,
                 "volume_lots": 123, "amount_twd": 456},
    })
    bulk_upsert_ohlc(conn, "2026-08-07", {"2330": {"close": 10.8}})
    row = conn.execute("SELECT open, close, volume_lots, amount_twd FROM stock_ohlc").fetchone()
    assert tuple(row) == (10.0, 10.8, 123.0, 456.0)

    bulk_upsert_stock_flow(conn, "2026-08-07", "TWSE", {
        "2330": {"name": "台積電", "foreign_lots": 10, "trust_lots": 2,
                 "dealer_lots": -1, "institutional_total_lots": 11},
    })
    bulk_upsert_stock_flow(conn, "2026-08-07", "TWSE", {
        "2330": {"margin_balance_lots": 0, "short_balance_lots": 3},
    })
    row = conn.execute(
        "SELECT name, foreign_lots, margin_balance_lots, short_balance_lots "
        "FROM stock_flow_daily").fetchone()
    assert tuple(row) == ("台積電", 10.0, 0.0, 3.0)


def test_stock_source_coverage_is_independent_by_market(tmp_path):
    from stocks_power_rich.db import set_stock_source_coverage

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    set_stock_source_coverage(conn, "2026-08-07", "TWSE", "quotes", "complete", 100)
    set_stock_source_coverage(conn, "2026-08-07", "TPEx", "quotes", "failed", 0, "not ready")
    rows = conn.execute(
        "SELECT market, status, attempts FROM stock_source_coverage ORDER BY market").fetchall()
    assert [tuple(row) for row in rows] == [("TPEx", "failed", 1), ("TWSE", "complete", 1)]


def test_stock_source_coverage_accepts_holiday_status_rejects_garbage(tmp_path):
    from stocks_power_rich.db import set_stock_source_coverage

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    set_stock_source_coverage(conn, "2026-08-08", "TWSE", "quotes", "holiday", 0)
    status = conn.execute(
        "SELECT status FROM stock_source_coverage WHERE date=?", ("2026-08-08",)).fetchone()[0]
    assert status == "holiday"
    with pytest.raises(ValueError):
        set_stock_source_coverage(conn, "2026-08-08", "TWSE", "quotes", "bogus", 0)


def test_bulk_upsert_revenue_keys_by_year_month_and_code(tmp_path):
    from stocks_power_rich.db import bulk_upsert_revenue, get_latest_revenue, revenue_yoy_map

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    rows = {
        "2330": {"name": "台積電", "industry": "半導體業", "year_month": "2026-07",
                 "report_date": "2026-08-11", "revenue": 467580548.0,
                 "revenue_prev_month": 442679969.0, "revenue_last_year": 323165707.0,
                 "mom_pct": 5.6, "yoy_pct": 44.68, "revenue_accum": 2872064238.0,
                 "revenue_accum_last_year": 2096211240.0, "accum_yoy_pct": 37.0, "note": None},
    }
    n = bulk_upsert_revenue(conn, "TWSE", rows)
    assert n == 1

    # 同一年月重複覆寫（每日排程重複呼叫是正常行為）不應該產生第二列
    rows["2330"]["yoy_pct"] = 44.70
    bulk_upsert_revenue(conn, "TWSE", rows)
    count = conn.execute("SELECT COUNT(*) FROM stock_revenue_monthly").fetchone()[0]
    assert count == 1

    # 新的一個月是新的一列，舊資料仍保留（累積歷史，不是覆寫）
    rows2 = {"2330": {**rows["2330"], "year_month": "2026-08", "report_date": "2026-09-10",
                      "yoy_pct": 30.0}}
    bulk_upsert_revenue(conn, "TWSE", rows2)
    count = conn.execute("SELECT COUNT(*) FROM stock_revenue_monthly").fetchone()[0]
    assert count == 2

    latest = get_latest_revenue(conn, "2330")
    assert latest["year_month"] == "2026-08" and latest["yoy_pct"] == 30.0

    # as_of 卡在 8 月報表公告前 → 只看得到 7 月那筆（不能用還沒公告的資料回推過去）
    as_of = get_latest_revenue(conn, "2330", as_of="2026-08-20")
    assert as_of["year_month"] == "2026-07" and as_of["yoy_pct"] == 44.70

    ymap = revenue_yoy_map(conn)
    assert ymap["2330"] == 30.0
    ymap_as_of = revenue_yoy_map(conn, as_of="2026-08-20")
    assert ymap_as_of["2330"] == 44.70


def test_bulk_upsert_custody_stores_total_holders_and_change_map_computes_diff(tmp_path):
    from stocks_power_rich.db import bulk_upsert_custody, custody_change_map

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(custody_dist)")}
    assert "total_holders" in columns

    bulk_upsert_custody(conn, "2026-06-18", {
        "1101": {"big1000_pct": 51.23, "big400_pct": 55.41, "big_holders": 412, "total_holders": 516680},
    })
    bulk_upsert_custody(conn, "2026-06-26", {
        "1101": {"big1000_pct": 51.34, "big400_pct": 55.52, "big_holders": 410, "total_holders": 514507},
        "1102": {"big1000_pct": 79.48, "big400_pct": 81.8, "big_holders": 200, "total_holders": 103241},
    })
    row = conn.execute(
        "SELECT total_holders FROM custody_dist WHERE week=? AND code=?",
        ("2026-06-26", "1101")).fetchone()
    assert row[0] == 514507.0

    # 1101 兩週都有資料才算得出差；1102 只有一週（上週缺）→ 不進 map（不是回 None，而是不存在該代號）
    cmap = custody_change_map(conn)
    assert cmap["1101"] == {"big_holder_ratio": 0.11, "holder_drop_ratio": -0.42}
    assert "1102" not in cmap

    # as_of 卡在兩週資料之前 → 不足兩週可比 → 全空
    assert custody_change_map(conn, as_of="2026-06-20") == {}


def test_institutional_3d_map_sums_last_three_trading_days(tmp_path):
    """投信/外資近3日淨買超＝最近 3 個交易日 trust_lots/foreign_lots 加總（單位張、帶正負號），
    對照 CSV 的 投三/外三。第 4 舊的日子不進窗口；某檔某日缺列＝當日 0（不整檔排除）。"""
    from stocks_power_rich.db import bulk_upsert_stock_flow, institutional_3d_map

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    # 2330：四天都有；1101：只有 08-04、08-06（缺 08-05）
    bulk_upsert_stock_flow(conn, "2026-08-03", "TWSE", {"2330": {"trust_lots": 5, "foreign_lots": 10}})
    bulk_upsert_stock_flow(conn, "2026-08-04", "TWSE", {"2330": {"trust_lots": 1, "foreign_lots": 2},
                                                        "1101": {"trust_lots": -1, "foreign_lots": 0}})
    bulk_upsert_stock_flow(conn, "2026-08-05", "TWSE", {"2330": {"trust_lots": 2, "foreign_lots": -3}})
    bulk_upsert_stock_flow(conn, "2026-08-06", "TWSE", {"2330": {"trust_lots": 3, "foreign_lots": 4},
                                                        "1101": {"trust_lots": 2, "foreign_lots": 1}})

    m = institutional_3d_map(conn)  # 預設 as_of=最新 → 窗口 {08-06,08-05,08-04}
    assert m["2330"] == {"trust_3d": 6.0, "foreign_3d": 3.0}   # 1+2+3 / 2+(-3)+4
    assert m["1101"] == {"trust_3d": 1.0, "foreign_3d": 1.0}   # (-1)+2 / 0+1（08-05 缺＝0）

    # as_of 卡在 08-05 → 窗口 {08-05,08-04,08-03}，2330 改為 5+1+2 / 10+2+(-3)
    m2 = institutional_3d_map(conn, as_of="2026-08-05")
    assert m2["2330"] == {"trust_3d": 8.0, "foreign_3d": 9.0}


def test_get_financials_bulk_shapes_for_lan_score(tmp_path):
    """全市場一次取 {代號:{指標:[值,新到舊]}}，正是 lan_score 期望的 [0]=最新 形狀。"""
    from stocks_power_rich.db import bulk_upsert_financials, get_financials_bulk
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_financials(conn, "revenue", {"2330": {"2026Q2": 300.0, "2026Q1": 200.0, "2025Q4": 100.0}})
    bulk_upsert_financials(conn, "roe", {"2330": {"2026Q2": 30.0}, "1101": {"2026Q2": 5.0}})
    out = get_financials_bulk(conn, ["revenue", "roe"])
    assert out["2330"]["revenue"] == [300.0, 200.0, 100.0]   # 季別由新到舊
    assert out["2330"]["roe"] == [30.0]
    assert out["1101"] == {"roe": [5.0]}                      # 缺 revenue → 沒那個 key
    assert get_financials_bulk(conn, []) == {}


def test_institutional_3d_map_empty_when_no_flow(tmp_path):
    from stocks_power_rich.db import institutional_3d_map
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    assert institutional_3d_map(conn) == {}


def test_custody_change_map_skips_sparse_partial_week(tmp_path):
    """迴歸（production 大戶增比全滅）：最新一週若是逐檔集保回補寫進的『殘缺週』（僅少數檔，
    因為全市場批次還沒公布那週），不可拿它當比較週——否則兩週交集只剩那幾檔。應改用最近
    兩週『完整』集保週（實測 production：最新週 1 檔、前一週 4028 檔 → 大戶增比只亮 1 檔）。"""
    from stocks_power_rich.db import bulk_upsert_custody, custody_change_map

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    full = {c: {"big400_pct": 50.0, "total_holders": 1000} for c in ("1101", "1102", "1103", "1104")}
    bulk_upsert_custody(conn, "2026-08-07", full)
    bulk_upsert_custody(conn, "2026-08-14",
                        {c: {"big400_pct": 51.0, "total_holders": 1000} for c in full})
    bulk_upsert_custody(conn, "2026-08-21", {"1101": {"big400_pct": 99.0, "total_holders": 1000}})  # 殘缺週

    cmap = custody_change_map(conn, as_of="2026-08-22")
    # 應改用 08-14 vs 08-07（各 4 檔），不是拿殘缺的 08-21 → 四檔都算得出，
    # 且 1101 增比＝51−50＝1.0（不是 99−51 那種被殘缺週污染的值）。
    assert set(cmap.keys()) == {"1101", "1102", "1103", "1104"}
    assert cmap["1101"]["big_holder_ratio"] == 1.0


def test_bulk_upsert_financials_stores_by_quarter_and_reads_series(tmp_path):
    from stocks_power_rich.db import bulk_upsert_financials, get_financial_series

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_financials)")}
    assert {"quarter", "code", "indicator", "value"} <= columns

    n = bulk_upsert_financials(conn, "roe", {
        "2330": {"2025Q4": 9.63, "2026Q1": 10.06},
        "2317": {"2026Q1": 2.88},
    })
    assert n == 3  # 2330 兩季 + 2317 一季

    # 覆寫同一 (quarter, code, indicator) 不新增列
    bulk_upsert_financials(conn, "roe", {"2330": {"2026Q1": 10.10}})
    count = conn.execute("SELECT COUNT(*) FROM stock_financials").fetchone()[0]
    assert count == 3

    # 讀回「最新在前」的季度數列（供之後接 lan_score 用）
    series = get_financial_series(conn, "2330", "roe")
    assert series == [("2026Q1", 10.10), ("2025Q4", 9.63)]  # 季別由新到舊

    # 不同 indicator 各自獨立
    bulk_upsert_financials(conn, "debt_ratio", {"2330": {"2026Q1": 24.5}})
    assert get_financial_series(conn, "2330", "debt_ratio") == [("2026Q1", 24.5)]
    assert get_financial_series(conn, "2330", "roe") == [("2026Q1", 10.10), ("2025Q4", 9.63)]
