"""每日財經新聞：台股／美股／日股新聞 → Gemini 統整 → 供頁面顯示與 Telegram 推播共用。"""
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from .deps import conn
from .helpers import (get_ai_cache, set_ai_cache, ai_cooling_down,
                      note_ai_failure, bump_ai_calls)
from .. import gemini, telegram_push
from ..config import load_config
from ..sources import news, taifex

router = APIRouter(prefix="/api")

MARKETS = ("tw", "us", "jp")
_TAIPEI = timezone(timedelta(hours=8))

_MARKET_META = {
    "tw": ("🇹🇼", "台股"),
    "us": ("🇺🇸", "美股"),
    "jp": ("🇯🇵", "日股"),
}
_PUSH_PLAN = {
    "morning": ("🌅 07:00 盤前早報", (("us", 10), ("jp", 10), ("tw", 10))),
    "midday": ("☀️ 12:00 午間財經快訊", (("tw", 10), ("jp", 10), ("us", 10))),
    "afternoon": ("🏁 17:00 收盤快訊", (("tw", 10), ("jp", 10), ("us", 10))),
    "evening": ("🌙 21:10 晚間全球焦點", (("us", 10), ("jp", 10), ("tw", 10))),
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


def _should_show_night(slot: str, today=None) -> bool:
    """台指期夜盤只在**平日的 17:00／21:10** 顯示（使用者規格）。

    週末刻意不顯示是為了資料誠信，不是懶：週五夜盤到週六 05:00 就結束，週末推播能拿到的
    必然是「上一個交易日夜盤的收盤」，掛在週日的訊息上會被讀成當下——同本專案「不可拿
    別天的收盤冒充今天」那條規矩。與其加一堆註解解釋，不如不顯示。
    07:00 夜盤早已收、12:00 夜盤還沒開，兩者本來就沒有「當下夜盤」可言。
    """
    import datetime as _dt
    d = today or _dt.date.today()
    return slot in ("afternoon", "evening") and d.weekday() < 5


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

    # 夜盤：**日期對不上就整行不出現**。實測期交所 Q_FUT 在非交易時段回的是「最後一個
    # 交易時段」的數字（2026-09-06 21:58 打回來的是 09-04 的），不比對就會把上一個交易日
    # 的夜盤當成當下。顯示與否由呼叫端的 _should_show_night 決定，這裡只管「有沒有資格畫」。
    night, n_chg = snapshot.get("台指期夜盤"), snapshot.get("台指期夜盤漲跌")
    n_date = snapshot.get("台指期夜盤日期")
    if night is not None and n_date and (not date or n_date == date):
        seg = f"• 台指期夜盤　{_fmt_num(night)}"
        if n_chg is not None:
            arrow = "▲" if n_chg > 0 else ("▼" if n_chg < 0 else "▬")
            seg += f"　{arrow}{_fmt_num(abs(n_chg))}"
        lines.append(seg)
    turnover = snapshot.get("成交金額(億)")
    lines.append(f"• 成交　{_fmt_num(turnover)} 億" if turnover is not None else "• 成交　—")

    intl = snapshot.get("那斯達克100/費半等國際指標若有") or {}
    labels = (("sox", "費半"), ("n225", "日經"), ("kospi", "韓股"), ("vix", "VIX"))
    prev = snapshot.get("國際前值") or {}
    # 日經永遠顯示（缺值寫「—」）：本站的三個市場是台／美／日，日股大盤缺席本身就是
    # 資訊——先前它是 NULL 就整個消失，讀者只會以為「今天沒這欄」而不會去補資料。
    # 韓股／VIX 是脈絡不是主體，有才顯示。
    always = {"n225", "sox"}
    picked = []
    for key, name in labels:
        cur = intl.get(key)
        if cur is None:
            if key in always:
                picked.append(f"{name} —")
            continue
        digits = 2 if key == "vix" else 0
        seg = f"{name} {_fmt_num(cur, digits)}"
        base = prev.get(key)
        if base:
            pct = (cur - base) / base * 100
            # 四捨五入後是 0.00% 就不寫。國際指數是「上一個交易時段收盤」，隔日常常
            # 還沒更新而與前一列同值，此時的「▬0.00%」是資料尚未換日的假象，
            # 不是「今天沒漲跌」——印出來只會被誤讀。
            if abs(pct) >= 0.005:
                seg += f" {'▲' if pct > 0 else '▼'}{abs(pct):.2f}%"
        picked.append(seg)
    if picked:
        lines.append("• " + "　".join(picked))
    return "\n".join(lines)


_READ_TITLE_MAX = 38     # 連結文字長度上限；超過截斷，避免一行在手機上洗版


def build_reading_links(markets: dict, per_market: int = 3) -> str:
    """底部「延伸閱讀」——**分市場列出原始標題**，每則掛該篇的原始網址。

    仍叫「延伸閱讀」而不是「來源」，但意義已比以前強：這些**就是原始文章本身**
    （原標題配原網址），不是對某一條 bullet 的引用宣稱。

    **為什麼不把連結掛在每一條 bullet 上**（使用者原本的想法，討論後改成這樣）：
    摘要是 AI 綜合改寫，一句可能併了兩三則新聞，「這句出自哪一篇」本身沒有唯一答案。
    就算讓模型輸出來源編號、由 Python 附網址（網址不會亂編），仍擋不住「編號選錯」——
    結果是連結看起來正常、點下去卻是另一篇，而**讀者不點開根本發現不了**。那比沒有
    連結更糟。改成獨立列出原始標題後，歸屬 100% 正確，這個風險整個消失。

    長度：Google 新聞的 CBMi… 網址實測 174～354 字元且解不出短網址（protobuf 編碼），
    所以 per_market 直接決定訊息長度。預設 3（9 條約 +2,500 字元）；調大要留意 4096
    上限——telegram_push.split_message 會在換行處切、不會切壞連結，但訊息則數會變多。
    """
    blocks = []
    for key in ("tw", "jp", "us"):
        flag, name = _MARKET_META[key]
        rows = []
        for item in (markets.get(key) or []):
            url = item.get("url")
            if not url:
                continue                      # 沒網址就略過，不掛死連結
            title = str(item.get("title") or item.get("source") or name).strip()
            if len(title) > _READ_TITLE_MAX:
                title = title[:_READ_TITLE_MAX] + "…"
            rows.append("• " + telegram_push.mdv2_link(title, url))
            if len(rows) >= max(1, per_market):
                break
        if rows:
            blocks.append(telegram_push.escape_mdv2(f"{flag} {name}") + "\n" + "\n".join(rows))
    if not blocks:
        return ""
    return telegram_push.escape_mdv2("🔗 延伸閱讀") + "\n" + "\n".join(blocks)


# Telegram 端的強調。與網頁同一套分工：**數字用粗體、關鍵詞用底線**——兩個不同的軸，
# 疊在一起不會互相稀釋（網頁那邊是亮度 vs 底線，這裡是粗體 vs 底線）。
# 關鍵詞表與 `web/app.js` 的 `_FACT_KEY` 同義但**各自維護**：那邊是 JS、這邊是 Python，
# 沒有共用的執行環境；改動時兩邊都要動（同 CLAUDE.md 對「兩份文件會漂移」的提醒）。
# **只收「狀態改變」，不收「例行活動的名稱」。** 第一版把 財報／配息／法說／外資／
# 買超／利率 這些也放進來，使用者回報「底線都劃在不關鍵的文字」——問題不是標太多
# （實測 18 條裡最高的「財報」也只有 11%），而是這些詞在財經句子裡本來就無所不在，
# 它們描述的是**每天都在發生的事**，不是這一則的重點。真正決定「接下來會怎樣」的是
# 制度變動、財測調整、重大公司行動這類**改變狀態**的詞。名字與數字才是句子的主體，
# 數字已由粗體負責。
# 另外每個詞都要確認不是別的詞的一部分：`利率` 已移除，它會標進「殖**利率**」
# （實跑輸出過）；其餘各詞經檢查都不是常見長詞的子字串。
_TG_KEY = re.compile("(" + "|".join([
    # 制度與規則改變
    # 複合詞排在前面（正則交替是先到先贏）：「處置股新制」若只認 `處置`＋`新制`，
    # 中間的「股」會把底線切成兩段，看起來像壞掉——使用者抱怨的正是這種破碎感。
    "處置股", "股票分割", "現金增資", "庫藏股",
    "處置", "新制", "鬆綁", "停牌", "下市", "關稅", "解禁", "禁令",
    # 公司重大行動
    "併購", "收購", "增資", "減資", "拆股", "分割", "回購", "違約", "召回", "停產",
    # 預期改變
    "財測", "上修", "下修", "上調", "下調", "創新高", "創新低", "漲停", "跌停",
    # 貨幣政策轉向
    "降息", "升息",
]) + ")")
# **必須帶單位才粗體**（`+` 而不是 `*`）。粗體很重，只留給「量測值」。
# 實跑輸出過 `H*2*O Retailing`、`標普 *500* 指數`、`\(*8242*\)`——名稱與代號裡的數字
# 不是數據，粗體之後反而讓人找不到真正的數字。帶單位是「這是量測值」最可靠的訊號。
# 前面再擋一個拉丁字母，避免 `H2O`／`COVID19` 這種字母數字混排被切開。
_TG_NUM = re.compile(
    r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:美元|日圓|港幣|人民幣|%|％|倍|點|元|億|萬|兆|口|張|家|人|年|月|日|檔))+")
