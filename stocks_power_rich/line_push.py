"""LINE 官方帳號推播：盤後速報/完整版訊息組裝、broadcast 與 webhook 回覆。

訊息組裝為純函數（單元測試）；網路呼叫為 thin wrapper，無 token 時安全降級。
broadcast 推給官方帳號的全部好友（單人自用帳號＝只推給自己），免查 userId。
版型：逐行條列＋全形空白對齊（LINE 非等寬字體，全形空白對中文標籤最穩）＋分區線。

**額度**：broadcast/push 按「收訊人數」計入每月免費額度（好友 6 人＝一則扣 6 則），
但 **reply（回覆使用者訊息）完全不計額度、無上限**——所以主動查詢一律走 reply_text。
"""
import base64
import hashlib
import hmac
import json
import re

import httpx

BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MAX_LEN = 4900  # LINE 單則文字上限 5000，留餘裕
SEP = "━━━━━━━━━━━━"

# 國際行情列：(欄位, 顯示名, 小數位)；日圓＝美元兌日圓（USD/JPY）
_INTL_FIELDS = (("n225", "日經", 0), ("kospi", "韓股", 0), ("gold", "黃金", 0),
                ("jpy", "日圓", 2), ("btc", "比特幣", 0))


# Gemini 回的是 markdown，但 LINE（純文字與 Flex 都是）不渲染 markdown，
# 那些 **粗體**／### 標題／--- 分隔線會原樣顯示成雜訊（實測一篇週報 AI 文 70 個 `**`）。
_MD_RULE = re.compile(r"^\s*[-*_]{3,}\s*$", re.M)
_MD_HEAD = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$", re.M)
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_BULLET = re.compile(r"^(\s*)[*+-]\s+", re.M)
_BLANKS = re.compile(r"\n{3,}")


def strip_markdown(text: str | None) -> str:
    """markdown → LINE 可讀的純文字。標題改標「▍」、項目改「・」，層級不靠字重也看得出來。"""
    if not text:
        return ""
    out = _MD_RULE.sub("", text)
    out = _MD_HEAD.sub(r"▍\1", out)
    out = _MD_BOLD.sub(r"\1", out)
    # 縮排的子項目用「　- 」，頂層用「・」——LINE 沒有粗體可用，只能靠符號分層級
    out = _MD_BULLET.sub(lambda m: "　- " if m.group(1) else "・", out)
    return _BLANKS.sub("\n\n", out).strip()


_AI_LABEL = re.compile(r"^[・•]\s*([^：:]{1,8})[：:]\s*(.*)$")
_AI_HEAD = re.compile(r"^▍\s*(.+)$")


