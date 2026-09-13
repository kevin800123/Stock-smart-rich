import os
import base64
import binascii
import logging
import secrets
import threading
import tempfile
import time
from datetime import date, datetime

from .. import ss_trader
from ..config import load_config
from ..db import (
    get_connection,
    init_db,
    get_setting,
    set_setting,
    get_ai_cache,
    set_ai_cache,
    list_watch,
    set_watch_estimate,
    get_snapshot_dates,
    get_snapshot,
    list_trades,
    get_tx_history,
    JOB_RUN_DONE,
    start_job_run,
    finish_job_run,
    job_run_status,
    mark_interrupted_job_runs,
)
from .. import line_push
from ..sources import twse, tpex, mis
from .. import analysis, patterns, backtest
from .deps import conn
from ..scheduler import parse_schedule_time

log = logging.getLogger("spr.jobs")

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


def checklist_inputs(c) -> dict:
    """`ss_trader.market_checklist()` 的三個動態輸入，供總覽儀表板與「操盤手」頁共用
    一份組裝邏輯——兩邊各自組一次容易在夜盤量比或結算週判定上悄悄漂移（同 CLAUDE.md
    對 Elliott wave／估價公式「只能有一份權威版本」的規矩）。"""
    tx = c.execute(
        "SELECT volume, night_volume FROM tx_history ORDER BY date DESC LIMIT 1").fetchone()
    night_ratio = (tx["night_volume"] / tx["volume"]) if tx and tx["night_volume"] and tx["volume"] else None
    return {
        "osfut": get_ai_cache(c, "osfut:current"),
        "night_ratio": night_ratio,
        "settlement_week": ss_trader.is_settlement_week(date.today()),
    }


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
            "holder_drop_ratio, big_holder_ratio, capital FROM chip_snapshot "
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
        # 股數(股) = 股本(億元) × 1e7，假設面額 10 元（絕大多數上市櫃股票通例，特例不逐股查表）。
        shares = chip["capital"] * 1e7 if (chip and chip.get("capital")) else None
        estimate = analysis.estimate_price_range(
            revenue=w.get("est_revenue"), gross_margin_pct=w.get("est_gross_margin"),
            opex=w.get("est_opex"), tax=w.get("est_tax"), shares=shares,
            pe_low=w.get("est_pe_low"), pe_mid=w.get("est_pe_mid"), pe_high=w.get("est_pe_high"),
        )
        out.append({**w, "name": nm, "in_latest": bool(latest and code in picks_by_date.get(latest, {})),
                    "times": len(on), "entry_date": entry, "ret_pct": ret, "chip": chip,
                    "shares": shares, "estimate": estimate})
    return {"stocks": out, "latest": latest}


def _save_watch_estimate(c, code: str, payload: dict) -> dict:
    """存自選股「輸入預估」面板的原始輸入（只存呼叫端有帶的欄位，其餘既有值不動）。"""
    from ..db import WATCHLIST_COLS
    fields = {k: payload.get(k) for k in WATCHLIST_COLS if k in payload}
    set_watch_estimate(c, code, fields)
    return _get_watchlist(c)


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


def _is_quota_exceeded(resp: dict) -> bool:
    """LINE broadcast 月額度用盡回 429，body 是 `{"message": "You have reached your
    monthly limit."}`——與其他 429（例如短時間內炸太多次）用同一個狀態碼，只能靠
    文字分辨，故比對 `error` 裡的關鍵詞而非只看 status。"""
    return resp.get("status") == 429 and "monthly limit" in (resp.get("error") or "").lower()


def line_quota_paused(c) -> bool:
    """本月是否已偵測到 LINE broadcast 月額度用盡。

    儲存的是「用盡當下的月份字串」，判斷式只比對是否等於**現在**的月份——月份
    字串一換（次月）比對自然不成立，不需要另外排程去清除設定就能自動恢復。
    只擋 broadcast（主動推播），webhook 的 reply 不耗額度、不受影響。"""
    return get_setting(c, "line_quota_month") == datetime.now().strftime("%Y-%m")


def _note_line_quota_exceeded(c) -> None:
    set_setting(c, "line_quota_month", datetime.now().strftime("%Y-%m"))


def cup_min_turnover_setting(c) -> float:
    """杯柄流動性門檻（日均成交額，元）的**單一權威解析**——設定頁與篩選端點共用同一支，
    免得兩邊對「未設」「空字串」「0」的處理漂移。

    未設／空字串 → patterns 的預設值；明確設 0 → 回 0（＝關閉濾網，filter_liquid 不過濾）；
    壞值 → 退回預設（寧可照預設篩，也不要因為一個爛設定值就把濾網整個關掉）。
    """
    raw = get_setting(c, "cup_min_turnover")
    if raw in (None, ""):
        return float(patterns.CUP_MIN_TURNOVER_DEFAULT)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(patterns.CUP_MIN_TURNOVER_DEFAULT)


