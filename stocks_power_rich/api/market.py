from fastapi import APIRouter, HTTPException
from datetime import datetime
from .deps import conn
from .helpers import (
    _latest_date,
    data_is_stale,
    _sectors_for,
    _industry_map,
    _quotes_for,
    _otc_industry,
    _otc_quotes_for,
    _otc_names,
    _ohlc_names,
    _attach_size,
    get_ai_cache,
    set_ai_cache,
    _os_futures,
    _turnover_for,
    ai_cooling_down,
    note_ai_failure,
    bump_ai_calls,
    checklist_inputs,
)
from ..sources import twse, taifex, mis, tpex
from .. import analysis, gemini, ss_trader, traders
from ..config import load_config

router = APIRouter(prefix="/api")

@router.get("/os-futures")
def os_futures(refresh: int = 0):
    # 「即時監控」已於 2026-07 移除（前端每 2 分鐘輪詢正是把 Zeabur 出站 IP 打到被 Yahoo
    # 429 限流的主因）。現在資料只由 main.py 的排程 job（每日 07:30／21:30）主動更新，
    # 這裡永遠是讀快取；refresh=1 供「更新報價」手動按鈕做一次性強制重抓。
    return _os_futures(refresh=bool(refresh))


def _avg_turnover_10(c, today: str, codes: list[str]) -> tuple[dict[str, float], int]:
    """Average each stock's turnover across the prior 10 published sessions."""
    totals = {code: 0.0 for code in codes}
    counts = {code: 0 for code in codes}
    sessions = 0
    # Some recent OHLC dates can still lack official turnover, so only count
    # dates with published turnover and look farther back when needed.
    rows = c.execute(
        "SELECT DISTINCT date FROM stock_ohlc WHERE date < ? ORDER BY date DESC LIMIT 35",
        (today,),
    ).fetchall()
    for (d,) in rows:
        try:
            day = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        turnover = _turnover_for(c, day)
        if not turnover:
            continue
        sessions += 1
        for code in codes:
            amount = (turnover.get(code) or {}).get("amount")
            if amount is not None:
                totals[code] += amount
                counts[code] += 1
        if sessions == 10:
            break
    return {code: totals[code] / counts[code] for code in codes if counts[code]}, sessions


def _stock_institutional_for(c, day) -> tuple[dict, dict]:
    """Published per-stock institutional net lots for one date, cached by market.

    Both official feeds publish after the close.  During market hours return an
    empty result instead of retrying two remote endpoints on every rank refresh.
    """
    now = datetime.now()
    if day == now.date() and now.weekday() < 5 and (now.hour, now.minute) < (14, 0):
        return {}, {}
    ds = day.strftime("%Y-%m-%d")
    parts = []
    for market, fetch in (("tse", twse.fetch_t86), ("otc", tpex.fetch_tpex_insti)):
        key = f"stockinst:{market}:{ds}"
        data = get_ai_cache(c, key)
        if data is None:
            try:
                data = fetch(day)
            except Exception:  # noqa: BLE001
                data = {}
            if data:
                set_ai_cache(c, key, data)
        parts.append(data or {})
    return parts[0], parts[1]


def _capital_signal(inst_amount, amount_vs_avg_pct, turnover_pct) -> dict | None:
    """Classify observable flow only; deliberately does not infer investor identity."""
    boosted = amount_vs_avg_pct is not None and amount_vs_avg_pct >= 30
    hot_turnover = turnover_pct is not None and turnover_pct >= 5
    quiet_turnover = turnover_pct is not None and turnover_pct <= 2
    if inst_amount is not None and inst_amount > 0 and boosted and quiet_turnover:
        return {"label": "法人承接", "tone": "up"}
    if inst_amount is not None and inst_amount < 0 and boosted:
        return {"label": "放量分歧", "tone": "warn"}
    if inst_amount is not None and inst_amount > 0 and quiet_turnover:
        return {"label": "低周轉偏多", "tone": "up"}
    if hot_turnover:
        return {"label": "高周轉觀察", "tone": "caution"}
    if boosted:
        return {"label": "放量觀察", "tone": "neutral"}
    return None