# 哨兵字元：先在**未跳脫**的文字上標記，跳脫完再換成真正的語法符號。
# 直接在跳脫後的字串上塞 `*` 會踩到反斜線（`1\.09` 的小數點已被跳脫），
# 正則要處理跳脫過的形式，既難寫又容易漏；用控制字元當佔位最穩——
# escape_mdv2 只跳脫 MarkdownV2 的特殊字元集，控制字元原樣通過。
_B0, _B1, _U0, _U1 = "\x01", "\x02", "\x03", "\x04"


def emphasize_push_body(text: str) -> str:
    """在**跳脫前**的內文標出數字與關鍵詞，回傳含哨兵的字串。"""
    out = []
    for line in (text or "").splitlines():
        if line.startswith(("•", "🔥")):     # 只強調條列本身，段落標題與免責聲明不動
            seen = set()

            def key_sub(m):
                w = m.group(1)
                if w in seen:
                    return w                 # 同一行同一個詞只標第一次（同網頁的理由）
                seen.add(w)
                return f"{_U0}{w}{_U1}"

            line = _TG_KEY.sub(key_sub, line)
            line = _TG_NUM.sub(lambda m: f"{_B0}{m.group(0)}{_B1}", line)
        out.append(line)
    return "\n".join(out)


def apply_mdv2_marks(escaped: str) -> str:
    """跳脫完成後，把哨兵換成 MarkdownV2 的粗體／底線符號。

    **相鄰的兩段要先併成一段。** 「聯準會降息」中間沒有分隔字，兩個關鍵詞各自包起來
    會產生 `__聯準會____降息__`——那個 `____` 會讓 Telegram 的解析器認不出配對，整則
    退回純文字（而且是**無聲**的：訊息照送、只是所有格式失效）。粗體同理。
    """
    merged = escaped.replace(_U1 + _U0, "").replace(_B1 + _B0, "")
    return (merged.replace(_B0, "*").replace(_B1, "*")
                  .replace(_U0, "__").replace(_U1, "__"))


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
        # 先在未跳脫的文字上塞哨兵 → 跳脫 → 再換成 * 與 __（見 emphasize_push_body）
        parts.append(apply_mdv2_marks(
            telegram_push.escape_mdv2(emphasize_push_body(body))))
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

