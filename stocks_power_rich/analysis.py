"""籌碼分析引擎：當日訊號榜、跨週比較、產業彙整。

訊號核心：大戶增比越高、人數降比越負（散戶減越多）→ 分數越高。
"""


import math

from . import patterns

W55_THRESHOLD = 50.0  # PercentR(55) 門檻：反推自 8 檔真實股票與 XQ CSV 的 W55 值全部對上（見 CLAUDE.md）


def w55_signal(highs: list, lows: list, closes: list) -> float | None:
    """W55 翻多訊號：PercentR(55) > 50 → 1.0（多方），否則 0.0（空方）；資料不足回 None。

    公式沿用 patterns.percent_r()（杯柄型態品質濾網也是同一支），不重算一份——
    兩者本質上是同一個「收盤在近 55 天高低區間的百分位」，只是使用情境不同
    （這裡是二元翻多旗標，杯柄那邊是 ≥70 的品質門檻）。
    """
    n = len(highs)
    if n < 55 or len(lows) != n or len(closes) != n:
        return None
    return 1.0 if patterns.percent_r(highs, lows, closes, 55) > W55_THRESHOLD else 0.0


def custody_change(cur: dict | None, prev: dict | None) -> dict:
    """大戶增比／人數降比：反推自使用者提供的 XS（見 tests/test_analysis_custody.py 的完整
    公式與真實資料驗證紀錄）。cur/prev＝相鄰兩週 custody_dist 列的 {big400_pct, total_holders}。

    大戶增比＝本週 big400_pct－上週（百分點差）；人數降比＝總持股人數相對變化%
    （負值＝股東人數減少＝籌碼集中，散戶減越多分數越高，同本模組頂部的訊號核心）。
    任一週缺列、或該欄位缺值都回 None；上週總持股人數為 0 時人數降比回 None（避免除以零），
    但大戶增比若兩週該欄位都有值仍照算（兩者是獨立算式，不互相拖累）。
    """
    out = {"big_holder_ratio": None, "holder_drop_ratio": None}
    if not cur or not prev:
        return out
    b400_now, b400_prev = cur.get("big400_pct"), prev.get("big400_pct")
    if b400_now is not None and b400_prev is not None:
        out["big_holder_ratio"] = round(b400_now - b400_prev, 2)
    th_now, th_prev = cur.get("total_holders"), prev.get("total_holders")
    if th_now is not None and th_prev:
        out["holder_drop_ratio"] = round((th_now - th_prev) / th_prev * 100, 2)
    return out


def estimate_quarterly_eps(monthly_revenue: list, gross_margin: list, opex: list,
                            tax: list, shares) -> float | None:
    """推估季EPS（XQ 的 Call_LE，CSV「推估獲利」欄位）：反推自使用者提供的 XS 原始碼

    （完整推導、單位換算依據、與台積電 2026Q1 真實數字驗證見 tests/test_analysis_call_le.py）。

    monthly_revenue：最新在前的月營收(億元)，至少 6 筆＝本季 3 個月＋上季 3 個月。
    gross_margin：最新在前的季毛利率(%)，至少 2 筆＝本季、上季。
    opex／tax：最新在前的季營業費用／所得稅費用(百萬元)，各至少 4 筆，XS 取近 4 季
    「最高」而非最新——保守估計，寧可高估成本也不要低估獲利。
    shares：發行股數（不是張數）。任一輸入不足筆數、缺值(None)、或 shares 非正 → 回 None
    （同 estimate_price_range／margin_maintenance 的「算不出回 None」慣例，不擲例外）。
    """
    if (monthly_revenue is None or len(monthly_revenue) < 6
            or any(v is None for v in monthly_revenue[:6])
            or gross_margin is None or len(gross_margin) < 2
            or any(v is None for v in gross_margin[:2])
            or opex is None or len(opex) < 4 or any(v is None for v in opex[:4])
            or tax is None or len(tax) < 4 or any(v is None for v in tax[:4])
            or shares is None or shares <= 0):
        return None

    this_q_revenue = sum(monthly_revenue[0:3])
    prior_q_revenue = sum(monthly_revenue[3:6])
    quarter_revenue = this_q_revenue * 100  # 億元 → 百萬元

    margin_now, margin_prev = gross_margin[0], gross_margin[1]
    if margin_now > margin_prev and this_q_revenue > prior_q_revenue:
        margin = round((margin_now + margin_prev) / 2, 2)
    else:
        margin = min(margin_now, margin_prev)

    max_opex = max(opex[0:4])
    max_tax = max(tax[0:4])

    net_income = (quarter_revenue * (margin / 100) - max_opex - max_tax) * 1_000_000  # 百萬元 → 元
    return round(net_income / shares, 3)


