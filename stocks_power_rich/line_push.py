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

import httpx

BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
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
    """週六籌碼週報：跨週變化（加速/新進榜/退榜）＋ AI 籌碼分析師。

    comparison＝weekly 端點回傳（{this_date, last_date, stocks:[{code,name,status,big_holder_ratio}]}）。
    持平不進週報（雜訊）；加速依大戶增比由高到低。超長由 broadcast 的 MAX_LEN 截斷。"""
    this_d, last_d = comparison.get("this_date"), comparison.get("last_date")
    period = f"{last_d} → {this_d}" if (this_d and last_d) else (this_d or "")
    lines = [f"📅 籌碼週報 {period}".rstrip()]
    stocks = comparison.get("stocks") or []
    acc = sorted([s for s in stocks if s.get("status") == "加速"],
                 key=lambda s: -(s.get("big_holder_ratio") or 0))
    new = [s for s in stocks if s.get("status") == "新進榜"]
    out_n = sum(1 for s in stocks if s.get("status") == "退榜")
    if not (acc or new or out_n):
        lines.append("本週無跨週變化資料（尚無上週快照或未匯入 CSV）")
    if acc:
        lines.append(SEP)
        lines.append("🚀 大戶加速")
        for s in acc[:8]:
            lines.append(f"{s.get('name') or s.get('code')} 大戶增比 {_fmt(s.get('big_holder_ratio'))}")
    if new:
        lines.append(SEP)
        lines.append("🆕 新進榜")
        for s in new[:5]:
            lines.append(f"{s.get('name') or s.get('code')}")
    if out_n:
        lines.append(f"📤 退榜 {out_n} 檔")
    if ai_text:
        lines.append(SEP)
        lines.append("🤖 AI 籌碼分析師")
        lines.append(ai_text)
    return "\n".join(lines)[:MAX_LEN]


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
    """回覆使用者訊息。**不計入每月免費額度**，失敗不拋例外。"""
    if not token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    if not (reply_token and text):
        return {"ok": False, "error": "缺 replyToken 或空訊息"}
    try:
        r = httpx.post(REPLY_URL, timeout=15,
                       headers={"Authorization": f"Bearer {token}"},
                       json={"replyToken": reply_token,
                             "messages": [{"type": "text", "text": text[:MAX_LEN]}]})
        out = {"ok": r.status_code == 200, "status": r.status_code}
        if r.status_code != 200:
            out["error"] = r.text[:200]
        return out
    except Exception as e:  # noqa: BLE001 — 回覆失敗不影響 webhook 必須回 200
        return {"ok": False, "error": str(e)}


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