def split_ai_sections(text: str | None) -> list[tuple[str | None, str]]:
    """AI 文 → [(欄目, 內容)]。

    盤後 AI 每行都是「・欄目：內容」的固定結構（國際／大盤／法人／期貨／情緒／族群／結論），
    整段塞成一個 text 等於把這個現成的骨架丟掉。抽出欄目名，掃視時才跳得到「法人」或「結論」。
    認不出欄目的行回 (None, 整行)；空行略過。
    """
    out: list[tuple[str | None, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _AI_LABEL.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
            continue
        h = _AI_HEAD.match(line)
        out.append((h.group(1).strip(), "") if h else (None, line))
    return out


def _fmt(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def _signed(v, d=2):
    return "—" if v is None else f"{v:+,.{d}f}"


def _pad(label: str, width: int) -> str:
    """中文標籤補全形空白到等寬。"""
    return label + "　" * max(0, width - len(label))


def _px_line(label: str, price, chg) -> str:
    """「標籤 價格 ▲漲跌（±%）」一行；% 由昨值回推。"""
    line = f"{_pad(label, 4)} {_fmt(price)}"
    if chg is not None and price is not None:
        arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
        base = price - chg
        pct = round(chg / base * 100, 2) if base else None
        line += f" {arrow}{_fmt(abs(chg))}"
        if pct is not None:
            line += f"（{_signed(pct)}%）"
    return line


def compose_daily_brief(row: dict, sectors: list, watch: list,
                        ai_text: str = "", full: bool = False,
                        tsmc: dict | None = None, prev: dict | None = None,
                        cup: dict | None = None) -> str:
    """組盤後訊息。full=True 加融資券（21:00 完整版）；速報（16:00）不含。

    row＝market_daily 最新列（含國際行情欄位）；prev＝前一交易日列（法人/期貨附「昨」對照）；
    sectors＝[{name, chg_pct}]；watch＝[{code, name, close, chg_pct, in_latest}]；
    tsmc＝台積電 {close, chg_pct}。無資料的段落整段省略，缺值以 — 顯示；
    多空比以百分比呈現（原始值×100）；「(昨…)」緊貼數字不加空白，避免換行。
    """
    pv = prev or {}

    def _yd(v, d=2, mul=1.0, unit=""):
        """昨值對照後綴，緊湊不換行；無昨值回空字串。"""
        return "" if v is None else f"(昨{_signed(v * mul, d)}{unit})"

    blocks: list[list[str]] = []
    # 大盤（加權＋台指期＋台積電權值指標）
    g = ["【大盤】"]
    taiex, chg = row.get("taiex"), row.get("taiex_chg")
    base = (taiex - chg) if (taiex is not None and chg is not None) else None
    pct = round(chg / base * 100, 2) if base else None
    line = f"{_pad('加權指數', 4)} {_fmt(taiex)}"
    if chg is not None:
        arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
        g.append(line)
        g.append(f"{_pad('漲跌幅', 4)} {arrow}{_fmt(abs(chg))}"
                 + (f"（{_signed(pct)}%）" if pct is not None else ""))
    else:
        g.append(line)
    if row.get("turnover") is not None:
        line = f"{_pad('成交金額', 4)} {_fmt(row['turnover'], 0)}億"
        if pv.get("turnover") is not None:
            line += f"(昨{_fmt(pv['turnover'], 0)}億)"
        g.append(line)
    if row.get("tx_price") is not None:
        g.append(_px_line("台指期", row["tx_price"], row.get("tx_chg")))
    if tsmc and tsmc.get("close") is not None:
        t = f"{_pad('台積電', 4)} {_fmt(tsmc['close'])}"
        if tsmc.get("chg_pct") is not None:
            t += f"（{_signed(tsmc['chg_pct'])}%）"
        g.append(t)
    blocks.append(g)
    # 國際行情
    intl = []
    for key, label, d in _INTL_FIELDS:
        v = row.get(key)
        if v is None:
            continue
        pc = row.get(key + "_chg")
        intl.append(f"{_pad(label, 3)} {_fmt(v, d)}"
                    + (f"　{_signed(pc)}%" if pc is not None else ""))
    if intl:
        blocks.append(["【國際行情】"] + intl)
    # 三大法人（買賣超金額，附昨值對照）
    inst = [(n, k) for n, k in
            (("外資", "inst_foreign"), ("投信", "inst_trust"), ("自營", "inst_dealer"))]
    if any(row.get(k) is not None for _, k in inst):
        blocks.append(["【三大法人】買賣超金額(億)"]
                      + [f"{n}　{_signed(row.get(k), 1)}{_yd(pv.get(k), 1)}" for n, k in inst])
    # 期貨籌碼（多空比×100 以 % 呈現，附昨值對照）
    fut = []
    if row.get("tx_foreign_oi") is not None:
        fut.append(f"外資台指OI　{_fmt(row['tx_foreign_oi'], 0)}口"
                   f"{_yd(pv.get('tx_foreign_oi'), 0)}")
    for label, k in (("小台多空比", "retail_ls_mtx"), ("微台多空比", "retail_ls_tmf")):
        v = row.get(k)
        if v is not None:
            fut.append(f"{label}　{_signed(v * 100)}%{_yd(pv.get(k), 2, 100, '%')}")
    if fut:
        blocks.append(["【期貨籌碼】"] + fut)
    # 融資券（僅完整版且有資料）：張數＋金額＋融券，括號為與昨日增減
    if full and any(row.get(k) is not None for k in ("margin_balance", "margin_value", "short_balance")):
        g = ["【融資券】"]
        if row.get("margin_balance") is not None:
            g.append(f"融資 {_fmt(row['margin_balance'], 0)}張({_signed(row.get('margin_chg'), 0)})")
        if row.get("margin_value") is not None:
            g.append(f"融資金額 {_fmt(row['margin_value'], 1)}億({_signed(row.get('margin_value_chg'), 1)})")
        if row.get("short_balance") is not None:
            g.append(f"融券 {_fmt(row['short_balance'], 0)}張({_signed(row.get('short_chg'), 0)})")
        if row.get("margin_maintenance") is not None:
            line = f"融資維持率 {_fmt(row['margin_maintenance'], 1)}%"
            if pv.get("margin_maintenance") is not None:
                line += f"(昨{_fmt(pv['margin_maintenance'], 1)}%)"
            g.append(line)
        blocks.append(g)
    # 類股強弱
    ups = sorted([s for s in sectors if (s.get("chg_pct") or 0) > 0],
                 key=lambda s: -s["chg_pct"])[:3]
    downs = sorted([s for s in sectors if (s.get("chg_pct") or 0) < 0],
                   key=lambda s: s["chg_pct"])[:3]
    if ups or downs:
        g = ["【類股強弱】"]
        g += [f"🔥 {_pad(s['name'], 5)} {s['chg_pct']:+.2f}%" for s in ups]
        g += [f"❄ {_pad(s['name'], 5)} {s['chg_pct']:+.2f}%" for s in downs]
        blocks.append(g)
    # 自選股
    if watch:
        g = ["【自選股】"]
        for w in watch[:10]:
            item = f"⭐ {_pad(w.get('name') or w.get('code'), 4)}"
            if w.get("close") is not None:
                item += f" {_fmt(w['close'])}"
            if w.get("chg_pct") is not None:
                item += f"　{w['chg_pct']:+.2f}%"
            if w.get("in_latest"):
                item += " ●在榜"
            g.append(item)
        blocks.append(g)
    # 杯柄型態（有「新符合」或「突破壓力」才顯示，避免天天重複整串清單）
    # picks=True＝清單已過「籌碼/基本選股」交集，標題明示；無 CSV 榜當日退回全杯柄
    if cup and (cup.get("new") or cup.get("breakout")):
        label = "杯柄型態&籌碼/基本" if cup.get("picks") else "杯柄型態"
        g = [f"【{label}】符合 {cup.get('count', 0)} 檔"]
        for b in (cup.get("breakout") or [])[:6]:
            g.append(f"🚀 突破 {b.get('name') or b.get('code')} {_fmt(b.get('close'))}(壓{_fmt(b.get('resistance'))})")
        new = (cup.get("new") or [])[:6]
        if new:
            g.append("🆕 新符合 " + "、".join(f"{s.get('name') or s.get('code')}" for s in new))
        blocks.append(g)
    # AI 解讀
    if ai_text:
        blocks.append(["【AI 解讀】", ai_text.strip()])
    title = f"📊 台股盤後{'總結' if full else '速報'} {row.get('date') or ''}"
    body = ("\n" + SEP + "\n").join("\n".join(b) for b in blocks)
    return (title + "\n" + SEP + "\n" + body)[:MAX_LEN]


def compose_weekly_brief(comparison: dict, ai_text: str = "") -> str:
    """週六籌碼週報（純文字，使用者拍板不做卡片）：重點類股＋本週前五＋AI 籌碼分析師。

    comparison＝weekly 端點回傳，內容取自 `highlights`（analysis.weekly_highlights）。
    不列「加速/新進榜/退榜」：新進榜退榜沒有決策價值，而「加速」的定義是大戶增比高於
    上週，實測某週全市場 0 檔。超長由 MAX_LEN 截斷。
    """
    this_d, last_d = comparison.get("this_date"), comparison.get("last_date")
    period = f"{last_d} → {this_d}" if (this_d and last_d) else (this_d or "")
    hl = comparison.get("highlights") or {}
    sectors, stocks = hl.get("sectors") or [], hl.get("stocks") or []
    lines = [f"📅 籌碼週報 {period}".rstrip()]
    if not (sectors or stocks):
        lines.append("本週無跨週變化資料（尚無上週快照或未匯入 CSV）")
    if sectors:
        lines.append(SEP)
        lines.append("【重點類股】")
        for s in sectors:
            lines.append(f"{_pad(s.get('sector') or '未分類', 5)} "
                         f"{s.get('count', 0)} 檔　均分 {_fmt(s.get('avg_score'))}")
    if stocks:
        lines.append(SEP)
        lines.append("【本週前五】")
        for s in stocks:
            lines.append(f"{_pad(s.get('name') or s.get('code') or '', 5)} "
                         f"大戶{_signed(s.get('big_holder_ratio'))}　"
                         f"人數{_signed(s.get('holder_drop_ratio'))}")
    if ai_text:
        lines.append(SEP)
        lines.append("🤖 AI 籌碼分析師")
        lines.append(ai_text)
    return "\n".join(lines)[:MAX_LEN]


def compose_rank_brief(data: dict) -> str:
    """高價股 Top N（/api/rank/price 回應）→ LINE 訊息。

    每檔壓成一行「排名 名稱 價 漲跌% 量 額增減」，手機才不會把一長串折得七零八落——
    LINE 訊息框約容 22 個全形字，超過就折行，所以每個欄位都砍到剛好夠用：
    - 價格取整數：高價股 tick ≥1 元本就沒小數，且「15,510.00」這種 7 位數字串會被
      LINE 誤判成電話號碼、自動加上藍色連結
    - 漲跌%取整數；成交額只留增減（絕對值由量×價即可推估，留著是重複資訊）
    - 盤中估算以 * 標記＋末尾註腳，不用「(估)」——那 4 格會把最長的一行撐到折行
    排名右靠補到 2 位、名稱補到 3 字寬，讓價格欄大致對齊（LINE 為比例字體，無法精確對齊）。
    缺值的欄位整段省略，不留「—」佔位。
    """
    items = data.get("items") or []
    if not items:
        return "尚無高價股資料（需先跑過 OHLC 回補）"
    lines = [f"💰 台股高價股 Top{len(items)}"]
    if data.get("prev_date"):
        lines.append(f"量額基準 {data['prev_date']}")
    lines.append(SEP)
    est_any = False
    for i, it in enumerate(items, 1):
        price, pct = it.get("price"), it.get("chg_pct")
        seg = [f"{i:>2} {_pad(it.get('name') or it.get('code') or '', 3)}", _fmt(price, 0)]
        if pct is not None:
            seg.append(f"{_signed(pct, 0)}%")
        if it.get("vol") is not None:
            seg.append(f"{_fmt(it['vol'], 0)}張")
        chg = it.get("amount_chg")
        if chg is not None:
            seg.append(f"{'▲' if chg > 0 else '▼'}{_fmt(abs(chg) / 1e8, 1)}億")
        if it.get("amount_est"):
            est_any = True
            seg.append("*")
        lines.append("　".join(seg))
    if est_any:
        lines.append("* 盤中估算（官方成交金額收盤後才發布）")
    return "\n".join(lines)[:MAX_LEN]


# Flex 版高價股用色。漲跌%沿台股慣例紅漲綠跌；「額增減」刻意換一組色系（金＝放量、
# 灰＝縮量）——量能變化與股價方向是兩個維度，共用紅綠會被誤讀成漲跌。
_C_BG, _C_HEAD, _C_TEXT, _C_MUTED = "#0f1419", "#1a2029", "#e6e6e6", "#8a94a3"
_C_UP, _C_DOWN, _C_GOLD = "#e8404a", "#1f9e6e", "#f0a500"
_RANK_COLS = (("股票", 5), ("成交價", 4), ("漲跌", 3), ("成交量", 4), ("額增減", 5))


def _cell(text, flex, color, align="end", weight=None):
    c = {"type": "text", "text": text, "flex": flex, "size": "xs",
         "color": color, "align": align, "gravity": "center"}
    if weight:
        c["weight"] = weight
    return c


def compose_rank_flex(data: dict) -> dict:
    """高價股 Top N → LINE Flex 訊息（回傳完整 message 物件，含 altText）。

    為什麼不用純文字：LINE 是比例字體，空白寬度 ≠ 數字寬度，補空白永遠對不齊；
    訊息框又只容約 31 個半形單位，五個欄位一行必折。Flex 用 flex 比例配欄寬，
    欄位無論內容長短都對得齊，也不會折行。
    放量的列下方加一條「資金流向 bar」，長度正比於增額、最大者滿格——把量能變化畫成
    長度，掃一眼就知道錢往哪去，這是純文字給不了的。**只畫放量**：縮量也畫的話，那條
    線會被讀成表格底線而不是資料（縮量已由 ▼ 文字表達）。
    """
    items = data.get("items") or []
    if not items:
        return {"type": "text", "text": "尚無高價股資料（需先跑過 OHLC 回補）"}
    peak = max((i["amount_chg"] for i in items if i.get("amount_chg")), default=0)
    rows = [{"type": "box", "layout": "vertical", "contents": [
        {"type": "box", "layout": "horizontal", "contents": [
            _cell(n, fx, _C_MUTED, "start" if i == 0 else "end")
            for i, (n, fx) in enumerate(_RANK_COLS)]}]}]
    for i, it in enumerate(items, 1):
        pct, chg = it.get("chg_pct"), it.get("amount_chg")
        pcol = _C_MUTED if not pct else (_C_UP if pct > 0 else _C_DOWN)
        ccol = _C_MUTED if not chg else (_C_GOLD if chg > 0 else _C_MUTED)
        cells = [
            _cell(f"{i} {it.get('name') or it.get('code') or ''}", 5, _C_TEXT, "start"),
            _cell(_fmt(it.get("price"), 0), 4, _C_TEXT, weight="bold"),
            _cell("—" if pct is None else f"{_signed(pct, 0)}%", 3, pcol),
            _cell("—" if it.get("vol") is None else f"{_fmt(it['vol'], 0)}張", 4, _C_MUTED),
            _cell("—" if chg is None else f"{'▲' if chg > 0 else '▼'}{_fmt(abs(chg) / 1e8, 1)}億",
                  5, ccol),
        ]
        row = {"type": "box", "layout": "vertical", "margin": "md",
               "contents": [{"type": "box", "layout": "horizontal", "contents": cells}]}
        if chg and chg > 0 and peak:
            row["contents"].append({
                "type": "box", "layout": "vertical", "margin": "xs",
                "width": f"{max(1, round(chg / peak * 100))}%", "height": "3px",
                "backgroundColor": _C_GOLD, "contents": [{"type": "filler"}]})
        rows.append(row)

    head = [{"type": "text", "text": f"台股高價股 Top{len(items)}",
             "color": _C_GOLD, "size": "md", "weight": "bold"},
            {"type": "text", "size": "xxs", "color": _C_MUTED, "margin": "xs",
             "text": f"量額基準 {data['prev_date']}" if data.get("prev_date") else "量額基準 —"}]
    foot = {"type": "box", "layout": "vertical", "paddingAll": "12px",
            "backgroundColor": _C_BG, "contents": [
                {"type": "text", "size": "xxs", "color": _C_MUTED, "wrap": True,
                 "text": "官方成交金額收盤後才發布，盤中成交額為估算"}]}

    def build(rs):
        return {"type": "bubble", "size": "giga",
                "header": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                           "backgroundColor": _C_HEAD, "contents": head},
                "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                         "backgroundColor": _C_BG, "contents": rs},
                "footer": foot}

    # 名單長/股名長時可能逼近 LINE 的 10 KB bubble 上限（超限整則被退件）→ 從榜尾開始砍
    while len(rows) > 4 and _bubble_size(build(rows)) > _BUBBLE_MAX:
        rows = rows[:-1]
    head[0]["text"] = f"台股高價股 Top{len(rows) - 1}"    # 扣掉欄位標題列
    return {"type": "flex", "altText": compose_rank_brief(data)[:400], "contents": build(rows)}


def _eyebrow(text):
    """區塊小標。分區靠它加留白就夠——原本每個區塊之間還畫一條分隔線，那是多餘的配件，
    每條 55 B 也正是國際行情能不能維持兩欄的差別。"""
    return {"type": "text", "text": text, "size": "xxs", "color": _C_MUTED, "margin": "xl"}


def _kv(label, value, color=_C_TEXT, note=""):
    """一列：左標籤、中數值、右灰色昨值。三欄直接用 flex 分配——包一層 box 再塞 filler
    對齊效果一樣，但每列多花約 110 bytes，第一頁的 10 KB 額度禁不起這種浪費。"""
    cells = [{"type": "text", "text": label, "size": "xs", "color": _C_MUTED, "flex": 5},
             {"type": "text", "text": value, "size": "xs", "color": color,
              "align": "end", "flex": 5}]
    if note:
        cells.append({"type": "text", "text": note, "size": "xxs", "color": _C_MUTED,
                      "align": "end", "flex": 4})
    return {"type": "box", "layout": "horizontal", "margin": "sm", "contents": cells}


def _scale(v, peak):
    return f"{0 if not (v and peak) else max(1, round(abs(v) / peak * 100))}%"


def _balance_bar(v, peak):
    """雙向天平：買超往右紅、賣超往左綠，共用中線。三大法人的資金方向一眼看完。

    只畫有值的那一側——0% 的 bar 看不見卻照吃 110 B，而第一頁要同時裝下三大法人／
    期貨／融資券／類股／國際行情，那點額度正是國際行情留不留得住的差別。
    """
    neg, pos = (v if v and v < 0 else 0), (v if v and v > 0 else 0)
    def half(value, colour, flip):
        if not value:
            return {"type": "box", "layout": "horizontal", "flex": 1,
                    "contents": [{"type": "filler"}]}
        bar = {"type": "box", "layout": "vertical", "width": _scale(value, peak),
               "height": "7px", "backgroundColor": colour, "contents": [{"type": "filler"}]}
        inner = [{"type": "filler"}, bar] if flip else [bar, {"type": "filler"}]
        return {"type": "box", "layout": "horizontal", "flex": 1, "contents": inner}
    return {"type": "box", "layout": "horizontal", "flex": 7, "contents": [
        half(neg, _C_DOWN, True),
        {"type": "box", "layout": "vertical", "width": "2px", "height": "13px",
         "backgroundColor": "#4a5768", "contents": [{"type": "filler"}]},
        half(pos, _C_UP, False)]}


def _pct_colour(v):
    return _C_MUTED if not v else (_C_UP if v > 0 else _C_DOWN)


def compose_daily_flex(row: dict, sectors: list, watch: list, full: bool = False,
                       tsmc: dict | None = None, prev: dict | None = None,
                       cup: dict | None = None, ai_text: str = "") -> dict:
    """盤後速報 → Flex 卡片（AI 解讀不放這裡，長散文另發一則純文字）。

    開場刻意不是「加權指數大數字」而是三大法人資金天平：指數使用者一天看好幾次早就知道，
    只有盤後才知道的是籌碼，而籌碼正是這個 App 的核心。指數退居標題帶當背景資訊。
    多空比一律不套漲跌色——散戶多空比是反向指標，染紅會被讀成利多。
    無資料的區塊整段省略，不留空殼。

    watch/cup 只用於產生 altText（純文字版仍完整）：自選股與杯柄型態經使用者確認不放卡片，
    第二頁改放 AI 解讀。參數保留，未來要加回卡片不必重寫取數邏輯。
    """
    pv = prev or {}
    taiex, chg = row.get("taiex"), row.get("taiex_chg")
    base = (taiex - chg) if (taiex is not None and chg is not None) else None
    pct = round(chg / base * 100, 2) if base else None
    # 左欄：加權指數為主角；右欄：台指期／台積電——判斷大盤真實情緒的兩個對照，
    # 與指數並列在標題帶才好互看，也讓 body 少一個區塊、把額度讓給類股強弱。
    left = [{"type": "text", "text": _fmt(taiex), "size": "xxl", "weight": "bold",
             "color": _C_TEXT}]
    if chg is not None:
        left.append({"type": "text", "margin": "xs", "size": "sm", "color": _pct_colour(chg),
                     "text": f"{'▲' if chg > 0 else '▼'}{_fmt(abs(chg))}"
                             + (f"（{_signed(pct)}%）" if pct is not None else "")})
    if row.get("turnover") is not None:
        t = f"成交 {_fmt(row['turnover'], 0)}億"
        if pv.get("turnover") is not None:
            t += f"(昨{_fmt(pv['turnover'], 0)}億)"
        left.append({"type": "text", "text": t, "size": "xxs",
                     "color": _C_MUTED, "margin": "xs"})
    tx_pct = None
    if row.get("tx_price") and row.get("tx_chg") is not None:
        tx_base = row["tx_price"] - row["tx_chg"]
        tx_pct = round(row["tx_chg"] / tx_base * 100, 2) if tx_base else None
    right = []
    for label, value, delta in (("台指期", row.get("tx_price"), tx_pct),
                                ("台積電", (tsmc or {}).get("close"),
                                 (tsmc or {}).get("chg_pct"))):
        if value is not None:
            # 只有紅綠色卻沒有數字，看不出漲多少——色彩負責方向，數字負責幅度
            right.append({"type": "text", "text": label, "size": "xxs", "color": _C_MUTED,
                          "align": "end", "margin": "sm" if right else "none"})
            right.append({"type": "text", "text": _fmt(value, 0), "size": "sm",
                          "weight": "bold", "color": _pct_colour(delta), "align": "end"})
            if delta is not None:
                right.append({"type": "text", "text": f"{_signed(delta)}%", "size": "xxs",
                              "color": _pct_colour(delta), "align": "end"})
    head = [{"type": "box", "layout": "horizontal", "contents": [
        {"type": "text", "text": f"台股盤後{'總結' if full else '速報'}",
         "size": "sm", "weight": "bold", "color": _C_GOLD, "flex": 1},
        {"type": "text", "text": str(row.get("date") or ""), "size": "xxs",
         "color": _C_MUTED, "align": "end", "flex": 0}]},
        {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
            {"type": "box", "layout": "vertical", "flex": 6, "contents": left},
            {"type": "box", "layout": "vertical", "flex": 4, "contents": right or [
                {"type": "filler"}]}]}]

    market, read = [], []
    inst = [(n, row.get(k), pv.get(k)) for n, k in
            (("外資", "inst_foreign"), ("投信", "inst_trust"), ("自營", "inst_dealer"))]
    if any(v is not None for _, v, _ in inst):
        peak = max((abs(v) for _, v, _ in inst if v is not None), default=0)
        rows = [_eyebrow("三大法人買賣超（億）")]
        for name, v, yv in inst:
            rows.append({"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                {"type": "text", "text": name, "size": "xs", "color": _C_MUTED, "flex": 2,
                 "gravity": "center"},
                _balance_bar(v, peak),
                {"type": "box", "layout": "vertical", "flex": 4, "contents": [
                    {"type": "text", "text": _signed(v, 1), "size": "xs",
                     "color": _pct_colour(v), "align": "end"},
                    {"type": "text", "text": "" if yv is None else f"昨{_signed(yv, 1)}",
                     "size": "xxs", "color": _C_MUTED, "align": "end"}]}]})
        market.append({"type": "box", "layout": "vertical", "contents": rows})

    fut = []
    if row.get("tx_foreign_oi") is not None:
        fut.append(_kv("外資台指OI", f"{_fmt(row['tx_foreign_oi'], 0)}口",
                       note="" if pv.get("tx_foreign_oi") is None
                            else f"昨{_fmt(pv['tx_foreign_oi'], 0)}"))
    for label, k in (("小台多空比", "retail_ls_mtx"), ("微台多空比", "retail_ls_tmf")):
        if row.get(k) is not None:
            fut.append(_kv(label, f"{_signed(row[k] * 100)}%",
                           note="" if pv.get(k) is None else f"昨{_signed(pv[k] * 100)}%"))
    if fut:
        market.append({"type": "box", "layout": "vertical", "contents": [_eyebrow("期貨籌碼")] + fut})

    if full and any(row.get(k) is not None
                    for k in ("margin_balance", "margin_value", "short_balance")):
        mg = [_eyebrow("融資券")]
        if row.get("margin_balance") is not None:
            mg.append(_kv("融資餘額", f"{_fmt(row['margin_balance'], 0)}張",
                          note=f"({_signed(row.get('margin_chg'), 0)})"))
        if row.get("margin_value") is not None:
            mg.append(_kv("融資金額", f"{_fmt(row['margin_value'], 1)}億",
                          note=f"({_signed(row.get('margin_value_chg'), 1)})"))
        if row.get("short_balance") is not None:
            mg.append(_kv("融券餘額", f"{_fmt(row['short_balance'], 0)}張",
                          note=f"({_signed(row.get('short_chg'), 0)})"))
        if row.get("margin_maintenance") is not None:
            mg.append(_kv("維持率", f"{_fmt(row['margin_maintenance'], 1)}%",
                          note="" if pv.get("margin_maintenance") is None
                               else f"昨{_fmt(pv['margin_maintenance'], 1)}%"))
        market.append({"type": "box", "layout": "vertical", "contents": mg})

    ups = sorted([s for s in sectors if (s.get("chg_pct") or 0) > 0],
                 key=lambda s: -s["chg_pct"])[:3]
    downs = sorted([s for s in sectors if (s.get("chg_pct") or 0) < 0],
                   key=lambda s: s["chg_pct"])[:3]
    if ups or downs:
        def col(title, group):
            items = [{"type": "text", "text": title, "size": "xxs", "color": _C_MUTED}]
            for s in group:
                items.append({"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
                    {"type": "text", "text": s["name"], "size": "xs", "color": _C_TEXT, "flex": 7},
                    {"type": "text", "text": f"{_signed(s['chg_pct'])}%", "size": "xxs",
                     "color": _pct_colour(s["chg_pct"]), "align": "end", "flex": 4}]})
            return {"type": "box", "layout": "vertical", "flex": 1, "contents": items}
        market.append({"type": "box", "layout": "vertical", "contents": [
            _eyebrow("類股強弱"),
            {"type": "box", "layout": "horizontal", "margin": "sm", "spacing": "lg",
             "contents": [col("領漲", ups), col("領跌", downs)]}]})

    for i, (label, body_text) in enumerate(split_ai_sections(ai_text)):
        if label is None:            # 免責等散句：退到次要色階，不跟正文搶
            read.append({"type": "text", "text": body_text, "size": "xxs",
                         "color": _C_MUTED, "wrap": True,
                         "margin": "lg" if i else "none"})
            continue
        inner = [{"type": "text", "text": label, "size": "xs", "weight": "bold",
                  "color": _C_GOLD}]
        if body_text:
            inner.append({"type": "text", "text": body_text, "size": "sm",
                          "color": _C_TEXT, "wrap": True, "margin": "xs"})
        if label == "結論":
            # 唯一會影響明天動作的段落，值得從文字流裡被提出來——左側金條，不是裝飾
            read.append({"type": "box", "layout": "horizontal",
                         "margin": "lg" if i else "none", "contents": [
                             {"type": "box", "layout": "vertical", "width": "3px",
                              "backgroundColor": _C_GOLD, "contents": [{"type": "filler"}]},
                             {"type": "box", "layout": "vertical", "flex": 1,
                              "margin": "md", "contents": inner}]})
        else:
            read.append({"type": "box", "layout": "vertical",
                         "margin": "lg" if i else "none", "contents": inner})

    # 國際行情每項兩欄：名稱數值維持主文色、只有漲跌%上色（整行染色會讓標籤失去層級）。
    # 四個 text 直接平鋪，不再為每項包一層 box——省下的容量正是這兩欄能留下來的原因。
    intl = [(lb, row.get(k), row.get(k + "_chg"), d) for k, lb, d in _INTL_FIELDS
            if row.get(k) is not None]
    if intl:
        rows = [_eyebrow("國際行情")]
        for i in range(0, len(intl), 2):
            pair = intl[i:i + 2]
            rows.append({"type": "box", "layout": "horizontal", "margin": "sm", "spacing": "lg",
                         "contents": [
                             cell for lb, v, p, d in pair for cell in (
                                 {"type": "text", "text": f"{lb} {_fmt(v, d)}",
                                  "size": "xxs", "color": _C_TEXT, "flex": 5},
                                 {"type": "text", "size": "xxs", "flex": 3, "align": "end",
                                  "color": _pct_colour(p),
                                  "text": "" if p is None else f"{_signed(p)}%"})]})
        market.append({"type": "box", "layout": "vertical", "contents": rows})

    if not (market or read):   # 只有指數、其餘全空：LINE 不接受空 body，且空白畫面該說明下一步
        market.append({"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "size": "xs", "color": _C_MUTED, "wrap": True,
             "text": "盤後籌碼尚未發布，稍後的更新會自動補上"}]})
    # 市場數據與 AI 長文一顆 bubble 裝不下（超過 LINE 的 10 KB 上限）→ 拆成可滑動的兩頁。
    # 沒有 AI 時 read 為空，_carousel 會自動退回單顆 bubble，不生出只有標題的空頁。
    read_head = [{"type": "text", "text": "AI 解讀", "size": "sm", "weight": "bold",
                  "color": _C_GOLD},
                 {"type": "text", "text": str(row.get("date") or ""), "size": "xxs",
                  "color": _C_MUTED, "margin": "xs"}]
    alt = compose_daily_brief(row, sectors, watch, full=full, tsmc=tsmc, prev=prev, cup=cup)
    return _carousel(alt, [(head, market), (read_head, read)])