def cup_handle_screen_logic(c, min_r: float = patterns.MIN_R_DEFAULT):
    from ..db import ohlc_dates, get_all_ohlc
    ods = ohlc_dates(c)
    if not ods:
        return {"date": None, "count": 0, "stocks": [],
                "note": "尚未回補個股歷史，請先執行 /api/ohlc/backfill"}
    latest = ods[-1]
    min_turnover = cup_min_turnover_setting(c)
    # **門檻必須進快取鍵**：否則調了門檻卻拿到上一次的結果，看起來像「設定沒生效」
    # （同 news:v7 版號那條教訓——換了語意就要換鍵）。
    key = f"cuphandle:{latest}:{len(ods)}:{min_r:g}:{min_turnover:g}"
    result = get_ai_cache(c, key)
    if result is None:
        data = get_all_ohlc(c, min_bars=patterns.LOOKBACK)
        names = _ohlc_names(c)
        for code, s in data.items():
            s["name"] = names.get(code) or code
        matches = patterns.screen_cup_handle(data, min_r=min_r)
        scanned = len(matches)
        matches, n_illiquid, n_no_vol = patterns.filter_liquid(matches, min_turnover)
        result = {"date": latest, "bars": len(ods), "count": len(matches),
                  "min_r": min_r, "stocks": matches,
                  # 缺料不靜默：把「型態成立但被流動性刷掉」的檔數攤開來，
                  # 否則量能覆蓋率不好時畫面會安靜地變少，看起來像程式壞了。
                  "min_turnover": min_turnover, "matched_before_liquidity": scanned,
                  "filtered_illiquid": n_illiquid, "filtered_no_volume": n_no_vol,
                  "adv_cap_pct": patterns.POSITION_ADV_CAP_PCT}
        set_ai_cache(c, key, result)
        # 盤中哨兵/前瞻測試的訊號快照只在「預設嚴格度」時寫入——
        # 避免使用者在 UI 暫調寬鬆值污染警示與績效統計的訊號集
        if min_r == patterns.MIN_R_DEFAULT and min_turnover == patterns.CUP_MIN_TURNOVER_DEFAULT:
            sig_snapshot = []
            for m in matches:
                o = c.execute("SELECT high, low, close FROM stock_ohlc WHERE code=? "
                              "ORDER BY date DESC LIMIT 15", (m["code"],)).fetchall()
                rows = list(reversed(o))
                a = patterns.atr([r["high"] for r in rows], [r["low"] for r in rows],
                                 [r["close"] for r in rows])
                # avg_vol＝突破量能確認的分母（近 20 日均量，張）。screen_cup_handle 早就
                # 附在 match 上了，這裡只是帶進哨兵快照，盤中不必每 5 分鐘重算一次。
                # 舊快照沒有這個鍵 → volume_confirmed 回 None → fail-open，隔天重建就有。
                sig_snapshot.append({"code": m["code"], "name": m["name"],
                                     "resistance": m["resistance"], "atr": a,
                                     "avg_vol": m.get("avg_volume_lots")})
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
        # 融資 16:00 還沒公布，但卡片兩則都要顯示 → 取「最近一筆有融資的交易日」與其前一日，
        # 由 compose_daily_flex 判斷是否標「截至 MM-DD」（同網頁 balanceCard 的處理）
        mrows = c.execute(
            "SELECT * FROM market_daily WHERE margin_balance IS NOT NULL "
            "ORDER BY date DESC LIMIT 1").fetchall()
        margin_row = dict(mrows[0]) if mrows else None
        margin_prev = None
        if margin_row:
            p = c.execute("SELECT * FROM market_daily WHERE date < ? ORDER BY date DESC LIMIT 1",
                          (margin_row["date"],)).fetchall()
            margin_prev = dict(p[0]) if p else None
        # AI 解讀改由卡片第二頁承載（使用者拍板：自選股/杯柄不放，改看 AI）→ 只回一則
        msgs = [line_push.compose_daily_flex(m, secs, watch, full=full, tsmc=tsmc,
                                            prev=prev_row, cup=cup, ai_text=ai_text,
                                            margin_row=margin_row, margin_prev=margin_prev)]
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
    if line_quota_paused(c):
        return {"ok": False, "error": "本月 LINE 推播額度已用盡，暫停主動推播（次月自動恢復）",
                "quota_paused": True}
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
    # 額度用盡重試也不會成功，且只是在浪費第二次呼叫——不重試，直接判定並記錄
    if not r.get("ok") and not _is_quota_exceeded(r):
        time.sleep(2)
        r = line_push.broadcast_messages(cfg.line_token, msgs)
    if r.get("ok"):
        if prev_fail:
            set_setting(c, "line_push_fail", "")
    elif _is_quota_exceeded(r):
        _note_line_quota_exceeded(c)
        r = {**r, "quota_paused": True}
    else:
        _note_push_fail(c, full, r.get("error") or f"HTTP {r.get('status')}")
    return r


