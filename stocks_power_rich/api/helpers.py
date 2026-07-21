import os
import base64
import binascii
import secrets
import threading
import tempfile
import time
from datetime import datetime

from ..config import load_config
from ..db import (
    get_connection,
    init_db,
    get_setting,
    set_setting,
    get_ai_cache,
    set_ai_cache,
    list_watch,
    get_snapshot_dates,
    get_snapshot,
    list_trades,
    get_tx_history,
)
from .. import line_push
from ..sources import twse, tpex, mis
from .. import analysis, patterns, backtest
from .deps import conn

WEB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))
REPO_DIR = os.path.dirname(WEB_DIR)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_EXTS = (".csv", ".xlsx", ".xlsm")

_TURNOVER_EXCLUDE = {"化學生技醫療", "電子工業", "水泥窯製", "塑膠化工", "機電"}
_PUBLIC_INTL_FIELDS = (("n225", "日經"), ("kospi", "韓股"), ("gold", "黃金"),
                      ("jpy", "美元兌日圓"), ("btc", "比特幣"), ("sox", "費半"), ("vix", "VIX"))

_mis_state = {"date": None, "fails": 0, "warned": False}

def _check_basic(auth_header: str, user: str, pw: str) -> bool:
    if not auth_header.startswith("Basic "):
        return False
    try:
        u, _, p = base64.b64decode(auth_header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(u, user) & secrets.compare_digest(p, pw)


def _dir_within(candidate: str, roots: list[str]) -> bool:
    try:
        real = os.path.realpath(candidate)
    except (OSError, ValueError):
        return False
    for root in roots:
        r = os.path.realpath(root)
        if real == r or real.startswith(r + os.sep):
            return True
    return False


def data_is_stale(data_date, today: str, weekday: int) -> bool:
    return bool(data_date and data_date < today and weekday < 5)


def _latest_date(c) -> str | None:
    row = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
    return row[0] if row else None


def effective_data_dir(c):
    cfg = load_config()
    return get_setting(c, "data_dir") or cfg.data_dir


def effective_schedule(c):
    cfg = load_config()
    return get_setting(c, "schedule_time") or cfg.schedule_time


def _clear_csv_cache(c, snap_date: str) -> None:
    c.execute("DELETE FROM ai_cache WHERE cache_key IN (?,?)",
              (f"csv:{snap_date}", f"watchpicks:{snap_date}"))
    c.commit()


def _industry_map(c) -> dict:
    key = f"listed_ind2:{datetime.now().strftime('%Y-%m')}"
    m = get_ai_cache(c, key)
    if not m:
        m = twse.fetch_listed_industry()
        if m:
            set_ai_cache(c, key, m)
    return m or {}


def _otc_names(c) -> dict:
    key = f"otc_names:{datetime.now().strftime('%Y-%m')}"
    m = get_ai_cache(c, key)
    if not m:
        m = tpex.fetch_otc_names()
        if m:
            set_ai_cache(c, key, m)
    return m or {}


def _otc_industry(c) -> dict:
    """上櫃 {code: {sector, name, shares}}，月快取（熱力圖上櫃分頁用）。"""
    key = f"otc_ind:{datetime.now().strftime('%Y-%m')}"
    m = get_ai_cache(c, key)
    if not m:
        m = tpex.fetch_otc_industry()
        if m:
            set_ai_cache(c, key, m)
    return m or {}


def _otc_quotes_for(c, date: str) -> dict:
    """上櫃 {code: {name, close, chg_pct}}，逐日快取（對齊 _quotes_for 上市版）。"""
    qkey = f"otc_quotes:{date}"
    quotes = get_ai_cache(c, qkey)
    if quotes is None:
        try:
            quotes = tpex.fetch_otc_quotes(datetime.fromisoformat(date).date())
        except Exception:  # noqa: BLE001
            quotes = {}
        if quotes:
            set_ai_cache(c, qkey, quotes)
    return quotes or {}


def _quotes_for(c, date: str) -> dict:
    qkey = f"stock_quotes:{date}"
    quotes = get_ai_cache(c, qkey)
    if quotes is None:
        try:
            quotes = twse.fetch_stock_quotes(datetime.fromisoformat(date).date())
        except Exception:  # noqa: BLE001
            quotes = {}
        if quotes:
            set_ai_cache(c, qkey, quotes)
    return quotes or {}


def _insti_for(c, ds: str, market: str) -> dict:
    """單日全市場三大法人買賣超（張），整日一次抓、快取於 ai_cache（跨股共用）。
    market＝twse→T86(上市)、tpex→櫃買。個股三大法人圖與 inst 預熱回補都走這裡。"""
    key = f"{'t86' if market == 'twse' else 'tpex'}:{ds}"
    t = get_ai_cache(c, key)
    if t is None:
        try:
            d = datetime.fromisoformat(ds).date()
            t = twse.fetch_t86(d) if market == "twse" else tpex.fetch_tpex_insti(d)
        except Exception:  # noqa: BLE001
            t = {}
        if t:
            set_ai_cache(c, key, t)
    return t or {}


def _sectors_for(c, ds: str) -> list:
    cached = get_ai_cache(c, f"sectors:{ds}")
    if cached is not None:
        return cached.get("sectors", [])
    try:
        secs = twse.fetch_sector_indices(datetime.fromisoformat(ds).date())
    except Exception:  # noqa: BLE001
        secs = []
    if secs:
        set_ai_cache(c, f"sectors:{ds}", {"date": ds, "sectors": secs})
    return secs


def _attach_size(c, date: str, secs: list) -> None:
    if not secs:
        return
    tkey = f"sector_turnover:{date}"
    tmap = get_ai_cache(c, tkey)
    if tmap is None:
        try:
            tmap = twse.fetch_sector_turnover(datetime.fromisoformat(date).date())
        except Exception:  # noqa: BLE001
            tmap = {}
        if tmap:
            set_ai_cache(c, tkey, tmap)
    mkey = f"sector_mcap:{date}"
    mmap = get_ai_cache(c, mkey)
    if mmap is None:
        acc: dict[str, float] = {}
        imap, quotes = _industry_map(c), _quotes_for(c, date)
        for code, info in imap.items():
            q, sh = quotes.get(code), info.get("shares")
            if not q or not sh or q.get("close") is None:
                continue
            acc[info["sector"]] = acc.get(info["sector"], 0) + sh * q["close"]
        mmap = {k: round(v / 1e8, 1) for k, v in acc.items()}
        if mmap:
            set_ai_cache(c, mkey, mmap)
    for s in secs:
        name = s.get("name")
        s["mcap"] = mmap.get(name)
        s["turnover"] = None if name in _TURNOVER_EXCLUDE else tmap.get(twse.norm_sector_name(name))


def _ohlc_names(c) -> dict:
    imap, omap = _industry_map(c), _otc_names(c)
    names = {code: (info.get("name") or code) for code, info in imap.items()}
    for code, nm in omap.items():
        names.setdefault(code, nm)
    return names


def _picks_code_set(c) -> set:
    dates = get_snapshot_dates(c)
    if not dates:
        return set()
    return {p["code"].split(".")[0] for p in analysis.filtered_picks(get_snapshot(c, dates[-1]))}


def _picks_index(c, ds: str) -> dict:
    key = f"watchpicks:{ds}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    idx = {p["code"]: {"name": p.get("name"), "close": p.get("close")}
           for p in analysis.filtered_picks(get_snapshot(c, ds))}
    set_ai_cache(c, key, idx)
    return idx