def _rank_ttl() -> int:
    """盤中 8 秒（前端 10 秒輪詢下 MIS 實際頻率安全）；非盤中 300 秒（只剩靜態收盤值）。"""
    now = datetime.now()
    in_session = now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (13, 35)
    return 8 if in_session else 300


@router.get("/rank/price")
def rank_price(market: str = "all", n: int = 30):
    """台股高價股即時排行（合併/上市/上櫃）。昨收(stock_ohlc)預選成員→MIS 即時價覆蓋；
    MIS 缺檔退回昨收（time 為空＝收盤價）。高價股名單變動極慢，昨收預選足夠準確。"""
    market = market if market in ("all", "twse", "otc") else "all"
    n = max(5, min(n, 50))
    c = conn()
    key = f"rankprice:{market}:{n}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        try:
            age = (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if age < _rank_ttl():
                return cached
        except (KeyError, ValueError, TypeError):
            pass
    # 昨收預選：每檔最新收盤（SQLite bare-column 取 MAX(date) 該列的 close）
    rows = c.execute("SELECT code, close, MAX(date) FROM stock_ohlc GROUP BY code").fetchall()
    otc = _otc_names(c)
    by_mkt = {"twse": [], "otc": []}
    for code, close, _d in rows:
        if close:
            by_mkt["otc" if code in otc else "twse"].append((code, float(close)))
    if market == "all":
        pool = sorted(by_mkt["twse"], key=lambda x: -x[1])[:n] + \
               sorted(by_mkt["otc"], key=lambda x: -x[1])[:n]
    else:
        pool = sorted(by_mkt[market], key=lambda x: -x[1])[:n]
    tokens = [f"{'otc' if code in otc else 'tse'}_{code}.tw" for code, _ in pool]
    quotes = mis.fetch_mis_rank(tokens) if tokens else {}
    today = datetime.now().date()
    t_today = _turnover_for(c, today)          # 盤中通常為空 → 成交額退回估算
    avg_amounts, avg_sessions = _avg_turnover_10(
        c, today.strftime("%Y-%m-%d"), [code for code, _ in pool]
    )
    listed_info = _industry_map(c)
    otc_info = _otc_industry(c)
    tse_inst, otc_inst = _stock_institutional_for(c, today)
    items = []
    for code, close in pool:
        q = quotes.get(code) or {}
        # 現價與漲跌必須同源：MIS 有報價就整組用它，沒有就退回昨收並讓漲跌留白。
        # 混用是這支曾出過的 bug 的成因——MIS 價為 0（委買首檔佔位 0.0000）時
        # `price or close` 把 0 當假值換成昨收，卻留著 MIS 用 0 算出的 −100%，
        # 畫面就成了「正常價格配假跌停」。缺價時寧可不顯示漲跌，也不要顯示錯的。
        live = q.get("price")
        price = live if live else close
        chg, chg_pct = (q.get("chg"), q.get("chg_pct")) if live else (None, None)
        # 成交量：MIS 即時（張）優先，盤前/缺檔退回官方盤後
        vol = q.get("vol")
        if vol is None:
            vol = (t_today.get(code) or {}).get("vol")
        # 成交額：官方精確值優先；盤中無官方值時以 量×1000×現價 估算（標記 amount_est）
        amount = (t_today.get(code) or {}).get("amount")
        est = amount is None
        if est:
            amount = round(vol * 1000 * price) if (vol is not None and price) else None
        avg_amount_10 = avg_amounts.get(code)
        shares = (otc_info if code in otc else listed_info).get(code, {}).get("shares")
        turnover_pct = round(vol * 1000 / shares * 100, 2) if vol is not None and shares else None
        inst = (otc_inst if code in otc else tse_inst).get(code) or {}
        inst_lots = inst.get("total")
        # Official feeds publish net lots, not net money; convert at the current
        # price and mark it as an estimate in the frontend.
        inst_amount = round(inst_lots * price * 1000 / 1e8, 1) if inst_lots is not None and price else None
        amount_vs_avg_pct = (
            round((amount / avg_amount_10 - 1) * 100, 1)
            if amount is not None and avg_amount_10 else None
        )
        items.append({
            "code": code, "market": "otc" if code in otc else "twse",
            "name": q.get("name") or otc.get(code) or code,
            "price": price,
            "chg": chg, "chg_pct": chg_pct,
            "vol": vol, "amount": amount, "amount_est": est and amount is not None,
            "amount_avg_10": avg_amount_10,
            "amount_vs_avg_10": amount - avg_amount_10 if amount is not None and avg_amount_10 else None,
            "amount_vs_avg_10_pct": amount_vs_avg_pct,
            "turnover_pct": turnover_pct,
            "inst_amount": inst_amount,
            "inst_foreign_lots": inst.get("foreign"),
            "inst_trust_lots": inst.get("trust"),
            "inst_dealer_lots": inst.get("dealer"),
            "capital_signal": _capital_signal(inst_amount, amount_vs_avg_pct, turnover_pct),
            "time": q.get("time"),
        })
    items.sort(key=lambda i: -(i["price"] or 0))
    result = {"market": market, "items": items[:n], "avg_sessions": avg_sessions,
              "fetched_at": datetime.now().isoformat()}
    if items:
        set_ai_cache(c, key, result)
    return result


@router.get("/traders")
def traders_list():
    """操盤手清單（人物選單用）。"""
    return {"traders": traders.list_traders()}


@router.get("/traders/{tid}")
def trader_detail(tid: str):
    """單一操盤手的每日分析（通用區塊）。方法論見 .claude/skills/<id>。非投資建議。"""
    m = traders.get_trader(tid)
    if m is None:
        raise HTTPException(status_code=404, detail=f"找不到操盤手 {tid}")
    return {**m.META, **m.analyze(conn())}

DASHBOARD_DAYS = 150
"""總覽一次取幾個交易日。

原本是 60，在 `market_daily` 只有 42 列時不是瓶頸；把 `/api/backfill` 的雙重上限修掉、
歷史回補到 130+ 列之後，這個 60 就成了「大盤×籌碼對照圖」看得到多少籌碼的天花板
（回補明明補到了，圖上卻停在 60 天）。放大到 150 同時也讓卡片的位階條從 60 日基準變成
150 日基準——樣本更長、極端值判定更穩，且位階條的 tooltip 本來就會誠實印出樣本數
（`rk.n`），不會讓人誤以為兩者同基準。
"""


@router.get("/dashboard")
def dashboard():
    c = conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM market_daily ORDER BY date DESC LIMIT ?", (DASHBOARD_DAYS,)
    ).fetchall()]
    # 10 日均量是純衍生值，不落地成 DB 欄位（落地就要配自己的自癒 pass——見融資維持率
    # 那條教訓）。逐列注入而非只算最新一列，是為了讓前端的位階條有整個視窗可取樣。
    asc = list(reversed(rows))
    mas = analysis.turnover_ma([r.get("turnover") for r in asc], ss_trader.VOL_MA_DAYS)
    for r, ma in zip(asc, mas):
        r["turnover_ma10"] = ma
    latest = rows[0] if rows else {}      # 與 asc[-1] 是同一個 dict，均量自動帶到
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    # 市場儀表板的半圓錶。checklist 用同一份 asc（舊→新），與 traders/ss.py 共用
    # checklist_inputs()，避免兩邊各自組一次夜盤量比／結算週判定而漂移。
    checklist = ss_trader.market_checklist(asc, **checklist_inputs(c))
    pulse = ss_trader.market_pulse(checklist)
    # 展開組成後看到的是 Ss 方法論的檢核清單，同一份免責聲明必須跟著出現——
    # 借用「操盤手」頁既有那句，不新造第二份文字。
    pulse["disclaimer"] = traders.ss._DISCLAIMER
    return {
        "latest": latest,
        "history": asc,
        "today": today,
        "data_stale": data_is_stale(latest.get("date"), today, now.weekday()),
        # 前端「異常讀數」判定用的固定門檻。刻意由後端供給而非在 app.js 複寫——
        # 同一組數字有兩份實作就會漂移（艾略特波浪已經吃過這個虧）。ss_trader 是
        # 這些門檻的唯一出處，「操盤手」頁與總覽卡片共用同一份。
        "bands": _BANDS,
        "pulse": pulse,
    }