# 抓失敗後的退避冷卻。海期監控 2026-07 已改為排程每日固定兩次（main.py 的 07:30／21:30
# job）＋ TradingView 資料源（Yahoo 被 Zeabur IP 429 擋死，已換掉）。冷卻在這裡的用途
# 縮小為「同一天兩次排程之間，使用者手動連按『更新報價』」時的保險，不再是主要防線；
# 成本低故保留。`refresh=True`（手動按鈕／排程 job）繞過冷卻。
_OSFUT_FAIL_COOLDOWN = 300  # 秒

# Gemini 免費層是 **每日 × 每專案 × 每模型 20 次**，額度很淺。呼叫失敗時我們刻意
# 不寫快取（免得把失敗永久化），但「不快取失敗」單獨存在就會變成重試風暴——每次
# 進頁面都重打一次。這裡沿用 `_osfut_cooling_down` 那套：失敗後靜置一段時間再試。
# 使用者按「更新摘要」（refresh=1）是明確意圖，可以穿透冷卻。
_AI_FAIL_COOLDOWN = 900  # 秒（15 分鐘；免費層一天只有 20 次，重試要克制）


def ai_cooling_down(c) -> bool:
    fail = get_ai_cache(c, "ai:fail_at")
    if not fail:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(fail["at"])).total_seconds() < _AI_FAIL_COOLDOWN
    except (KeyError, ValueError, TypeError):
        return False


def note_ai_failure(c) -> None:
    set_ai_cache(c, "ai:fail_at", {"at": datetime.now().isoformat()})


def bump_ai_calls(c) -> int:
    """記錄今天成功打了幾次 Gemini，讓設定頁看得到離 20 次上限還有多遠。

    這次額度用盡是**完全無聲**發生的——撞上限前沒有任何可查的跡象，網頁才突然
    整段吐出 429 原文。只算成功的呼叫：被 429 擋掉的請求本來就沒算進當日配額。
    """
    key = f"aicalls:{datetime.now().strftime('%Y-%m-%d')}"
    n = (get_ai_cache(c, key) or {}).get("n", 0) + 1
    set_ai_cache(c, key, {"n": n})
    return n


def ai_calls_today(c) -> int:
    key = f"aicalls:{datetime.now().strftime('%Y-%m-%d')}"
    return (get_ai_cache(c, key) or {}).get("n", 0)


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
    # 讀取端也要認「有沒有真的遠端資料」，不能只擋寫入端：已寫進去的壞快取沒有 TTL，
    # 只防未來、不治現有的話，那份壞快取會被永遠端出來（部署後仍空白就是這個原因）。
    # 用 has_remote 旗標判斷——不能看「有沒有 items」，因為下面會注入本地的加權/台指期，
    # 「遠端全滅、只剩注入兩檔」也會有 items。只有 has_remote=True 的快取才可信；
    # 舊版寫的壞快取沒有這個旗標 → 視為未命中 → 重抓 → 自己好，不必人工進 DB 清。
    # key 帶日期：這是當日快取，舊寫法固定 key 又無 TTL，曾把 7/5 的報價一路端到 7/24。
    key = f"osfut:{datetime.now().strftime('%Y-%m-%d')}"
    cached = get_ai_cache(c, key)
    if cached and cached.get("has_remote") and not refresh:
        return cached
    skip_network = not refresh and _osfut_cooling_down(c)
    if skip_network:
        cats = [{"category": cat, "items": []} for cat, _ in intl.OS_FUTURES]
    else:
        try:
            cats = intl.fetch_futures_monitor()
        except Exception:  # noqa: BLE001
            cats = []
    # 快取「是否成功」要看遠端抓到沒，不能看最終結果——下面會注入本地的加權/台指期，
    # 那兩檔永遠有值，用 _has_quotes(result) 判斷會把「遠端全滅、只剩注入兩檔」也當成功寫入
    # （這是 Yahoo 全滅時 updated_at 照樣更新、資料卻空的成因）。故在注入前先記下遠端戰果。
    got_remote = any(g.get("items") for g in cats)
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
    result = {"categories": cats, "updated_at": datetime.now().isoformat(),
              "has_remote": got_remote}
    if got_remote:
        set_ai_cache(c, key, result)
    elif not skip_network:   # 這次真的打了網路才算一次失敗；冷卻中跳過的不重複計時
        set_ai_cache(c, "osfut:fail_at", {"at": datetime.now().isoformat()})
    return result



# 自算選股當天一定要到齊的資料。融資不在內：約 21:00 才公布，而且只是參考欄、不進篩選。
SELF_SCREEN_REQUIRED = (("TWSE", "quotes"), ("TWSE", "institutional"),
                        ("TPEx", "quotes"), ("TPEx", "institutional"))