def _valuation_for(c, code: str):
    key = f"valuation:{datetime.now().strftime('%Y-%m-%d')}"
    cached = get_ai_cache(c, key)
    if cached is None:
        try:
            cached = {v["code"]: v for v in twse.fetch_valuation()}
        except Exception:  # noqa: BLE001
            cached = {}
        if cached:
            set_ai_cache(c, key, cached)
    return cached.get(code)


def _get_watchlist(c) -> dict:
    wl = list_watch(c)
    dates = get_snapshot_dates(c)
    picks_by_date = {d: _picks_index(c, d) for d in dates}
    latest = dates[-1] if dates else None
    out = []
    imap = omap = None
    for w in wl:
        code = w["code"]
        on = [d for d in dates if code in picks_by_date[d]]
        entry = on[0] if on else None
        ec = picks_by_date[entry][code].get("close") if entry else None
        lc = picks_by_date[latest][code].get("close") if (latest and code in picks_by_date.get(latest, {})) else None
        ret = round((lc - ec) / ec * 100, 2) if (ec and lc) else None
        chip_row = c.execute(
            "SELECT snap_date, name, close, lan_value, lpe, est_profit, rev_yoy, "
            "holder_drop_ratio, big_holder_ratio FROM chip_snapshot "
            "WHERE code=? ORDER BY snap_date DESC LIMIT 1", (code,)).fetchone()
        chip = dict(chip_row) if chip_row else None
        nm = w["name"] or (chip or {}).get("name") or ""
        if not nm:
            pure = code.split(".")[0]
            if imap is None:
                imap = _industry_map(c)
            nm = (imap.get(pure) or {}).get("name") or ""
            if not nm:
                if omap is None:
                    omap = _otc_names(c)
                nm = omap.get(pure) or ""
        out.append({**w, "name": nm, "in_latest": bool(latest and code in picks_by_date.get(latest, {})),
                    "times": len(on), "entry_date": entry, "ret_pct": ret, "chip": chip})
    return {"stocks": out, "latest": latest}


