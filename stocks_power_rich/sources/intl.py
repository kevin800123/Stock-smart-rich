"""國際市場資料抓取。兩套來源，用途不同：

- **國際指數歷史**（總覽的 費半/VIX/日經/KOSPI/黃金/日圓/台幣/比特幣 卡）：來源 yfinance，
  需要多日歷史做場次對齊，故仍走 `fetch_intl_history`＋Yahoo chart API 備援。
- **海期監控**（fetch_futures_monitor）：來源 **TradingView 公開 scanner**（不用 key、單一
  POST 涵蓋期貨/商品/外匯/股票）。Yahoo 的 download／chart API 皆被 Zeabur 出站 IP 429 擋死，
  2026-07 全面換掉；只需當下報價、不需歷史，TradingView scanner 剛好夠用。
"""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import yfinance as yf

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
_CHART_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _fetch_chart_raw(sym: str, range_: str = "5d", interval: str = "1d") -> dict | None:
    """直連 chart API 抓單一代碼原始 payload（^ 等符號需編碼進路徑）。失敗回 None。"""
    try:
        r = httpx.get(_CHART_URL + quote(sym, safe=""),
                      params={"range": range_, "interval": interval},
                      timeout=15, headers=_CHART_UA)
        if r.status_code == 200:
            return r.json()
    except Exception:  # noqa: BLE001 — 備援失敗不影響其他代碼
        pass
    return None


# 註：曾有 fetch_futures_live（逐檔 chart meta 抓盤中準即時價，供「海期監控」每 2 分鐘
# 輪詢）與 parse_chart_quote，2026-07 隨「海期監控」改為每日排程 07:30/21:30 兩次更新
# 一併移除——那樣的輪詢頻率正是把 Zeabur 的出站 IP 打到被 Yahoo 429 限流的主因（net-check
# 診斷實測 yfinance 與 chart API 備援皆遭拒），改成一天兩次後才真的降得下請求量。
# 註：曾有 fetch_intl_indices（抓「當下最新值」）供每日更新使用，已於 2026-07 移除。
# 它回傳的是「更新程式跑的當下」的報價，而不是任何一場的收盤，等於把不確定日期的價格
# 寫進資料日 D 那一列；且因 _backfill_intl 只填 NULL 不覆蓋，寫錯的值永遠不會被修正。
# 現在每日更新與歷史回補共用 fetch_intl_history + pick_close_for 一套場次定義。


# ===== 歷史回補 =====
# fetch_intl_indices 只給「當下最新值」，沒有任何機制把歷史補回來。後果有二：
#   ① 新加入的代碼（vix 2026-06-25、jpy 07-02、twd 07-14）只能從加入當天往後長；
#   ② yfinance 偶發失敗那天就永久留空（_refresh_recent 只治三大法人與融資券）。
# 以下三個純函數把「某代碼的歷史收盤」對齊到台股資料日 D，供 updater 回補缺值。

# 台北 D 日晚間檢視時，哪些代碼「D 當日的收盤」已經產生。
# 亞股約 14:00 收盤 → 已有 D 當日值；其餘（美股指數 04:00 才收、24 小時商品尚未結算）
# 當下最新的完整場次是 D 之前那一場。
INTL_SAME_DAY = {"n225", "kospi"}


def parse_history_closes(rows) -> dict:
    """[(session_date, close|None)] → {session_date: {value, chg_pct}}。

    close 為 None（休市/缺報價）的日子不產生列；漲跌% 以「前一個有效收盤」為基準，
    不是前一列日期——中間隔幾天沒報價時，用日期相減會算出錯誤的基準。
    """
    out, prev = {}, None
    for ds, close in rows:
        if close is None:
            continue
        v = round(float(close), 2)
        out[ds] = {"value": v, "chg_pct": round((v - prev) / prev * 100, 2) if prev else None}
        prev = v
    return out


