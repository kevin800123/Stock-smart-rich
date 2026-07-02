"""LINE 官方帳號推播：盤後速報/完整版訊息組裝與 broadcast。

訊息組裝為純函數（單元測試）；網路呼叫為 thin wrapper，無 token 時安全降級。
broadcast 推給官方帳號的全部好友（單人自用帳號＝只推給自己），免查 userId。
"""
import httpx

BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
MAX_LEN = 4900  # LINE 單則文字上限 5000，留餘裕


def _fmt(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def _signed(v, d=2):
    return "—" if v is None else f"{v:+,.{d}f}"


def compose_daily_brief(row: dict, sectors: list, watch: list,
                        ai_text: str = "", full: bool = False) -> str:
    """組盤後訊息。full=True 加融資券（21:00 完整版）；速報（16:00）不含。

    row＝market_daily 最新列；sectors＝[{name, chg_pct}]；watch＝[{code, name, chg_pct, in_latest}]。
    無資料的段落整段省略，缺值以 — 顯示。
    """
    lines = [f"📊 台股盤後{'總結' if full else '速報'} {row.get('date') or ''}"]
    taiex, chg = row.get("taiex"), row.get("taiex_chg")
    prev = (taiex - chg) if (taiex is not None and chg is not None) else None
    pct = round(chg / prev * 100, 2) if prev else None
    arrow = "▲" if (chg or 0) > 0 else ("▼" if (chg or 0) < 0 else "")
    head = f"加權 {_fmt(taiex)} {arrow}{_fmt(abs(chg)) if chg is not None else '—'}"
    lines.append(head + (f" ({_signed(pct)}%)" if pct is not None else ""))
    lines.append("─ 三大法人(億) ─")
    lines.append(f"外資 {_signed(row.get('inst_foreign'), 1)}｜投信 {_signed(row.get('inst_trust'), 1)}"
                 f"｜自營 {_signed(row.get('inst_dealer'), 1)}")
    lines.append("─ 期貨籌碼 ─")
    lines.append(f"外資台指OI {_fmt(row.get('tx_foreign_oi'), 0)} 口"
                 f"｜散戶小台多空比 {_fmt(row.get('retail_ls_mtx'), 4)}")
    if full:
        lines.append("─ 融資券(張) ─")
        lines.append(f"融資餘額 {_fmt(row.get('margin_balance'), 0)}"
                     f"（{_signed(row.get('margin_chg'), 0)}）")
    ups = [s for s in sectors if (s.get("chg_pct") or 0) > 0]
    ups.sort(key=lambda s: -s["chg_pct"])
    downs = [s for s in sectors if (s.get("chg_pct") or 0) < 0]
    downs.sort(key=lambda s: s["chg_pct"])
    if ups or downs:
        lines.append("─ 類股 ─")
        if ups:
            lines.append("🔥 " + "、".join(f"{s['name']}{s['chg_pct']:+.2f}%" for s in ups[:3]))
        if downs:
            lines.append("❄ " + "、".join(f"{s['name']}{s['chg_pct']:+.2f}%" for s in downs[:3]))
    if watch:
        lines.append("─ 自選股 ─")
        for w in watch[:10]:
            chg_w = "" if w.get("chg_pct") is None else f" {w['chg_pct']:+.2f}%"
            tag = " ●在榜" if w.get("in_latest") else ""
            lines.append(f"⭐ {w.get('name') or w.get('code')}{chg_w}{tag}")
    if ai_text:
        lines.append("─ AI 解讀 ─")
        lines.append(ai_text.strip())
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
