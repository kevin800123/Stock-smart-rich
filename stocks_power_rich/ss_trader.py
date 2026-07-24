"""SS 操盤手規則引擎：把「Ss」方法論中可量化的規則轉成每日檢核與選股訊號。純函數。

方法論全文見 .claude/skills/ss-trader/SKILL.md（蒸餾自使用者提供的 LINE 對話實錄）。
誠實前提：這些是經驗法則的量化「近似」（例如 CSV 無季營收欄位，季季高以
月增/年增/累增同正近似），輸出僅為規則對照，非投資建議——前端須帶免責聲明。
"""
from datetime import date, timedelta

# 融資維持率的刻度。原本用固定的 135/165（抄底/偏熱），那是照外部常見指標的刻度訂的，
# 而我們的算法口徑不同：實測自家 42 天序列 min=168.0 > 165，於是 42 天全判「偏熱」、
# 另外兩檔永遠不可能觸發，整個檢核項等於沒有資訊量。
# 改用「相對損益兩平」——維持率的分母是融資金額，剛買進時 市值÷融資金額 = 1/融資成數，
# 所以兩平線可由成數直接推出，是我們自己定義下就成立的錨點，不必借外部數字。
# 一套比例規則同時適用兩個市場（成數不同，兩平線自然不同）。
MARGIN_CALL_LINE = 130.0         # 整戶維持率追繳線（法規常數，兩市場相同）
MARGIN_RATIO_TSE = 0.6           # 上市融資成數 → 兩平線 166.7%
MARGIN_RATIO_OTC = 0.5           # 上櫃融資成數 → 兩平線 200%
MARGIN_SUNK_DEEP = -20.0         # 相對兩平低於此％：融資深度套牢＝Ss 的抄底區（反指標）
MARGIN_HOT = 20.0                # 高於此％：融資整體獲利偏多，防高檔反殺
# 註：成數是一般股票的標準值，警示股／處置股更低，故兩平線是近似值。


def margin_breakeven(ratio: float) -> float:
    """融資成數 → 整體損益兩平的維持率（%）。剛融資買進、價格未動時的水準。"""
    return round(100 / ratio, 1)


def margin_verdict(maintenance, ratio: float) -> tuple[str, float, str]:
    """維持率 → (status, 相對兩平%, 說明)。兩個市場共用這一套判讀。

    方向與 VIX 同為反指標：維持率極低代表融資被斷頭清洗，Ss 視為抄底區而非利空
    （「散戶都怕的時候，就是很好的底部」）；極高代表融資戶普遍獲利、追高水位偏熱。
    """
    even = margin_breakeven(ratio)
    rel = round((maintenance - even) / even * 100, 1)
    near_call = maintenance < MARGIN_CALL_LINE * 1.08      # 追繳線上方約 8% 內
    if near_call or rel <= MARGIN_SUNK_DEEP:
        tail = "，已逼近追繳線" if near_call else ""
        return "bull", rel, (f"融資整體套牢 {abs(rel):.0f}%{tail}"
                             f"，斷頭清洗（Ss：可留意抄底，除非有重大事件）")
    if rel >= MARGIN_HOT:
        return "warn", rel, f"融資整體獲利 {rel:.0f}%，水位偏熱、防高檔反殺（兩平線 {even}%）"
    if rel < 0:
        return "neutral", rel, f"融資整體套牢 {abs(rel):.0f}%（兩平線 {even}%）"
    return "neutral", rel, f"融資整體帳面獲利 {rel:.0f}%（兩平線 {even}%）"
VIX_PANIC = 30.0            # 極度恐慌：散戶拋、大戶接（反指標偏多）
VIX_COMPLACENT = 15.0       # 過度樂觀：注意高點
TWD_DEPRECIATE_PCT = 0.5    # 台幣單日急貶 %（USD/TWD 上漲）→ 資金流出警訊
VOL_BURST = 1.5             # 今量 / 前5日均量 ≥ 1.5 視為爆量
VOL_SHRINK = 0.7            # ≤ 0.7 視為量縮
POS_HIGH = 0.7              # 收盤位於 60 日區間的相對位置（高檔/低檔判定）
POS_LOW = 0.3