# ======================================================================
# 選股自算對照（picks self-check）：CSV 匯入值 vs App 自算值 的逐欄容差判定。
# 容差是「規則的單一權威版本」——經 /api/picks/selfcheck 揭露給設定/對照頁唯讀，
# 前端不得複製一份寫死（同 scoring-rules/bands 的規矩）。
# ======================================================================
SELFCHECK_TOL = {          # 絕對容差
    "rev_yoy": 0.5,        # 百分點
    "big_holder_ratio": 0.05,  # 百分點
    "holder_drop_ratio": 0.5,  # 人數降比（總持股人數相對變化%），比大戶增比鬆（值域較大）
    "mu_score": 1.0,       # 木質 0–19 小整數刻度
    "mu_value": 1.0,       # 木率
}
SELFCHECK_REL = {"est_profit": 0.05}   # 相對容差 |self-csv|/|csv| ≤ 5%
# 隨量級寬容 (下限張, 比例)：match if |self-csv| ≤ max(下限, 比例×|csv|)。外資/投信近3日是
# 張數淨額、量級跨度大（外資可達數萬張、投信常近 0）：實測我方自算＝官方 T86，與 XQ 的
# CSV 僅 feed 級 sub-% 差異（外資中位差 80 張 ≒ 0.3%），用固定 3 張絕對容差會把正確的大值
# 全判 diff。相對容差吸收大值噪音、下限守住小值，真正偏差很多（>比例）才 diff。
SELFCHECK_ABS_REL = {"trust_3d": (5.0, 0.02), "foreign_3d": (5.0, 0.02)}
# w55 不入表 → 完全相等才算一致


def selfcheck_compare(field: str, csv_v, self_v) -> str:
    """逐欄位比對 CSV 值與自算值 → "match" / "diff" / "self_na" / "csv_na"。

    自算值為 None（資料未成熟/無來源）→ "self_na"（前端標「尚無自算」，不算不一致）。
    CSV 值為 None（該檔 CSV 端也沒這欄）→ "csv_na"（無從比對）。
    w55 為二元、完全相等才 match；est_profit 走相對容差；trust_3d/foreign_3d 走隨量級
    寬容（max(下限, 比例×|csv|)）；其餘走絕對容差。
    """
    if csv_v is None:
        return "csv_na"
    if self_v is None:
        return "self_na"
    if field == "w55":
        return "match" if csv_v == self_v else "diff"
    if field in SELFCHECK_REL:
        denom = abs(csv_v)
        if denom == 0:
            return "match" if csv_v == self_v else "diff"
        return "match" if abs(self_v - csv_v) / denom <= SELFCHECK_REL[field] else "diff"
    if field in SELFCHECK_ABS_REL:
        floor, rel = SELFCHECK_ABS_REL[field]
        return "match" if abs(self_v - csv_v) <= max(floor, rel * abs(csv_v)) else "diff"
    tol = SELFCHECK_TOL.get(field)
    if tol is None:
        return "match" if csv_v == self_v else "diff"
    return "match" if abs(self_v - csv_v) <= tol else "diff"


def turnover_ma(values: list, n: int = 10) -> list:
    """逐點回傳「到該點為止最近 n 個有效值」的均值，長度與輸入相同、不足 n 筆給 None。

    None 是略過而非中斷視窗：turnover 偶因來源當日尚未發布而留 NULL（實測 3/41），
    若要求連續 n 筆非空，一個洞會讓後面整整 n 列都算不出來。取「最近 n 個有效值」
    可以跨過洞往前撈，代價是均量偶爾會橫跨多於 n 個日曆交易日——對量能水位這種
    緩變量是可接受的近似，總比整段留白好。同 ss_trader 既有量能檢查的做法。

    不足 n 筆一律 None，不用「有幾筆算幾筆」：拿 3 筆算出來的東西叫「10 日均量」
    是誤導，寧可留白。
    """
    out, seen = [], []
    for v in values:
        if v is not None:
            seen.append(v)
        out.append(round(sum(seen[-n:]) / n, 1) if len(seen) >= n else None)
    return out


