"""選股自算對照組裝：逐欄位比對 CSV 匯入值 vs App 自算值。只讀、不改 filtered_picks。

三個「已成熟」欄（rev_yoy/w55/big_holder_ratio）逐檔自算並與 CSV 對照；三個「待資料」欄
（est_profit/mu_score/mu_value）本案不自算、一律回 None＋原因（見 blocked_reason），前端顯示
「尚無自算」——正好把「要全放自己的還缺什麼」顯示出來。放獨立模組（import db + analysis）
避免 analysis↔db 反向依賴。
"""
import statistics

from . import analysis, db

FIELDS = ["rev_yoy", "w55", "big_holder_ratio", "holder_drop_ratio",
          "trust_3d", "foreign_3d", "lan_score", "est_profit", "mu_score", "mu_value"]
LIVE_FIELDS = list(FIELDS)   # 10 欄全部自算（est_profit/mu_score/mu_value 於季報回補完成後解鎖）
BLOCKED_REASON: dict = {}   # 10 欄全部接上自算（財報分/木質/木率/推估EPS 於季報回補完成後解鎖）


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


def _row_self(code, shares, yoy, custody, inst3d, fin, mrev, ohlc, date) -> dict:
    """單檔 10 個自算值——selfcheck 對照與 self-screen 選股**共用同一份權威計算**
    （同 bands/Elliott「算式只能有一份」的規矩）。

    code 可帶 .TW/.TWO 後綴，內部去後綴 join 各自算來源（一律 bare）。shares＝發行股數：
    selfcheck 傳「CSV 股本×1e7」維持與 CSV 對照口徑一致（不破壞已驗證的中位差 0）；
    self-screen 傳真實已發行股數。缺料的欄位回 None（各自的守衛負責）。
    """
    bare = str(code).split(".")[0]
    cc = custody.get(bare) or {}
    self_bhr, self_hdr = cc.get("big_holder_ratio"), cc.get("holder_drop_ratio")
    i3 = inst3d.get(bare) or {}
    self_t3, self_f3 = i3.get("trust_3d"), i3.get("foreign_3d")
    ls = analysis.lan_score(fin.get(bare) or {})
    self_lan = ls["score"] if ls else None
    ms = analysis.mu_score(self_lan, {"big_holder_ratio": self_bhr, "holder_drop_ratio": self_hdr,
                                      "trust_3d": self_t3, "foreign_3d": self_f3})
    self_mu = ms["score"] if ms else None
    f = fin.get(bare) or {}
    _m = lambda xs: [x / 1000 if x is not None else None for x in xs]   # 仟元 → 百萬元
    self_est = analysis.estimate_quarterly_eps(
        mrev.get(bare, []), f.get("gross_margin") or [], _m(f.get("opex") or []),
        _m(f.get("income_tax") or []), shares)
    oc = ohlc.get(bare) or {}
    price = oc["closes"][-1] if oc.get("closes") else None
    self_lpe = (price / (self_est * 4)) if (self_est and self_est > 0 and price) else None
    mv = analysis.mu_value(self_mu, self_lpe)
    return {
        "rev_yoy": yoy.get(bare),
        "w55": _self_w55(ohlc.get(bare), date),
        "big_holder_ratio": self_bhr,
        "holder_drop_ratio": self_hdr,
        "trust_3d": self_t3,
        "foreign_3d": self_f3,
        "lan_score": self_lan,
        "est_profit": self_est,
        "mu_score": self_mu,
        "mu_value": mv["value"] if mv else None,
    }


def build_selfcheck(conn, date: str | None) -> dict:
    dates = _snap_dates(conn)
    if date is None:
        date = dates[0] if dates else None
    rows_csv = conn.execute(
        "SELECT code, name, rev_yoy, w55, big_holder_ratio, holder_drop_ratio, "
        "trust_3d, foreign_3d, lan_score, est_profit, capital "
        "FROM chip_snapshot WHERE snap_date=? ORDER BY code", (date,)).fetchall() if date else []

    yoy = db.revenue_yoy_map(conn, as_of=date) if date else {}
    custody = db.custody_change_map(conn, as_of=date) if date else {}
    inst3d = db.institutional_3d_map(conn, as_of=date) if date else {}
    # 財報分（蘭質）＋Call_LE（推估EPS）的季報原料：_LAN_USED ∪ {opex, income_tax}
    fin = db.get_financials_bulk(conn, list(analysis._LAN_USED) + ["opex", "income_tax"]) if date else {}
    mrev = db.monthly_revenue_bulk(conn, as_of=date) if date else {}   # {代號: [月營收億元,新到舊]}
    ohlc = db.get_all_ohlc(conn, min_bars=55)

    out_rows = []
    for code, name, csv_yoy, csv_w55, csv_bhr, csv_hdr, csv_t3, csv_f3, csv_lan, csv_est, csv_cap in rows_csv:
        # chip_snapshot 的 code 帶 .TW/.TWO 後綴（XQ CSV 一律加 .TW），去後綴 join 各自算來源
        # （一律 bare code）——見 _row_self。shares 用 CSV 股本×1e7，維持與 CSV 對照口徑一致。
        shares = csv_cap * 1e7 if csv_cap else None
        s = _row_self(code, shares, yoy, custody, inst3d, fin, mrev, ohlc, date)
        vals = {
            "rev_yoy": (csv_yoy, s["rev_yoy"]),
            "w55": (csv_w55, s["w55"]),
            "big_holder_ratio": (csv_bhr, s["big_holder_ratio"]),
            "holder_drop_ratio": (csv_hdr, s["holder_drop_ratio"]),
            "trust_3d": (csv_t3, s["trust_3d"]),
            "foreign_3d": (csv_f3, s["foreign_3d"]),
            "lan_score": (csv_lan, s["lan_score"]),   # 財報分：自算 lan_score vs CSV 蘭質
            "est_profit": (csv_est, s["est_profit"]),  # 推估EPS：自算 Call_LE vs CSV 推估獲利
            "mu_score": (None, s["mu_score"]),        # 木質：自算（CSV 無此欄 → csv_na），本站自有分數
            "mu_value": (None, s["mu_value"]),        # 木率：自算（CSV 無此欄 → csv_na）
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
        "tolerances": {"SELFCHECK_TOL": analysis.SELFCHECK_TOL, "SELFCHECK_REL": analysis.SELFCHECK_REL,
                       "SELFCHECK_ABS_REL": analysis.SELFCHECK_ABS_REL},
        "rows": out_rows, "coverage": coverage,
        "custody_diag": _custody_diag(conn, date),
    }