def pick_close_for(history: dict, ds: str, same_day: bool) -> dict | None:
    """取台股資料日 ds 該有的收盤；取不到回 None（不硬湊、不往未來取）。

    兩種取法對應兩種不同的「這一欄是什麼」，不是同一件事的寬鬆/嚴格版：
    - same_day=True（日經/KOSPI）：這一欄就是「該市場在 D 當天的收盤」，故**只認 D 當天**。
      D 當天尚未收盤（白天跑的更新）或該市場當天休市 → 留 None 等下次回補。
      這裡若退一步取前一場，就是把別天的收盤貼上 D 的標籤——本專案明令禁止。
    - same_day=False（美股/24 小時商品）：這一欄的定義本來就是「台北 D 日晚間可得的最近一場」，
      D 當天那場還沒開始，所以取 D **之前**最近一場。取的是最近一個「場次」而非 D 減一個曆日，
      週一才會正確落到上週五而不是沒有場次的週日。
    """
    if same_day:
        return history.get(ds)
    cands = [d for d in history if d < ds]
    return history[max(cands)] if cands else None


def parse_chart_history(payload) -> dict:
    """Yahoo v8 chart payload（timestamp 陣列版）→ {session_date: {value, chg_pct}}。

    timestamp 是每根日 K 的開盤 epoch；各市場的開盤時間換算成 UTC 後仍落在同一個
    場次日期（日經 09:00 JST＝00:00 UTC、美股 09:30 ET＝13:30/14:30 UTC），故直接取 UTC 日期。
    """
    from datetime import datetime, timezone
    try:
        res = payload["chart"]["result"][0]
        ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return {}
    rows = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(ts or [], closes or [])]
    return parse_history_closes(rows)


def fetch_intl_history(tickers: dict, days: int = 120) -> dict:
    """逐代碼抓近 days 天日線收盤 → {key: {session_date: {value, chg_pct}}}。

    yfinance 失敗 → 直連 chart API 備援（`yf.Ticker().history()` 走 cookie/crumb 握手，
    正是機房 IP 會被擋的那段）。兩條路都失敗只是該 key 缺席，呼叫端維持 NULL 等下次回補。
    """
    out = {}
    for key, sym in tickers.items():
        hist = {}
        try:
            h = yf.Ticker(sym).history(period=f"{max(days, 5)}d")["Close"]
            rows = [(str(idx)[:10], None if v != v else v) for idx, v in h.items()]  # v!=v → NaN
            hist = parse_history_closes(rows)
        except Exception:  # noqa: BLE001 — 落到 chart API 備援
            hist = {}
        if not hist:
            rng = f"{max(days // 30, 1)}mo"
            payload = _fetch_chart_raw(sym, range_=rng, interval="1d")
            hist = parse_chart_history(payload) if payload else {}
        if hist:
            out[key] = hist
    return out


# 海期監控：五大分類 × (顯示名, TradingView 代碼)。中國A50 無穩定代碼故不列。
# 2026-07 從 yfinance 換成 TradingView 公開 scanner：Yahoo 的 download／chart API 皆被
# Zeabur 出站 IP 整段 429 擋死（net-check 診斷實測，排程降頻也無效）。TradingView scanner
# 不用 key、單一 POST 涵蓋全部四類資產，且從 Zeabur 實測可通（tv-check 診斷 ok:true）。
# 期貨用近月連續合約（"1!"），日經/恆生/法蘭克福維持現貨指數（同舊 yfinance 口徑）。
OS_FUTURES: list[tuple[str, list[tuple[str, str]]]] = [
    ("指數期貨", [("小道瓊", "CBOT_MINI:YM1!"), ("小那斯達克", "CME_MINI:NQ1!"),
                  ("小S&P500", "CME_MINI:ES1!"), ("小羅素", "CME_MINI:RTY1!"),
                  ("日經", "TVC:NI225"), ("恆生", "TVC:HSI"), ("法蘭克福", "XETR:DAX")]),
    ("能源金屬", [("輕原油", "NYMEX:CL1!"), ("天然氣", "NYMEX:NG1!"), ("高級銅", "COMEX:HG1!"),
                  ("白銀", "COMEX:SI1!"), ("黃金", "COMEX:GC1!"), ("白金", "NYMEX:PL1!")]),
    ("農產品", [("黃豆", "CBOT:ZS1!"), ("小麥", "CBOT:ZW1!"), ("玉米", "CBOT:ZC1!"),
                ("咖啡", "ICEUS:KC1!"), ("11號糖", "ICEUS:SB1!"), ("可可", "ICEUS:CC1!"),
                ("黃豆油", "CBOT:ZL1!")]),
    ("外匯", [("美元指數", "TVC:DXY"), ("澳幣", "FX:AUDUSD"), ("英鎊", "FX:GBPUSD"),
              ("加幣", "FX:USDCAD"), ("歐元", "FX:EURUSD"), ("日圓", "FX:USDJPY"),
              ("瑞朗", "FX:USDCHF")]),
    ("美股", [("輝達", "NASDAQ:NVDA"), ("蘋果", "NASDAQ:AAPL"), ("Alphabet", "NASDAQ:GOOGL"),
              ("微軟", "NASDAQ:MSFT"), ("亞馬遜", "NASDAQ:AMZN"), ("META", "NASDAQ:META"),
              ("特斯拉", "NASDAQ:TSLA"), ("台積電ADR", "NYSE:TSM"), ("博通", "NASDAQ:AVGO"),
              ("甲骨文", "NYSE:ORCL"), ("美光", "NASDAQ:MU"), ("英特爾", "NASDAQ:INTC"),
              ("美超微", "NASDAQ:AMD"), ("Palantir", "NASDAQ:PLTR")]),
]

