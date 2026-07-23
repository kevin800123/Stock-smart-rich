"""SQLite 資料層：建立 schema、每日大盤快照與籌碼快照的 upsert/查詢。"""
import glob
import json
import os
import sqlite3
from datetime import datetime

MARKET_COLS = [
    "date", "taiex", "taiex_chg", "turnover", "inst_foreign", "inst_trust", "inst_dealer",
    "margin_balance", "margin_chg", "short_balance", "short_chg",
    "margin_value", "margin_value_chg", "margin_maintenance",
    "tx_price", "tx_chg", "tx_open", "tx_high", "tx_low",
    "fut_inst_net", "retail_ls_mtx", "retail_ls_tmf",
    "tx_foreign_oi", "retail_oi_mtx",
    "sox", "n225", "kospi", "gold", "jpy", "btc", "vix", "twd",
    "sox_chg", "n225_chg", "kospi_chg", "gold_chg", "jpy_chg", "btc_chg", "vix_chg", "twd_chg", "updated_at",
]

CHIP_COLS = [
    "snap_date", "code", "name", "industry", "sub_industry", "close",
    "big_holder_ratio", "holder_drop_ratio", "month_inc", "rev_yoy", "accum_inc",
    "trust_3d", "foreign_3d", "custody", "w55", "market_cap", "capital",
    "est_profit", "lan_score", "lpe", "lan_value", "raw_json",
]


def backup_db(db_path: str, keep: int = 7, stamp: str | None = None) -> str | None:
    """以 SQLite 線上備份 API 複製整個 DB 到同目錄 backup/spr-YYYYMMDD.sqlite，輪替保留最近 keep 份。

    用官方 Connection.backup（可在服務運行中安全備份，不鎖庫）；來源不存在回 None。
    集保逐週資料等無法重建，故排程每日執行以防 Volume 故障/誤刪造成永久遺失。
    """
    if not os.path.exists(db_path):
        return None
    bdir = os.path.join(os.path.dirname(db_path) or ".", "backup")
    os.makedirs(bdir, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d")
    dest = os.path.join(bdir, f"spr-{stamp}.sqlite")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    files = sorted(glob.glob(os.path.join(bdir, "spr-*.sqlite")))
    for old in files[:-keep]:  # 只留最近 keep 份（檔名日期字典序＝時序）
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    real_cols = ", ".join(
        f"{c} REAL" for c in MARKET_COLS if c not in ("date", "updated_at")
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS market_daily "
        f"(date TEXT PRIMARY KEY, {real_cols}, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chip_snapshot ("
        "snap_date TEXT, code TEXT, name TEXT, industry TEXT, sub_industry TEXT, "
        "close REAL, big_holder_ratio REAL, holder_drop_ratio REAL, month_inc REAL, "
        "rev_yoy REAL, accum_inc REAL, trust_3d REAL, foreign_3d REAL, custody REAL, "
        "w55 REAL, market_cap REAL, capital REAL, est_profit REAL, lan_score REAL, "
        "lpe REAL, lan_value REAL, raw_json TEXT, PRIMARY KEY(snap_date, code))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS csv_files ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, snap_date TEXT, "
        "stored_path TEXT, imported_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ai_cache ("
        "cache_key TEXT PRIMARY KEY, payload TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tx_history ("
        "date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, "
        "night_volume REAL)"
    )
    # 依股號查最新快照（watchlist/個股頁）用；PK 是 (snap_date, code)，無此索引會全表掃描
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chip_code ON chip_snapshot(code)")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS watchlist (code TEXT PRIMARY KEY, name TEXT, added_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS custody_dist (week TEXT, code TEXT, big1000_pct REAL, "
                 "big400_pct REAL, big_holders REAL, PRIMARY KEY(week, code))")
    # 全市場個股每日 OHLC（型態選股用；由 MI_INDEX ALLBUT0999 逐日回補與累積）
    conn.execute("CREATE TABLE IF NOT EXISTS stock_ohlc (date TEXT, code TEXT, open REAL, high REAL, "
                 "low REAL, close REAL, PRIMARY KEY(date, code))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlc_code ON stock_ohlc(code, date)")
    # 交易帳本（實單/模擬單；fee_pct=來回費用%，NULL=用預設 0.585）
    conn.execute("CREATE TABLE IF NOT EXISTS trades ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, shares INTEGER, "
                 "entry_date TEXT, entry_price REAL, exit_date TEXT, exit_price REAL, "
                 "fee_pct REAL, note TEXT, created_at TEXT)")
    # 訊號追蹤帳本/前瞻測試（filtered_picks / cup_handle 每日命中快照及後續報酬）
    conn.execute("CREATE TABLE IF NOT EXISTS signal_ledger ("
                 "signal_date TEXT, code TEXT, name TEXT, source TEXT, "
                 "entry_ref_price REAL, ret5 REAL, ret10 REAL, ret20 REAL, "
                 "PRIMARY KEY(signal_date, code, source))")
    # 既有資料庫補上後來新增的欄位
    mkt_existing = {r[1] for r in conn.execute("PRAGMA table_info(market_daily)").fetchall()}
    for col in MARKET_COLS:
        if col not in mkt_existing and col != "date":
            coltype = "TEXT" if col == "updated_at" else "REAL"
            conn.execute(f"ALTER TABLE market_daily ADD COLUMN {col} {coltype}")
    chip_existing = {r[1] for r in conn.execute("PRAGMA table_info(chip_snapshot)").fetchall()}
    for col in CHIP_COLS:
        if col not in chip_existing and col not in ("snap_date", "code"):
            coltype = "TEXT" if col in ("name", "industry", "sub_industry", "raw_json") else "REAL"
            conn.execute(f"ALTER TABLE chip_snapshot ADD COLUMN {col} {coltype}")
    tx_existing = {r[1] for r in conn.execute("PRAGMA table_info(tx_history)").fetchall()}
    if "night_volume" not in tx_existing:
        conn.execute("ALTER TABLE tx_history ADD COLUMN night_volume REAL")
    # 一次性資料修正：jpy 語意由「日圓兌台幣(~0.2)」改為「美元兌日圓(~150)」，清掉舊語意殘值
    conn.execute("UPDATE market_daily SET jpy=NULL, jpy_chg=NULL WHERE jpy IS NOT NULL AND jpy < 10")
    conn.commit()