# 只列「跨過就值得看一眼」的欄位；沒有公認門檻的欄位不硬編，交給位階條處理。
_BANDS = {
    # 維持率沒有單一門檻——兩個市場的融資成數不同，兩平線也不同。前端要靠 breakeven
    # 才能把「180.1% 與 166.8% 意義相反」講清楚，所以送的是各自的錨點而非一組上下限。
    "margin_maintenance": {"breakeven": ss_trader.margin_breakeven(ss_trader.MARGIN_RATIO_TSE),
                           "call": ss_trader.MARGIN_CALL_LINE},
    "otc_margin_maintenance": {"breakeven": ss_trader.margin_breakeven(ss_trader.MARGIN_RATIO_OTC),
                               "call": ss_trader.MARGIN_CALL_LINE},
    "vix": {"low": ss_trader.VIX_COMPLACENT, "high": ss_trader.VIX_PANIC},
    # 量能只有下緣有意義（量縮才是要看的事），不給 high——前端 isAlert 對 undefined
    # 的比較恆為 false，單邊門檻是安全的。
    "turnover_ma10": {"low": ss_trader.VOL_QUIET_YI},
}

@router.get("/health")
def health():
    from datetime import date
    c = conn()

    r_market = c.execute("SELECT MAX(date) FROM market_daily").fetchone()
    latest_market = r_market[0] if r_market and r_market[0] else None

    r_chip = c.execute("SELECT MAX(snap_date) FROM chip_snapshot").fetchone()
    latest_chip = r_chip[0] if r_chip and r_chip[0] else None

    r_ohlc = c.execute("SELECT MAX(date) FROM stock_ohlc").fetchone()
    latest_ohlc = r_ohlc[0] if r_ohlc and r_ohlc[0] else None

    r_custody = c.execute("SELECT MAX(week) FROM custody_dist").fetchone()
    latest_custody = r_custody[0] if r_custody and r_custody[0] else None

    today = date.today()

    lag_m = None
    if latest_market:
        try:
            lag_m = (today - date.fromisoformat(latest_market)).days
        except Exception:  # noqa: BLE001
            pass

    lag_c = None
    if latest_chip:
        try:
            lag_c = (today - date.fromisoformat(latest_chip)).days
        except Exception:  # noqa: BLE001
            pass

    lag_s = None
    if latest_ohlc:
        try:
            lag_s = (today - date.fromisoformat(latest_ohlc)).days
        except Exception:  # noqa: BLE001
            pass

    lag_cu = None
    if latest_custody:
        try:
            lag_cu = (today - date.fromisoformat(latest_custody)).days
        except Exception:  # noqa: BLE001
            pass

    ok = (
        latest_market is not None and lag_m is not None and lag_m <= 3 and
        latest_chip is not None and lag_c is not None and lag_c <= 4 and
        latest_ohlc is not None and lag_s is not None and lag_s <= 4 and
        latest_custody is not None and lag_cu is not None and lag_cu <= 10
    )

    return {
        "market_daily": {"latest": latest_market, "lag_days": lag_m},
        "chip_snapshot": {"latest": latest_chip},
        "stock_ohlc": {"latest": latest_ohlc},
        "custody_dist": {"latest_week": latest_custody},
        "ok": ok
    }