def self_screen_missing_inputs(c, day: str) -> list[str]:
    """回傳 day 這天還沒到齊的「市場/來源」。依據是 stock_flow.update_day 寫的覆蓋表。

    **這道門檻是提早計算的前提**：`institutional_3d_map` 取「截至當天、有資料的最近 3 天」，
    法人還沒公布時會安靜地拿昨天的窗口冒充今天，而投信／外資三日是木質的加分——名單會算錯，
    還會被記進前瞻訊號、補不回來。
    """
    status = {(m, s): st for m, s, st in c.execute(
        "SELECT market, source, status FROM stock_source_coverage WHERE date=?", (day,))}
    return [f"{m}/{s}" for m, s in SELF_SCREEN_REQUIRED if status.get((m, s)) != "complete"]


def early_self_screen(c) -> dict:
    """平日傍晚提早算今天的自算選股（使用者要求最晚 20:00 更新好）。排程 17:30／18:30／19:30。

    這時 21:00 的每日更新還沒跑，market_daily 沒有今天那一列，所以**日期取真實日曆**，並明確
    傳給 refresh_self_screen_cache（它再傳給前瞻訊號）。當天行情與法人由 update_day 自己抓
    ——一天 6 個請求，比整支 run_update 輕得多。已算好就直接略過，後面兩次不重抓。
    資料沒到齊就回 data_not_ready，下一次再試；三次都沒到齊，21:00 那次仍會照常算。
    """
    from .. import selfcheck, stock_flow
    now = _now()
    if now.weekday() >= 5:
        return {"cached": False, "skipped": "weekend"}
    day = now.date().isoformat()
    if selfcheck.load_precomputed(c, day):
        return {"cached": False, "date": day, "skipped": "already_ready"}
    try:
        stock_flow.update_day(c, now.date())
    except Exception as e:  # noqa: BLE001 — 抓取失敗時覆蓋表不會是 complete，下面的門檻自然擋下
        log.warning("[self_screen_early] update_day 失敗：%s: %s", type(e).__name__, e)
    return refresh_self_screen_cache(c, day=day)


def refresh_self_screen_cache(c, day: str | None = None) -> dict:
    """每日排程的自算選股：**算一次、存一次**，前瞻追蹤吃同一份。回一份可觀察的結果。

    放在 helpers 而不是 main.py 的閉包裡，是本專案的既定分工（「排程 Job 需要的邏輯先
    放 api/helpers.py，main.py 只呼叫」）——寫在閉包裡就**測不到、也沒辦法單獨跑一次
    看它到底有沒有成功**，而它在排程裡被 `except: pass` 包著，壞掉不會有任何聲音。

    全市場逐檔自算（季報／月營收／集保／法人／OHLC 約 2,000 檔）不能放進請求路徑
    ——同「請求裡不要放無界時間的同步計算」那條教訓（stock-flow/research 與
    financials/backfill-report 都踩過）。

    回傳的 dict 就是「這次到底做了什麼」：`date`／`universe`／`cached`／`picked`／
    `skipped`（沒做的原因）。呼叫端可以印出來，不必去猜。
    """
    from ..ledger import record_self_screen_signals
    from .. import analysis, selfcheck

    day = day or _latest_date(c)
    if not day:
        return {"cached": False, "date": None, "skipped": "no_market_date"}
    missing = self_screen_missing_inputs(c, day)
    if missing:
        # 當天行情或法人還沒到齊：不算、不存、不記前瞻訊號（理由見 self_screen_missing_inputs）
        return {"cached": False, "date": day, "skipped": "data_not_ready", "missing": missing}
    otc, listed = _otc_industry(c), _industry_map(c)
    universe = {**otc, **listed}
    if not universe:
        # 缺哪一邊要講出來——「今天沒做」與「今天做了但沒選到股」是兩件事
        return {"cached": False, "date": day, "universe": 0, "skipped": "empty_universe"}
    if not otc or not listed:
        # **只抓到一個市場就整天跳過**。原本只擋「兩邊都空」，於是櫃買斷線那天會拿「只有
        # 上市」照常算、存快取、記前瞻訊號。最後一項補不回來：record_self_screen_signals
        # 每個訊號日只寫一次，偏差的樣本會永遠留在前瞻勝率裡。一天空缺只是少一天樣本，
        # 一天偏差會讓結論失真。上櫃名單是月快取，換月後第一次才去櫃買抓，正是風險點。
        return {"cached": False, "date": day, "universe": len(universe),
                "listed": len(listed), "otc": len(otc), "skipped": "partial_universe"}

    def _th(key, dflt):
        raw = get_setting(c, key)
        try:
            return float(raw) if raw is not None else dflt
        except (TypeError, ValueError):
            return dflt

    vmin = _th("screen_mu_value_min", analysis.SCREEN_MU_VALUE_MIN)
    smin = _th("screen_mu_score_min", analysis.SCREEN_MU_SCORE_MIN)
    pre = selfcheck.compute_self_screen(c, day, universe)
    # ready_at＝這一天的名單「最早」算好的時間，computed_at＝這份快取最後寫入的時間。
    # 21:00 那次會為了補上融資再算一遍，只記最後寫入的話畫面永遠是 21:0x，看不出 20:00 前算好沒。
    stamp = _now().isoformat(timespec="seconds")
    prev = selfcheck.load_precomputed(c, day)
    pre["computed_at"] = stamp
    pre["ready_at"] = (prev or {}).get("ready_at") or stamp
    selfcheck.save_precomputed(c, pre)
    # 前瞻追蹤吃同一份，不重算；訊號日明講是哪一天（提早計算時 market_daily 還沒有今天）
    record_self_screen_signals(c, universe, vmin, smin, precomputed=pre, signal_date=day)
    picked = len(selfcheck.build_self_screen(
        c, day, universe, vmin, smin, precomputed=pre)["rows"])
    return {"cached": True, "date": day, "universe": len(universe),
            "listed": len(listed), "otc": len(otc),
            "rows": len(pre["rows"]), "sectors": len(pre["heatmap"]), "picked": picked}


