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
                               mu_value_min, mu_score_min,
                               precomputed: dict | None = None,
                               signal_date: str | None = None) -> None:
    """自算選股 picks → signal_ledger（source='self_screen'），做前瞻績效追蹤。

    **為什麼要有這支**：訊號追蹤頁雖然移除了，記錄仍刻意持續（見 CLAUDE.md）——前瞻報酬
    不能事後回補，一旦回補就有存活者偏誤。自算選股先前完全沒被記錄，等於「自算到底有沒有比
    CSV 那套準」永遠拿不出證據；越晚接、能比較的歷史就越短。

    **signal_date 用市場最新交易日（market_daily），不是 CSV 快照日。** 原本跟著
    `MAX(snap_date)` 走是為了與 `record_daily_signals`（CSV 那套）落在同一天好對照，
    但那讓它跟著 CSV 一起凍——使用者已經決定停止每日上傳，實測也出現過 CSV 停在 08-28
    而市場資料已到 09-04。自算這條線本來就零 CSV 依賴，訊號日沒有理由由上傳頻率決定。
    改用市場日之後它每個交易日都記一筆，前瞻追蹤才不會因為忘記上傳就斷掉。

    `precomputed`＝`selfcheck.compute_self_screen` 的輸出（每日排程算好的那份），帶了就
    不重算——這支跟排程的快取本來就要算同一份東西，算兩次是純粹浪費。
    `universe` 由呼叫端給（來自 api.helpers 的公司基本資料月快取）——ledger 屬核心層，
    不反向 import api 層。

    只在每日排程呼叫，不掛在 CSV 上傳等請求路徑上：build_self_screen 是全市場計算，放進請求
    會拖慢回應（同「請求裡不要放無界時間的同步計算」那條教訓）。
    """
    # signal_date：提早計算（20:00 前）時 market_daily 還沒有今天那一列，照舊取最新列會把
    # 今天的名單記在昨天、進場價用昨天收盤——拿未來資訊回填過去。呼叫端知道算的是哪天就要明講。
    if signal_date:
        date_str = signal_date
    else:
        row = conn.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        if not (row and row[0]):
            return
        date_str = row[0]
    exists = conn.execute(
        "SELECT 1 FROM signal_ledger WHERE signal_date=? AND source='self_screen' LIMIT 1",
        (date_str,)
    ).fetchone()
    if exists:
        return
    result = selfcheck.build_self_screen(conn, date_str, universe, mu_value_min, mu_score_min,
                                         precomputed=precomputed)
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


def previous_self_screen_codes(conn: sqlite3.Connection, before: str) -> tuple[str | None, set]:
    """自算選股「新進榜」的比對基準：`before` 之前最近一個有記錄的訊號日，及那天入選的代號。

    來源是 signal_ledger（每日排程記下的正式名單），不是現算——前一天的全市場自算很貴，
    而且記下來的那份才是當天真正送出去的名單。取「有記錄的最近一天」而不是日曆上的前一天：
    週末、假日、排程漏跑的那天本來就沒有名單，跳過它們才是「上一份名單」。
    沒有更早的記錄回 (None, set())，呼叫端據此一檔都不標（分不出新舊時標滿 new 等於沒標）。"""
    row = conn.execute(
        "SELECT MAX(signal_date) FROM signal_ledger WHERE source='self_screen' AND signal_date < ?",
        (before,)).fetchone()
    prev = row[0] if row else None
    if not prev:
        return None, set()
    codes = {r[0] for r in conn.execute(
        "SELECT code FROM signal_ledger WHERE source='self_screen' AND signal_date=?", (prev,))}
    return prev, codes


def previous_custody_week_codes(conn: sqlite3.Connection, before: str) -> tuple[dict | None, set]:
    """自算選股「Week NEW」的比對基準：**上一個集保週期**內記下的所有自算名單的代號聯集。

    大戶增比／人數降比一週才變一次，所以「集保換週後才進榜」要對照的是上一整個集保週期，
    不是前一天。週期以 custody_dist 的週日期（週五）為界，**從該週五隔天起算**：週五那份名單
    是 17:30~19:30 提早算的，那時新一週的集保通常還沒進來，用的仍是舊資料，歸在前一個週期
    才對。週日期沿用 custody_compare_weeks（會略過逐檔回補造成的殘缺週），並且只看 `before`
    前一天以前的週——`before` 當天若剛好是週五，那一週的資料還不算數。

    回傳 ({"from": 期間內最早的名單日, "to": 最晚的名單日}, 代號集合)。沒有兩個可用的集保週、
    或前一週期內一份名單都沒有時回 (None, set())，呼叫端據此一檔都不標。"""
    from datetime import date as _date, timedelta
    from .db import custody_compare_weeks
    as_of = (_date.fromisoformat(before) - timedelta(days=1)).isoformat()
    weeks = custody_compare_weeks(conn, as_of)
    if len(weeks) < 2:
        return None, set()
    this_week, last_week = weeks
    rows = conn.execute(
        "SELECT signal_date, code FROM signal_ledger WHERE source='self_screen' "
        "AND signal_date > ? AND signal_date <= ?", (last_week, this_week)).fetchall()
    if not rows:
        return None, set()
    dates = sorted({r[0] for r in rows})
    return {"from": dates[0], "to": dates[-1]}, {r[1] for r in rows}