def _trades_payload(c) -> dict:
    trades = list_trades(c)
    closes = {}
    for code in {t["code"] for t in trades if t["exit_price"] is None}:
        r = c.execute("SELECT close FROM stock_ohlc WHERE code=? ORDER BY date DESC LIMIT 1",
                      (code,)).fetchone()
        if r and r[0]:
            closes[code] = r[0]
    taiex = {r[0]: r[1] for r in c.execute(
        "SELECT date, taiex FROM market_daily WHERE taiex IS NOT NULL").fetchall()}
    return {"ok": True, **analysis.trade_stats(trades, closes, taiex)}


def _note_push_fail(c, full: bool, err) -> None:
    set_setting(c, "line_push_fail",
                f"{datetime.now().strftime('%m-%d %H:%M')} "
                f"{'完整版' if full else '速報'} {str(err)[:80]}")


def cup_handle_screen_logic(c, min_r: float = patterns.MIN_R_DEFAULT):
    from ..db import ohlc_dates, get_all_ohlc
    ods = ohlc_dates(c)
    if not ods:
        return {"date": None, "count": 0, "stocks": [],
                "note": "尚未回補個股歷史，請先執行 /api/ohlc/backfill"}
    latest = ods[-1]
    key = f"cuphandle:{latest}:{len(ods)}:{min_r:g}"
    result = get_ai_cache(c, key)
    if result is None:
        data = get_all_ohlc(c, min_bars=patterns.LOOKBACK)
        names = _ohlc_names(c)
        for code, s in data.items():
            s["name"] = names.get(code) or code
        matches = patterns.screen_cup_handle(data, min_r=min_r)
        result = {"date": latest, "bars": len(ods), "count": len(matches),
                  "min_r": min_r, "stocks": matches}
        set_ai_cache(c, key, result)
        # 盤中哨兵/前瞻測試的訊號快照只在「預設嚴格度」時寫入——
        # 避免使用者在 UI 暫調寬鬆值污染警示與績效統計的訊號集
        if min_r == patterns.MIN_R_DEFAULT:
            sig_snapshot = []
            for m in matches:
                o = c.execute("SELECT high, low, close FROM stock_ohlc WHERE code=? "
                              "ORDER BY date DESC LIMIT 15", (m["code"],)).fetchall()
                rows = list(reversed(o))
                a = patterns.atr([r["high"] for r in rows], [r["low"] for r in rows],
                                 [r["close"] for r in rows])
                sig_snapshot.append({"code": m["code"], "name": m["name"],
                                     "resistance": m["resistance"], "atr": a})
            set_ai_cache(c, f"cupsig:{latest}", sig_snapshot)
    picks = _picks_code_set(c)
    for m in result["stocks"]:
        m["in_picks"] = m["code"] in picks
    result["has_picks"] = bool(picks)
    result["picks_count"] = sum(1 for m in result["stocks"] if m["in_picks"])
    for m in result["stocks"]:
        o = c.execute("SELECT high, low, close FROM stock_ohlc WHERE code=? "
                      "ORDER BY date DESC LIMIT 15", (m["code"],)).fetchall()
        rows = list(reversed(o))
        a = patterns.atr([r["high"] for r in rows], [r["low"] for r in rows],
                         [r["close"] for r in rows])
        m["atr"] = a
        m["stop_loss"] = round(m["resistance"] - 2 * a, 2) if (a and m.get("resistance")) else None
    result["loss_tolerance"] = int(get_setting(c, "loss_tolerance") or 0) or None
    return result