def _intraday_scan(c, push: bool = True) -> dict:
    cfg = load_config()
    # 每 5 分鐘一次的排程最容易在額度用盡當天反覆撞 429——已知本月用盡就直接不送，
    # 掃描/命中判定照常跑（回傳值不受影響），只是不廣播、也不把命中標成「已警示」
    # （這樣額度恢復後同一天若還在盤中，仍會補送這次沒送出的警示）。
    push = push and not line_quota_paused(c)
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
    # 改用 fetch_mis_rank（同一支 MIS 端點、同樣一次請求），差別只在它把 v=當日累積量
    # 一併解出來；fetch_mis_quotes 只回價格，量能閘就沒有分子可用。
    quotes = mis.fetch_mis_rank(tokens)
    if not quotes:
        if _mis_state["date"] != today:
            _mis_state.update({"date": today, "fails": 0, "warned": False})
        _mis_state["fails"] += 1
        if _mis_state["fails"] >= 6 and not _mis_state["warned"] and push:
            r = line_push.broadcast_text(cfg.line_token, "⚠️ 盤中突破哨兵連續無法取得報價（來源可能失效），今日暫停警示。")
            if not r.get("ok") and _is_quota_exceeded(r):
                _note_line_quota_exceeded(c)
            _mis_state["warned"] = True
        return {"checked": len(pending), "hits": [], "note": "查查無報價"}
    _mis_state.update({"date": today, "fails": 0})
    threshold = lambda s: s["resistance"] + 0.3 * s["atr"] if s.get("atr") else s["resistance"]
    crossing = {}
    for s in pending:
        q = quotes.get(s["code"])
        if not q or q.get("price") is None or q["price"] <= threshold(s):
            continue
        vol, avg_vol = q.get("vol"), s.get("avg_vol")
        crossing[s["code"]] = {
            **s, "price": q["price"], "pick": s["code"] in picks,
            "vol": vol, "avg_vol": avg_vol,
            "vol_ratio": round(vol / avg_vol, 1) if (vol is not None and avg_vol) else None,
            "vol_ok": patterns.volume_confirmed(vol, avg_vol)}
    candidates = set(get_ai_cache(c, f"cuppending:{today}") or [])
    confirmed = [v for code, v in crossing.items() if code in candidates]
    # 量能閘：**只擋 False，不擋 None**。None＝算不出（舊快照沒有 avg_vol、或 MIS 這塊
    # 沒回量），照常送出並在訊息裡標「量能未確認」——缺料不該讓警示安靜消失。
    hits = [v for v in confirmed if v["vol_ok"] is not False]
    held = len(confirmed) - len(hits)
    # 被量能擋下的**仍留在 cuppending**：價格還站在門檻上，量堆上來的下一輪就會發，
    # 不必重新穿越一次。也不寫進 cupalerted，所以不會被當成「已警示」而永久略過。
    set_ai_cache(c, f"cuppending:{today}", sorted(crossing.keys()))
    if hits and push:
        txt = line_push.compose_breakout_alert(hits, datetime.now().strftime("%H:%M"))
        r = line_push.broadcast_text(cfg.line_token, txt)
        if not r.get("ok") and _is_quota_exceeded(r):
            _note_line_quota_exceeded(c)
        set_ai_cache(c, f"cupalerted:{today}", sorted(alerted | {h["code"] for h in hits}))
    # held_by_volume 攤開來，否則「今天怎麼都沒警示」分不出是沒股票突破還是量都不夠
    return {"checked": len(pending), "hits": hits, "held_by_volume": held}