@router.get("/sectors")
def sectors(date: str | None = None):
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None, "sectors": []}
    key = f"sectors:{date}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        result = cached
    else:
        try:
            secs = twse.fetch_sector_indices(datetime.fromisoformat(date).date())
        except Exception:  # noqa: BLE001
            secs = []
        secs.sort(key=lambda s: (s.get("chg_pct") is None, -(s.get("chg_pct") or 0)))
        result = {"date": date, "sectors": secs}
        if secs:
            set_ai_cache(c, key, result)
    _attach_size(c, date, result.get("sectors") or [])
    return result

@router.get("/sectors/picks")
def sectors_picks(date: str | None = None):
    c = conn()
    from ..db import get_snapshot_dates, get_snapshot
    dates = get_snapshot_dates(c)
    snap = date if date in dates else (dates[-1] if dates else None)
    if not snap:
        return {"date": None, "groups": []}
    picks = analysis.filtered_picks(get_snapshot(c, snap))
    sector_chg = {s["name"]: s["chg_pct"] for s in _sectors_for(c, snap)}
    return {"date": snap, "groups": analysis.picks_by_sector(picks, sector_chg)}

@router.get("/sectors/{sector}/stocks")
def sector_stocks(sector: str, date: str | None = None):
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"sector": sector, "date": None, "stocks": []}
    imap, quotes = _industry_map(c), _quotes_for(c, date)
    stocks = []
    for code, info in imap.items():
        if info.get("sector") != sector:
            continue
        q = quotes.get(code)
        if not q:
            continue
        shares, close = info.get("shares"), q.get("close")
        mcap = round(shares * close / 1e8, 1) if (shares and close) else None
        stocks.append({"code": code, "name": q.get("name"),
                       "chg_pct": q.get("chg_pct"), "close": close, "mcap": mcap})
    stocks.sort(key=lambda s: (s["mcap"] is None, -(s["mcap"] or 0)))
    return {"sector": sector, "date": date, "count": len(stocks), "stocks": stocks}