def change_histogram(pcts: list[float], lo: int = -10, hi: int = 10) -> dict:
    """全市場漲跌幅分布。pcts＝各檔當日漲跌%清單 → 分整數桶的家數 + 摘要。

    每檔以 floor 分桶、夾在 [lo, hi]（±10% 是漲跌停上限，超界併入端桶，不長出 −11/+11）。
    桶的 `bucket` 是「下界」——−3 代表 [−3%, −2%)、0 代表 [0%, 1%)。桶架構固定 lo..hi 全列出
    （空桶也給 count 0），前端 X 軸才不會隨當日資料抖動。
    up/down/flat 依原值正負分（0% 算平盤），與漲跌家數同語意；avg 為全體平均漲跌幅。
    """
    buckets = {b: 0 for b in range(lo, hi + 1)}
    up = down = flat = 0
    for p in pcts:
        if p is None:
            continue
        b = max(lo, min(hi, math.floor(p)))
        buckets[b] += 1
        if p > 0:
            up += 1
        elif p < 0:
            down += 1
        else:
            flat += 1
    n = up + down + flat
    return {
        "buckets": [{"bucket": b, "count": buckets[b]} for b in range(lo, hi + 1)],
        "up": up, "down": down, "flat": flat, "n": n,
        "avg": round(sum(p for p in pcts if p is not None) / n, 2) if n else None,
    }


def top_movers(rows: list[dict], n: int = 8) -> dict:
    """漲幅／跌幅排行。rows＝[{code, name, chg_pct}] → {"up": [...], "down": [...], "n": 採計檔數}。

    **過濾寫在這裡而不是呼叫端**，因為「只取 4 碼普通股」正是這份排行的定義的一部分：
    原始報價快取約 14k 筆、6 碼權證佔絕大多數且天天漲跌停，不濾的話整個榜都是權證
    （與 change_histogram 同一條規則、同一個宇宙）。放在純函式裡才測得到。

    漲幅由高到低、跌幅由低到高；同漲跌幅時以代號排序，讓結果穩定不隨 dict 順序抖動。
    """
    ok = [r for r in rows
          if str(r.get("code", "")).isdigit() and len(str(r.get("code"))) == 4
          and r.get("chg_pct") is not None]
    # 漲幅榜只收真的上漲的、跌幅榜只收真的下跌的。**寧可少幾列，也不要湊滿**——
    # 全面下殺那天若把 −4% 的股票排進「漲幅排行」，那一列就是在說謊。
    up = sorted([r for r in ok if r["chg_pct"] > 0], key=lambda r: (-r["chg_pct"], r["code"]))
    down = sorted([r for r in ok if r["chg_pct"] < 0], key=lambda r: (r["chg_pct"], r["code"]))
    trim = lambda xs: [{"code": r["code"], "name": r.get("name") or r["code"],
                        "chg_pct": round(r["chg_pct"], 2)} for r in xs[:n]]
    return {"up": trim(up), "down": trim(down), "n": len(ok)}


def search_symbols(names: dict, q: str, n: int = 8) -> list[dict]:
    """全域搜尋用的代號／名稱比對。names＝{code: name} → [{code, name}]，最多 n 筆。

    兩種查法是**分開的**，不是同一種模糊比對：
    - 純數字 → 只比對**代號前綴**。打 `23` 的人要的是 2330／2317 這種開頭相符的，
      不是名字裡剛好有 23 的（實測「2330」若允許名稱包含，會撈到一堆無關的興櫃）。
    - 其他 → 只比對**名稱包含**（不分大小寫，涵蓋英文代號如 TSMC 這類別名）。
      代號不會誤中：使用者打中文時本來就不是在找代號。

    名稱比對的排序是「越接近完全相同越前面」：完全相等 → 開頭相符 → 包含，
    同級再依代號。若只用「包含」一種權重，打「台積」會讓 台積電 排在
    某檔名字更長、只是碰巧含這兩個字的股票後面（順序由 dict 決定＝不穩定）。
    """
    q = (q or "").strip()
    if not q:
        return []
    if q.isdigit():
        hits = [(code, nm) for code, nm in names.items() if str(code).startswith(q)]
        hits.sort(key=lambda kv: (len(str(kv[0])), str(kv[0])))
    else:
        low = q.lower()
        scored = []
        for code, nm in names.items():
            s = (nm or "").lower()
            if low not in s:
                continue
            rank = 0 if s == low else (1 if s.startswith(low) else 2)
            scored.append((rank, str(code), code, nm))
        scored.sort(key=lambda t: (t[0], t[1]))
        hits = [(code, nm) for _, _, code, nm in scored]
    return [{"code": str(code), "name": nm or str(code)} for code, nm in hits[:max(1, n)]]


def _num(v):
    return v if isinstance(v, (int, float)) and v is not None else 0.0


def _score(r: dict) -> float:
    # 大戶增比越高、人數降比越負（散戶減越多）得分越高
    return _num(r.get("big_holder_ratio")) - _num(r.get("holder_drop_ratio"))


def _flags(r: dict) -> dict:
    return {
        "w55_bull": _num(r.get("w55")) >= 1,
        "rev_growth": _num(r.get("rev_yoy")) > 0,
        "inst_buy": _num(r.get("trust_3d")) > 0 or _num(r.get("foreign_3d")) > 0,
    }