def _now() -> datetime:
    """告警用的「現在」。獨立成函式是為了讓測試固定星期幾——週末不推告警之後，
    直接吃 datetime.now() 會讓測試「星期天紅、星期一綠」。"""
    return datetime.now()


def _check_update_result_and_alert(c, result: dict) -> None:
    from datetime import date as _dt_date
    cfg = load_config()
    today_dt = _now()
    today_str = today_dt.strftime("%Y-%m-%d")
    failed = result.get("failed") or []
    res_date_str = result.get("date")

    is_weekday = today_dt.weekday() < 5
    # **週六日一律不推資料告警**（使用者決定：六日台股沒開盤，LINE 只要週六的選股週報）。
    # 2026-09-12(六)／13(日) 21:00 各收到一則，才發現這條路徑從來沒看星期幾——底下的
    # lagging 有擋週末，失敗來源那一支卻沒有。每日排程週末照跑是對的（月營收每天重抓、
    # 備份、自算選股快取），只是不推。**也不記去重鍵**：週一同一個來源還失敗，週一照樣要講。
    if not is_weekday:
        return
    lagging = False
    lag_days = 0
    if res_date_str and res_date_str != today_str and is_weekday:
        try:
            lag_days = (today_dt.date() - _dt_date.fromisoformat(res_date_str)).days
            if lag_days > 0:
                lagging = True
        except Exception:  # noqa: BLE001
            pass

    # These sources are published later than the 21:00 Taiwan close workflow
    # (or on the next overseas-market close).  Keep them in the update result
    # for the UI/backfill, but do not turn expected timing into a LINE alarm.
    def expected_later(f: dict) -> bool:
        name, error = f.get("name") or "", f.get("error") or ""
        if name in ("margin_maintenance", "otc_margin_maintenance"):
            return "尚未" in error or "稍後回補" in error
        return name == "intl" and ("尚未取得" in error or "自動回補" in error)

    alertable = [f for f in failed if not expected_later(f)]
    if alertable or lagging:
        failed_sources = []
        # **要印 source**：月營收是逐市場判定的（上市 twse／上櫃 tpex 各自失敗），
        # 只印 name 的話使用者永遠看到「revenue」、分不出是哪一邊——實測 2026-09-12
        # 收到那則時完全無從判斷，而 source 其實一直都記著、只是沒印出來。
        for f in alertable:
            err_str = f.get("error") or ""
            if len(err_str) > 40:
                err_str = err_str[:37] + "..."
            src = f.get("source")
            label = f"{f.get('name')}／{src}" if src else f"{f.get('name')}"
            failed_sources.append(f"{label}（{err_str}）")

        failed_sources_str = "、".join(failed_sources) if failed_sources else "無"
        lag_msg = f"（落後 {lag_days} 個交易日）" if lagging else ""
        msg = (
            f"⚠️ 資料更新警告 {today_str}\n"
            f"失敗來源：{failed_sources_str}\n"
            f"資料日期：{res_date_str or '未知'}{lag_msg}"
        )

        failed_names = sorted(list({f.get("name") for f in alertable if f.get("name")}))
        alert_key = f"{res_date_str}|{','.join(failed_names)}"

        last_alert = get_setting(c, "last_alert_key")
        if last_alert != alert_key:
            line_push.broadcast_text(cfg.line_token, msg)
            set_setting(c, "last_alert_key", alert_key)


# ---------------------------------------------------------------------------
# 排程規格 ＋ 執行紀錄 ＋ 啟動補跑
#
# 為什麼 misfire_grace_time 不夠：APScheduler 用的是記憶體 jobstore，程序重啟後它不知道
# 自己錯過了什麼——寬限時間只對「排程器活著但來不及跑」有用。push 到 main 會觸發 Zeabur
# 重新部署＝程序重啟，橫跨排程時間那一場就整場消失，而告警本身就寫在那支 job 裡。
# 所以要的是：每次執行留紀錄（job_runs），啟動時查「今天該跑的有沒有成功紀錄」，沒有就補。
# ---------------------------------------------------------------------------
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_job_lock = threading.Lock()


