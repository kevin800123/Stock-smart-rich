"""每日財經新聞：台股／美股／日股新聞 → Gemini 統整 → 供頁面顯示與 Telegram 推播共用。"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from .deps import conn
from .helpers import get_ai_cache, set_ai_cache
from .. import gemini, telegram_push
from ..config import load_config
from ..sources import news

router = APIRouter(prefix="/api")

MARKETS = ("tw", "us", "jp")
_TAIPEI = timezone(timedelta(hours=8))

_MARKET_META = {
    "tw": ("🇹🇼", "台股"),
    "us": ("🇺🇸", "美股"),
    "jp": ("🇯🇵", "日股"),
}
_PUSH_PLAN = {
    "morning": ("🌅 07:00 盤前早報", (("us", 6), ("jp", 6), ("tw", 6))),
    "midday": ("☀️ 12:00 午間財經快訊", (("tw", 6), ("jp", 6), ("us", 6))),
    "afternoon": ("🏁 17:00 收盤快訊", (("tw", 6), ("jp", 6), ("us", 6))),
    "evening": ("🌙 21:10 晚間全球焦點", (("us", 6), ("jp", 6), ("tw", 6))),
}
_DETAIL_LABELS = {"事件摘要", "事件", "市場影響", "影響", "後續指標", "關注", "關鍵數據"}

# 投資建議阻擋（第二層）。第一層是 prompt 禁令，但參考專案的實測教訓是 prompt 層一定
# 會漏（他們的來源白名單 6 次漏 1 次），所以這裡再擋一次。實際發生過的漏網句：
# 「法人分析台股8月可能在39000至45000點區間震盪，建議在半年線附近謹慎布局。」
# 刻意不單擋「建議」二字：「金管會建議業者強化風控」是新聞事實、不是對讀者的勸誘，
# 全站基調是「只陳述觀察、不給動作」，而不是禁止出現某個字。
_ADVICE_STRONG = ("目標價", "逢低承接", "逢低買進", "逢低布局", "逢高減碼", "逢高出脫",
                  "值得買進", "可以進場", "建議買進", "建議賣出", "押寶")
_ADVICE_PAIR = re.compile(
    r"建議[^。；\n]{0,12}?(布局|買進|賣出|加碼|減碼|進場|出場|持有|承接|抄底)")


def is_advice_line(line: str) -> bool:
    """這一行是否在給讀者投資動作建議。"""
    text = line or ""
    return any(term in text for term in _ADVICE_STRONG) or bool(_ADVICE_PAIR.search(text))


def strip_advice_lines(text: str) -> tuple[str, int]:
    """濾掉含投資建議的條列。回 (過濾後文字, 丟掉幾條)。

    丟掉後該市場可能只剩 5 則——**寧可少一則，也不能發投資建議**。
    """
    kept, dropped = [], 0
    for line in (text or "").splitlines():
        if line.strip().startswith(("•", "🔥")) and is_advice_line(line):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def mark_lead_bullets(text: str) -> str:
    """每個市場區塊的第一則 `•` 換成 🔥，讓 18 則平鋪的推播有個掃視入口。

    用 emoji 而非 MarkdownV2 粗體：不受跳脫影響，且與網頁全文版既有的 🔥 同一套語彙。
    """
    flags = {flag for flag, _ in _MARKET_META.values()}
    out, awaiting = [], False
    for line in (text or "").splitlines():
        if any(line.startswith(flag) for flag in flags):
            awaiting = True
            out.append(line)
            continue
        stripped = line.lstrip()
        if awaiting and stripped.startswith("•"):
            out.append(line.replace("•", "🔥", 1))
            awaiting = False
            continue
        out.append(line)
    return "\n".join(out)


def _fmt_num(value, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def render_snapshot_block(snapshot: dict, report_date: str = "") -> str:
    """盤面由程式輸出，AI 一律不得改寫（見 _snapshot_from_market_daily 的說明）。

    先前推播沒有這一段，模型就把指數揉進散文（「加權指數收漲266點至43386點」），
    等於繞過本專案最重要的那條防線。漲跌%在這裡用 Python 算，不交給 LLM。
    缺值一律寫「—」，**絕不寫成「43,386（無資料）」這種自相矛盾的格式**。
    """
    if not snapshot:
        return ""
    date = str(snapshot.get("日期") or "")
    stale = bool(date and report_date and date != report_date)
    lines = ["📈 盤面" + (f"（截至 {date[5:]}）" if stale else "")]

    taiex, chg = snapshot.get("加權指數"), snapshot.get("加權漲跌")
    if taiex is None:
        lines.append("• 加權　—")
    else:
        seg = f"• 加權　{_fmt_num(taiex)}"
        if chg is not None:
            base = taiex - chg
            arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "▬")
            seg += f"　{arrow}{_fmt_num(abs(chg))}"
            if base:
                seg += f"（{chg / base * 100:+.2f}%）"
        lines.append(seg)

    tx = snapshot.get("台指期")
    lines.append(f"• 台指期　{_fmt_num(tx)}" if tx is not None else "• 台指期　—")
    turnover = snapshot.get("成交金額(億)")
    lines.append(f"• 成交　{_fmt_num(turnover)} 億" if turnover is not None else "• 成交　—")

    intl = snapshot.get("那斯達克100/費半等國際指標若有") or {}
    labels = (("sox", "費半"), ("n225", "日經"), ("kospi", "韓股"), ("vix", "VIX"))
    picked = [f"{name} {_fmt_num(intl[key], 2 if key == 'vix' else 0)}"
              for key, name in labels if intl.get(key) is not None]
    if picked:
        lines.append("• " + "　".join(picked))
    return "\n".join(lines)


def build_reading_links(markets: dict, per_market: int = 1) -> str:
    """底部「延伸閱讀」——每市場一則原始新聞的行內連結。

    刻意叫「延伸閱讀」而不是「來源」：這些是**餵給 AI 的輸入清單**的首則，不宣稱是
    某一條 bullet 的引用出處（AI 是綜合改寫，無法逐條回溯），標成「來源」會誤導。
    只放 3 條：Google 新聞網址實測 174～354 字元且無法縮短，多放會逼近 4096 上限。
    """
    rows = []
    for key in ("tw", "jp", "us"):
        flag, name = _MARKET_META[key]
        for item in (markets.get(key) or [])[:per_market]:
            url = item.get("url")
            if url:
                rows.append(telegram_push.mdv2_link(f"{flag} {item.get('source') or name}", url))
    if not rows:
        return ""
    return telegram_push.escape_mdv2("🔗 延伸閱讀") + "\n" + "　".join(rows)


def compose_push_message(ai_text: str, snapshot: dict, markets: dict,
                        slot: str = "afternoon", report_date: str = "") -> str:
    """組最終 Telegram 訊息：標題＋盤面（程式）＋新聞（AI，過濾後）＋延伸閱讀（程式）。

    標題與盤面都由程式產生，AI 只負責中間那段新聞判讀。標題若讓 AI 出，它會連同
    日期一起重寫，而日期是我們自己算得出來的東西，沒有理由交給模型。

    **跳脫在這裡統一做**。先前是把 AI 產出的純文字直接送出去，裡面的 `(8306)`、
    `3.5%`、`48.93%。` 在 MarkdownV2 都是特殊字元，於是每一則都 400、每一則都退純
    文字重送——多打一次 API，且行內連結永遠不會生效。
    """
    heading = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])[0]
    title = f"{heading} ｜ {report_date}" if report_date else heading

    body, _dropped = strip_advice_lines(ai_text or "")
    body = mark_lead_bullets(body)
    # AI 通常會自己再寫一次標題行；標題已由程式產生，重複的那行要拿掉
    body = "\n".join(ln for ln in body.splitlines() if heading not in ln).strip()

    parts = [telegram_push.escape_mdv2(title)]
    snap = render_snapshot_block(snapshot, report_date)
    if snap:
        parts.append(telegram_push.escape_mdv2(snap))
    if body:
        parts.append(telegram_push.escape_mdv2(body))
    links = build_reading_links(markets)
    if links:
        parts.append(links)
    return "\n\n".join(parts)


def telegram_digest(summary: str, slot: str, report_date: str = "") -> str:
    """Build a time-prioritized 15-story push: five translated stories per market."""
    stories = {key: [] for key in MARKETS}
    market = None
    for raw in (summary or "").splitlines():
        if raw.startswith("####"):
            market = next((key for key, (flag, name) in _MARKET_META.items()
                           if flag in raw or name in raw), None)
            continue
        if not market:
            continue
        match = re.search(r"\*\*([^*]+)\*\*", raw)
        if not match:
            continue
        bold = match.group(1).strip()
        if bold == "關鍵數據" and stories[market]:
            data = raw.split("：", 1)[-1].strip()
            data = re.sub(r"\*\*([^*]+)\*\*", r"\1", data)
            stories[market][-1]["data"] = data
        elif bold not in _DETAIL_LABELS and not any(item["title"] == bold for item in stories[market]):
            stories[market].append({"title": bold, "data": ""})

    heading, plan = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])
    lines = [heading]
    if report_date:
        lines.append(f"📅 {report_date}")
    for market, count in plan:
        flag, name = _MARKET_META[market]
        chosen = stories[market][:count]
        lines.extend(["", f"{flag} {name}｜重點掃描"])
        for index, item in enumerate(chosen, 1):
            lines.append(f"{index}. {item['title']}")
            data = item["data"]
            if data and "來源未提供" not in data:
                lines.append(f"   🔢 {data}")
    if not any(stories.values()):
        return summary or "目前尚無可推播的財經新聞。"
    lines.extend(["", "👀 完整事件、影響與後續指標請見每日財經新聞頁", "⚠️ 非投資建議，資訊僅供研究參考"])
    return "\n".join(lines)


def _current_slot(now: datetime | None = None) -> str:
    """依台北時間判斷最接近哪個時段（07:00／17:00／21:00），供未帶 slot 的手動呼叫使用。"""
    h = (now or datetime.now(_TAIPEI)).hour
    if h < 11:
        return "morning"
    if h < 15:
        return "midday"
    if h < 19:
        return "afternoon"
    return "evening"


def _snapshot_from_market_daily(c) -> dict:
    """盤面數字一律取自本站已有的 market_daily，絕不讓 Gemini 自行檢索或重算
    （每日財經專案的核心防線：實測模型曾把台股 −3.79% 寫成 +3.76%，方向全反）。

    **要往回找「真的有指數的那一列」，不能直接拿最新日期。** market_daily 當天早上
    就會有列（其他來源先建列），指數卻要收盤後才寫入——實測 2026-08-03 那列
    taiex/turnover/tx_price 全是 NULL，直接取最新日期會讓整個盤面區塊變成三個「—」。
    同 `/api/inst-ranking` 與 `balanceCard` 的既有處理：往回掃有值的那一天，
    再由 render_snapshot_block 標「截至 MM-DD」。上限 7 天，連假時不要無限往回掃。
    """
    cutoff = (datetime.now(_TAIPEI) - timedelta(days=7)).strftime("%Y-%m-%d")
    row = c.execute(
        "SELECT * FROM market_daily WHERE taiex IS NOT NULL AND date >= ? "
        "ORDER BY date DESC LIMIT 1", (cutoff,)).fetchone()
    if not row:
        return {}          # 一週內都沒有指數 → 寧可不出盤面，也不端出過期數字
    m = dict(row)
    return {
        "日期": m.get("date"), "加權指數": m.get("taiex"), "加權漲跌": m.get("taiex_chg"),
        "成交金額(億)": m.get("turnover"), "台指期": m.get("tx_price"),
        "那斯達克100/費半等國際指標若有": {
            k: m.get(k) for k in ("sox", "n225", "kospi", "vix") if m.get(k) is not None},
    }


def news_logic(c, slot: str | None = None, refresh: int = 0) -> dict:
    """組今日新聞摘要。slot 未帶時依台北時間推斷。refresh=1 繞過快取。

    快取鍵含日期＋slot：同一天三個時段內容本就不同（必含主題不同），不可共用一把鍵，
    否則下午場會讀到早上場快取的「必含美股收盤」版本。只有 Gemini 呼叫成功
    （enabled）才寫快取，同 market_summary_logic 的規則。
    """
    slot = slot or _current_slot()
    today = datetime.now(_TAIPEI).strftime("%Y-%m-%d")
    # 推播格式改版（盤面區塊／投資建議過濾／行內連結／MarkdownV2 跳脫）→ 進版號，
    # 否則舊格式的快取會被當成今天的結果直接送出（同 dist 快取那次的教訓）。
    key = f"news:v6:{today}:{slot}"
    cached = get_ai_cache(c, key)
    if cached and not refresh:
        return cached

    markets, fallback_flags = {}, {}
    for m in MARKETS:
        items, fell_back = news.fetch_market_news(m, n=20)
        markets[m] = [{"title": it["title"], "url": it["url"], "source": it["source"]}
                      for it in items]
        fallback_flags[m] = fell_back

    cfg = load_config()
    snapshot = _snapshot_from_market_daily(c)
    request_payload = {"slot": slot, "report_date": today, "snapshot": snapshot, "markets": markets}
    result = gemini.summarize_news(request_payload, cfg.gemini_api_key)
    summary = result.get("text", "")
    push_result = (gemini.summarize_news_push(request_payload, summary, cfg.gemini_api_key)
                   if result.get("enabled") else {})
    raw_push = push_result.get("text") or telegram_digest(summary, slot, today)
    telegram_text = compose_push_message(raw_push, snapshot, markets, slot, today)
    payload = {"date": today, "slot": slot, "summary": summary,
              "telegram_text": telegram_text,
              "enabled": result.get("enabled", False),
              "fallback": fallback_flags, "markets": markets}
    if result.get("enabled"):
        set_ai_cache(c, key, payload)
    return payload


@router.get("/news")
def get_news(slot: str | None = None, refresh: int = 0):
    return news_logic(conn(), slot=slot, refresh=refresh)


@router.post("/news/test")
def test_news_push():
    """手動觸發一次新聞摘要＋Telegram 推播（比照 POST /api/line/test）。

    回傳的 push.parse_mode_used 會告訴你這次是走 MarkdownV2 還是退回純文字——
    退純文字是無聲的（訊息照送、只是連結失效），不看這欄位不會發現。
    """
    c = conn()
    cfg = load_config()
    payload = news_logic(c, refresh=1)
    text = payload.get("telegram_text") or payload.get("summary") or "（本次無法產生摘要）"
    push = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
    return {"news": payload, "push": push}
