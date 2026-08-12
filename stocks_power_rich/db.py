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
    # 維持率的分子分母（億）——存下來卡片才能把「怎麼算出來的」秀給人看
    "margin_mv", "short_mv",
    # 上櫃自成一組：融資成數 50%（上市 60%），損益兩平線 200% vs 166.7%，
    # 併成單一「大盤」數字會把兩個市場的反向訊號互相抵銷掉
    "otc_margin_balance", "otc_short_balance", "otc_margin_value",
    "otc_margin_mv", "otc_short_mv", "otc_margin_maintenance",
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

# 自選股「輸入預估」面板的使用者輸入（analysis.estimate_price_range 的原始參數）。
# 全部可為 NULL：既有 watchlist 列補上這些欄位後預設是「尚未填預估」，前端據此顯示「—」
# 並用當日 chip.lpe 預帶中本益比，不是資料錯誤。
WATCHLIST_COLS = [
    "est_revenue", "est_gross_margin", "est_opex", "est_tax",
    "est_pe_low", "est_pe_mid", "est_pe_high",
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
                 "low REAL, close REAL, volume_lots REAL, amount_twd REAL, PRIMARY KEY(date, code))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlc_code ON stock_ohlc(code, date)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_flow_daily ("
        "date TEXT, code TEXT, market TEXT, name TEXT, foreign_lots REAL, trust_lots REAL, "
        "dealer_lots REAL, institutional_total_lots REAL, margin_balance_lots REAL, "
        "short_balance_lots REAL, PRIMARY KEY(date, code))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_flow_market_date "
                 "ON stock_flow_daily(market, date)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_source_coverage ("
        "date TEXT, market TEXT, source TEXT, status TEXT, row_count INTEGER DEFAULT 0, "
        "attempts INTEGER DEFAULT 0, last_error TEXT, updated_at TEXT, "
        "PRIMARY KEY(date, market, source))"
    )
    # 月營收（MOPS t187ap05，上市/上櫃共用格式）；PK 含 year_month 是刻意的——這個端點
    # 只給「最新一次已公告的月份」，每天呼叫都會重複覆寫同一個 year_month（無害），但月份
    # 一換就是新的一列，讓歷史逐月累積而非只留最新一筆（同 stock_ohlc/stock_flow_daily
    # 逐日累積的道理，只是週期換成月）。
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock_revenue_monthly ("
        "year_month TEXT, code TEXT, market TEXT, name TEXT, industry TEXT, report_date TEXT, "
        "revenue REAL, revenue_prev_month REAL, revenue_last_year REAL, mom_pct REAL, "
        "yoy_pct REAL, revenue_accum REAL, revenue_accum_last_year REAL, accum_yoy_pct REAL, "
        "note TEXT, PRIMARY KEY(year_month, code))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revenue_code ON stock_revenue_monthly(code, year_month)")
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
    wl_existing = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    for col in WATCHLIST_COLS:
        if col not in wl_existing:
            conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} REAL")
    ohlc_existing = {r[1] for r in conn.execute("PRAGMA table_info(stock_ohlc)").fetchall()}
    for col in ("volume_lots", "amount_twd"):
        if col not in ohlc_existing:
            conn.execute(f"ALTER TABLE stock_ohlc ADD COLUMN {col} REAL")
    custody_existing = {r[1] for r in conn.execute("PRAGMA table_info(custody_dist)").fetchall()}
    if "total_holders" not in custody_existing:
        conn.execute("ALTER TABLE custody_dist ADD COLUMN total_holders REAL")
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
    rows = [(week, code, v.get("big1000_pct"), v.get("big400_pct"), v.get("big_holders"),
             v.get("total_holders"))
            for code, v in data.items()]
    conn.executemany(
        "INSERT INTO custody_dist (week, code, big1000_pct, big400_pct, big_holders, total_holders) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(week, code) DO UPDATE SET "
        "big1000_pct=excluded.big1000_pct, big400_pct=excluded.big400_pct, "
        "big_holders=excluded.big_holders, total_holders=excluded.total_holders",
        rows,
    )
    conn.commit()
    return len(rows)