_TV_SCAN_URL = "https://scanner.tradingview.com/global/scan"
_TV_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def parse_tv_scan(payload) -> dict:
    """TradingView scanner 回應 → {ticker: {value, chg, chg_pct}}。

    每列 `d` 依 columns 順序＝[close, change(百分比), change_abs]；缺 close 的列（休市/無報價）
    跳過，回傳只含有效報價的代碼。以 "s"(代碼) 對映——回應順序未必等於請求順序。
    """
    out = {}
    for row in (payload.get("data") or []):
        d = row.get("d") or []
        if len(d) < 3 or d[0] is None:
            continue
        out[row.get("s")] = {
            "value": round(float(d[0]), 4),
            "chg_pct": round(float(d[1]), 2) if d[1] is not None else None,
            "chg": round(float(d[2]), 4) if d[2] is not None else None,
        }
    return out


# ===== TradingView 帶日期快照（補「今天」那一格）=====
# yfinance 與 FRED 各自補得到歷史，卻都補不到「今天」：
#   - yfinance 從 Zeabur 出站 IP 整段被 429 擋死（見上面海期監控那段），sox 因此長期全 NULL。
#   - FRED 逐日更新但**慢一天**：實測 2026-08-05 當下 VIXCLS 最新只到 08-03、NIKKEI225
#     只到 08-04，而台股資料日 08-05 需要的正是 VIX 的 08-04 收盤與日經的 08-05 收盤。
# scanner 給不了歷史，但給得了「現在」，剛好補上這一格。
#
# **`time` 欄位是該根日 K 的「開盤」時間戳，不是收盤**（實測：SOX 09:30 NY、NI225 09:00 JST、
# KOSPI 09:00 KST、VIX 03:15 NY）。所以它只說明「這根 bar 屬於哪一場」，**完全不保證那一場
# 已經收完**——09:05 台北實測 NI225／KOSPI 回的就是當天進行中的盤中值。原本 kospi 那條路徑
# 只靠「排程剛好在南韓收盤後才跑」而安全，不是由程式保證的；任何時候手動打 /api/intl/backfill
# 都會把盤中價寫成收盤。現在每個代碼自帶場次收盤時刻，由 session_closed() 判定，
# 這也正是先前 sox 被判定不能用這招的原因（它的 21:30 台北是**開盤**）——加上守衛後就能用了。
#
# {key: (TradingView 代碼, 市場時區, 場次收盤 (時, 分))}
TV_DATED: dict[str, tuple[str, str, tuple[int, int]]] = {
    "sox": ("TVC:SOX", "America/New_York", (16, 0)),
    "vix": ("TVC:VIX", "America/New_York", (16, 15)),      # VIX 揭露到 16:15 ET
    "n225": ("TVC:NI225", "Asia/Tokyo", (15, 0)),
    "kospi": ("TVC:KOSPI", "Asia/Seoul", (15, 30)),
}
# 收盤後再等一段才採用：指數的最終值會在鐘響後幾分鐘才定案，且各市場偶有提早/延後收盤
# （南韓大學入學考當天延後一小時），留 30 分鐘不影響任何一場排程（實際讀取都在數小時後）。
TV_SETTLE_MIN = 30