def _cup_push_info(c) -> dict | None:
    from ..db import ohlc_dates
    ods = ohlc_dates(c)
    if not ods:
        return None
    scr = cup_handle_screen_logic(c)
    if scr.get("note") or not scr.get("date"):
        return None
    today = scr["date"]
    stocks = scr.get("stocks") or []
    picks = _picks_code_set(c)
    if picks:
        stocks = [s for s in stocks if s["code"] in picks]
    prev_ds = ods[-2] if len(ods) >= 2 else None
    prev = get_ai_cache(c, f"cupsig:{prev_ds}") if prev_ds else None
    new = []
    if prev is not None:
        prev_codes = {p["code"] for p in prev}
        new = [{"code": s["code"], "name": s["name"]} for s in stocks
               if s["code"] not in prev_codes]
    if prev and picks:
        prev = [p for p in prev if p["code"] in picks]
    breakout = []
    if prev:
        codes = [p["code"] for p in prev]
        ph = ",".join("?" * len(codes))
        closes = {r[0]: r[1] for r in c.execute(
            f"SELECT code, close FROM stock_ohlc WHERE date=? AND code IN ({ph})",
            [today] + codes)}
        for p in prev:
            cl = closes.get(p["code"])
            if cl is not None and p.get("resistance") is not None and cl > p["resistance"]:
                breakout.append({**p, "close": cl})
    return {"count": len(stocks), "new": new[:6], "breakout": breakout[:6],
            "picks": bool(picks)}


def _push_line(c, full: bool, force: bool = False) -> dict:
    cfg = load_config()
    if not cfg.line_token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    try:
        rows = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 2").fetchall()
        if not rows:
            return {"ok": False, "error": "尚無大盤資料"}
        m = dict(rows[0])
        prev_row = dict(rows[1]) if len(rows) > 1 else {}
        if not force and m["date"] != datetime.now().strftime("%Y-%m-%d"):
            return {"ok": False, "skipped": True, "error": f"資料日 {m['date']} 非今日，略過"}
        secs = _sectors_for(c, m["date"])
        watch = []
        try:
            quotes = _quotes_for(c, m["date"])
            tsmc = quotes.get("2330")
            stocks = _get_watchlist(c).get("stocks", [])
            otc = {}
            if any(s["code"].split(".")[0] not in quotes for s in stocks):
                okey = f"tpex_quotes:{m['date']}"
                otc = get_ai_cache(c, okey)
                if otc is None:
                    try:
                        otc = tpex.fetch_otc_quotes(datetime.fromisoformat(m["date"]).date())
                    except Exception:  # noqa: BLE001
                        otc = {}
                    if otc:
                        set_ai_cache(c, okey, otc)
            for s in stocks:
                pure = s["code"].split(".")[0]
                q = quotes.get(pure) or (otc or {}).get(pure) or {}
                chip = s.get("chip") or {}
                close, pct = q.get("close"), q.get("chg_pct")
                if pct is None:
                    o = c.execute("SELECT date, close FROM stock_ohlc WHERE code=? "
                                  "ORDER BY date DESC LIMIT 2", (pure,)).fetchall()
                    if o and o[0]["date"] == m["date"] and o[0]["close"] is not None:
                        close = close or o[0]["close"]
                        if len(o) > 1 and o[1]["close"]:
                            pct = round((o[0]["close"] - o[1]["close"]) / o[1]["close"] * 100, 2)
                watch.append({"code": s["code"], "name": s.get("name"),
                              "close": close or chip.get("close"),
                              "chg_pct": pct, "in_latest": s.get("in_latest")})
        except Exception:  # noqa: BLE001
            pass
        
        # summary & market summary helpers
        from ..api.market import market_summary_logic
        from ..api.public import summary_logic
        ai = market_summary_logic(c, refresh=0)
        ai_text = (ai.get("text") or "") if ai.get("enabled") else ""
        try:
            cup = _cup_push_info(c)
        except Exception:  # noqa: BLE001
            cup = None
        txt = line_push.compose_daily_brief(m, secs, watch, ai_text=ai_text, full=full,
                                            tsmc=tsmc, prev=prev_row, cup=cup)
    except Exception as e:  # noqa: BLE001
        _note_push_fail(c, full, e)
        return {"ok": False, "error": str(e)}
    prev_fail = get_setting(c, "line_push_fail")
    if prev_fail:
        txt = f"⚠️ 前次推播失敗（{prev_fail}），數據以本則為準\n" + txt
    r = line_push.broadcast_text(cfg.line_token, txt)
    if not r.get("ok"):
        time.sleep(2)
        r = line_push.broadcast_text(cfg.line_token, txt)
    if r.get("ok"):
        if prev_fail:
            set_setting(c, "line_push_fail", "")
    else:
        _note_push_fail(c, full, r.get("error") or f"HTTP {r.get('status')}")
    return r


