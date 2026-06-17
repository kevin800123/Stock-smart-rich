from stocks_power_rich.db import (
    get_connection,
    init_db,
    upsert_market_daily,
    insert_chip_snapshot,
    get_snapshot_dates,
    get_snapshot,
)


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