def daily_signals(rows: list[dict], top_n: int = 30) -> list[dict]:
    scored = []
    for r in rows:
        item = dict(r)
        item["score"] = round(_score(r), 4)
        item["flags"] = _flags(r)
        scored.append(item)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def attach_mu(row: dict) -> dict:
    """就地補上木質/木率並回傳同一個 dict（見「木質/木率」段）。

    財報分 Stage 1 用匯入的蘭質（lan_score）；籌碼四欄就取同一列（大戶/人數/投信/外資）。
    這是木質/木率進入每一列的唯一入口——在後端算、不在 JS 算（同 bands/Elliott 的規矩）。
    """
    ms = mu_score(row.get("lan_score"), row)
    row["mu_score"] = ms["score"] if ms else None
    mv = mu_value(ms["score"] if ms else None, row.get("lpe"))
    row["mu_value"] = mv["value"] if mv else None
    row["mu_raw"] = mv["raw"] if mv else None
    row["mu_quality_ok"] = mv["quality_ok"] if mv else None
    return row


def filtered_picks(rows: list[dict]) -> list[dict]:
    """選股篩選：W55=1（技術翻多）＋大戶增比>0＋營收年增>0＋推估EPS>0，再依蘭值由高到低排序。"""
    out = []
    for r in rows:
        if _num(r.get("w55")) < 1:
            continue
        if _num(r.get("big_holder_ratio")) <= 0:
            continue
        if _num(r.get("rev_yoy")) <= 0:
            continue
        if _num(r.get("est_profit")) <= 0:
            continue
        out.append(attach_mu(dict(r)))
    out.sort(key=lambda r: (r["lan_value"] if r.get("lan_value") is not None else float("-inf")), reverse=True)
    return out


def subindustry_counts(rows: list[dict]) -> list[dict]:
    """統計（已篩選個股）每個細產業的檔數，由多到少排序。"""
    groups: dict[str, int] = {}
    for r in rows:
        key = r.get("sub_industry") or "未分類"
        groups[key] = groups.get(key, 0) + 1
    out = [{"sub_industry": k, "count": v} for k, v in groups.items()]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


# CSV 產業欄 → 官方類股名 的別名（少數命名差異；其餘去前綴後即相同）
_SECTOR_ALIAS = {"化工": "化學", "航運業": "航運", "金融": "金融保險",
                 "文化創意": "其他", "農業科技業": "其他"}


def industry_to_sector(industry: str | None) -> str | None:
    """CSV 產業欄（如「上市半導體」/「上櫃IC」）→ 官方類股名（「半導體」）。"""
    if not industry:
        return None
    name = industry
    for p in ("上市", "上櫃"):
        if name.startswith(p):
            name = name[len(p):]
            break
    return _SECTOR_ALIAS.get(name, name)


def margin_maintenance(lots_by_code: dict, closes: dict, margin_value_yi,
                       short_lots_by_code: dict | None = None, short_margin_pct: float = 0.9) -> float | None:
    """大盤整戶擔保維持率(%) ≒ (融資市值＋融券擔保品市值＋融券保證金) ÷ (融資金額＋融券市值) ×100。

    官方完整定義：整戶維持率＝(融資買進證券市值＋融券賣出所得價金＋融券保證金) ÷ (融資金額＋融券股票現值)。
    lots_by_code/short_lots_by_code＝{代號: 融資/融券餘額(張)}、closes＝{代號: 收盤}、
    margin_value_yi＝官方融資金額(億，TWSE 直接公布)。TWSE 不公布個股「融券賣出原始價金」，
    故以「融券張數×1000×現價」近似（原始賣出價與現價有差時會有偏差）；融券保證金成數固定近似 90%
    （多數個股適用，警示股實際可能到 120%，此處未逐股區分）。只加總兩邊都有報價的代號，
    缺報價的部位不計入分子（比實際略低，屬保守估）。short_lots_by_code 缺省時完全退化為純融資版本。
    """
    if not margin_value_yi or margin_value_yi <= 0:
        return None
    margin_val = sum(lots * 1000 * closes[code]
                     for code, lots in lots_by_code.items() if code in closes and lots)
    short_val = sum(lots * 1000 * closes[code]
                    for code, lots in (short_lots_by_code or {}).items() if code in closes and lots)
    if margin_val <= 0 and short_val <= 0:
        return None
    numerator = margin_val + (1 + short_margin_pct) * short_val
    denominator = margin_value_yi * 1e8 + short_val
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def estimate_price_range(revenue, gross_margin_pct, opex, tax, shares,
                         pe_low, pe_mid, pe_high) -> dict | None:
    """自選股「輸入預估」面板：單季逐步推導 EPS 再年化 ×4，套本益比低/中/高算價位區間。

    推導順序對齊財報狗風格的階梯（使用者截圖）：
    最近三個月營收 ×毛利率(估)% → 毛利(估) → −營業費用(估) −所得稅(估) → 稅後淨利
    → ÷股數 → 單季本業EPS → ×4 年化 → ×本益比低/中/高 → 三個預估價位。

    任何一個輸入缺漏（None）或 shares 非正 → 回 None，前端顯示「—」（算不出就留白，
    不擲例外——跟 margin_maintenance 同一套風格）。稅後淨利/EPS 可以是負值（虧損季度），
    不擋，讓使用者自己判讀。
    """
    inputs = (revenue, gross_margin_pct, opex, tax, shares, pe_low, pe_mid, pe_high)
    if any(v is None for v in inputs):
        return None
    if shares <= 0:
        return None
    gross_profit = revenue * gross_margin_pct / 100
    net_income = gross_profit - opex - tax
    eps_quarter = net_income / shares
    eps_annual = eps_quarter * 4
    return {
        "eps_quarter": round(eps_quarter, 2),
        "eps_annual": round(eps_annual, 2),
        "low": round(eps_annual * pe_low, 2),
        "mid": round(eps_annual * pe_mid, 2),
        "high": round(eps_annual * pe_high, 2),
    }