def _heatmap_rows(imap: dict, quotes: dict) -> list:
    """(產業對照, 報價) → 個股熱力圖資料列 [{code,name,sector,mcap,chg_pct}]；
    無產業/漲跌/市值者剔除。"""
    out = []
    for code, info in imap.items():
        sector = info.get("sector")
        q = quotes.get(code)
        if not sector or not q or q.get("chg_pct") is None:
            continue
        shares, close = info.get("shares"), q.get("close")
        mcap = round(shares * close / 1e8, 1) if (shares and close) else None
        if not mcap:
            continue
        out.append({"code": code, "name": q.get("name") or code, "sector": sector,
                    "mcap": mcap, "chg_pct": q.get("chg_pct")})
    return out


@router.get("/heatmap")
def heatmap(date: str | None = None, market: str = "tse"):
    """個股熱力圖資料：依產業分組，每檔含市值(面積)與當日漲跌幅(顏色)。
    market: tse(上市) / otc(上櫃) / all(全部)。分組與組內個股皆依市值由大到小。"""
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None, "market": market, "groups": []}
    rows = []
    if market in ("tse", "all"):
        rows += _heatmap_rows(_industry_map(c), _quotes_for(c, date))
    if market in ("otc", "all"):
        rows += _heatmap_rows(_otc_industry(c), _otc_quotes_for(c, date))
    groups: dict = {}
    for s in rows:
        groups.setdefault(s["sector"], []).append(s)
    out = []
    for sector, stocks in groups.items():
        stocks.sort(key=lambda s: -s["mcap"])
        out.append({"sector": sector, "mcap": round(sum(s["mcap"] for s in stocks), 1), "stocks": stocks})
    out.sort(key=lambda g: -g["mcap"])
    return {"date": date, "market": market, "groups": out}

@router.get("/sectors/rotation")
def sectors_rotation():
    c = conn()
    from ..db import get_ai_cache, set_ai_cache
    rows = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 20").fetchall()
    dlist = [r[0] for r in reversed(rows)]
    if not dlist:
        return {"dates": [], "sectors": {}}
    ckey = f"rotation2:{dlist[-1]}:{len(dlist)}"
    cached = get_ai_cache(c, ckey)
    if cached is not None:
        return cached
    sectors = {}
    names = set()
    for ds in dlist:
        secs = _sectors_for(c, ds)
        for s in secs:
            nm = s.get("name")
            if nm:
                names.add(nm)
                sectors.setdefault(nm, []).append(s.get("chg_pct"))
    for nm in names:
        arr = sectors[nm]
        if len(arr) < len(dlist):
            sectors[nm] = [None] * (len(dlist) - len(arr)) + arr
    result = {"dates": dlist, "sectors": sectors}
    if names:
        set_ai_cache(c, ckey, result)
    return result