def session_closed(ds: str, tz: str, close_hm: tuple[int, int],
                   now: datetime | None = None, settle_min: int = TV_SETTLE_MIN) -> bool:
    """ds 那天、該市場的場次是否已經收完（含 settle_min 緩衝）。

    用 ZoneInfo 組當地時間再比較，夏令時間自動處理——美股 16:00 ET 對台北是 04:00 或 05:00，
    寫死時差會在換季那兩週靜默錯一小時。
    """
    from datetime import date as _d
    from datetime import time as _t
    h, m = close_hm
    end = datetime.combine(_d.fromisoformat(ds), _t(h, m), tzinfo=ZoneInfo(tz)) \
        + timedelta(minutes=settle_min)
    return (now or datetime.now(timezone.utc)) >= end


def parse_tv_dated(payload, specs: dict, now: datetime | None = None) -> dict:
    """scanner 回應（columns 多要 time）→ {key: {date, value, chg_pct}}。

    specs = {key: (ticker, tz, close_hm)}；以 "s"(代碼) 反查 key，回應順序不保證。
    **只收「該場次已收盤」的列**：缺 time／缺 close／場次未收完 → 該 key 直接不出現，
    由呼叫端維持 NULL 等下次。寧可少一格，也不要把盤中價貼上收盤的標籤——後者因為
    「只填 NULL 不覆蓋」而永遠不會被修正。
    """
    by_ticker = {spec[0]: (key, spec) for key, spec in specs.items()}
    out = {}
    for row in (payload.get("data") or []):
        hit = by_ticker.get(row.get("s"))
        d = row.get("d") or []
        if not hit or len(d) < 4 or d[0] is None or d[3] is None:
            continue
        key, (_, tz, close_hm) = hit
        ds = datetime.fromtimestamp(d[3], tz=timezone.utc).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")
        if not session_closed(ds, tz, close_hm, now=now):
            continue
        out[key] = {"date": ds, "value": round(float(d[0]), 4),
                    "chg_pct": round(float(d[1]), 2) if d[1] is not None else None}
    return out


def fetch_dated_closes(keys=None) -> dict:
    """一次 POST 抓 TV_DATED 各代碼的帶日期快照 → {key: {date, value, chg_pct}}。

    只回「場次已收盤」的 key（見 parse_tv_dated）。整批失敗回空 dict——呼叫端維持 NULL，
    下次更新自己會再試，不需要備援來源。
    """
    specs = {k: v for k, v in TV_DATED.items() if keys is None or k in keys}
    if not specs:
        return {}
    body = {"symbols": {"tickers": [s[0] for s in specs.values()], "query": {"types": []}},
            "columns": ["close", "change", "change_abs", "time"]}
    try:
        r = httpx.post(_TV_SCAN_URL, json=body, timeout=20, headers=_TV_UA)
        if r.status_code == 200:
            return parse_tv_dated(r.json(), specs)
    except Exception:  # noqa: BLE001 — 抓不到就維持 NULL，不是錯誤
        pass
    return {}


def fetch_futures_monitor(tries: int = 3) -> list[dict]:
    """一次 POST TradingView 公開 scanner 抓五大分類報價，回
    [{category, items:[{name,value,chg,chg_pct}]}]。抓不到的代碼略過不顯示。

    整批失敗（非 200／連線失敗）就回「5 個分類、每組 0 檔」，由呼叫端 _os_futures 據此
    判定不寫快取（見該處 got_remote 說明）。單一 POST 就涵蓋所有資產，不需逐檔備援。
    """
    tickers = [t for _, items in OS_FUTURES for _, t in items]
    body = {"symbols": {"tickers": tickers, "query": {"types": []}},
            "columns": ["close", "change", "change_abs"]}
    quotes: dict[str, dict] = {}
    for attempt in range(tries):
        try:
            r = httpx.post(_TV_SCAN_URL, json=body, timeout=20, headers=_TV_UA)
            if r.status_code == 200:
                quotes = parse_tv_scan(r.json())
                break
        except Exception:  # noqa: BLE001 — 整批失敗就重試，仍失敗回空分類
            pass
        if attempt < tries - 1:
            time.sleep(1.0)
    out = []
    for cat, items in OS_FUTURES:
        rows = [{"name": name, **quotes[t]} for name, t in items if t in quotes]
        out.append({"category": cat, "items": rows})
    return out