# ======================================================================
# 木質 / 木率：自家版「籌碼 × 基本面」評分（見 CLAUDE.md「木質/木率」段）
#
# 三個純函數、三個常數。常數是「規則的單一權威版本」——供 /api/scoring-rules 與
# 設定頁引用，前端不得複製一份寫死（同 bands/Elliott「算式只有一份」的規矩）。
# 分三層：
#   lan_score —— 忠實還原蘭弦「蘭質」15 項（Stage 1 先鎖邏輯，季報源接上後才 wire）
#   mu_score  —— 木質＝財報分 × 本站已在算的籌碼（Stage 1 上線版，用匯入蘭質當財報分）
#   mu_value  —— 木率＝木質 ÷ 本業PE × 100，加品質閘避開價值陷阱
# ======================================================================

# 蘭質 15 項評分（忠實對照 XQ XScript Call_LQ；有序、含配分，合計 15）。
LAN_SCORE_ITEMS = [
    ("rev_yoy", "營收年增（營收[0]>[4]）", 1),
    ("rev_qoq", "營收季增（營收[0]>[1]）", 1),
    ("pretax_qoq", "稅前淨利季增（稅前淨利[0]>[1]）", 1),
    ("gm_yoy", "毛利率年增（毛利率[0]>[4]）", 1),
    ("ar_turn_yoy", "應收帳款週轉率年增（[0]>[4]）", 1),
    ("inv_turn_yoy", "存貨週轉率年增（[0]>[4]）", 1),
    ("turn_qoq", "應收+存貨週轉率季增（合計[0]>[1]）", 2),
    ("debt_down", "負債比率下降（[0]<[1] 或 [0]<[4]）", 1),
    ("ocf_up", "營運現金流雙增（[0]>[1] 且 [0]>[4]）", 1),
    ("ocf_gt_ni3", "近3季營運現金流 > 稅後淨利", 1),
    ("cash_content", "近4季現金含量>100%（Σ4現金流/Σ4淨利>1）", 2),
    ("roe_up", "ROE 雙增（[0]>[1] 且 [0]>[4]）", 1),
    ("capex_expand", "資本支出擴張（|近3季均|>|近8季均|）", 1),
]

# 每指標實際用到的季別 index（最新在前）；充足性守衛只查這些位置。
_LAN_USED = {
    "revenue": (0, 1, 4),
    "pretax_income": (0, 1),
    "gross_margin": (0, 4),
    "ar_turnover": (0, 1, 4),
    "inv_turnover": (0, 1, 4),
    "debt_ratio": (0, 1, 4),
    "ocf": (0, 1, 2, 3, 4),
    "net_income": (0, 1, 2, 3),
    "roe": (0, 1, 4),
    "capex": (0, 1, 2, 3, 4, 5, 6, 7),
}


