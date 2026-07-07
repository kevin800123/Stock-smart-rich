"""LINE 官方帳號推播：盤後速報/完整版訊息組裝與 broadcast。

訊息組裝為純函數（單元測試）；網路呼叫為 thin wrapper，無 token 時安全降級。
broadcast 推給官方帳號的全部好友（單人自用帳號＝只推給自己），免查 userId。
版型：逐行條列＋全形空白對齊（LINE 非等寬字體，全形空白對中文標籤最穩）＋分區線。
"""
import httpx

BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
MAX_LEN = 4900  # LINE 單則文字上限 5000，留餘裕
SEP = "━━━━━━━━━━━━"

# 國際行情列：(欄位, 顯示名, 小數位)；日圓＝美元兌日圓（USD/JPY）
_INTL_FIELDS = (("n225", "日經", 0), ("kospi", "韓股", 0), ("gold", "黃金", 0),
                ("jpy", "日圓", 2), ("btc", "比特幣", 0))


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
    if cup and (cup.get("new") or cup.get("breakout")):
        g = [f"【杯柄型態】符合 {cup.get('count', 0)} 檔"]
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


def compose_breakout_alert(hits: list[dict], hhmm: str) -> str:
    """盤中突破警示訊息。hits＝[{code,name,price,resistance}]，同輪多檔合併成一則。"""
    lines = [f"🚀 盤中突破壓力 {hhmm}"]
    for h in hits[:10]:
        lines.append(f"{h.get('name') or h.get('code')} {_fmt(h.get('price'))}(壓{_fmt(h.get('resistance'))})")
    lines.append("（盤中價有延遲，確認量價後再行動）")
    return "\n".join(lines)[:MAX_LEN]


def broadcast_text(token: str, text: str) -> dict:
    """推播文字給官方帳號全部好友。回 {ok, status?/error?}，失敗不拋例外。"""
    if not token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    if not text:
        return {"ok": False, "error": "空訊息"}
    try:
        r = httpx.post(BROADCAST_URL, timeout=15,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"messages": [{"type": "text", "text": text[:MAX_LEN]}]})
        out = {"ok": r.status_code == 200, "status": r.status_code}
        if r.status_code != 200:
            out["error"] = r.text[:200]
        return out
    except Exception as e:  # noqa: BLE001 — 推播失敗不影響主流程
        return {"ok": False, "error": str(e)}