@router.get("/index-movers")
def index_movers(date: str | None = None, top: int = 20):
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None, "movers": []}
    row = c.execute("SELECT taiex, taiex_chg FROM market_daily WHERE date=?", (date,)).fetchone()
    if not row or row[0] is None or row[1] is None:
        return {"date": date, "index": row[0] if row else None, "index_chg": None, "movers": []}
    taiex, taiex_chg = row[0], row[1]
    top = max(5, min(top, 40))
    key = f"movers:{date}:{top}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    prev_index = taiex - taiex_chg
    imap, quotes = _industry_map(c), _quotes_for(c, date)
    items, total_prev = [], 0.0
    for code, info in imap.items():
        sh, q = info.get("shares"), quotes.get(code)
        if not sh or not q or q.get("close") is None or q.get("chg_pct") is None:
            continue
        close, chg = q["close"], q["chg_pct"]
        denom = 1 + chg / 100
        if denom <= 0:
            continue
        prev = close / denom
        total_prev += sh * prev
        items.append({"code": code, "name": q.get("name") or info.get("name"),
                      "close": close, "chg_pct": chg, "_d": sh * (close - prev), "_p": sh * prev})
    if total_prev <= 0:
        return {"date": date, "index": taiex, "index_chg": taiex_chg, "movers": []}
    raw_total = sum(i["_d"] for i in items) / total_prev * prev_index
    scale = (taiex_chg / raw_total) if raw_total else 1.0
    for i in items:
        i["contribution"] = round(i["_d"] / total_prev * prev_index * scale, 2)
        i["weight"] = round(i["_p"] / total_prev * 100, 2)
        del i["_d"], i["_p"]
    items.sort(key=lambda i: -abs(i["contribution"]))
    result = {"date": date, "index": taiex, "index_chg": taiex_chg, "movers": items[:top]}
    set_ai_cache(c, key, result)
    return result

@router.get("/breadth")
def breadth(date: str | None = None):
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None}
    key = f"breadth:{date}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    try:
        b = twse.fetch_advance_decline(datetime.fromisoformat(date).date())
    except Exception:  # noqa: BLE001
        b = None
    result = {"date": date, **(b or {})}
    if b:
        set_ai_cache(c, key, result)
    return result

def _distribution(date: str | None = None):
    """全市場漲跌幅分布直方圖。漲跌家數只給數量，這個給『形狀』——今天跌很多是廣而淺
    還是窄而深。資料取自已逐日快取的全市場報價，濾 4 碼普通股（排除 6 碼權證/ETF，
    與漲跌家數同宇宙），合併上市＋上櫃餵 analysis.change_histogram。

    快取必須兩市場都有資料才寫（`has_otc`），不能只憑 pcts 非空就存——上市/上櫃同步休市，
    但不同步失敗（曾發生 tpex 端 TLS 憑證問題導致 _otc_quotes_for 靜默抓空，
    上市那邊仍抓得到），若只看「有沒有 pcts」，一次上櫃單邊失敗就會把「只算上市」的
    退化結果寫進無 TTL 的快取，之後 tpex 端修好了也永遠讀到那筆舊的半套資料——
    讀取端因此也要同時檢查 `has_otc`，不能只看「快取存在與否」，舊快取缺這個欄位
    （fix 之前寫入的）視同未命中，才能自己重算自癒（跟 os-futures 的 has_remote 同一套規則）。"""
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None}
    key = f"dist:{date}"
    cached = get_ai_cache(c, key)
    if cached is not None and cached.get("has_otc"):
        return cached
    tse, otc = _quotes_for(c, date), _otc_quotes_for(c, date)
    pcts = [q["chg_pct"] for src in (tse, otc) for code, q in src.items()
            if len(code) == 4 and code.isdigit() and q.get("chg_pct") is not None]
    result = {"date": date, "has_otc": bool(otc), **analysis.change_histogram(pcts)}
    if pcts and otc:                # 兩市場都有資料才快取；否則下次重試，等上櫃也抓到
        set_ai_cache(c, key, result)
    return result

@router.get("/breadth/distribution")
def breadth_distribution(date: str | None = None):
    return _distribution(date)