def job_schedule(cfg, schedule_time: str) -> list[dict]:
    """所有排程 job 的唯一權威清單。main.py 註冊 APScheduler 與啟動補跑都吃這一份，
    才不會註冊一套、補跑另一套。

    每筆：id（APScheduler job id）、family（補跑時同一家族只補**最近錯過的一場**，使用者
    拍板：整天停機晚上恢復不該一次收到四場新聞）、hour／minute／dow（cron 欄位字串，
    與 add_job 同寫法）、catchup（False＝不補跑：盤中警示過了時間就沒意義）。
    """
    h, m = parse_schedule_time(schedule_time)
    specs = [
        {"id": "daily_update", "family": "daily_update", "hour": str(h), "minute": str(m), "dow": None},
        # 海期監控：一天固定兩次，與 LINE 是否設定無關（Yahoo 被 Zeabur IP 429 之後改成排程）
        {"id": "osfut_morning", "family": "osfut", "hour": "7", "minute": "30", "dow": None},
        {"id": "osfut_evening", "family": "osfut", "hour": "21", "minute": "30", "dow": None},
        # 自算選股：平日 17:30／18:30／19:30 各試一次，資料到齊就算、算好就略過，最後一次在
        # 20:00 之前（使用者要求）。實測 2026-08-26 19:27 行情與法人已到齊、只差融資。
        {"id": "self_screen_early", "family": "self_screen_early",
         "hour": "17,18,19", "minute": "30", "dow": "mon-fri"},
    ]
    # Telegram 新聞：token 與 chat id 缺一不可（只檢查 token 會註冊永遠送不出去的工作）。
    # 平日四場、週末只留 12:00／21:10（盤前／收盤快訊在沒開盤的日子是在報舊事）。
    # 21:10 不是 21:00：預設 daily_update 也是 21:00，排同一分鐘是巧合式的資源競爭。
    if cfg.telegram_token and cfg.telegram_chat_id:
        specs += [
            {"id": "news_morning", "family": "news", "hour": "7", "minute": "0", "dow": "mon-fri"},
            {"id": "news_midday", "family": "news", "hour": "12", "minute": "0", "dow": None},
            {"id": "news_afternoon", "family": "news", "hour": "17", "minute": "0", "dow": "mon-fri"},
            {"id": "news_evening", "family": "news", "hour": "21", "minute": "10", "dow": None},
        ]
    # LINE：盤中突破（平日 09:00–13:55 每 5 分，不補跑）與週六選股週報（時間可調、日固定）
    if cfg.line_token:
        wh, wm = parse_schedule_time(cfg.weekly_push_time)
        specs += [
            {"id": "intraday_watch", "family": "intraday_watch", "hour": "9-13", "minute": "*/5",
             "dow": "mon-fri", "catchup": False},
            {"id": "weekly_line", "family": "weekly_line", "hour": str(wh), "minute": str(wm), "dow": "sat"},
        ]
    return specs


def _dow_matches(dow: str | None, weekday: int) -> bool:
    if not dow:
        return True
    for part in dow.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if _DOW[a] <= weekday <= _DOW[b]:
                return True
        elif _DOW[part] == weekday:
            return True
    return False


def _cron_hours(hour: str) -> list[int]:
    out: list[int] = []
    for part in str(hour).split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(out)


def slot_times(spec: dict, day: date) -> list[datetime]:
    """這個 spec 在指定日曆日的所有觸發時刻（依 dow 過濾，早到晚）。只給可補跑的 spec 用。"""
    if not spec.get("catchup", True) or not _dow_matches(spec.get("dow"), day.weekday()):
        return []
    minute = int(spec["minute"])
    return [datetime(day.year, day.month, day.day, h, minute) for h in _cron_hours(spec["hour"])]


def run_key_for(spec: dict, slot: datetime) -> str:
    """一天只有一場 → 日期；一天多場（self_screen_early 三次）→ 日期:HH:MM，各場分開記。"""
    multi = len(_cron_hours(spec["hour"])) > 1
    return slot.strftime("%Y-%m-%d:%H:%M") if multi else slot.strftime("%Y-%m-%d")


def scheduled_run_key(spec: dict, now: datetime) -> str:
    """排程觸發時算 run_key：取「今天 ≤ now 的最後一個時段」（cron 只會晚不會早）。
    不補跑的 job（盤中每 5 分）直接用當下分鐘。"""
    if not spec.get("catchup", True):
        return now.strftime("%Y-%m-%d:%H:%M")
    slots = [t for t in slot_times(spec, now.date()) if t <= now]
    if not slots:   # dow 不符或提早觸發（理論上不會）：退回第一個時段，仍可去重
        slots = slot_times(spec, now.date()) or [now.replace(second=0, microsecond=0)]
        return run_key_for(spec, slots[0])
    return run_key_for(spec, slots[-1])