# 模型寫「關鍵數據」時混用頓號與逗號——實跑輸出（2026-08-18）用的是「，」：
#   「加權指數收在 45308.68 點，下挫 548.59 點，震盪幅度逾 800 點。」
# 只以「、」切句時整串會被當成**一個**含盤面詞的子句而全部丟掉，連同後面真正的增量。
_CLAUSE_SEP = re.compile(r"[、，,；;]")

# 模型沒有可驗證數字時的佔位語。『無』是新 prompt 的寫法（省 token 也省版面），
# 『來源未提供…』是舊 prompt 的，兩者都要當成「沒有數據」。
_NO_DATA = ("來源未提供", "無可驗證", "尚無數據")


def snapshot_numbers(snapshot: dict | None) -> frozenset:
    """盤面區塊用到的數字（取小數點前的整數部分），供 `useful_data` 比對。

    `_SNAPSHOT_TERMS` 只擋得住「有講出欄位名」的子句；實跑輸出過「下挫 548.59 點」
    這種**沒有欄位名、數字卻正是盤面漲跌**的寫法。盤面是程式從 market_daily 算的、
    這裡是 LLM 轉述，一旦有出入就會在同一則訊息裡自相矛盾，所以要比數字不只比詞。
    """
    out = set()

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            out.add(str(abs(int(value))))
        elif isinstance(value, str):
            for n in _NUM.findall(value):
                out.add(n.split(".")[0].replace(",", ""))

    walk(snapshot or {})
    out.discard("")
    return frozenset(out)


