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


def _turnover_for(c, day) -> dict:
    """指定交易日全市場（上市＋上櫃）成交量額 {code: {vol: 張, amount: 元}}，依日期永久快取。

    盤後數字定案後不再變動，故快取無 TTL；抓不到（盤中尚未發布/來源失效）回空且**不寫快取**，
    讓同一天稍後可重試。

    **兩個市場分開快取**：舊版把兩邊合併成一個 key，只要櫃買當下失敗（憑證/限流）而證交所
    成功，就會把「只有上市」的半套結果永久寫死——上櫃高價股的成交額增減從此永遠是「—」，
    且因為無 TTL 而不會自己好。分開存之後，失敗的那半留白、下次自行重抓。
    """
    ds = day.strftime("%Y-%m-%d")
    out = {}
    for name, fetch in (("tse", lambda: twse.fetch_stock_turnover(day)),
                        ("otc", lambda: tpex.fetch_otc_turnover(day))):
        key = f"turnover:{name}:{ds}"
        part = get_ai_cache(c, key)
        if part is None:
            try:
                part = fetch()
            except Exception:  # noqa: BLE001 — 單一市場失敗不影響另一邊
                part = {}
            if part:
                set_ai_cache(c, key, part)
        out.update(part or {})
    return out


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


def _daily_messages(c, full: bool, force: bool = False) -> tuple[list, dict | None]:
    """組盤後 LINE 訊息。回 (messages, err)；err 非 None 時 messages 為空。

    回一則 Flex carousel：第一頁市場籌碼、第二頁類股強弱＋AI 解讀。推播與 webhook
    共用，避免兩邊內容漂移。force=True 略過「資料日須為今日」檢查——使用者自己問的
    就該回，即使是昨天的收盤。
    """
    try:
        rows = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 2").fetchall()
        if not rows:
            return [], {"ok": False, "error": "尚無大盤資料"}
        m = dict(rows[0])
        prev_row = dict(rows[1]) if len(rows) > 1 else {}
        if not force and m["date"] != datetime.now().strftime("%Y-%m-%d"):
            return [], {"ok": False, "skipped": True, "error": f"資料日 {m['date']} 非今日，略過"}
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
        # Gemini 回 markdown，LINE 不渲染 → 先清成純文字，否則滿屏 ** 與 ###
        ai_text = line_push.strip_markdown(ai.get("text")) if ai.get("enabled") else ""
        try:
            cup = _cup_push_info(c)
        except Exception:  # noqa: BLE001
            cup = None
        # AI 解讀改由卡片第二頁承載（使用者拍板：自選股/杯柄不放，改看 AI）→ 只回一則
        msgs = [line_push.compose_daily_flex(m, secs, watch, full=full, tsmc=tsmc,
                                            prev=prev_row, cup=cup, ai_text=ai_text)]
    except Exception as e:  # noqa: BLE001 — fatal 才記推播失敗（缺資料/非今日屬正常略過）
        return [], {"ok": False, "error": str(e), "fatal": True}
    return msgs, None


def _weekly_messages(c) -> list:
    """籌碼週報訊息：**純文字一則**（使用者拍板不做卡片）。週六排程與 webhook「週報」共用。

    內容＝重點類股＋本週前五＋AI 分析；AI 佔篇幅最大且是週報的重點，純文字最好讀也好複製。
    """
    from ..api.public import weekly, summary_logic
    comparison = weekly()
    ai = summary_logic(c, refresh=0)   # 讀既有快取，不觸發重新扣費
    ai_text = line_push.strip_markdown(ai.get("text")) if ai.get("enabled") else ""
    return [{"type": "text",
             "text": line_push.compose_weekly_brief(comparison, ai_text=ai_text)}]


def _rank_message(c) -> dict:
    """高價股 Top10 的 LINE 訊息（Flex 表格，欄位真正對齊）。排版理由見 compose_rank_flex。"""
    from ..api.market import rank_price
    return line_push.compose_rank_flex(rank_price(market="all", n=10))


def _push_line(c, full: bool, force: bool = False) -> dict:
    cfg = load_config()
    if not cfg.line_token:
        return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
    msgs, err = _daily_messages(c, full=full, force=force)
    if err:
        if err.get("fatal"):
            _note_push_fail(c, full, err.get("error"))
        return err
    prev_fail = get_setting(c, "line_push_fail")
    if prev_fail:
        msgs = [{"type": "text",
                 "text": f"⚠️ 前次推播失敗（{prev_fail}），數據以本則為準"}] + msgs
    r = line_push.broadcast_messages(cfg.line_token, msgs)
    if not r.get("ok"):
        time.sleep(2)
        r = line_push.broadcast_messages(cfg.line_token, msgs)
    if r.get("ok"):
        if prev_fail:
            set_setting(c, "line_push_fail", "")
    else:
        _note_push_fail(c, full, r.get("error") or f"HTTP {r.get('status')}")
    return r


def _has_quotes(payload) -> bool:
    """這包海期資料裡到底有沒有報價。空的分類清單是「抓失敗」而非「今天沒行情」。"""
    return bool(payload) and any(g.get("items") for g in (payload.get("categories") or []))