def build_self_screen(conn, date, universe: dict, mu_value_min, mu_score_min) -> dict:
    """自算籌碼/基本選股：**全市場自算池**（零 CSV 依賴），與 build_selfcheck 共用 _row_self。

    universe＝`{code: {sector, name, shares}}`（由端點用 _industry_map/_otc_industry 組好傳入，
    讓網路／月快取留在 api 層、selfcheck 保持不依賴 api.helpers）。回傳：
    - heatmap：大戶增比>0 的股依「類股」分組，版塊大小＝本週成交額、顏色資料＝avg 大戶增比、
      WoW＝本週 vs 上週同期成交額（見 db.weekly_amounts）。
    - rows：7 條件（見 analysis.screen_pass）篩選後依木率(mu_value)由大到小，欄位同 selfcheck。
    - coverage：universe / 大戶增比>0 / 有成交額 / 入選 的檔數，讓覆蓋缺口不被靜默吃掉。
    """
    yoy = db.revenue_yoy_map(conn, as_of=date) if date else {}
    custody = db.custody_change_map(conn, as_of=date) if date else {}
    inst3d = db.institutional_3d_map(conn, as_of=date) if date else {}
    fin = db.get_financials_bulk(conn, list(analysis._LAN_USED) + ["opex", "income_tax"]) if date else {}
    mrev = db.monthly_revenue_bulk(conn, as_of=date) if date else {}
    ohlc = db.get_all_ohlc(conn, min_bars=55)
    wk = db.weekly_amounts(conn, date) if date else {}
    submap = db.sub_industry_map(conn) if date else {}   # 細分類（子產業）：XQ CSV 才有，退回官方類股

    groups: dict = {}
    picked, big_pos, with_amount, with_mcap, with_subindustry = [], 0, 0, 0, 0
    for code, info in universe.items():
        s = _row_self(code, info.get("shares"), yoy, custody, inst3d, fin, mrev, ohlc, date)
        gk = submap.get(code) or info.get("sector") or "未分類"   # 細分類優先、退回官方類股
        bhr = s["big_holder_ratio"]
        if bhr is not None and bhr > 0:
            big_pos += 1
            amt = wk.get(code)
            if amt:                       # 只納入本週有成交額的（缺料不靜默併進版塊）
                with_amount += 1
                if submap.get(code):
                    with_subindustry += 1
                # 大戶淨買進金額估計＝大戶增比% × 市值（股數×收盤）——大戶當週實際加碼的錢。
                # 缺股數或收盤（無 55 根 K）就算不出，不計入該類股 buy_value（with_mcap 揭露缺口）。
                shares = info.get("shares")
                closes = (ohlc.get(code) or {}).get("closes")
                price = closes[-1] if closes else None
                buy_value = (bhr / 100 * shares * price) if (shares and price) else None
                if buy_value is not None:
                    with_mcap += 1
                g = groups.setdefault(gk, {"this": 0.0, "prev": 0.0, "bhr": 0.0,
                                           "buy": 0.0, "n": 0, "children": []})
                g["this"] += amt["this"]
                g["prev"] += amt["prev"]
                g["bhr"] += bhr
                g["n"] += 1
                if buy_value is not None:
                    g["buy"] += buy_value
                g["children"].append({"code": code, "name": info.get("name") or code,
                                      "amount": amt["this"], "big_holder_ratio": bhr,
                                      "buy_value": round(buy_value) if buy_value is not None else None})
        if analysis.screen_pass(s, mu_value_min, mu_score_min):
            picked.append({"code": code, "name": info.get("name") or code,
                           "sector": gk, "vals": s})   # 用細分類，drill-down 才對得上泡泡

    heatmap = []
    for sector, g in groups.items():
        wow = round((g["this"] - g["prev"]) / g["prev"] * 100, 1) if g["prev"] else None
        g["children"].sort(key=lambda x: (x["buy_value"] or 0), reverse=True)
        heatmap.append({"sector": sector, "buy_value": round(g["buy"]),
                        "amount": round(g["this"]), "prev_amount": round(g["prev"]),
                        "wow_pct": wow, "avg_big_holder": round(g["bhr"] / g["n"], 2),
                        "count": g["n"], "children": g["children"]})
    heatmap.sort(key=lambda x: x["buy_value"], reverse=True)   # 依大戶淨買進金額由大到小
    picked.sort(key=lambda r: (r["vals"]["mu_value"] if r["vals"]["mu_value"] is not None
                               else float("-inf")), reverse=True)
    return {
        "date": date,
        "thresholds": {"mu_value_min": mu_value_min, "mu_score_min": mu_score_min},
        "heatmap": heatmap, "rows": picked,
        "coverage": {"universe": len(universe), "big_holder_pos": big_pos,
                     "with_amount": with_amount, "with_mcap": with_mcap,
                     "with_subindustry": with_subindustry, "picked": len(picked)},
    }