def _on_conflict(keys: str, updates: str) -> str:
    """沒有非鍵欄位要更新時，DO UPDATE SET 後面會是空字串而讓 SQL 語法不完整
    （sqlite3.OperationalError: incomplete input）。這種「只帶鍵」的列語意是
    「沒有就建、有了就別動」＝DO NOTHING，而不是把既有欄位洗成 NULL——
    _refresh_recent/_backfill_* 都是先確保列存在、再逐步補欄位，洗掉會毀資料。"""
    return f"ON CONFLICT({keys}) DO NOTHING" if not updates \
        else f"ON CONFLICT({keys}) DO UPDATE SET {updates}"


def upsert_market_daily(conn: sqlite3.Connection, row: dict) -> None:
    if not row.get("date"):
        raise ValueError("upsert_market_daily 需要 date（market_daily 以交易日為鍵）")
    cols = [c for c in MARKET_COLS if c in row]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    conn.execute(
        f"INSERT INTO market_daily ({','.join(cols)}) VALUES ({placeholders}) "
        + _on_conflict("date", updates),
        [row[c] for c in cols],
    )
    conn.commit()


def insert_chip_snapshot(conn: sqlite3.Connection, snap_date: str, rows: list[dict]) -> None:
    for r in rows:
        cols = ["snap_date"] + [c for c in CHIP_COLS if c != "snap_date" and c in r]
        vals = [snap_date] + [r[c] for c in cols if c != "snap_date"]
        ph = ",".join("?" for _ in cols)
        upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("snap_date", "code"))
        conn.execute(
            f"INSERT INTO chip_snapshot ({','.join(cols)}) VALUES ({ph}) "
            + _on_conflict("snap_date,code", upd),
            vals,
        )
    conn.commit()


def get_snapshot_dates(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT snap_date FROM chip_snapshot ORDER BY snap_date"
        ).fetchall()
    ]


def get_snapshot(conn: sqlite3.Connection, snap_date: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chip_snapshot WHERE snap_date=?", (snap_date,)
        ).fetchall()
    ]


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def upsert_tx_history(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO tx_history (date, open, high, low, close, volume, night_volume) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET open=excluded.open, high=excluded.high, "
            "low=excluded.low, close=excluded.close, volume=excluded.volume, "
            "night_volume=excluded.night_volume",
            (r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
             r.get("volume"), r.get("night_volume")),
        )
    conn.commit()


def upsert_custody(conn: sqlite3.Connection, week: str, code: str, rec: dict) -> None:
    conn.execute(
        "INSERT INTO custody_dist (week, code, big1000_pct, big400_pct, big_holders) VALUES (?,?,?,?,?) "
        "ON CONFLICT(week, code) DO UPDATE SET big1000_pct=excluded.big1000_pct, "
        "big400_pct=excluded.big400_pct, big_holders=excluded.big_holders",
        (week, code, rec.get("big1000_pct"), rec.get("big400_pct"), rec.get("big_holders")),
    )
    conn.commit()


