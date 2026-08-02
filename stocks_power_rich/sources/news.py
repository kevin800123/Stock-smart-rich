"""每日財經新聞：台股／美股走 Google 新聞 RSS（白名單過濾），日股走株探 kabutan.jp。

純函式（解析）+ 薄 wrapper（網路）分離，呼叫端一律拿 [{title,url,source,domain,
published,category}]，日股額外回傳 fallback 旗標（見 fetch_market_news）。
"""
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

# 台股／美股走 Google 新聞 RSS；日股走株探（見 fetch_kabutan）。
NEWS_QUERIES = {"tw": "台股", "us": "美股"}

# 白名單：每日財經/hermes-skill/finance/daily-finance-brief/sources.md 的 20 個網域
# （國際通訊社/財經媒體 8、台灣財經媒體 5、官方機構 7），加上中央社／共同社——
# 兩者是與名單內 Reuters／AP 同級的國家通訊社，原名單偏重西方媒體、對中文查詢
# 覆蓋不足（實測「台股」查詢在原 20 個網域下僅命中 21/100，勉強達標 20）。
WHITELIST = {
    "reuters.com", "bloomberg.com", "cnbc.com", "ft.com", "wsj.com",
    "apnews.com", "cnn.com", "nikkei.com",
    "cnyes.com", "moneydj.com", "udn.com", "ctee.com.tw", "chinatimes.com",
    "federalreserve.gov", "bls.gov", "bea.gov", "cbc.gov.tw", "twse.com.tw",
    "dgbas.gov.tw", "stat.gov.tw",
    "cna.com.tw", "kyodonews.net",
}

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_JP_FALLBACK_QUERY = "日經指數"

# 株探沒有 RSS（/rss/、/news/rss/、/rss/news.rdf 皆 404），只能解析 HTML。
# robots.txt 允許 /news/（只 Disallow /94446337/ 與 /search*），但要求
# Crawl-delay: 3——兩頁之間必須睡 3 秒。實測第 1+2 頁共可取得 27 則不重複新聞。
_KABUTAN_PAGES = (
    "https://kabutan.jp/news/marketnews/",
    "https://kabutan.jp/news/marketnews/?page=2",
)
_KABUTAN_CRAWL_DELAY = 3

_UA = {"User-Agent": "Mozilla/5.0"}
_THREE_LEVEL_2ND = {"com", "org", "gov", "net", "co"}   # .com.tw / .co.jp 這類三段式


def registrable_domain(host: str) -> str:
    """host → 可註冊網域（去 www.；.com.tw/.co.jp 這類三段式網域整段保留）。"""
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _THREE_LEVEL_2ND and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


_TITLE_SOURCE_SUFFIX = re.compile(r"\s+-\s+[^-]+$")