# LINE 硬限制：單顆 bubble 的 JSON 上限 10 KB、carousel 全體 50 KB。
# 超限會被 API 直接退件（整則推播消失），所以組完一定要量、寧可少一段也不能爆。
# 閾值留 200B 給 LINE 端可能的計算差異——_bubble_size 已與 httpx 實際送出的位元組一致，
# 不必再為編碼落差多留。21:00 完整版實測 9.5K，太貼閾值會讓國際行情動不動就被裁掉。
_BUBBLE_MAX = 9800


def _bubble_size(bubble: dict) -> int:
    """httpx 以 ensure_ascii=False + compact separators 送出，量測必須一致——
    用預設的寬鬆 separators 會高估約 10%，逼得卡片白白砍掉內容。"""
    return len(json.dumps(bubble, ensure_ascii=False, separators=(",", ":")).encode())


def _one_bubble(head: list, body: list) -> dict:
    """單顆 bubble：深色標題帶＋主體，區塊間以細線分隔。三種卡共用同一個殼＝同一家人。

    body 依重要性由前往後排；超過 10 KB 時從**尾端**開始丟，先犧牲最不重要的區塊。
    區塊之間不畫分隔線，靠各區塊自身的 margin 分隔。
    """
    while True:
        bubble = {
            "type": "bubble", "size": "giga",
            "header": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                       "backgroundColor": _C_HEAD, "contents": head},
            "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                     "backgroundColor": _C_BG, "contents": body},
        }
        if len(body) <= 1 or _bubble_size(bubble) <= _BUBBLE_MAX:
            return bubble
        body = body[:-1]


