"""選股自算對照組裝：逐欄位比對 CSV 匯入值 vs App 自算值。只讀、不改 filtered_picks。

三個「已成熟」欄（rev_yoy/w55/big_holder_ratio）逐檔自算並與 CSV 對照；三個「待資料」欄
（est_profit/mu_score/mu_value）本案不自算、一律回 None＋原因（見 blocked_reason），前端顯示
「尚無自算」——正好把「要全放自己的還缺什麼」顯示出來。放獨立模組（import db + analysis）
避免 analysis↔db 反向依賴。
"""
import statistics

from . import analysis, db

FIELDS = ["rev_yoy", "w55", "big_holder_ratio", "holder_drop_ratio",
          "est_profit", "mu_score", "mu_value"]
LIVE_FIELDS = ["rev_yoy", "w55", "big_holder_ratio", "holder_drop_ratio"]
BLOCKED_REASON = {
    "est_profit": "需回補 6 個月歷史月營收（來源已建立，待回補後接線）",
    "mu_score": "需季報財務成熟 ＋ 投信/外資三日自算來源（尚未建立）",
    "mu_value": "同木質，另需自算本業PE（待推估EPS）",
}


def _snap_dates(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT snap_date FROM chip_snapshot ORDER BY snap_date DESC")]


def _self_w55(ohlc_code: dict | None, as_of: str):
    """該檔 OHLC 切到 as_of（含）之前，取序列餵 w55_signal；不足或無資料回 None。"""
    if not ohlc_code:
        return None
    ds, hs, ls, cs = ohlc_code["dates"], ohlc_code["highs"], ohlc_code["lows"], ohlc_code["closes"]
    hi, lo, cl = [], [], []
    for i, d in enumerate(ds):
        if d <= as_of:
            hi.append(hs[i]); lo.append(ls[i]); cl.append(cs[i])
    return analysis.w55_signal(hi, lo, cl)


def _custody_diag(conn, date: str | None) -> dict:
    """集保診斷：大戶增比要 as_of 前最近兩週都有 big400_pct 才算得出來。回報用了哪兩週、
    各週 big400_pct 非空檔數、兩週交集——讓 production 頁面直接看出集保覆蓋不足在哪
    （這正是「大戶增比只亮少數檔」的原因，非 code bug）。"""
    weeks = db.custody_compare_weeks(conn, date)   # 與 custody_change_map 同一組週（已略過殘缺週）
    if len(weeks) < 2:
        return {"weeks": weeks, "this_n": None, "prev_n": None, "overlap": 0}

    def _bset(w):
        return {r[0] for r in conn.execute(
            "SELECT code FROM custody_dist WHERE week=? AND big400_pct IS NOT NULL", (w,))}

    a, b = _bset(weeks[0]), _bset(weeks[1])
    return {"weeks": weeks, "this_n": len(a), "prev_n": len(b), "overlap": len(a & b)}


def build_selfcheck(conn, date: str | None) -> dict:
    dates = _snap_dates(conn)
    if date is None:
        date = dates[0] if dates else None
    rows_csv = conn.execute(
        "SELECT code, name, rev_yoy, w55, big_holder_ratio, holder_drop_ratio, est_profit "
        "FROM chip_snapshot WHERE snap_date=? ORDER BY code", (date,)).fetchall() if date else []

    yoy = db.revenue_yoy_map(conn, as_of=date) if date else {}
    custody = db.custody_change_map(conn, as_of=date) if date else {}
    ohlc = db.get_all_ohlc(conn, min_bars=55)

    out_rows = []
    for code, name, csv_yoy, csv_w55, csv_bhr, csv_hdr, csv_est in rows_csv:
        # chip_snapshot 的 code 帶 .TW/.TWO 後綴（XQ CSV 一律加 .TW），但自算來源
        # （月營收 revenue_yoy_map／集保 custody_change_map／OHLC get_all_ohlc）一律 bare
        # code——不去後綴，每一列 join 都 miss、全部「尚無自算」（production 實況）。
        # 去後綴是本 codebase 既有慣例（updater._financials_universe、api/* 皆 .split(".")[0]）。
        bare = str(code).split(".")[0]
        self_yoy = yoy.get(bare)
        self_w55 = _self_w55(ohlc.get(bare), date)
        cc = custody.get(bare) or {}          # 大戶增比／人數降比同一支 custody_change_map
        self_bhr = cc.get("big_holder_ratio")
        self_hdr = cc.get("holder_drop_ratio")
        vals = {
            "rev_yoy": (csv_yoy, self_yoy),
            "w55": (csv_w55, self_w55),
            "big_holder_ratio": (csv_bhr, self_bhr),
            "holder_drop_ratio": (csv_hdr, self_hdr),
            "est_profit": (csv_est, None),      # blocked（本案不自算）
            "mu_score": (None, None),           # blocked
            "mu_value": (None, None),           # blocked
        }
        fields = {}
        for f, (cv, sv) in vals.items():
            if f in BLOCKED_REASON:
                # 三個待資料欄：self 恆 None、status 恆 self_na（見 Interfaces 段的明文規定）
                # ——不走 selfcheck_compare，因為它對 (csv=None, self=None) 會判成 "csv_na"，
                # 而這裡真正的原因是「自算尚未建立」而非「CSV 端沒這欄」，兩者語意不同。
                fields[f] = {"csv": cv, "self": None, "status": "self_na"}
            else:
                fields[f] = {"csv": cv, "self": sv, "status": analysis.selfcheck_compare(f, cv, sv)}
        out_rows.append({"code": code, "name": name, "fields": fields})

    coverage = {}
    for f in FIELDS:
        diffs, computable = [], 0
        for r in out_rows:
            cell = r["fields"][f]
            if cell["self"] is not None:
                computable += 1
                if cell["csv"] is not None and f != "w55":
                    diffs.append(abs(cell["self"] - cell["csv"]))
        coverage[f] = {"computable": computable, "total": len(out_rows),
                       "median_abs_diff": round(statistics.median(diffs), 4) if diffs else None}

    return {
        "date": date, "dates": dates, "fields": FIELDS,
        "blocked_reason": dict(BLOCKED_REASON),
        "tolerances": {"SELFCHECK_TOL": analysis.SELFCHECK_TOL, "SELFCHECK_REL": analysis.SELFCHECK_REL},
        "rows": out_rows, "coverage": coverage,
        "custody_diag": _custody_diag(conn, date),
    }
