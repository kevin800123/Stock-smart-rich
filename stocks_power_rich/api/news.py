"""每日財經新聞：台股／美股／日股新聞 → Gemini 統整 → 供頁面顯示與 Telegram 推播共用。"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from .deps import conn
from .helpers import _latest_date, get_ai_cache, set_ai_cache
from .. import gemini
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
    "morning": ("🌅 07:00 盤前早報｜先讀美股、日股，再看台股", ("us", "jp", "tw")),
    "midday": ("☀️ 12:00 午間快訊｜聚焦台股與日股盤中", ("tw", "jp", "us")),
    "afternoon": ("🏁 17:00 收盤快訊｜先讀台股，再看日美股", ("tw", "jp", "us")),
    "evening": ("🌙 21:10 晚間全球焦點｜先讀美股與日股", ("us", "jp", "tw")),
}
_DETAIL_LABELS = {"事件摘要", "事件", "市場影響", "影響", "後續指標", "關注", "關鍵數據"}


def _telegram_digest_v1(summary: str, slot: str, report_date: str = "") -> str:
    """Extract five headline-level items for a readable, time-specific push."""
    titles = {key: [] for key in MARKETS}
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
        title = match.group(1).strip()
        if title not in _DETAIL_LABELS and title not in titles[market]:
            titles[market].append(title)

    heading, plan = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])
    lines = [heading]
    if report_date:
        lines.append(f"📅 {report_date}")
    lines.append("")
    pushed = 0
    for market, count in plan:
        flag, name = _MARKET_META[market]
        for title in titles[market][:count]:
            lines.append(f"{flag} {name}｜{title}")
            pushed += 1
    if not pushed:
        return summary or "目前尚無可推播的財經新聞。"
    lines.extend(["", "👀 詳細事件、影響與後續指標請見每日財經新聞頁", "⚠️ 非投資建議，資訊僅供研究參考"])
    return "\n".join(lines)


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

    heading, order = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])
    lines = [heading]
    if report_date:
        lines.append(f"📅 {report_date}")
    for market in order:
        flag, name = _MARKET_META[market]
        chosen = stories[market][:5]
        lines.extend(["", f"{flag} {name}｜{len(chosen)}/5 則"])
        for index, item in enumerate(chosen, 1):
            lines.append(f"{index}. {item['title']}")
            lines.append(f"   🔢 {item['data'] or '來源未提供可驗證數據'}")
        if len(chosen) < 5:
            lines.append(f"⚠️ 本次僅取得 {len(chosen)} 則可用來源，未以其他市場新聞補足。")
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
    """
    date = _latest_date(c)
    if not date:
        return {}
    row = c.execute("SELECT * FROM market_daily WHERE date=?", (date,)).fetchone()
    if not row:
        return {}
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
    # Prompt v2 changes the report contract; do not serve a prior-format cache.
    key = f"news:v4:{today}:{slot}"
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
    result = gemini.summarize_news(
        {"slot": slot, "report_date": today, "snapshot": snapshot, "markets": markets},
        cfg.gemini_api_key)
    summary = result.get("text", "")
    payload = {"date": today, "slot": slot, "summary": summary,
              "telegram_text": telegram_digest(summary, slot, today),
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
    """手動觸發一次新聞摘要＋Telegram 推播（比照 POST /api/line/test）。"""
    from .. import telegram_push
    c = conn()
    cfg = load_config()
    payload = news_logic(c, refresh=1)
    text = payload.get("summary") or "（本次無法產生摘要）"
    text = payload.get("telegram_text") or text
    push = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
    return {"news": payload, "push": push}