def _bubble(alt: str, head: list, body: list) -> dict:
    return {"type": "flex", "altText": alt[:400], "contents": _one_bubble(head, body)}


def _carousel(alt: str, pages: list) -> dict:
    """多頁卡片（pages＝[(head, body), ...]）。內容塞不進一顆 bubble 時用，可左右滑動。"""
    bubbles = [_one_bubble(h, b) for h, b in pages if b]
    if len(bubbles) == 1:
        return {"type": "flex", "altText": alt[:400], "contents": bubbles[0]}
    return {"type": "flex", "altText": alt[:400],
            "contents": {"type": "carousel", "contents": bubbles}}


def reply_messages(token: str, reply_token: str, messages: list) -> dict:
    """一次回覆多則（LINE 上限 5 則）。卡片放結構化數據、長散文另發純文字，各司其職。"""
    if not token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    if not (reply_token and messages):
        return {"ok": False, "error": "缺 replyToken 或空訊息"}
    try:
        r = httpx.post(REPLY_URL, timeout=15,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"replyToken": reply_token, "messages": messages[:5]})
        out = {"ok": r.status_code == 200, "status": r.status_code}
        if r.status_code != 200:
            out["error"] = r.text[:200]
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def compose_breakout_alert(hits: list[dict], hhmm: str) -> str:
    """盤中突破警示訊息。hits＝[{code,name,price,resistance,pick}]，同輪多檔合併成一則。

    pick=True（同時符合籌碼/基本選股）標 ⭐，並排在前面。
    """
    ordered = sorted(hits, key=lambda h: not h.get("pick"))
    lines = [f"🚀 盤中突破壓力 {hhmm}"]
    for h in ordered[:10]:
        star = "⭐" if h.get("pick") else ""
        lines.append(f"{star}{h.get('name') or h.get('code')} {_fmt(h.get('price'))}(壓{_fmt(h.get('resistance'))})")
    if any(h.get("pick") for h in hits):
        lines.append("⭐=同時符合籌碼/基本選股")
    lines.append("（盤中價有延遲，確認量價後再行動）")
    return "\n".join(lines)[:MAX_LEN]