def custody_change_map(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    """全市場 {代號: {big_holder_ratio, holder_drop_ratio}}——公式見 analysis.custody_change()。

    集保逐週全市場批次寫入（見 updater._accumulate_custody），同一週所有代號共用同一個
    week 值，因此「最近兩週」是對整張表找、不必逐代號找——不同代號各自缺列（該代號當週
    未列入分散表）時自然不進回傳結果，不必特別處理。不足兩週可比資料時回空 dict。
    """
    cutoff = as_of or "9999-99-99"
    weeks = [r[0] for r in conn.execute(
        "SELECT DISTINCT week FROM custody_dist WHERE week<=? ORDER BY week DESC LIMIT 2",
        (cutoff,)).fetchall()]
    if len(weeks) < 2:
        return {}
    this_week, last_week = weeks

    def _rows(week):
        return {code: {"big400_pct": b400, "total_holders": th} for code, b400, th in conn.execute(
            "SELECT code, big400_pct, total_holders FROM custody_dist WHERE week=?", (week,))}

    cur, prev = _rows(this_week), _rows(last_week)
    from . import analysis
    out = {}
    for code, cur_row in cur.items():
        if code not in prev:
            continue
        out[code] = analysis.custody_change(cur_row, prev[code])
    return out


def bulk_upsert_ohlc(conn: sqlite3.Connection, date: str, rows: dict) -> int:
    """一日全市場 OHLC 批次入庫。rows＝{code: {open,high,low,close}}。"""
    data = [(date, code, v.get("open"), v.get("high"), v.get("low"), v.get("close"),
             v.get("volume_lots", v.get("vol")), v.get("amount_twd", v.get("amount")))
            for code, v in rows.items()]
    conn.executemany(
        "INSERT INTO stock_ohlc (date, code, open, high, low, close, volume_lots, amount_twd) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(date, code) DO UPDATE SET "
        "open=COALESCE(excluded.open, stock_ohlc.open), "
        "high=COALESCE(excluded.high, stock_ohlc.high), "
        "low=COALESCE(excluded.low, stock_ohlc.low), "
        "close=COALESCE(excluded.close, stock_ohlc.close), "
        "volume_lots=COALESCE(excluded.volume_lots, stock_ohlc.volume_lots), "
        "amount_twd=COALESCE(excluded.amount_twd, stock_ohlc.amount_twd)",
        data,
    )
    conn.commit()
    return len(data)


def bulk_upsert_stock_flow(conn: sqlite3.Connection, date: str, market: str,
                           rows: dict) -> int:
    """Partially upsert normalized institutional and margin observations."""
    cols = ("name", "foreign_lots", "trust_lots", "dealer_lots",
            "institutional_total_lots", "margin_balance_lots", "short_balance_lots")
    data = [(date, code, market, *(value.get(col) for col in cols))
            for code, value in rows.items()]
    if not data:
        return 0
    assignments = ", ".join(
        f"{col}=COALESCE(excluded.{col}, stock_flow_daily.{col})" for col in cols)
    conn.executemany(
        "INSERT INTO stock_flow_daily (date, code, market, " + ", ".join(cols) + ") "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date, code) DO UPDATE SET "
        "market=excluded.market, " + assignments,
        data,
    )
    conn.commit()
    return len(data)


def bulk_upsert_revenue(conn: sqlite3.Connection, market: str, rows: dict) -> int:
    """月營收批次入庫。rows＝{code: {...}}（sources.revenue.parse_monthly_revenue 的輸出）。

    PK 含每列自己的 year_month（而非呼叫端傳入單一日期）——同一次回應裡不同公司的申報
    進度可能跨月交界（多數已是新月份、少數還沒公告完就仍是舊月份），不能假設整批同月。
    缺 year_month 的列（理論上不會發生，防禦性略過）不落地，避免 PK 出現 NULL。
    """
    cols = ("market", "name", "industry", "report_date", "revenue", "revenue_prev_month",
            "revenue_last_year", "mom_pct", "yoy_pct", "revenue_accum",
            "revenue_accum_last_year", "accum_yoy_pct", "note")
    data = [
        (v.get("year_month"), code, market, v.get("name"), v.get("industry"),
         v.get("report_date"), v.get("revenue"), v.get("revenue_prev_month"),
         v.get("revenue_last_year"), v.get("mom_pct"), v.get("yoy_pct"),
         v.get("revenue_accum"), v.get("revenue_accum_last_year"), v.get("accum_yoy_pct"),
         v.get("note"))
        for code, v in rows.items() if v.get("year_month")
    ]
    if not data:
        return 0
    assignments = ", ".join(f"{col}=excluded.{col}" for col in cols)
    conn.executemany(
        "INSERT INTO stock_revenue_monthly (year_month, code, " + ", ".join(cols) + ") "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(year_month, code) DO UPDATE SET "
        + assignments,
        data,
    )
    conn.commit()
    return len(data)