def parse_news_rss(xml_bytes) -> list:
    """Google 新聞 RSS → [{title,url,source,domain,published,category}]。

    壞 XML／空回應一律回空 list，不拋例外——單次壞回應不該讓整個排程 job 掛掉。
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for item in root.findall(".//item"):
        title_el, link_el = item.find("title"), item.find("link")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        if not title or not link:
            continue
        # <title>本文標題 - 來源</title>：來源已經有獨立的 <source> 欄位，
        # 標題留著尾巴會讓每則新聞把來源重複顯示一次
        title = _TITLE_SOURCE_SUFFIX.sub("", title)
        src_el = item.find("source")
        source_name = (src_el.text or "").strip() if src_el is not None else ""
        source_url = src_el.attrib.get("url", "") if src_el is not None else ""
        domain = registrable_domain(urllib.parse.urlparse(source_url).netloc)
        pub_el = item.find("pubDate")
        published = None
        if pub_el is not None and pub_el.text:
            try:
                dt = parsedate_to_datetime(pub_el.text.strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                published = dt.isoformat()
            except (ValueError, TypeError):
                published = None
        out.append({"title": title, "url": link, "source": source_name or domain,
                    "domain": domain, "published": published, "category": None})
    return out


_KABUTAN_ROW = re.compile(
    r'<td class="news_time"><time datetime="([^"]+)"[^>]*>.*?</time></td>\s*'
    r'<td><div class="newslist_ctg[^"]*">([^<]*)</div></td>\s*'
    r'<td><a href="([^"]+)">([^<]+)</a>',
    re.S,
)


def parse_kabutan(html: str) -> list:
    """株探 /news/marketnews/ 列表頁 → [{title,url,source,domain,published,category}]。

    版面改版導致一列都解析不到時回空 list——交給 fetch_market_news 判斷是否要
    退回 Google 新聞，解析函式本身不做這個決定（純函式不碰網路，也不猜備援策略）。
    """
    out = []
    for dt_raw, category, href, title in _KABUTAN_ROW.findall(html or ""):
        try:
            published = datetime.fromisoformat(dt_raw).isoformat()
        except ValueError:
            published = None
        url = href if href.startswith("http") else "https://kabutan.jp" + href
        out.append({"title": title.strip(), "url": url, "source": "株探",
                    "domain": "kabutan.jp", "published": published,
                    "category": category.strip() or None})
    return out


_TITLE_DEDUP_LEN = 12
_TITLE_NOISE = re.compile(r"[\s、，。！？「」『』【】\-—:：·,.!?()（）]")


def _normalize_title(title: str) -> str:
    """去標點與空白後取前 N 字比對——同一則新聞常被多站轉載，標題常有些微差異。"""
    return _TITLE_NOISE.sub("", title or "")[:_TITLE_DEDUP_LEN]


def pick_top(items: list, n: int = 20, hours: int = 36,
             now: datetime | None = None) -> list:
    """濾時效 → 依標題前綴去重 → 取前 n（新到舊）。

    hours=36 而非 24：07:00 那場要涵蓋前一晚美股收盤，24 小時在週一早上會把
    週五晚間的新聞濾掉（週五 21:00 到週一 07:00 已超過 24 小時）。
    無時間戳的項目排在有時間戳的之後，當作湊數用（多半是解析失敗的少數個案），
    不因為缺一個欄位就整則丟棄。
    """
    # now 是測試用注入點；正式抓取一律用 UTC aware datetime。所有來源的 published
    # 也必須保留時區（株探是 +09:00），這樣換算到台北或 UTC 後比較都等價。
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=hours)
    dated, undated = [], []
    for it in items:
        pub = it.get("published")
        dt = None
        if pub:
            try:
                dt = datetime.fromisoformat(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                dt = None
        if dt is None:
            undated.append(it)
        elif dt >= cutoff:
            dated.append((dt, it))
    dated.sort(key=lambda x: x[0], reverse=True)
    ordered = [it for _, it in dated] + undated
    seen, out = set(), []
    for it in ordered:
        key = _normalize_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= n:
            break
    return out


def fetch_google_news(market: str, n: int = 20,
                      now: datetime | None = None) -> list:
    """market ∈ NEWS_QUERIES → 白名單過濾後的新聞清單（新到舊，至多 n 則）。"""
    query = NEWS_QUERIES.get(market)
    if not query:
        return []
    return _fetch_google_news_query(query, n, now=now)


def _fetch_google_news_query(query: str, n: int,
                             now: datetime | None = None) -> list:
    try:
        r = httpx.get(_GOOGLE_NEWS_RSS,
                      params={"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
                      timeout=20, headers=_UA)
        items = parse_news_rss(r.content) if r.status_code == 200 else []
    except Exception:  # noqa: BLE001 — 單次抓取失敗回空，呼叫端不因此中斷
        items = []
    whitelisted = [it for it in items if it["domain"] in WHITELIST]
    return pick_top(whitelisted, n=n, now=now)


def fetch_kabutan(n: int = 20, now: datetime | None = None) -> list:
    """株探市場新聞，兩頁之間依 robots.txt 睡 Crawl-delay 秒。"""
    out = []
    for i, url in enumerate(_KABUTAN_PAGES):
        if i:
            time.sleep(_KABUTAN_CRAWL_DELAY)
        try:
            r = httpx.get(url, timeout=20, headers=_UA)
            if r.status_code == 200:
                out.extend(parse_kabutan(r.text))
        except Exception:  # noqa: BLE001
            pass
    return pick_top(out, n=n, now=now)


def fetch_market_news(market: str, n: int = 20,
                      now: datetime | None = None):
    """market ∈ {tw,us,jp} → (新聞清單, fallback)。

    fallback=True 只會發生在 jp：株探解析不到任何一列時（多半是改版）退回
    Google 新聞的「日經指數」查詢，呼叫端必須把這個旗標往上傳、標在回應裡，
    不能悄悄換了來源卻讓使用者以為還是株探。
    """
    if market == "jp":
        items = fetch_kabutan(n, now=now)
        if items:
            return items, False
        return _fetch_google_news_query(_JP_FALLBACK_QUERY, n, now=now), True
    return fetch_google_news(market, n=n, now=now), False