# ===== Webhook（使用者主動查詢 → reply，不計免費額度）=====

# 關鍵字 → 指令。一個指令收多個同義詞，手機打字才不用記得精準用詞。
_COMMANDS = {
    "brief": ("大盤", "簡報", "速報", "盤後"),
    "full": ("完整", "總結", "完整版"),
    "weekly": ("週報", "周報"),
    "rank": ("高價股", "高價"),
    "help": ("help", "說明", "指令", "?", "？"),
}

HELP_TEXT = ("📖 可用指令（直接傳給我）\n" + SEP
             + "\n大盤　　盤後速報（大盤/法人/期貨/類股/自選股）"
             + "\n完整　　速報＋融資券餘額與維持率"
             + "\n週報　　跨週籌碼變化＋AI 分析"
             + "\n高價股　高價股 Top10（價/量/額/額增減）"
             + "\n說明　　顯示這則")

# LINE Developers Console 按「Verify」時送的假 replyToken（全 0），不可拿去回覆
_VERIFY_TOKEN = "0" * 32


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """驗 X-Line-Signature＝base64(HMAC-SHA256(channel_secret, raw_body))。

    webhook 免帳密（LINE 伺服器無法帶 Basic Auth），簽章是唯一把關；secret 未設定一律不放行。
    """
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature)


