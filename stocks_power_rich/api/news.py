"""每日財經新聞：台股／美股／日股新聞 → Gemini 統整 → 供頁面顯示與 Telegram 推播共用。"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from .deps import conn
from .helpers import (get_ai_cache, set_ai_cache, ai_cooling_down,
                      note_ai_failure, bump_ai_calls)
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

# gemini._run 失敗時回傳 {"enabled": False, "text": "（AI 摘要失敗：503 UNAVAILABLE...）"}——
# 這段文字是給網頁顯示用的降級提示，**不是新聞內容**。news_logic 曾經只看 text 是否為真值
# 就把它當內容送進 telegram_digest／直接當 raw_push，於是 Gemini 503 時 Python 例外字串
# （含原始錯誤 dict）整段被跳脫後推播到使用者的 Telegram。任何要進 Telegram 正文的字串
# 都必須先確認來源呼叫的 enabled 是 True，不能只看 text 是否非空。
_AI_UNAVAILABLE_NOTE = "AI 新聞摘要暫時無法使用（模型忙碌或逾時），本次僅提供盤面數字，詳情請見下方延伸閱讀。"

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


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


# 這些讀數 📈 盤面 那一段已經用**程式算出來的**數字講過了，內文再講一次就是重複，
# 而且是「同一個數字兩個來源」——盤面來自 market_daily，這裡來自 LLM 轉述，一旦有出入
# 就會在同一則訊息裡自相矛盾。原本 summarize_news_push 的 prompt 有一條「不得複述指數
# 數字」，但那支已經移除；完整版 prompt 沒有這條（網頁版沒有盤面區塊，本來就該寫），
# 所以改由組裝端負責剃掉。
_SNAPSHOT_TERMS = ("加權指數", "加權漲跌", "成交金額", "成交值", "台指期",
                   "日經225", "日經指數", "費半", "費城半導體", "VIX")


def useful_data(title: str, data: str) -> str:
    """關鍵數據只有在「標題與盤面都沒講過」時才值得附上，否則就是噪音。

    兩層處理：
    1. **剃掉複述盤面的子句**（見 `_SNAPSHOT_TERMS`）。實跑輸出過
       「…　加權指數 43386.41 點、加權漲跌 266.66 點、成交金額 8855.1 億元。」——
       這三個數字上方 📈 盤面 已經講過，是移除第二支 LLM 呼叫後跑掉的規則。
       以「、」逐句剃而不是整段丟，因為同一串常混著真的增量
       （「外資期貨空單突破 9 萬口、台指期 43230 點」只有後半要剃）。
    2. 剩下的再比**數字**而不是比字串：取小數點前的整數部分，全部都已出現在標題裡
       就丟掉；有任何一個是新的就保留（「日經指數續跌…　255 日圓」的 255 是增量）。
    """
    if not data or "來源未提供" in data:
        return ""
    kept = [seg for seg in data.split("、")
            if seg.strip() and not any(t in seg for t in _SNAPSHOT_TERMS)]
    data = "、".join(kept).strip(" 、。")
    if not data:
        return ""
    nums = [n.split(".")[0].replace(",", "") for n in _NUM.findall(data)]
    if not nums:
        return ""                      # 沒有數字的「關鍵數據」多半是複述，不值得占版面
    bare = title.replace(",", "")
    return "" if all(n in bare for n in nums) else data


def telegram_digest(summary: str, slot: str, report_date: str = "") -> str:
    """把完整版摘要（markdown）在 Python 端壓成推播用的條列，**不再多打一次 Gemini**。

    先前是「Gemini 寫完整版 → 再叫 Gemini 壓縮成推播版」，等於同一份素材付兩次錢，
    而第二支純粹在做改寫與截長。改由這支純函式做之後，新聞的 LLM 呼叫從
    8 次/天降為 4 次/天（輸入輸出都減半），且推播文字**直接取自完整版的標題與
    關鍵數據**，不再有第二次改寫可能引入的偏移。

    輸出**必須是 `• ` 條列**，這是 `compose_push_message` 那條管線的契約：
    `strip_advice_lines` 只過濾 `•`／`🔥` 開頭的行、`mark_lead_bullets` 也只認 `•`。
    舊版吐的是「1. 標題」加下一行「   🔢 數據」，若直接沿用，**投資建議過濾器會
    整個失效**（安全性回歸，不只是版面問題），每則也會佔掉兩行。
    標題與日期一律由 `compose_push_message` 產生，這裡不重複輸出。
    """
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
        # 冒號可能落在粗體**裡面**（`**關鍵數據：**`）也可能在外面（`**關鍵數據**：`），
        # 模型兩種都寫得出來。不正規化的話「關鍵數據：」不等於 _DETAIL_LABELS 裡的
        # 「關鍵數據」，就會被當成一則新聞的標題，推播因此多出一行空的「• 關鍵數據：」
        # （實跑輸出過）。
        bold = match.group(1).strip().rstrip("：:").strip()
        if bold == "關鍵數據" and stories[market]:
            data = raw.split("：", 1)[-1].strip()
            data = re.sub(r"\*\*([^*]+)\*\*", r"\1", data)
            stories[market][-1]["data"] = data
        elif bold not in _DETAIL_LABELS and not any(item["title"] == bold for item in stories[market]):
            stories[market].append({"title": bold, "data": ""})

    if not any(stories.values()):
        # 解析不到任何一則（完整版格式改版）——**絕不可 return summary**，那會把整份
        # markdown（#### 與 ** 全都在）倒進 Telegram。給一句話請使用者看網頁版。
        return "本次摘要格式無法轉為推播條列，完整內容請見每日財經新聞頁。"

    _, plan = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])
    blocks = []
    for market, count in plan:
        chosen = stories[market][:count]
        if not chosen:
            continue
        flag, name = _MARKET_META[market]
        lines = [f"{flag} {name}｜重點掃描"]
        for item in chosen:
            data = useful_data(item["title"], item["data"])
            lines.append(f"• {item['title']}" + (f"　{data}" if data else ""))
        blocks.append("\n".join(lines))
    blocks.append("⚠️ 非投資建議，資訊僅供研究參考")
    return "\n\n".join(blocks)


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
    if not refresh and ai_cooling_down(c):
        # 剛失敗過就先不打（免費層一天只有 20 次，重試要克制）。新聞標題照樣抓、
        # 照樣顯示，少的只有 AI 摘要那一段。按「更新摘要」可以穿透這個冷卻。
        result = {"enabled": False, "text": _AI_UNAVAILABLE_NOTE}
    else:
        result = gemini.summarize_news(request_payload, cfg.gemini_api_key)
        if result.get("enabled"):
            bump_ai_calls(c)
        else:
            note_ai_failure(c)
    summary = result.get("text", "")
    # 推播條列由 Python 從完整版壓出來，不再為了「同一份素材的另一種寫法」
    # 多打一次 Gemini（見 telegram_digest 的說明）。
    raw_push = telegram_digest(summary, slot, today) if result.get("enabled") else _AI_UNAVAILABLE_NOTE
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
def test_news_push(slot: str | None = None):
    """手動觸發一次新聞摘要＋Telegram 推播（比照 POST /api/line/test）。

    `slot` 可指定 morning／midday／afternoon／evening；不帶就依當下台北時間推斷。
    要補發某一場（例如中午那場沒發成）時就用得到，否則只能等到那個時段才測得到。
    **這支會真的送出 Telegram，且 refresh=1 會實扣一次 Gemini 配額**（免費層一天 20 次）。

    回傳的 push.parse_mode_used 會告訴你這次是走 MarkdownV2 還是退回純文字——
    退純文字是無聲的（訊息照送、只是連結失效），不看這欄位不會發現。
    """
    c = conn()
    cfg = load_config()
    payload = news_logic(c, slot=slot, refresh=1)
    text = payload.get("telegram_text") or payload.get("summary") or "（本次無法產生摘要）"
    push = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
    return {"news": payload, "push": push}