# 每日例行檢查與不可量化心法（前端「操盤手」頁靜態呈現）
ROUTINE = {
    "pre": ["夜盤（對應美股，與日盤關聯不大）", "台幣匯率", "日韓股是否跌破前日低點",
            "美股相對強弱：小那 vs 小道瓊 vs 費半"],
    "intra": ["台幣匯率（急升→大盤不易跌；急貶→資金流出）", "高價股（漲停＝主力作多訊號）",
              "權值股關鍵價位是否有大單（主力守護處）"],
    "post": ["道瓊、小那", "融資融券", "期權 P/C 比", "主力分點進出"],
    "mind": ["任何線形都是主力畫出來的，籌碼（量、主力分點）最可靠",
             "散戶都怕的時候，就是很好的底部；行情總在全面毀滅中出現",
             "高點爆量要跑；急漲是賣點，不要追高",
             "期貨設好停損，不要凹單；大轉小賠要懂得停損",
             "試單獲利→加碼到基本持股→再加碼到最大部位；跌回加碼部位就減碼，一定讓自己有獲利帶走",
             "不要見漲說漲、見跌說跌；別用情緒做股票"],
}


def _item(key, name, status, value=None, note=""):
    return {"key": key, "name": name, "status": status, "value": value, "note": note}


def _last_valid(rows, col):
    for r in reversed(rows):
        v = r.get(col)
        if v is not None:
            return v
    return None