def parse_webhook_events(payload: dict) -> list[dict]:
    """webhook body → [{reply_token, text}]。只取文字訊息；其他事件型別與驗證用假 token 略過。"""
    out = []
    for ev in payload.get("events") or []:
        if ev.get("type") != "message" or (ev.get("message") or {}).get("type") != "text":
            continue
        rt = str(ev.get("replyToken") or "")
        if not rt or rt == _VERIFY_TOKEN:
            continue
        out.append({"reply_token": rt, "text": str(ev["message"].get("text") or "").strip()})
    return out


def route_command(text: str) -> str | None:
    """使用者輸入 → 指令代號；認不得回 None（呼叫端回 HELP_TEXT）。"""
    t = (text or "").strip().lower()
    for cmd, words in _COMMANDS.items():
        if t in words:
            return cmd
    return None


def reply_text(token: str, reply_token: str, text: str) -> dict:
    """回覆純文字。**不計入每月免費額度**，失敗不拋例外。"""
    if not text:
        return {"ok": False, "error": "缺 replyToken 或空訊息"}
    return reply_message(token, reply_token, {"type": "text", "text": text[:MAX_LEN]})


def reply_message(token: str, reply_token: str, message: dict) -> dict:
    """回覆任意型別訊息（text / flex）。**不計入每月免費額度**，失敗不拋例外。"""
    if not token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    if not (reply_token and message):
        return {"ok": False, "error": "缺 replyToken 或空訊息"}
    try:
        r = httpx.post(REPLY_URL, timeout=15,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"replyToken": reply_token, "messages": [message]})
        out = {"ok": r.status_code == 200, "status": r.status_code}
        if r.status_code != 200:
            out["error"] = r.text[:200]
        return out
    except Exception as e:  # noqa: BLE001 — 回覆失敗不影響 webhook 必須回 200
        return {"ok": False, "error": str(e)}


def broadcast_text(token: str, text: str) -> dict:
    """推播文字給官方帳號全部好友。回 {ok, status?/error?}，失敗不拋例外。"""
    if not text:
        return {"ok": False, "error": "空訊息"}
    return broadcast_messages(token, [{"type": "text", "text": text[:MAX_LEN]}])


def broadcast_messages(token: str, messages: list) -> dict:
    """推播多則（LINE 上限 5 則）。**注意：broadcast 按收訊人數計入每月免費額度。**"""
    if not token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    if not messages:
        return {"ok": False, "error": "空訊息"}
    try:
        r = httpx.post(BROADCAST_URL, timeout=15,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"messages": messages[:5]})
        out = {"ok": r.status_code == 200, "status": r.status_code}
        if r.status_code != 200:
            out["error"] = r.text[:200]
        return out
    except Exception as e:  # noqa: BLE001 — 推播失敗不影響主流程
        return {"ok": False, "error": str(e)}