def _movers(date: str | None = None, n: int = 8):
    """漲幅／跌幅排行。與分布直方圖同一份報價來源、同一條 4 碼普通股規則。

    **刻意不另外加一層快取**：`_quotes_for`／`_otc_quotes_for` 本身已經逐日快取，
    這裡只是把它們排個序，成本極低。多開一個無 TTL 的快取鍵只會多一個「失敗值被
    永久化」的面——本專案已經在 os-futures、turnover、dist 上各踩過一次。
    """
    c = conn()
    date = date or _latest_date(c)
    if not date:
        return {"date": None, "up": [], "down": [], "n": 0}
    names = _ohlc_names(c)
    rows = [{"code": code, "name": names.get(code), "chg_pct": q.get("chg_pct")}
            for src in (_quotes_for(c, date), _otc_quotes_for(c, date))
            for code, q in src.items()]
    return {"date": date, **analysis.top_movers(rows, n=n)}

@router.get("/rank/movers")
def rank_movers(date: str | None = None, n: int = 8):
    return _movers(date, n=max(3, min(n, 20)))

def market_summary_logic(c, refresh: int = 0):
    cfg = load_config()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM market_daily ORDER BY date DESC LIMIT 6").fetchall()]
    if not rows:
        return gemini.summarize_market({}, cfg.gemini_api_key)
    m = rows[0]
    sig = "".join("1" if m.get(k) is not None else "0"
                  for k in ("taiex", "inst_foreign", "tx_foreign_oi", "retail_ls_mtx"))
    key = f"market:{m.get('date')}:{sig}"
    cached = get_ai_cache(c, key)
    if cached and not refresh:
        return cached
    hist = list(reversed(rows))
    pv = rows[1] if len(rows) > 1 else {}

    def _streak(k: str) -> int:
        vals = [r.get(k) for r in hist if r.get(k) is not None]
        if not vals or not vals[-1]:
            return 0
        sign = 1 if vals[-1] > 0 else -1
        n = 0
        for v in reversed(vals):
            if v and (v > 0) == (sign > 0):
                n += 1
            else:
                break
        return n * sign

    def _pct100(v):
        return round(v * 100, 2) if v is not None else None

    oi, oi_pv = m.get("tx_foreign_oi"), pv.get("tx_foreign_oi")
    tv = m.get("turnover")
    prev_tvs = [r.get("turnover") for r in hist[:-1] if r.get("turnover") is not None]
    vol_vs_avg = (round((tv / (sum(prev_tvs) / len(prev_tvs)) - 1) * 100, 1)
                  if (tv and prev_tvs) else None)
    latest = {
        "日期": m.get("date"),
        "加權指數": m.get("taiex"), "加權漲跌(點)": m.get("taiex_chg"),
        "成交金額(億)": tv, "量能較前幾日均量(%)": vol_vs_avg,
        "外資買賣超(億)": m.get("inst_foreign"), "外資連買賣(天,正買負賣)": _streak("inst_foreign"),
        "投信買賣超(億)": m.get("inst_trust"), "投信連買賣(天)": _streak("inst_trust"),
        "自營買賣超(億)": m.get("inst_dealer"),
        "外資台指淨未平倉(口)": oi,
        "外資台指OI較昨增減(口)": (round(oi - oi_pv) if (oi is not None and oi_pv is not None) else None),
        "散戶小台多空比(%)": _pct100(m.get("retail_ls_mtx")),
        "散戶微台多空比(%)": _pct100(m.get("retail_ls_tmf")),
        "融資餘額(張)": m.get("margin_balance"), "融資增減(張)": m.get("margin_chg"),
        "融資金額(億)": m.get("margin_value"), "融資金額增減(億)": m.get("margin_value_chg"),
        "融資維持率(%)": m.get("margin_maintenance"),
        "VIX": m.get("vix"), "VIX漲跌(%)": m.get("vix_chg"),
        "費半漲跌(%)": m.get("sox_chg"), "日經漲跌(%)": m.get("n225_chg"),
        "韓股漲跌(%)": m.get("kospi_chg"), "黃金漲跌(%)": m.get("gold_chg"),
        "美元兌日圓": m.get("jpy"), "美元兌日圓漲跌(%)": m.get("jpy_chg"),
        "比特幣漲跌(%)": m.get("btc_chg"),
    }
    latest = {k: v for k, v in latest.items() if v is not None}
    keys = [("inst_foreign", "外資買賣超(億)"), ("inst_trust", "投信買賣超(億)"),
            ("tx_foreign_oi", "外資台指淨未平倉(口)"), ("taiex", "加權指數"),
            ("turnover", "成交金額(億)")]
    trend = {"日期": [r.get("date") for r in hist]}
    trend.update({label: [r.get(k) for r in hist] for k, label in keys})
    secs = [s for s in _sectors_for(c, m["date"]) if s.get("chg_pct") is not None]
    secs.sort(key=lambda s: -s["chg_pct"])
    sectors = {"領漲(%)": [[s["name"], s["chg_pct"]] for s in secs[:3]],
               "領跌(%)": [[s["name"], s["chg_pct"]] for s in secs[-3:][::-1]]}
    payload = {"最新盤後": latest, "近6日走勢": trend, "類股": sectors}
    if not refresh and ai_cooling_down(c):
        return {"enabled": False, "text": "（AI 摘要暫時無法使用，稍後自動重試）"}
    result = gemini.summarize_market(payload, cfg.gemini_api_key)
    if result.get("enabled"):
        bump_ai_calls(c)
        set_ai_cache(c, key, result)
    else:
        note_ai_failure(c)
    return result