def custody_week_exists(conn: sqlite3.Connection, week: str) -> bool:
    return conn.execute("SELECT 1 FROM custody_dist WHERE week=? LIMIT 1", (week,)).fetchone() is not None


def latest_custody_week(conn: sqlite3.Connection):
    r = conn.execute("SELECT MAX(week) FROM custody_dist").fetchone()
    return r[0] if r and r[0] else None


def bulk_upsert_custody(conn: sqlite3.Connection, week: str, data: dict) -> int:
    rows = [(week, code, v.get("big1000_pct"), v.get("big400_pct"), v.get("big_holders"))
            for code, v in data.items()]
    conn.executemany(
        "INSERT INTO custody_dist (week, code, big1000_pct, big400_pct, big_holders) VALUES (?,?,?,?,?) "
        "ON CONFLICT(week, code) DO UPDATE SET big1000_pct=excluded.big1000_pct, "
        "big400_pct=excluded.big400_pct, big_holders=excluded.big_holders",
        rows,
    )
    conn.commit()
    return len(rows)


def bulk_upsert_ohlc(conn: sqlite3.Connection, date: str, rows: dict) -> int:
    """一日全市場 OHLC 批次入庫。rows＝{code: {open,high,low,close}}。"""
    data = [(date, code, v.get("open"), v.get("high"), v.get("low"), v.get("close"))
            for code, v in rows.items()]
    conn.executemany(
        "INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(date, code) DO UPDATE SET open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close",
        data,
    )
    conn.commit()
    return len(data)


def ohlc_dates(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM stock_ohlc ORDER BY date").fetchall()]


def get_all_ohlc(conn: sqlite3.Connection, min_bars: int = 1) -> dict:
    """{code: {dates[], highs[], lows[], closes[]}}（各檔由舊到新）；不足 min_bars 者略過。"""
    out: dict[str, dict] = {}
    for code, d, h, l, c in conn.execute(
            "SELECT code, date, high, low, close FROM stock_ohlc ORDER BY code, date"):
        s = out.setdefault(code, {"dates": [], "highs": [], "lows": [], "closes": []})
        s["dates"].append(d); s["highs"].append(h); s["lows"].append(l); s["closes"].append(c)
    return {code: s for code, s in out.items() if len(s["dates"]) >= min_bars}


def get_ohlc_history(conn: sqlite3.Connection, code: str) -> list[dict]:
    return [{"date": d, "open": o, "high": h, "low": l, "close": c}
            for d, o, h, l, c in conn.execute(
                "SELECT date, open, high, low, close FROM stock_ohlc WHERE code=? ORDER BY date",
                (code,))]


def get_custody_trend(conn: sqlite3.Connection, code: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT week, big1000_pct, big400_pct, big_holders FROM custody_dist WHERE code=? ORDER BY week",
        (code,)).fetchall()]


def list_watch(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT code, name, added_at FROM watchlist ORDER BY added_at").fetchall()]


def add_watch(conn: sqlite3.Connection, code: str, name: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (code, name, added_at) VALUES (?,?,?)",
        (code, name, datetime.now().isoformat()),
    )
    conn.commit()


def remove_watch(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit()


def list_trades(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades ORDER BY entry_date DESC, id DESC").fetchall()]


def add_trade(conn: sqlite3.Connection, t: dict) -> int:
    cur = conn.execute(
        "INSERT INTO trades (code, name, shares, entry_date, entry_price, "
        "exit_date, exit_price, fee_pct, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (t["code"], t.get("name") or "", int(t["shares"]), t["entry_date"],
         float(t["entry_price"]), t.get("exit_date"), t.get("exit_price"),
         t.get("fee_pct"), t.get("note") or "", datetime.now().isoformat()))
    conn.commit()
    return cur.lastrowid


def close_trade(conn: sqlite3.Connection, tid: int, exit_date: str, exit_price: float) -> bool:
    cur = conn.execute("UPDATE trades SET exit_date=?, exit_price=? WHERE id=?",
                       (exit_date, float(exit_price), int(tid)))
    conn.commit()
    return cur.rowcount > 0


def delete_trade(conn: sqlite3.Connection, tid: int) -> bool:
    cur = conn.execute("DELETE FROM trades WHERE id=?", (int(tid),))
    conn.commit()
    return cur.rowcount > 0


def get_tx_history(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM tx_history ORDER BY date").fetchall()]


def get_ai_cache(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT payload FROM ai_cache WHERE cache_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_ai_cache(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO ai_cache (cache_key, payload, created_at) VALUES (?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
        (key, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