def lan_score(financials: dict) -> dict | None:
    """蘭質＝6 財報紅綠燈 15 項評分（滿分 15），忠實還原 XQ XScript 的 Call_LQ。

    financials＝{指標key: 最新在前的季度數列}，[n]＝n 季前、[4]＝去年同季。key ↔ GetField：
    revenue(營業收入淨額)、pretax_income(稅前淨利)、gross_margin(營業毛利率)、
    ar_turnover(應收帳款週轉率)、inv_turnover(存貨週轉率)、debt_ratio(負債比率)、
    ocf(來自營運之現金流量)、net_income(本期稅後淨利)、roe(ROE)、capex(資本支出金額)。

    比較一律用 XScript 的嚴格 > / <（相等不給分）。任一指標缺 key、或它「用到的季別」越界/為
    None → 回 None（不可比的部分分數不如不給，同 estimate_price_range 風格）；未用到的 index
    缺值不影響。cash_content 遇 Σ4(淨利)=0 判 0 分而非擲例外；其餘照原式（含 XScript「負/負可能
    >1」的口徑怪癖，忠實對齊、非 bug——這函數的用途之一就是回算比對 CSV 的蘭質）。
    """
    for key, idxs in _LAN_USED.items():
        s = financials.get(key)
        if s is None:
            return None
        for i in idxs:
            if i >= len(s) or s[i] is None:
                return None

    rev = financials["revenue"]
    pt = financials["pretax_income"]
    gm = financials["gross_margin"]
    ar = financials["ar_turnover"]
    inv = financials["inv_turnover"]
    db = financials["debt_ratio"]
    ocf = financials["ocf"]
    ni = financials["net_income"]
    roe = financials["roe"]
    cap = financials["capex"]

    ni4 = ni[0] + ni[1] + ni[2] + ni[3]
    checks = {
        "rev_yoy": 1 if rev[0] > rev[4] else 0,
        "rev_qoq": 1 if rev[0] > rev[1] else 0,
        "pretax_qoq": 1 if pt[0] > pt[1] else 0,
        "gm_yoy": 1 if gm[0] > gm[4] else 0,
        "ar_turn_yoy": 1 if ar[0] > ar[4] else 0,
        "inv_turn_yoy": 1 if inv[0] > inv[4] else 0,
        "turn_qoq": 2 if (ar[0] + inv[0]) > (ar[1] + inv[1]) else 0,
        "debt_down": 1 if (db[0] < db[1] or db[0] < db[4]) else 0,
        "ocf_up": 1 if (ocf[0] > ocf[1] and ocf[0] > ocf[4]) else 0,
        "ocf_gt_ni3": 1 if (ocf[0] + ocf[1] + ocf[2]) > (ni[0] + ni[1] + ni[2]) else 0,
        "cash_content": 2 if (ni4 != 0 and (ocf[0] + ocf[1] + ocf[2] + ocf[3]) / ni4 > 1) else 0,
        "roe_up": 1 if (roe[0] > roe[1] and roe[0] > roe[4]) else 0,
        "capex_expand": 1 if abs(sum(cap[:3]) / 3) > abs(sum(cap[:8]) / 8) else 0,
    }
    return {"score": sum(checks.values()), "max": 15, "checks": checks}


# 木質的籌碼加成：四個訊號各 +1（boost-only、權重可調——設定頁揭露後再議是否改帶負分）。
# 每筆＝(chip_snapshot 欄位, 說明, 方向)；方向 "gt"＝>0 favourable、"lt"＝<0 favourable。
MU_CHIP_ITEMS = [
    ("big_holder_ratio", "大戶增比 > 0（大戶加碼）", "gt"),
    ("holder_drop_ratio", "人數降比 < 0（散戶退場、籌碼集中）", "lt"),
    ("trust_3d", "投信近3日淨買超 > 0", "gt"),
    ("foreign_3d", "外資近3日淨買超 > 0", "gt"),
]


def mu_score(lan_q, chips: dict | None = None) -> dict | None:
    """木質＝財報分（lan_q）＋ 本站已在算的籌碼加成（0–4），刻度 0–19。

    lan_q＝財報體質分：Stage 1 用匯入的蘭質（chip_snapshot.lan_score），Stage 2 改吃
    lan_score()["score"]。lan_q is None → None（財報是主幹）。籌碼是傾斜：缺欄位＝該訊號
    中性 0 分、不整檔回 None。刻度刻意從 15 換成 19（財報 15 + 籌碼 4）。
    """
    if lan_q is None:
        return None
    chips = chips or {}
    hits, bonus = {}, 0
    for key, _label, op in MU_CHIP_ITEMS:
        v = chips.get(key)
        ok = v is not None and ((op == "gt" and v > 0) or (op == "lt" and v < 0))
        hits[key] = ok
        if ok:
            bonus += 1
    return {"score": lan_q + bonus, "base": lan_q, "chip_bonus": bonus,
            "chips": hits, "max": 15 + len(MU_CHIP_ITEMS)}


