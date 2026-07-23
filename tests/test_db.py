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
)


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
