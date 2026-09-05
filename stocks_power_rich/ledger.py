import sqlite3
from .db import get_snapshot, get_all_ohlc
from . import analysis, patterns, selfcheck

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


def record_self_screen_signals(conn: sqlite3.Connection, universe: dict,
                               mu_value_min, mu_score_min) -> None:
    """自算選股 picks → signal_ledger（source='self_screen'），做前瞻績效追蹤。

    **為什麼要有這支**：訊號追蹤頁雖然移除了，記錄仍刻意持續（見 CLAUDE.md）——前瞻報酬
    不能事後回補，一旦回補就有存活者偏誤。自算選股先前完全沒被記錄，等於「自算到底有沒有比
    CSV 那套準」永遠拿不出證據；越晚接、能比較的歷史就越短。

    signal_date 刻意用**最新 CSV 快照日**（與 record_daily_signals 的 filtered_picks 相同），
    兩個來源落在同一天才能直接對照。`universe` 由呼叫端給（來自 api.helpers 的公司基本資料
    月快取）——ledger 屬核心層，不反向 import api 層。

    只在每日排程呼叫，不掛在 CSV 上傳等請求路徑上：build_self_screen 是全市場計算，放進請求
    會拖慢回應（同「請求裡不要放無界時間的同步計算」那條教訓）。
    """
    r_chip = conn.execute("SELECT MAX(snap_date) FROM chip_snapshot").fetchone()
    if not (r_chip and r_chip[0]):
        return
    date_str = r_chip[0]
    exists = conn.execute(
        "SELECT 1 FROM signal_ledger WHERE signal_date=? AND source='self_screen' LIMIT 1",
        (date_str,)
    ).fetchone()
    if exists:
        return
    result = selfcheck.build_self_screen(conn, date_str, universe, mu_value_min, mu_score_min)
    for p in result.get("rows", []):
        # 進場價＝訊號日(含)之前最近一筆收盤。不可用該檔 OHLC 的最新收盤——signal_date 可能
        # 是較舊的 CSV 日，那樣等於用未來價當進場價，前瞻報酬會被灌水。
        px = conn.execute(
            "SELECT close FROM stock_ohlc WHERE code=? AND date<=? AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", (p["code"], date_str)
        ).fetchone()
        if not px or not px[0]:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO signal_ledger (signal_date, code, name, source, entry_ref_price) "
            "VALUES (?, ?, ?, ?, ?)",
            (date_str, p["code"], p.get("name") or p["code"], "self_screen", px[0])
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