def _short(v, n: int = 300) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def run_job(job_id: str, run_key: str, fn, trigger: str = "scheduled") -> dict:
    """每支排程 job 的外層：開始寫一列 running、結束更新狀態，並各記一行 log。

    - 同一 (job_id, run_key) 已 ok／partial／running → 直接略過（補跑與排程撞在同一分鐘
      也不會跑兩次；Telegram 推播本身沒有去重，靠這裡）。
    - job 丟例外 → failed（例外進 log，不再是 except: pass 的無聲失敗）。
    - job 回 dict 且帶非空 failed_steps → partial（跑完了但有步驟失敗），note 存回傳摘要。
    - **紀錄本身失敗不吞**：finish_job_run 丟出來就讓它往上，呼叫端（APScheduler／補跑迴圈）
      會 log 出來——寬鬆的 except 把記錄失敗吃掉，正是本專案記過的坑。
    """
    import sqlite3
    c = conn()
    with _job_lock:
        st = job_run_status(c, job_id, run_key)
        if st in JOB_RUN_DONE or st == "running":
            log.info("[%s] skip run_key=%s（已 %s）", job_id, run_key, st)
            return {"job_id": job_id, "run_key": run_key, "status": "skipped", "reason": st}
        try:
            run_id = start_job_run(c, job_id, run_key, trigger, _now().isoformat(timespec="seconds"))
        except sqlite3.IntegrityError:
            # 唯一鍵 uq_job_runs_running 擋下：同 key 已有一列 running（另一條路徑搶先了）。
            # 上面的「先查」只是省一次失敗的 INSERT，真正的去重是這條唯一鍵。
            log.info("[%s] skip run_key=%s（唯一鍵：已在執行）", job_id, run_key)
            return {"job_id": job_id, "run_key": run_key, "status": "skipped", "reason": "running"}
    log.info("[%s] start run_key=%s trigger=%s", job_id, run_key, trigger)
    t0 = time.monotonic()
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 — 記下來、log 出來，不往排程器丟
        err = _short(f"{type(e).__name__}: {e}")
        log.exception("[%s] failed run_key=%s %.1fs %s", job_id, run_key, time.monotonic() - t0, err)
        finish_job_run(c, run_id, "failed", _now().isoformat(timespec="seconds"), error=err)
        return {"job_id": job_id, "run_key": run_key, "status": "failed", "error": err}
    status, err = "ok", None
    if isinstance(result, dict) and result.get("failed_steps"):
        status = "partial"
        err = _short("; ".join(str(x) for x in result["failed_steps"]))
    note = _short(result) if result is not None else None
    finish_job_run(c, run_id, status, _now().isoformat(timespec="seconds"), error=err, note=note)
    log.info("[%s] done status=%s run_key=%s %.1fs %s", job_id, status, run_key,
             time.monotonic() - t0, note or "")
    return {"job_id": job_id, "run_key": run_key, "status": status, "error": err, "note": note}


def catchup_plan(c, specs: list[dict], now: datetime) -> list[dict]:
    """今天已經該觸發、卻沒有成功紀錄的場次。只看**今天**（使用者決定不補前幾天），
    同一 family 只取最近的一場：那一場 ok 就整個家族不補（更早錯過的內容已過時）。
    依時段排序，所以 21:00 daily_update 會排在 21:10 news_evening 前面。"""
    latest: dict[str, tuple] = {}
    for spec in specs:
        for slot in slot_times(spec, now.date()):
            if slot > now:
                continue
            fam = spec["family"]
            if fam not in latest or slot > latest[fam][0]:
                latest[fam] = (slot, spec)
    plan = []
    for slot, spec in sorted(latest.values(), key=lambda t: t[0]):
        key = run_key_for(spec, slot)
        st = job_run_status(c, spec["id"], key)
        if st in JOB_RUN_DONE:
            continue
        plan.append({"job_id": spec["id"], "run_key": key,
                     "slot": slot.strftime("%H:%M"), "last_status": st})
    return plan


def catchup_missed_jobs(c, specs: list[dict], jobs: dict, now: datetime | None = None) -> dict:
    """啟動補跑（在背景執行緒呼叫，不可阻塞啟動）。

    1. 先把還停在 running 的列標成 interrupted——單 worker、本程序才剛起來，那些必然是上一個
       程序被重啟時留下的，補跑要把它們當「沒跑完」。
    2. 依 catchup_plan 逐一走 run_job（trigger=catchup），走的是同一支 job 函式，所以
       週末不推 LINE、資料日≠今天不推卡片那些既有守衛全部自動生效，不另寫推播路徑。
    """
    now = now or _now()
    n_int = mark_interrupted_job_runs(c, now.isoformat(timespec="seconds"))
    if n_int:
        log.warning("啟動補跑：%d 列 running 標成 interrupted（上一個程序被重啟）", n_int)
    plan = catchup_plan(c, specs, now)
    log.info("啟動補跑：%s", [f"{p['job_id']}@{p['slot']}" for p in plan] or "無")
    ran = []
    for item in plan:
        fn = jobs.get(item["job_id"])
        if fn is None:
            log.warning("啟動補跑：%s 沒有對應的 job 函式，略過", item["job_id"])
            continue
        try:
            r = run_job(item["job_id"], item["run_key"], fn, trigger="catchup")
        except Exception:  # noqa: BLE001 — 紀錄本身失敗：log 出來、繼續下一個，不無聲
            log.exception("啟動補跑：%s 執行紀錄寫入失敗", item["job_id"])
            r = {"status": "record_failed"}
        ran.append({**item, "result": r.get("status")})
    return {"interrupted": n_int, "plan": plan, "ran": ran}