# 木率的品質閘門檻（木質 0–19 刻度）：未達此分 → 不給「便宜」分，避開價值陷阱。
# 10 ≒「財報 8–9/15 ＋ 一點籌碼」。設定頁揭露、之後可調。
MU_QUALITY_FLOOR = 10


def mu_value(mu_q, lpe, quality_floor: float = MU_QUALITY_FLOOR) -> dict | None:
    """木率＝木質 ÷ 本業PE × 100（沿用蘭值公式，只換品質分子），加品質閘。

    mu_q＝木質、lpe＝本業PE（chip_snapshot.lpe），任一 None → None。lpe<=0 照 XScript
    對無效 PE 給 0。品質閘：木質未達 quality_floor 時 value 歸 0（不給便宜分），但 raw 仍
    保留、不隱藏便宜這件事——由呈現層決定要不要標「疑似價值陷阱」。
    """
    if mu_q is None or lpe is None:
        return None
    quality_ok = mu_q >= quality_floor
    if lpe <= 0:
        return {"value": 0, "raw": 0, "quality_ok": quality_ok, "reason": "本業PE≤0"}
    raw = round(mu_q / lpe * 100)
    return {"value": raw if quality_ok else 0, "raw": raw, "quality_ok": quality_ok}


DEFAULT_TRADE_FEE = 0.585  # 來回費用%＝買賣手續費 0.1425%×2 ＋ 賣出證交稅 0.3%


def trade_stats(trades: list[dict], closes: dict | None = None,
                taiex_by_date: dict | None = None) -> dict:
    """交易帳本統計（純函數）。trades＝db.list_trades 列；closes＝{代號: 最新收盤}
    供未平倉估值；taiex_by_date＝{日期: 加權收盤} 供同期大盤對照。

    每筆皆為「淨值」：報酬%＝毛報酬% − 來回費用%（fee_pct，NULL 用預設 0.585）；
    未平倉以最新收盤估、同樣先扣費用（出場終究要付，避免高估）。
    同期大盤取「≤ 該日的最近一個交易日」加權值；未平倉的出場參考日＝最新一個交易日。
    統計只算已平倉：期望值＝勝率×平均賺%＋(1−勝率)×平均賠%（每筆交易的期望報酬）。
    """
    closes = closes or {}
    tx_dates = sorted((taiex_by_date or {}).keys())

    def _taiex_at(ds):
        """≤ ds 的最近交易日加權值（非交易日進出場也對得到基準）。"""
        prior = [d for d in tx_dates if d <= ds]
        return taiex_by_date[prior[-1]] if prior else None

    enriched, wins, losses, alphas, realized, unrealized = [], [], [], [], 0, 0
    for t in trades:
        fee = t.get("fee_pct") if t.get("fee_pct") is not None else DEFAULT_TRADE_FEE
        closed = t.get("exit_price") is not None
        mark = t["exit_price"] if closed else closes.get(t["code"])
        e = dict(t, status="closed" if closed else "open",
                 mark=None if closed else mark,
                 net_pct=None, pnl=None, mkt_pct=None, alpha=None)
        if mark is not None and t.get("entry_price"):
            cost = t["entry_price"] * t["shares"]
            net_pct = (mark - t["entry_price"]) / t["entry_price"] * 100 - fee
            pnl = (mark - t["entry_price"]) * t["shares"] - cost * fee / 100
            e["net_pct"], e["pnl"] = round(net_pct, 2), round(pnl)
            m0 = _taiex_at(t["entry_date"]) if t.get("entry_date") else None
            m1 = _taiex_at(t["exit_date"]) if closed else (
                taiex_by_date[tx_dates[-1]] if tx_dates else None)
            if m0 and m1:
                mkt = (m1 - m0) / m0 * 100
                e["mkt_pct"], e["alpha"] = round(mkt, 2), round(net_pct - mkt, 2)
            if closed:
                realized += pnl
                (wins if net_pct > 0 else losses).append(net_pct)
                if e["alpha"] is not None:
                    alphas.append(net_pct - (m1 - m0) / m0 * 100)
            else:
                unrealized += pnl
        enriched.append(e)
    n = len(wins) + len(losses)
    win_rate = len(wins) / n * 100 if n else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    expectancy = ((win_rate / 100) * (avg_win or 0) + (1 - win_rate / 100) * (avg_loss or 0)) \
        if n else None
    return {"trades": enriched, "stats": {
        "closed_n": n, "open_n": sum(1 for e in enriched if e["status"] == "open"),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "payoff": round(avg_win / abs(avg_loss), 2) if (avg_win and avg_loss) else None,
        "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "realized_pnl": round(realized), "open_pnl": round(unrealized),
        "avg_alpha": round(sum(alphas) / len(alphas), 2) if alphas else None,
    }}


