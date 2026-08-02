"""每日財經新聞：台股／美股／日股新聞 → Gemini 統整 → 供頁面顯示與 Telegram 推播共用。"""
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


def _current_slot(now: datetime | None = None) -> str:
    """依台北時間判斷最接近哪個時段（07:00／17:00／21:00），供未帶 slot 的手動呼叫使用。"""
    h = (now or datetime.now(_TAIPEI)).hour
    if h < 12:
        return "morning"
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
    key = f"news:{today}:{slot}"
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
        {"slot": slot, "snapshot": snapshot, "markets": markets}, cfg.gemini_api_key)
    payload = {"date": today, "slot": slot, "summary": result.get("text", ""),
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
    push = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
    return {"news": payload, "push": push}
