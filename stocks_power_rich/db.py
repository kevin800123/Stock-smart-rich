"""SQLite 資料層：建立 schema、每日大盤快照與籌碼快照的 upsert/查詢。"""
import os
import sqlite3

MARKET_COLS = [
    "date", "taiex", "taiex_chg", "inst_foreign", "inst_trust", "inst_dealer",
    "margin_balance", "margin_chg", "short_balance", "short_chg",
    "tx_price", "tx_chg", "tx_open", "tx_high", "tx_low",
    "fut_inst_net", "retail_ls_mtx", "retail_ls_tmf",
    "sox", "n225", "kospi", "gold", "btc", "updated_at",
]

CHIP_COLS = [
    "snap_date", "code", "name", "industry", "sub_industry", "close",
    "big_holder_ratio", "holder_drop_ratio", "month_inc", "rev_yoy", "accum_inc",
    "trust_3d", "foreign_3d", "custody", "w55", "market_cap", "capital",
    "est_profit", "lpe", "raw_json",
]


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
        "w55 REAL, market_cap REAL, capital REAL, est_profit REAL, lpe REAL, "
        "raw_json TEXT, PRIMARY KEY(snap_date, code))"
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
    # 既有資料庫補上後來新增的欄位
    existing = {r[1] for r in conn.execute("PRAGMA table_info(market_daily)").fetchall()}
    for col in MARKET_COLS:
        if col not in existing and col not in ("date",):
            coltype = "TEXT" if col == "updated_at" else "REAL"
            conn.execute(f"ALTER TABLE market_daily ADD COLUMN {col} {coltype}")
    conn.commit()


def upsert_market_daily(conn: sqlite3.Connection, row: dict) -> None:
    cols = [c for c in MARKET_COLS if c in row]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    conn.execute(
        f"INSERT INTO market_daily ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {updates}",
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
            f"ON CONFLICT(snap_date,code) DO UPDATE SET {upd}",
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


def get_ai_cache(conn: sqlite3.Connection, key: str):
    import json

    row = conn.execute("SELECT payload FROM ai_cache WHERE cache_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set_ai_cache(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    import json
    from datetime import datetime

    conn.execute(
        "INSERT INTO ai_cache (cache_key, payload, created_at) VALUES (?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
        (key, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
