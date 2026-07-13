import sqlite3
from .db import get_snapshot, get_all_ohlc
from . import analysis, patterns

def record_daily_signals(conn: sqlite3.Connection) -> None:
    # 1. filtered_picks
    r_chip = conn.execute("SELECT MAX(snap_date) FROM chip_snapshot").fetchone()
    if r_chip and r_chip[0]:
        date_str = r_chip[0]
        exists = conn.execute(
            "SELECT 1 FROM signal_ledger WHERE signal_date=? AND source='filtered_picks' LIMIT 1",
            (date_str,)
        ).fetchone()
        if not exists:
            rows = get_snapshot(conn, date_str)
            picks = analysis.filtered_picks(rows)
            for p in picks:
                conn.execute(
                    "INSERT OR IGNORE INTO signal_ledger (signal_date, code, name, source, entry_ref_price) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (date_str, p["code"], p["name"], "filtered_picks", p["close"])
                )
            conn.commit()

    # 2. cup_handle
    r_ohlc = conn.execute("SELECT MAX(date) FROM stock_ohlc").fetchone()
    if r_ohlc and r_ohlc[0]:
        date_str = r_ohlc[0]
        exists = conn.execute(
            "SELECT 1 FROM signal_ledger WHERE signal_date=? AND source='cup_handle' LIMIT 1",
            (date_str,)
        ).fetchone()
        if not exists:
            data = get_all_ohlc(conn, min_bars=patterns.LOOKBACK)
            matches = patterns.screen_cup_handle(data)
            for m in matches:
                stock_dates = data.get(m["code"], {}).get("dates") or []
                if stock_dates and stock_dates[-1] == date_str:
                    conn.execute(
                        "INSERT OR IGNORE INTO signal_ledger (signal_date, code, name, source, entry_ref_price) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (date_str, m["code"], m["name"], "cup_handle", m["last_close"])
                    )
            conn.commit()


def update_ledger_returns(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        "SELECT signal_date, code, source, entry_ref_price, ret5, ret10, ret20 "
        "FROM signal_ledger "
        "WHERE ret5 IS NULL OR ret10 IS NULL OR ret20 IS NULL"
    )
    pending = cursor.fetchall()
    for row in pending:
        sig_date, code, source, ref_price, r5, r10, r20 = row
        if not ref_price or ref_price <= 0:
            continue

        ohlc = conn.execute(
            "SELECT date, close FROM stock_ohlc "
            "WHERE code=? AND date >= ? "
            "ORDER BY date ASC",
            (code, sig_date)
        ).fetchall()

        if not ohlc:
            continue

        updates = {}
        if r5 is None and len(ohlc) > 5:
            updates["ret5"] = (ohlc[5]["close"] - ref_price) / ref_price * 100
        if r10 is None and len(ohlc) > 10:
            updates["ret10"] = (ohlc[10]["close"] - ref_price) / ref_price * 100
        if r20 is None and len(ohlc) > 20:
            updates["ret20"] = (ohlc[20]["close"] - ref_price) / ref_price * 100

        if updates:
            cols = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [sig_date, code, source]
            conn.execute(
                f"UPDATE signal_ledger SET {cols} WHERE signal_date=? AND code=? AND source=?",
                vals
            )
    conn.commit()