def headline_digest(markets: dict, slot: str, per_market: int = 6) -> str:
    """AI 不可用時的替代內文：**直接列原始新聞標題**。

    先前這種情況只送一句「AI 摘要暫時無法使用」，但那時候三個市場的新聞**其實都已經
    抓回來了**（60 則標題就在手上），等於白白丟掉。實際收到的推播只有盤面加一句道歉，
    使用者要的是內容——有內容就該給，只是換一種（未經改寫的）呈現。

    輸出與 `telegram_digest` 同樣是 `• ` 條列，才能繼續走同一條管線
    （投資建議過濾、首則標 🔥、跳脫、粗體／底線強調）。標題是媒體原文、沒有經過改寫，
    所以更需要那道投資建議過濾——原始標題出現「逢低布局」這類字眼的機率比 AI 改寫後高。
    日股標題維持日文原文：這裡沒有翻譯能力，硬譯不如照實呈現（來源標了株探）。
    """
    _, plan = _PUSH_PLAN.get(slot, _PUSH_PLAN["afternoon"])
    blocks = []
    for market, _count in plan:
        items = (markets.get(market) or [])[:per_market]
        if not items:
            continue
        flag, name = _MARKET_META[market]
        lines = [f"{flag} {name}｜標題快覽"]
        lines += [f"• {it.get('title')}" for it in items if it.get("title")]
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    if not blocks:
        return _AI_UNAVAILABLE_NOTE
    blocks.append("⚠️ 非投資建議，資訊僅供研究參考")
    return "\n\n".join(blocks)


def useful_data(title: str, data: str, snapshot: dict | None = None) -> str:
    """關鍵數據只有在「標題與盤面都沒講過」時才值得附上，否則就是噪音。

    **逐子句判斷**（不是整段一起留或一起丟）。實測 2026-08-18 那場 18 則裡只有
    5 則的關鍵數據送得出去，一半的損失來自整段丟：模型用「，」而不是「、」連接，
    舊版只以「、」切句，於是「加權指數收在 45308.68 點，下挫 548.59 點，震盪幅度
    逾 800 點」被當成**一個**含盤面詞的子句而整串消失。

    一個子句要留下來，四關都得過：
    1. 不含盤面欄位名（見 `_SNAPSHOT_TERMS`）——那些讀數 📈 盤面 已經用程式算過。
    2. 真的有數字。沒有數字的「關鍵數據」多半是複述，不值得占版面。
    3. 數字不是標題已經講過的（比**數字**不比字串：取小數點前的整數部分、去千分位）。
    4. 數字不是盤面已經講過的（`snapshot` 有帶才比）。第 1 關只擋得住有講出欄位名的
       寫法，實跑輸出過「下挫 548.59 點」這種沒有欄位名、數字卻正是盤面漲跌的句子。
    """
    if not data or any(t in data for t in _NO_DATA):
        return ""
    snap_nums = snapshot_numbers(snapshot) if snapshot else frozenset()
    bare = title.replace(",", "")
    kept = []
    for seg in _CLAUSE_SEP.split(data):
        seg = seg.strip().strip(" 。.")
        if not seg or any(t in seg for t in _SNAPSHOT_TERMS):
            continue
        nums = [n.split(".")[0].replace(",", "") for n in _NUM.findall(seg)]
        if not nums:
            continue
        if all(n in bare for n in nums):
            continue
        if snap_nums and all(n in snap_nums for n in nums):
            continue
        kept.append(seg)
    return "、".join(kept)