def market_checklist(rows: list[dict], osfut: dict | None = None,
                     night_ratio: float | None = None,
                     settlement_week: bool = False) -> list[dict]:
    """rows: market_daily 由舊到新（近 60 日）。回傳檢核項清單，
    每項 {key, name, status: bull|bear|warn|neutral|na, value, note}。
    bull/bear 指「對後市偏多/偏空」，warn 為需留意的中性警示。"""
    out = []

    # 1) 融資維持率：上市與上櫃分開判讀。兩者融資成數不同（60%/50%），兩平線 166.7% vs 200%，
    #    所以原始數字看起來接近時意義可能相反——併成單一「大盤」值會把這個訊號抵銷掉。
    for key, name, col, ratio in (
        ("margin_maint", "融資維持率（上市）", "margin_maintenance", MARGIN_RATIO_TSE),
        ("margin_maint_otc", "融資維持率（上櫃）", "otc_margin_maintenance", MARGIN_RATIO_OTC),
    ):
        mm = _last_valid(rows, col)
        if mm is None:
            out.append(_item(key, name, "na", note="尚無資料"))
            continue
        status, rel, note = margin_verdict(mm, ratio)
        out.append(_item(key, name, status, round(mm, 1), note))

    # 2) 融資 vs 大盤（近5日）：融資跌幅大於大盤 = 籌碼清洗，底部訊號
    recent = [r for r in rows if r.get("taiex") is not None and r.get("margin_balance") is not None][-5:]
    if len(recent) >= 2:
        t0, t1 = recent[0]["taiex"], recent[-1]["taiex"]
        m0, m1 = recent[0]["margin_balance"], recent[-1]["margin_balance"]
        tp = (t1 - t0) / t0 * 100 if t0 else None
        mp = (m1 - m0) / m0 * 100 if m0 else None
        if tp is not None and mp is not None:
            v = f"融資{mp:+.1f}% / 大盤{tp:+.1f}%"
            if mp < tp and mp < 0:
                out.append(_item("margin_wash", "融資 vs 大盤（5日）", "bull", v, "融資跌幅大於大盤＝籌碼清洗，底部訊號"))
            elif mp > 0 and tp < 0:
                out.append(_item("margin_wash", "融資 vs 大盤（5日）", "warn", v, "大盤跌融資增：散戶逆勢加碼，慎防多殺多"))
            else:
                out.append(_item("margin_wash", "融資 vs 大盤（5日）", "neutral", v, ""))
        else:
            out.append(_item("margin_wash", "融資 vs 大盤（5日）", "na", note="尚無資料"))
    else:
        out.append(_item("margin_wash", "融資 vs 大盤（5日）", "na", note="尚無資料"))

    # 3) VIX：極度恐慌＝散戶拋大戶接（反指標）
    vix = _last_valid(rows, "vix")
    if vix is None:
        out.append(_item("vix", "VIX 恐慌指數", "na", note="尚無資料"))
    elif vix >= VIX_PANIC:
        out.append(_item("vix", "VIX 恐慌指數", "bull", round(vix, 1), "極度恐慌：散戶拋、大戶接（反指標偏多）"))
    elif vix <= VIX_COMPLACENT:
        out.append(_item("vix", "VIX 恐慌指數", "warn", round(vix, 1), "過度樂觀，注意高點"))
    else:
        out.append(_item("vix", "VIX 恐慌指數", "neutral", round(vix, 1), ""))

    # 4) 台幣匯率（USD/TWD）：台幣升值＝熱錢流入、大盤不易跌；急貶＝資金流出
    twd = _last_valid(rows, "twd")
    twd_chg = _last_valid(rows, "twd_chg")
    if twd is None or twd_chg is None:
        out.append(_item("twd", "台幣匯率", "na", note="尚無資料（需回補國際指數）"))
    elif twd_chg < 0:
        out.append(_item("twd", "台幣匯率", "bull", f"{twd:.2f} ({twd_chg:+.2f}%)",
                         "台幣升值＝熱錢流入，Ss：台幣急升大盤就不會跌"))
    elif twd_chg >= TWD_DEPRECIATE_PCT:
        out.append(_item("twd", "台幣匯率", "bear", f"{twd:.2f} ({twd_chg:+.2f}%)",
                         "台幣急貶＝資金流出；Ss：崩盤時台幣會狂貶（vs 洗盤）"))
    else:
        out.append(_item("twd", "台幣匯率", "neutral", f"{twd:.2f} ({twd_chg:+.2f}%)", ""))

    # 5) 量能 × 位階：高檔爆量要跑；低檔爆量＝加速趕底/明確方向；量縮＝測支撐等突破
    tx_rows = [r for r in rows if r.get("taiex") is not None]
    vols = [r.get("turnover") for r in rows if r.get("turnover") is not None]
    if len(tx_rows) >= 10 and len(vols) >= 6:
        closes = [r["taiex"] for r in tx_rows][-60:]
        pos = (closes[-1] - min(closes)) / (max(closes) - min(closes)) if max(closes) > min(closes) else 0.5
        ratio = vols[-1] / (sum(vols[-6:-1]) / 5) if sum(vols[-6:-1]) else None
        if ratio is None:
            out.append(_item("volume", "量能判讀", "na", note="尚無資料"))
        elif ratio >= VOL_BURST and pos >= POS_HIGH:
            out.append(_item("volume", "量能判讀", "bear", f"量比 {ratio:.1f}x・位階 {pos:.0%}", "高點爆量要跑"))
        elif ratio >= VOL_BURST and pos <= POS_LOW:
            out.append(_item("volume", "量能判讀", "bull", f"量比 {ratio:.1f}x・位階 {pos:.0%}",
                             "低檔爆量：加速趕底，A波爆量才有明確方向"))
        elif ratio <= VOL_SHRINK:
            out.append(_item("volume", "量能判讀", "neutral", f"量比 {ratio:.1f}x・位階 {pos:.0%}",
                             "量縮：測支撐/橫盤整理，等待再突破"))
        else:
            out.append(_item("volume", "量能判讀", "neutral", f"量比 {ratio:.1f}x・位階 {pos:.0%}", ""))
    else:
        out.append(_item("volume", "量能判讀", "na", note="資料不足"))

    # 6) 資金流向：小那漲幅 > 小道瓊＝資金在科技股（利台股電子）；費半更強＝更明確
    flow = _fund_flow(osfut)
    out.append(flow)

    # 7) 台指期夜盤量比：夜盤對應美股；夜盤量能偏高＝美股主導、波動放大
    if night_ratio is None:
        out.append(_item("night", "台指期夜盤量比", "na", note="尚無資料"))
    elif night_ratio >= 0.6:
        out.append(_item("night", "台指期夜盤量比", "warn", f"{night_ratio:.0%}", "夜盤量能偏高：美股主導，波動放大"))
    else:
        out.append(_item("night", "台指期夜盤量比", "neutral", f"{night_ratio:.0%}", "夜盤對應美股，與日盤關聯不大"))

    # 8) 結算週提醒
    if settlement_week:
        out.append(_item("settle", "台指期月結算週", "warn", "本週結算",
                         "Ss：站在空方看多單、不想輸太多會拉近；用籌碼＋技術判月結算"))
    else:
        out.append(_item("settle", "台指期月結算週", "neutral", "非結算週", ""))
    return out