def picks_by_sector(picks: list[dict], sector_chg: dict) -> list[dict]:
    """把選股清單依官方類股分組，附該類股當日漲跌%，依漲跌%由強到弱排序。

    sector_chg：{官方類股名: 當日漲跌%}。回傳 [{sector, chg_pct, count, stocks:[...]}]。
    """
    groups: dict[str, list[dict]] = {}
    for p in picks:
        sec = industry_to_sector(p.get("industry"))
        if sec:
            groups.setdefault(sec, []).append(p)
    out = [{"sector": s, "chg_pct": sector_chg.get(s), "count": len(st), "stocks": st}
           for s, st in groups.items()]
    out.sort(key=lambda g: (g["chg_pct"] is None, -(g["chg_pct"] or 0)))
    return out


def pick_weekly_pair(dates: list[str]) -> tuple[str, str | None]:
    """跨週比較的日期配對：本期＝最新快照；上期＝最新一筆「ISO 週早於本期」的快照
    （即上週或更早的最後一份 CSV）。集保週資料一週一更，日對日比較的集保Δ沒有意義。
    全部同週（尚無上週資料）退回前一筆至少能比；單筆回 (d, None)。dates 需遞增排序。"""
    from datetime import date as _date
    if not dates:
        return ("", None)
    this = dates[-1]
    if len(dates) < 2:
        return (this, None)
    this_week = _date.fromisoformat(this).isocalendar()[:2]
    for d in reversed(dates[:-1]):
        if _date.fromisoformat(d).isocalendar()[:2] < this_week:
            return (this, d)
    return (this, dates[-2])


def weekly_comparison(this_rows: list[dict], last_rows: list[dict]) -> dict:
    """比較本週最新 vs 上週最新快照，標記每檔 新進榜/加速/持平/退榜 與集保大戶持股 Δ。"""
    last = {r["code"]: r for r in last_rows}
    this = {r["code"]: r for r in this_rows}
    stocks = []
    for code, r in this.items():
        prev = last.get(code)
        custody_delta = (
            round(_num(r.get("custody")) - _num(prev.get("custody")), 4) if prev else None
        )
        if not prev:
            status = "新進榜"
        elif _num(r.get("big_holder_ratio")) > _num(prev.get("big_holder_ratio")):
            status = "加速"
        else:
            status = "持平"
        stocks.append({**r, "custody_delta": custody_delta, "status": status})
    for code, prev in last.items():
        if code not in this:
            stocks.append({**prev, "custody_delta": None, "status": "退榜"})
    return {"stocks": stocks}


def weekly_highlights(rows: list[dict], min_count: int = 10, top_n: int = 5) -> dict:
    """週報卡用的「重點類股」與「本週前五個股」。

    類股先以 industry_to_sector 正規化再分組——CSV 的產業欄帶「上市/上櫃」前綴，
    不合併會把同一個半導體拆成兩筆互相稀釋。並要求至少 min_count 檔才入榜：
    平均分數對樣本數很敏感，實測只有 5 檔的「其他電子」就能衝到第一名。

    個股一律取全體依 _score 排序，不限「加速」狀態——實測某週「加速」是 0 檔，
    沿用該篩選會長期是空榜。
    """
    groups: dict[str, list[float]] = {}
    for r in rows:
        key = industry_to_sector(r.get("industry")) or "未分類"
        groups.setdefault(key, []).append(_score(r))
    sectors = [{"sector": k, "count": len(v), "avg_score": round(sum(v) / len(v), 4)}
               for k, v in groups.items() if len(v) >= min_count]
    sectors.sort(key=lambda x: -x["avg_score"])

    scored = sorted(rows, key=lambda r: -_score(r))[:top_n]
    stocks = [{"code": r.get("code"), "name": r.get("name"),
               "score": round(_score(r), 4),
               "big_holder_ratio": r.get("big_holder_ratio"),
               "holder_drop_ratio": r.get("holder_drop_ratio"),
               "sector": industry_to_sector(r.get("industry"))} for r in scored]
    return {"sectors": sectors[:top_n], "stocks": stocks}


def industry_aggregate(rows: list[dict]) -> list[dict]:
    """依產業分組，算平均訊號分數並由高至低排名。"""
    groups: dict[str, list[float]] = {}
    for r in rows:
        key = r.get("industry") or "未分類"
        groups.setdefault(key, []).append(_score(r))
    out = [
        {"industry": k, "count": len(v), "avg_score": round(sum(v) / len(v), 4)}
        for k, v in groups.items()
    ]
    out.sort(key=lambda x: x["avg_score"], reverse=True)
    return out