def annotate_new_entries(conn: sqlite3.Connection, result: dict, day: str) -> dict:
    """在 build_self_screen 的結果上標 `is_new`／`is_week_new`，並補 `new_vs`／`week_new_vs`／計數。

    網頁端點與 Telegram 新進榜推播**共用這一支**——兩邊對「新進榜」的定義必須是同一份，
    否則網頁標 NEW 的股推播裡卻沒有（或反過來），而且沒有人會發現。沒有比對基準就一檔都不標。"""
    new_vs, prev_codes = previous_self_screen_codes(conn, day)
    week_vs, week_codes = previous_custody_week_codes(conn, day)
    for r in result.get("rows", []):
        r["is_new"] = bool(new_vs) and r["code"] not in prev_codes
        r["is_week_new"] = bool(week_vs) and r["code"] not in week_codes
    result["new_vs"] = new_vs
    result["new_count"] = sum(1 for r in result.get("rows", []) if r["is_new"])
    result["week_new_vs"] = week_vs
    result["week_new_count"] = sum(1 for r in result.get("rows", []) if r["is_week_new"])
    return result


def update_ledger_returns(conn: sqlite3.Connection) -> None:
    """回填 5／10／20 日報酬＝訊號日之後第 N 個**交易日**的收盤相對進場價。

    兩個 2026-09 實際踩到的錯（設定頁上 filtered_picks 7,576 筆、自 07-13 起，5/10/20 日
    全顯示「尚未到期」，同期的杯柄卻有數字）：
    1. **代號後綴**：CSV 匯入的 filtered_picks 存 `2330.TW`／`6488.TWO`，官方日線 stock_ohlc
       存 `2330`。舊寫法 `code=?` 一筆都對不到，報酬永遠是空的。查價一律用去掉後綴的代號。
    2. **交易日要數日曆，不是數該檔的日線筆數**：舊寫法取「該檔 stock_ohlc 第 N 筆」，
       日線缺一天就安靜地量成第 N+1 天。交易日曆改取 market_daily（有加權指數的日子；
       當天早上指數還沒寫入的列不算），找出第 N 個交易日，再查該檔**那一天**的收盤；
       那天缺價就先留空、之後補到再算，不拿別天頂替。

    補算已記下的訊號**不是**事後回補偏誤：名單當天就記了，這裡只是補上後來的價格結果。
    """
    import bisect
    cal = [r[0] for r in conn.execute(
        "SELECT date FROM market_daily WHERE taiex IS NOT NULL ORDER BY date")]
    if not cal:
        return
    pending = conn.execute(
        "SELECT signal_date, code, source, entry_ref_price, ret5, ret10, ret20 "
        "FROM signal_ledger "
        "WHERE ret5 IS NULL OR ret10 IS NULL OR ret20 IS NULL"
    ).fetchall()
    for row in pending:
        sig_date, code, source, ref_price, r5, r10, r20 = row
        if not ref_price or ref_price <= 0:
            continue
        bare = str(code).split(".")[0]
        start = bisect.bisect_right(cal, sig_date)     # 訊號日之後的第一個交易日
        updates = {}
        for col, n, cur in (("ret5", 5, r5), ("ret10", 10, r10), ("ret20", 20, r20)):
            j = start + n - 1
            if cur is not None or j >= len(cal):
                continue
            px = conn.execute(
                "SELECT close FROM stock_ohlc WHERE code=? AND date=? AND close IS NOT NULL",
                (bare, cal[j])).fetchone()
            if px and px[0]:
                updates[col] = (px[0] - ref_price) / ref_price * 100

        if updates:
            cols = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [sig_date, code, source]
            conn.execute(
                f"UPDATE signal_ledger SET {cols} WHERE signal_date=? AND code=? AND source=?",
                vals
            )
    conn.commit()