def _fund_flow(osfut: dict | None) -> dict:
    items = []
    for g in (osfut or {}).get("categories", []):
        if g.get("category") == "指數期貨":
            items = g.get("items", [])
            break
    chg = {}
    for it in items:
        n = it.get("name") or ""
        if "那斯達克" in n:
            chg["nq"] = it.get("chg_pct")
        elif "道瓊" in n:
            chg["dj"] = it.get("chg_pct")
        elif "費半" in n or "費城" in n:
            chg["sox"] = it.get("chg_pct")
    if chg.get("nq") is None or chg.get("dj") is None:
        return _item("fund_flow", "資金流向（小那 vs 小道）", "na", note="尚無海期資料")
    v = f"小那{chg['nq']:+.2f}% / 小道{chg['dj']:+.2f}%"
    if chg["nq"] > chg["dj"]:
        note = "小那強於小道＝資金在科技股，利台股電子"
        if chg.get("sox") is not None and chg["sox"] > chg["nq"]:
            note += "；費半又強於小那，更明確"
        return _item("fund_flow", "資金流向（小那 vs 小道）", "bull", v, note)
    return _item("fund_flow", "資金流向（小那 vs 小道）", "neutral", v, "小道較強＝資金偏傳產/防禦")


def is_settlement_week(d: date) -> bool:
    """台指期月結算＝當月第三個週三；判定 d 是否落在該結算日所在的週（一~日）。"""
    first = date(d.year, d.month, 1)
    offset = (2 - first.weekday()) % 7
    third_wed = first + timedelta(days=offset + 14)
    monday = d - timedelta(days=d.weekday())
    return monday <= third_wed <= monday + timedelta(days=6)


def red_engulfs_three_black(opens: list, closes: list) -> bool:
    """一紅吃三黑：前三根黑K（收<開）、今日紅K（收>開）且收盤 ≥ 三黑最高開盤（實體全吞）。
    Ss：「一紅吃三黑，轉強，可試單」。"""
    if len(opens) < 4 or len(closes) < 4:
        return False
    o, c = opens[-4:], closes[-4:]
    if any(v is None for v in o + c):
        return False
    blacks = all(c[i] < o[i] for i in range(3))
    red = c[3] > o[3]
    return blacks and red and c[3] >= max(o[:3])


def qoq_rising_picks(rows: list[dict]) -> list[dict]:
    """「季季高」近似選股：月增>0 ∧ 年增>0 ∧ 累增>0 ∧ 大戶增比>0，依蘭值排序。
    （CSV 無季營收欄位，以月增/年增/累增同正近似「季季高」——前端已註明）"""
    def ok(r):
        return all((r.get(k) or 0) > 0 for k in ("month_inc", "rev_yoy", "accum_inc", "big_holder_ratio"))

    picked = [r for r in rows if ok(r)]
    picked.sort(key=lambda r: (r.get("lan_value") or 0), reverse=True)
    return [{k: r.get(k) for k in ("code", "name", "close", "month_inc", "rev_yoy",
                                   "accum_inc", "big_holder_ratio", "lan_value")} for r in picked]