# 抓失敗（Yahoo 429）後的退避冷卻。實測 Zeabur：yf.download 與 chart API 備援
# 兩條路徑同時被限流（"Edge: Too Many Requests"）。失敗不寫快取（見下）雖然讓快取能
# 自己痊癒，卻也讓「每次輪詢都對 Yahoo 重打一整輪」——前端海期頁每 2 分鐘一次，
# 一輪失敗就是 1 次批次 yfinance + 最多 34 次逐檔 chart API 請求，等於在幫限流拖時間。
# 冷卻期內完全不打網路，只在冷卻過後才重試一次；`refresh=True`（使用者按「更新報價」）
# 是明確的手動動作，繞過冷卻。
_OSFUT_FAIL_COOLDOWN = 300  # 秒


def _osfut_cooling_down(c) -> bool:
    fail = get_ai_cache(c, "osfut:fail_at")
    if not fail:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(fail["at"])).total_seconds() < _OSFUT_FAIL_COOLDOWN
    except (KeyError, ValueError, TypeError):
        return False


def _os_futures(refresh: bool = False) -> dict:
    from ..sources import intl
    c = conn()
    # 讀取端也要擋空值，不能只擋寫入端：已經寫進去的壞快取沒有 TTL，
    # 只防未來、不治現有的話，機房那份空結果會被永遠端出來（部署後仍空白就是這個原因）。
    # 空的一律當快取未命中 → 重抓 → 自己好，不必人工進 DB 清。
    # key 帶日期：這是「日線底」，舊寫法固定 key 又無 TTL，曾把 7/5 的報價一路端到 7/24。
    key = f"osfut:{datetime.now().strftime('%Y-%m-%d')}"
    cached = get_ai_cache(c, key)
    if _has_quotes(cached) and not refresh:
        return cached
    skip_network = not refresh and _osfut_cooling_down(c)
    if skip_network:
        cats = [{"category": cat, "items": []} for cat, _ in intl.OS_FUTURES]
    else:
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
    # 只在「真的抓到報價」時才寫快取。fetch_futures_monitor 抓不到時回的是
    # 「5 個分類、每組 0 檔」——那是個真值，舊寫法 `if cats:` 會把整包失敗結果寫進去。
    if _has_quotes(result):
        set_ai_cache(c, key, result)
    elif not skip_network:   # 這次真的打了網路才算一次失敗；冷卻中跳過的不重複計時
        set_ai_cache(c, "osfut:fail_at", {"at": datetime.now().isoformat()})
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
    if _has_quotes(cached):        # 空的當未命中，理由同 _os_futures
        try:
            age = (datetime.now() - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if age < _OSFUT_LIVE_TTL:
                return cached
        except (KeyError, ValueError, TypeError):
            pass
    # 冷卻狀態要在呼叫 base 之前先讀：base 若這次剛好失敗，會當場寫入 fail_at，
    # 若在那之後才判斷冷卻，read 到的會是自己剛寫的那筆，導致「這一輪」的 live 被
    # base 這一輪的失敗誤擋——冷卻只該擋「下一輪」，不是同一輪內互相牽連。
    skip_network = _osfut_cooling_down(c)
    base = copy.deepcopy(_os_futures(refresh=False))   # 日線底（含加權/台指期注入，已內建冷卻）
    # live 打的是同一個被限流的 Yahoo chart API，必須共用同一個冷卻窗——否則 base 那邊
    # 已經在退避，這裡卻還是每次照樣打 34 檔逐檔請求，冷卻等於白設。
    if skip_network:
        live = []
    else:
        try:
            live = intl.fetch_futures_live()
        except Exception:  # noqa: BLE001
            live = []
    live_map = {(g["category"], i["name"]): i for g in live for i in g["items"]}
    for g in base.get("categories") or []:
        g["items"] = [{**item, **live_map.get((g["category"], item["name"]), {})}
                      for item in g["items"]]
        # 日線底缺的檔直接用 live 報價補上（聯集，不是只覆蓋）。原本只把 live 合併
        # 「進日線清單」，機房 yfinance 被擋、日線底空掉時，明明抓到的 live 全被丟棄，
        # 頁面只剩注入的加權/台指期——base 的空與 live 的成功互相獨立，不能讓前者否決後者。
        have = {i["name"] for i in g["items"]}
        g["items"].extend(i for lg in live if lg["category"] == g["category"]
                          for i in lg["items"] if i["name"] not in have)
    result = {**base, "live": True, "updated_at": datetime.now().isoformat(),
              "fetched_at": datetime.now().isoformat()}
    if _has_quotes(result):        # 與 _os_futures 同規則：空結果不寫快取
        set_ai_cache(c, "osfut:live", result)
    elif not skip_network and not live:
        # 這次真的打了 live 卻一無所獲，記一次失敗——base 若也失敗會各記各的，
        # 但都是「延長冷卻到現在」，不會互相蓋掉彼此的退避
        set_ai_cache(c, "osfut:fail_at", {"at": datetime.now().isoformat()})
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
