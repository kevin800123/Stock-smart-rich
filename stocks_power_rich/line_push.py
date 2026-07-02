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


def compose_daily_brief(row: dict, sectors: list, watch: list,
                        ai_text: str = "", full: bool = False,
                        tsmc: dict | None = None) -> str:
    """組盤後訊息。full=True 加融資券（21:00 完整版）；速報（16:00）不含。

    row＝market_daily 最新列（含國際行情欄位）；sectors＝[{name, chg_pct}]；
    watch＝[{code, name, close, chg_pct, in_latest}]；tsmc＝台積電 {close, chg_pct}。
    無資料的段落整段省略，缺值以 — 顯示；多空比以百分比呈現（原始值×100）。
    """
    blocks: list[list[str]] = []
    # 大盤（含台積電權值指標）
    taiex, chg = row.get("taiex"), row.get("taiex_chg")
    prev = (taiex - chg) if (taiex is not None and chg is not None) else None
    pct = round(chg / prev * 100, 2) if prev else None
    g = ["【大盤】", f"{_pad('加權指數', 4)} {_fmt(taiex)}"]
    if chg is not None:
        arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "")
        g.append(f"{_pad('漲跌幅', 4)} {arrow}{_fmt(abs(chg))}"
                 + (f"（{_signed(pct)}%）" if pct is not None else ""))
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
    # 三大法人
    inst = [(n, row.get(k)) for n, k in
            (("外資", "inst_foreign"), ("投信", "inst_trust"), ("自營", "inst_dealer"))]
    if any(v is not None for _, v in inst):
        blocks.append(["【三大法人】(億)"] + [f"{n}　{_signed(v, 1)}" for n, v in inst])
    # 期貨籌碼（多空比×100 以 % 呈現）
    fut = []
    if row.get("tx_foreign_oi") is not None:
        fut.append(f"外資台指OI　{_fmt(row['tx_foreign_oi'], 0)} 口")
    for label, k in (("小台多空比", "retail_ls_mtx"), ("微台多空比", "retail_ls_tmf")):
        v = row.get(k)
        if v is not None:
            fut.append(f"{label}　{_signed(v * 100)}%")
    if fut:
        blocks.append(["【期貨籌碼】"] + fut)
    # 融資券（僅完整版且有資料）
    if full and row.get("margin_balance") is not None:
        blocks.append(["【融資券】(張)",
                       f"融資餘額 {_fmt(row['margin_balance'], 0)}（{_signed(row.get('margin_chg'), 0)}）"])
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
    # AI 解讀
    if ai_text:
        blocks.append(["【AI 解讀】", ai_text.strip()])
    title = f"📊 台股盤後{'總結' if full else '速報'} {row.get('date') or ''}"
    body = ("\n" + SEP + "\n").join("\n".join(b) for b in blocks)
    return (title + "\n" + SEP + "\n" + body)[:MAX_LEN]


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