def _os_futures(refresh: bool = False) -> dict:
    from ..sources import intl
    c = conn()
    cached = get_ai_cache(c, "osfut:current")
    if cached is not None and not refresh:
        return cached
    try:
        cats = intl.fetch_futures_monitor()
    except Exception:  # noqa: BLE001
        cats = []
    last = c.execute("SELECT taiex, taiex_chg, tx_price, tx_chg FROM market_daily "
                     "ORDER BY date DESC LIMIT 1").fetchone()
    idx = next((g for g in cats if g["category"] == "指數期貨"), None)
    if last and idx:
        local = []
        for val, chg, name in ((last[0], last[1], "加權指數"), (last[2], last[3], "台指期")):
            if val is not None:
                base = (val - chg) if chg is not None else None
                local.append({"name": name, "value": val, "chg": chg,
                              "chg_pct": round(chg / base * 100, 2) if base else None})
        idx["items"] = local + idx["items"]
    result = {"categories": cats, "updated_at": datetime.now().isoformat()}
    if cats:
        set_ai_cache(c, "osfut:current", result)
    return result


_OSFUT_LIVE_TTL = 90   # 秒；前端每 120 秒輪詢，TTL 略短於輪詢間隔即可防狂刷

def _os_futures_live() -> dict:
    """海期準即時：以日線快照為底、逐檔盤中 meta 報價覆蓋（缺檔沿用日線＝延遲值）。

    Yahoo v8 per-symbol 較貴，故 90 秒 TTL 快取；雲端被節流時單檔失敗自動退回日線值，
    頁面不破版只是該檔顯示延遲。
    """
    import copy
    from ..sources import intl
    c = conn()
    cached = get_ai_cache(c, "osfut:live")
    if cached is not None:
        try:
            age = (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if age < _OSFUT_LIVE_TTL:
                return cached
        except (KeyError, ValueError, TypeError):
            pass
    base = copy.deepcopy(_os_futures(refresh=False))   # 日線底（含加權/台指期注入）
    try:
        live = intl.fetch_futures_live()
    except Exception:  # noqa: BLE001
        live = []
    live_map = {(g["category"], i["name"]): i for g in live for i in g["items"]}
    for g in base.get("categories") or []:
        g["items"] = [{**item, **live_map.get((g["category"], item["name"]), {})}
                      for item in g["items"]]
    result = {**base, "live": True, "updated_at": datetime.now().isoformat(),
              "fetched_at": datetime.now().isoformat()}
    if live:
        set_ai_cache(c, "osfut:live", result)
    return result


def _intraday_scan(c, push: bool = True) -> dict:
    cfg = load_config()
    ods = [r[0] for r in c.execute("SELECT DISTINCT date FROM stock_ohlc ORDER BY date").fetchall()]
    if not ods:
        return {"checked": 0, "hits": [], "note": "無 OHLC 歷史"}
    sig = get_ai_cache(c, f"cupsig:{ods[-1]}") or []
    today = datetime.now().strftime("%Y-%m-%d")
    alerted = set(get_ai_cache(c, f"cupalerted:{today}") or [])
    pending = [s for s in sig if s["code"] not in alerted and s.get("resistance")]
    picks = _picks_code_set(c)
    if picks and get_setting(c, "intraday_picks_only") == "1":
        pending = [s for s in pending if s["code"] in picks]
    if not pending:
        return {"checked": 0, "hits": [], "note": "無待監控訊號（或今日皆已警示）"}
    otc = _otc_names(c)
    tokens = [f"{'otc' if s['code'] in otc else 'tse'}_{s['code']}.tw" for s in pending]
    prices = mis.fetch_mis_quotes(tokens)
    if not prices:
        if _mis_state["date"] != today:
            _mis_state.update({"date": today, "fails": 0, "warned": False})
        _mis_state["fails"] += 1
        if _mis_state["fails"] >= 6 and not _mis_state["warned"] and push:
            line_push.broadcast_text(cfg.line_token, "⚠️ 盤中突破哨兵連續無法取得報價（來源可能失效），今日暫停警示。")
            _mis_state["warned"] = True
        return {"checked": len(pending), "hits": [], "note": "查查無報價"}
    _mis_state.update({"date": today, "fails": 0})
    threshold = lambda s: s["resistance"] + 0.3 * s["atr"] if s.get("atr") else s["resistance"]
    crossing = {s["code"]: {**s, "price": prices[s["code"]], "pick": s["code"] in picks}
                for s in pending if s["code"] in prices and prices[s["code"]] > threshold(s)}
    candidates = set(get_ai_cache(c, f"cuppending:{today}") or [])
    hits = [v for code, v in crossing.items() if code in candidates]
    set_ai_cache(c, f"cuppending:{today}", sorted(crossing.keys()))
    if hits and push:
        txt = line_push.compose_breakout_alert(hits, datetime.now().strftime("%H:%M"))
        line_push.broadcast_text(cfg.line_token, txt)
        set_ai_cache(c, f"cupalerted:{today}", sorted(alerted | {h["code"] for h in hits}))
    return {"checked": len(pending), "hits": hits}


def _check_update_result_and_alert(c, result: dict) -> None:
    from datetime import date as _dt_date
    cfg = load_config()
    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    failed = result.get("failed") or []
    res_date_str = result.get("date")

    is_weekday = today_dt.weekday() < 5
    lagging = False
    lag_days = 0
    if res_date_str and res_date_str != today_str and is_weekday:
        try:
            lag_days = (today_dt.date() - _dt_date.fromisoformat(res_date_str)).days
            if lag_days > 0:
                lagging = True
        except Exception:  # noqa: BLE001
            pass

    if failed or lagging:
        failed_sources = []
        for f in failed:
            err_str = f.get("error") or ""
            if len(err_str) > 30:
                err_str = err_str[:27] + "..."
            failed_sources.append(f"{f.get('name')}（{err_str}）")

        failed_sources_str = "、".join(failed_sources) if failed_sources else "無"
        lag_msg = f"（落後 {lag_days} 個交易日）" if lagging else ""
        msg = (
            f"⚠️ 資料更新警告 {today_str}\n"
            f"失敗來源：{failed_sources_str}\n"
            f"資料日期：{res_date_str or '未知'}{lag_msg}"
        )

        failed_names = sorted(list({f.get("name") for f in failed if f.get("name")}))
        alert_key = f"{res_date_str}|{','.join(failed_names)}"

        last_alert = get_setting(c, "last_alert_key")
        if last_alert != alert_key:
            line_push.broadcast_text(cfg.line_token, msg)
            set_setting(c, "last_alert_key", alert_key)