@router.get("/market/summary")
def market_summary(refresh: int = 0):
    c = conn()
    return market_summary_logic(c, refresh=refresh)

@router.get("/options-sentiment")
def options_sentiment():
    c = conn()
    key = f"optsent:{_latest_date(c) or 'na'}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    try:
        pcr = taifex.fetch_put_call_ratio()
    except Exception:  # noqa: BLE001
        pcr = {}
    try:
        large = taifex.fetch_large_traders()
    except Exception:  # noqa: BLE001
        large = {}
    result = {"pcr": pcr, "large": large}
    if pcr or large:
        set_ai_cache(c, key, result)
    return result

@router.get("/inst-ranking")
def inst_ranking(who: str = "foreign", date: str | None = None, top: int = 20, unit: str = "shares"):
    c = conn()
    if who not in ("foreign", "trust", "dealer", "total"):
        who = "foreign"
    if unit not in ("shares", "value"):
        unit = "shares"
    # 呼叫端沒指定日期時，往回找到最近一個真的有 T86 的交易日。
    # 三大法人約 16:00 後才公布，而 market_daily 當天早上就有列（指數盤中就有），
    # 所以直接用最新日期會整張榜空白、標題卻寫著今天。上限 5 天，避免連假時掃一整週。
    t, cand = None, []
    if date:
        cand = [date]
    else:
        cand = [r[0] for r in c.execute(
            "SELECT date FROM market_daily ORDER BY date DESC LIMIT 5").fetchall()]
    for ds in cand:
        cached = get_ai_cache(c, f"t86:{ds}")
        if cached is None:
            cached = twse.fetch_t86(datetime.fromisoformat(ds).date())
            if cached:
                set_ai_cache(c, f"t86:{ds}", cached)
        if cached:
            t, date = cached, ds
            break
    if not date:
        return {"date": None, "who": who, "unit": unit, "buy": [], "sell": []}
    prices = {}
    if unit == "value":
        prices = get_ai_cache(c, f"close:{date}")
        if prices is None:
            prices = twse.fetch_close_prices(datetime.fromisoformat(date).date())
            if prices:
                set_ai_cache(c, f"close:{date}", prices)
    top = max(5, min(top, 50))
    items = []
    for code, v in (t or {}).items():
        if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
            continue
        lots = v.get(who)
        if lots is None:
            continue
        if unit == "value":
            close = (prices or {}).get(code)
            if close is None:
                continue
            net = round(lots * close / 1e5, 2)
        else:
            net = lots
        items.append({"code": code, "name": v.get("name") or code, "net": net})
    buy = sorted(items, key=lambda x: -x["net"])[:top]
    sell = sorted(items, key=lambda x: x["net"])[:top]
    return {"date": date, "who": who, "unit": unit, "buy": buy, "sell": sell}