def get_latest_revenue(conn: sqlite3.Connection, code: str, as_of: str | None = None) -> dict | None:
    """單一代號「已知最新」月營收列，可用 as_of 限定只看該日之前已公告的月份（不回推未來）。"""
    if as_of:
        row = conn.execute(
            "SELECT year_month, report_date, revenue, yoy_pct, accum_yoy_pct FROM stock_revenue_monthly "
            "WHERE code=? AND report_date<=? ORDER BY year_month DESC LIMIT 1",
            (code, as_of),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT year_month, report_date, revenue, yoy_pct, accum_yoy_pct FROM stock_revenue_monthly "
            "WHERE code=? ORDER BY year_month DESC LIMIT 1",
            (code,),
        ).fetchone()
    if not row:
        return None
    return {"year_month": row[0], "report_date": row[1], "revenue": row[2],
            "yoy_pct": row[3], "accum_yoy_pct": row[4]}


def revenue_yoy_map(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    """全市場 {代號: 最新已知營收年增%}，as_of 限定只看該日之前已公告的月份。

    用相關子查詢取每個代號「report_date<=as_of 範圍內 year_month 最大」的那一列——
    不是 MAX(yoy_pct)，是先選對月份再取那個月的 yoy_pct。
    """
    cutoff = as_of or "9999-99-99"
    rows = conn.execute(
        "SELECT r1.code, r1.yoy_pct FROM stock_revenue_monthly r1 "
        "WHERE r1.report_date<=? AND r1.year_month=("
        "  SELECT MAX(r2.year_month) FROM stock_revenue_monthly r2"
        "  WHERE r2.code=r1.code AND r2.report_date<=?)",
        (cutoff, cutoff),
    ).fetchall()
    return {code: yoy for code, yoy in rows if yoy is not None}


def set_stock_source_coverage(conn: sqlite3.Connection, date: str, market: str,
                              source: str, status: str, row_count: int = 0,
                              error: str | None = None, updated_at: str | None = None) -> None:
    """Record each market/source independently; one market never completes another.

    "holiday" is a terminal status distinct from "failed": it means both markets
    returned no quotes on this date across two separate backfill rounds, so it is
    treated as a confirmed non-trading day (see stock_flow.backfill) rather than a
    fetch error to retry.
    """
    if status not in ("complete", "failed", "holiday"):
        raise ValueError("coverage status must be complete, failed, or holiday")
    stamp = updated_at or datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO stock_source_coverage "
        "(date, market, source, status, row_count, attempts, last_error, updated_at) "
        "VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(date, market, source) DO UPDATE SET "
        "status=excluded.status, row_count=excluded.row_count, "
        "attempts=stock_source_coverage.attempts+1, last_error=excluded.last_error, "
        "updated_at=excluded.updated_at",
        (date, market, source, status, row_count, error, stamp),
    )
    conn.commit()


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
    cols = ", ".join(["code", "name", "added_at"] + WATCHLIST_COLS)
    return [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM watchlist ORDER BY added_at").fetchall()]


def add_watch(conn: sqlite3.Connection, code: str, name: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (code, name, added_at) VALUES (?,?,?)",
        (code, name, datetime.now().isoformat()),
    )
    conn.commit()


def remove_watch(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit()


def get_watch_estimate(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(WATCHLIST_COLS)} FROM watchlist WHERE code=?", (code,)
    ).fetchone()
    return dict(row) if row else None


def set_watch_estimate(conn: sqlite3.Connection, code: str, fields: dict) -> None:
    """存「輸入預估」面板的原始輸入。code 必須已在 watchlist（先 add_watch 才會呼叫這裡）。

    SET 子句只組呼叫端這次有帶的欄位，沒帶的既有值原封不動——跟 upsert_market_daily
    同一個「不得洗掉既有欄位」的原則，差別是這裡保證列已存在，用 UPDATE 即可，
    不需要 _on_conflict 的 INSERT-ON-CONFLICT 三段式。
    """
    cols = [c for c in WATCHLIST_COLS if c in fields]
    if not cols:
        return
    updates = ", ".join(f"{c}=?" for c in cols)
    conn.execute(
        f"UPDATE watchlist SET {updates} WHERE code=?",
        [fields[c] for c in cols] + [code],
    )
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
