from stocks_power_rich.db import (get_connection, init_db, bulk_upsert_ssf_daily,
                                  get_ssf_dates, get_ssf_rows, prune_ssf_daily)


def _row(date, root, **kw):
    base = {"date": date, "root": root, "main_month": "202610", "open": 1.0, "high": 2.0,
            "low": 0.5, "close": 1.5, "chg": 0.1, "chg_pct": 7.1, "settlement": 1.5,
            "oi": 100, "volume": 10, "main_volume": 8}
    base.update(kw)
    return base


def test_upsert_and_read_back(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    assert bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD"), _row("2026-09-17", "QF")]) == 2
    assert get_ssf_dates(conn) == ["2026-09-17"]
    rows = get_ssf_rows(conn, ["2026-09-17"])
    assert {r["root"] for r in rows} == {"CD", "QF"}
    assert rows[0]["main_month"] == "202610"


def test_upsert_is_coalescing_so_a_partial_rewrite_does_not_blank_columns(tmp_path):
    """重抓前兩個交易日時，官方偶爾少給某欄；COALESCE 讓它保留既有值而不是洗成空。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD", close=2433.0, oi=25045)])
    bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD", close=2440.0, oi=None)])
    row = get_ssf_rows(conn, ["2026-09-17"])[0]
    assert row["close"] == 2440.0     # 新值覆蓋
    assert row["oi"] == 25045         # None 不洗掉舊值


def test_dates_are_newest_first_and_limited(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row(f"2026-09-{d:02d}", "CD") for d in range(1, 16)])
    assert get_ssf_dates(conn, limit=3) == ["2026-09-15", "2026-09-14", "2026-09-13"]


def test_prune_keeps_only_the_newest_n_trading_days(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row(f"2026-09-{d:02d}", "CD") for d in range(1, 16)])
    assert prune_ssf_daily(conn, keep_days=5) == 10
    assert len(get_ssf_dates(conn, limit=99)) == 5