def telegram_digest(summary: str, slot: str, report_date: str = "",
                    snapshot: dict | None = None) -> str:
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
            data = useful_data(item["title"], item["data"], snapshot)
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
    keys = ("sox", "n225", "kospi", "vix")
    # 漲跌%只跟**緊鄰的前一列**比，不逐欄往回找最近的非空值。
    # 一開始是往回找的，實測就出事：韓股本日 6,359、最近一筆非空是 5 天前的 5,593，
    # 算出「▲13.68%」掛在盤面上——那是跨多日的累計，讀者卻會當成今天的漲跌。
    # 「日漲跌」的定義就是「對前一個交易日」；那一天沒有值就是算不出來，
    # 寧可不顯示，也不要端出一個看起來像當日、實際上不是的數字。
    prow = c.execute(
        "SELECT * FROM market_daily WHERE date < ? ORDER BY date DESC LIMIT 1",
        (m.get("date"),)).fetchone()
    pm = dict(prow) if prow else {}
    prev = {k: pm[k] for k in keys if pm.get(k) is not None}
    return {
        "日期": m.get("date"), "加權指數": m.get("taiex"), "加權漲跌": m.get("taiex_chg"),
        "成交金額(億)": m.get("turnover"), "台指期": m.get("tx_price"),
        "那斯達克100/費半等國際指標若有": {
            k: m.get(k) for k in keys if m.get(k) is not None},
        "國際前值": prev,
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
    # v7：盤面加上國際指數漲跌%＋日經缺值也顯示，推播內文加上粗體／底線強調。
    # v8：關鍵數據改為「補回濃縮標題時捨棄的原始數字」，佔位語由『來源未提供…』縮為『無』。
    # 推播格式一改就要進版，否則今天稍早存的舊格式快取會被當成今天的結果直接送出。
    key = f"news:v8:{today}:{slot}"
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
    # 夜盤只在平日 17:00／21:10 抓（見 _should_show_night）——其餘時段沒有「當下夜盤」
    # 可言，不必為此多打一次期交所。抓失敗就當作沒有，不讓它拖垮整則推播。
    if _should_show_night(slot):
        try:
            nq = taifex.fetch_tx_night_quote()
            if nq.get("tx_night_price") is not None:
                snapshot["台指期夜盤"] = nq["tx_night_price"]
                snapshot["台指期夜盤漲跌"] = nq["tx_night_chg"]
                snapshot["台指期夜盤日期"] = nq["tx_night_date"]
        except Exception:  # noqa: BLE001
            pass
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
    # AI 不可用時**不要只送一句道歉**：新聞這時候其實都已經抓回來了（三個市場共 60 則
    # 標題就在 markets 裡），退成「標題快覽」至少還是今天的新聞。實際發生過一次
    # 21:10 推播只有盤面加一句「AI 摘要暫時無法使用」，內容整段消失。
    raw_push = (telegram_digest(summary, slot, today, snapshot) if result.get("enabled")
                else headline_digest(markets, slot))
    telegram_text = compose_push_message(raw_push, snapshot, markets, slot, today)
    payload = {"date": today, "slot": slot, "summary": summary,
              "telegram_text": telegram_text,
              "enabled": result.get("enabled", False),
              "fallback": fallback_flags, "markets": markets}
    if result.get("enabled"):
        set_ai_cache(c, key, payload)
    return payload


def pick_headlines(markets: dict, per_market: int = 1) -> list[dict]:
    """每個市場取前 per_market 則 → [{market, flag, name, title, url}]。

    每市場各取一則（而不是「台股取三則」）是刻意的：總覽這一格要的是「今天世界發生什麼」，
    三則全是台股就跟下面整頁的台股資料重複了。順序照 _MARKET_META，與新聞頁一致——
    同一份資料不該在兩個地方用不同的排序，讀者會以為那是某種權重。
    """
    out = []
    for key, (flag, name) in _MARKET_META.items():
        for it in (markets.get(key) or [])[:per_market]:
            if it.get("title"):
                out.append({"market": key, "flag": flag, "name": name,
                            "title": it["title"], "url": it.get("url") or ""})
    return out


def headlines_logic(c, n: int = 3) -> dict:
    """總覽用的新聞標題：**只讀已存在的快取，絕不抓取、絕不呼叫 Gemini。**

    這一格是「順帶看一眼」，不值得為它付出代價。直接叫 news_logic 會有兩個問題：
    快取沒中時它會去抓三個市場的新聞（株探還要遵守 3 秒 Crawl-delay），然後打一次
    Gemini——**開一次總覽就吃掉一格免費層額度（一天只有 20 次）**。所以這裡只掃
    `news:v8:{date}:{slot}` 這些既有的鍵，全都沒有就回空陣列，前端整塊不顯示。

    掃描順序是今天由晚到早、再退到昨天：07:00 之前今天還沒有任何一場，
    這時顯示昨晚那場並標上它的日期，比顯示空白有用。
    """
    today = datetime.now(_TAIPEI)
    for day in (today, today - timedelta(days=1)):
        ds = day.strftime("%Y-%m-%d")
        for slot in ("evening", "afternoon", "midday", "morning"):
            cached = get_ai_cache(c, f"news:v8:{ds}:{slot}")
            if not cached:
                continue
            items = pick_headlines(cached.get("markets") or {})[:max(1, n)]
            if items:
                return {"date": ds, "slot": slot, "items": items}
    return {"date": None, "slot": None, "items": []}


@router.get("/news/headlines")
def get_news_headlines(n: int = 3):
    return headlines_logic(conn(), n=n)


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
