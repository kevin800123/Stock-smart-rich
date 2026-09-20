import logging
import threading
from fastapi import APIRouter, Body
from datetime import datetime, timedelta
from .deps import conn
from .helpers import (
    _latest_date,
    _get_watchlist,
    _save_watch_estimate,
    _ohlc_names,
    _picks_code_set,
    _valuation_for,
    _insti_for,
    get_ai_cache,
    set_ai_cache,
    cup_handle_screen_logic
)
from ..db import (
    get_ohlc_history,
    add_watch,
    remove_watch,
    get_snapshot_dates,
    get_custody_trend,
    upsert_custody,
    get_tx_history,
    upsert_tx_history,
    ohlc_dates,
    get_all_ohlc,
    get_connection
)
from ..config import load_config
from ..sources import kline, tdcc, twse, tpex, taifex
from .. import analysis, patterns, backtest

router = APIRouter(prefix="/api")
_custody_lock = threading.Lock()
_autofill_guard = threading.Lock()   # 護住 _start_custody_autofill 的 check-then-act：
                                      # 單程序單 worker，一把鎖就夠讓「同一天只補一次」成立
_log = logging.getLogger("spr")

CUSTODY_MIN_WEEKS = 30          # 少於這個週數就自動補歷史（集保一年約 52 週）
_custody_autofill = set()        # 本程序正在補的代號，避免同一檔重複起執行緒


def _should_autofill(trend: list) -> bool:
    """這檔的集保歷史夠不夠畫趨勢與人均數。

    兩種都要補：週數太少（新查的股票只有排程累積的那幾週），以及**有股數的週數不到一半**
    ——total_shares 是後加的欄位，既有部署的舊列全是空的，人均數會整片算不出來。"""
    if len(trend) < CUSTODY_MIN_WEEKS:
        return True
    # **用 `is not None` 不用真值判斷**：股數 0 是一筆有效觀測（智能網某週的股數欄
    # 解析不出來時 `_aggregate_levels` 會給 0.0），當成缺值會讓那一週永遠留在待補
    # 清單裡、每天重抓一次而且永遠補不完——同 `tpex.parse_otc_margin`「零餘額是有效
    # 觀測」與假日回補「要有終止狀態」那兩條既有教訓。
    return sum(1 for t in trend if t.get("total_shares") is not None) < len(trend) / 2


def _autofill_cache_key(pure: str) -> str:
    """節流鍵 `custodyauto:{code}:{date}` 的唯一組裝處——`_start_custody_autofill` 用它
    標記「今天已經真的試過」，`_custody_autofill_job` 搶不到鎖時用它撤銷同一把標記
    （見下方 final review I2 的說明）。抽成函式避免兩處字串各自拼、日後改格式漏改一處。"""
    return f"custodyauto:{pure}:{datetime.now().date().isoformat()}"


def _start_custody_autofill(code: str) -> bool:
    """背景補這一檔的集保歷史；回傳這次有沒有真的起跑。

    節流：同一檔同一天只補一次（ai_cache `custodyauto:{code}:{date}`），同時間只允許一個
    補歷史在跑（`_custody_lock`，由執行緒內取得）。智能網是逐週抓、每次要帶上一次回應輪替的
    CSRF token，52 週約半分到一分鐘，所以只能背景做，絕不能卡住請求。

    「同一天只補一次」是 check-then-act（先讀 ai_cache／集合成員，再寫入、起執行緒）——
    `_custody_lock` 只擋得住兩個背景執行緒同時刮智能網，擋不住「兩個幾乎同時的請求都通過
    檢查、各自起一條執行緒」這件事本身。所以整段檢查＋標記＋起執行緒要用 `_autofill_guard`
    包成一個原子區塊；單程序單 worker，一把普通鎖就夠。

    **這把節流鍵只代表「排進去了」，不代表「真的補到了」**（final review I2）：這裡只負責
    原子地檢查＋標記＋起執行緒，鎖真正搶不搶得到是背景執行緒的事，所以「搶不到鎖時要不要
    撤銷這把標記」的判斷放在 `_custody_autofill_job` 裡（它才知道結果），不是這裡。"""
    pure = code.split(".")[0]
    key = _autofill_cache_key(pure)
    c = conn()
    with _autofill_guard:
        if get_ai_cache(c, key) or pure in _custody_autofill:
            return False
        set_ai_cache(c, key, {"at": datetime.now().isoformat(timespec="seconds")})
        _custody_autofill.add(pure)
        threading.Thread(target=_custody_autofill_job, args=(pure,), daemon=True,
                         name=f"spr-custody-{pure}").start()
    return True


def _custody_autofill_job(pure: str) -> None:
    """背景執行緒本體：自己開一條 sqlite 連線（sqlite 預設不跨執行緒共用）。
    失敗只記 log，不重試——下一天使用者再查同一檔會再補一次。"""
    try:
        c = get_connection(load_config().db_path)   # 自己開一條：請求那條屬於別的執行緒
        c.execute("PRAGMA busy_timeout=30000")       # 與同時進行的寫入短暫相撞時等待（同 api/stock_flow.py）
        if not _custody_lock.acquire(blocking=False):
            _log.info("custody autofill skipped (another backfill running): %s", pure)
            # 搶不到鎖時要把「今天已補過」這把節流鍵撤掉（final review I2）：
            # `_start_custody_autofill` 在還沒確認搶得到鎖之前就已經寫入這把鍵，若這裡不
            # 撤掉，這檔股票今天就再也不會有機會真的補到——連續查好幾檔時，握著鎖的那檔在
            # 跑，其餘幾檔全部在這裡放棄，卻各自已經把「今天補過了」寫進 ai_cache，之後
            # 同一天重查也不會再試。節流鍵的用意是「今天已經真的試過」，不是「今天曾經被
            # 排過隊」，兩者不同（實測：連續對 5 個代號呼叫，只有握到鎖的那檔真的補到，
            # 另外 4 檔同一天再查全部回 False；撤鍵之後這 4 檔下次查詢會重新排隊）。
            c.execute("DELETE FROM ai_cache WHERE cache_key=?", (_autofill_cache_key(pure),))
            c.commit()
            return
        try:
            have = get_custody_trend(c, pure)
            have_weeks = {t["week"] for t in have}
            # 已有列但缺股數（total_shares）的週——舊資料沒有這欄，或欄位剛加不久還沒補到。
            weak_weeks = {t["week"] for t in have if t.get("total_shares") is None}
            avail = tdcc.fetch_custody_weeks()
            # `want` 同時收「全新的週」與「已有列但缺股數的週」兩種（final review I1）：
            # 舊版只濾掉 have_weeks，於是「有列但缺股數」的週永遠不會被列進 want——這正是
            # `_should_autofill` 第二個條件存在的理由，舊版對這種股票是永久 no-op：條件
            # 天天為真、天天白跑一次智能網，avg_shares 卻永遠是 None（實測本機真實 DB：
            # 2330 有 60 週集保，只有 2 週有股數，ai_cache 顯示今天已經跑過自動補、什麼也
            # 沒補到）。avail 本身是「新到舊」排序，直接濾掉不需要的週即可維持「新的優先」
            # ——新查的股票缺的是「有沒有資料」，舊資料缺的只是「有沒有股數」，兩者搶 52
            # 筆上限時讓較新的週優先補，補不到的等下一次（明天）再補。
            want = [w for w in avail
                    if f"{w[:4]}-{w[4:6]}-{w[6:8]}" not in have_weeks
                    or f"{w[:4]}-{w[4:6]}-{w[6:8]}" in weak_weeks][:52]
            hist = tdcc.fetch_custody_history(pure, weeks=want, max_weeks=52) if want else {}
            for wk_iso, rec in hist.items():
                upsert_custody(c, wk_iso, pure, rec)
            _log.info("custody autofill %s: +%d weeks", pure, len(hist))
        finally:
            _custody_lock.release()
    except Exception:  # noqa: BLE001  背景執行緒的例外沒有人接，記下來才看得見
        _log.exception("custody autofill failed: %s", pure)
    finally:
        _custody_autofill.discard(pure)


# **這條路由必須排在 `/stock/{code}/...` 之前**——FastAPI 依註冊順序比對，
# 若 `{code}` 那組先註冊，`/api/stock/search` 會被當成 code="search" 吃掉。
@router.get("/stock/search")
def stock_search(q: str = "", n: int = 8):
    """全域搜尋：代號前綴或名稱包含 → [{code, name}]。比對規則見 analysis.search_symbols。

    名稱表直接用 `_ohlc_names`（上市 + 上櫃，`ai_cache` 逐月快取），**不另開資料源也不另設
    快取鍵**：多一把無 TTL 的鍵就多一個「抓失敗的空表被永久化」的面（os-futures／turnover／
    dist 已各踩過一次），而這張表本來就是高價股／熱力圖在用的同一份。
    """
    return {"q": q, "items": analysis.search_symbols(_ohlc_names(conn()), q, n=n)}


@router.get("/stock/{code}/ohlc")
def stock_ohlc(code: str, bars: int = 400):
    pure = code.split(".")[0]
    rows = get_ohlc_history(conn(), pure)[-max(60, min(bars, 500)):]
    # volumes（張）：杯柄圖的量能窗格用。缺量給 0 而非 None——這是「畫得出來的柱高」，
    # 不是分析輸入；圖表沒有辦法呈現 None，而 0 在視覺上就是「那天沒有量能資料」。
    return {"code": pure, "dates": [r["date"] for r in rows],
            "candles": [[r["open"], r["close"], r["low"], r["high"]] for r in rows],
            "volumes": [r.get("volume") or 0 for r in rows]}

# 後備資料的時間窗。yfinance 自己會依 period 截斷，stock_ohlc 不會——它是逐日累積
# 的整張表，`get_ohlc_history` 一次回傳該股**所有**歷史列。
_PERIOD_DAYS = {"6mo": 183, "1y": 365, "2y": 730, "5y": 1825}


@router.get("/stock/{code}/kline")
def stock_kline(code: str, interval: str = "1d", period: str | None = None):
    if period is None:
        period = {"1d": "1y", "1wk": "2y", "1mo": "5y"}.get(interval, "1y")
    out = kline.fetch_kline(code, period=period, interval=interval)
    # 雲端資料中心 IP 常被 yfinance 限流回空 → 後備用 stock_ohlc（杯柄回補的官方
    # TWSE/TPEx OHLC，雲端抓得到）：日K直接組、週/月K聚合；1h 無官方日內源，維持回空。
    if not out.get("candles") and interval != "1h":
        rows = get_ohlc_history(conn(), code.split(".")[0])
        # **必須自己套 period**：這條路徑原本把整張表倒出來，於是「近一年日K」會把
        # 幾年前的零星列一起畫進去。stock_ohlc 的覆蓋度取決於 /api/ohlc/backfill 跑到
        # 哪，很容易出現「2017 一列 + 2026 幾列」這種稀疏分布；不截窗的話 X 軸就會
        # 橫跨數年、相鄰兩個刻度之間的實際間隔從 1 天到 6 年都有，圖形完全失去意義
        # （實測 2615 在雲端就是這樣）。截窗後若窗內沒有資料，寧可回空讓前端顯示
        # 「尚無資料」，也不要畫一張看起來像 K 線、實際上尺度錯亂的圖。
        cutoff = (datetime.now() - timedelta(days=_PERIOD_DAYS.get(period, 365))).strftime("%Y-%m-%d")
        rows = [r for r in rows if r["date"] >= cutoff]
        if rows:
            out = {"code": code, "source": "stock_ohlc", **kline.ohlc_candles(rows, interval)}
    return out

@router.get("/index/kline")
def index_kline(symbol: str = "taiex", interval: str = "1d"):
    if symbol == "tx":
        c = conn()
        hist = get_tx_history(c)
        if len(hist) < 20:
            try:
                rows = taifex.fetch_tx_history()
                if rows:
                    upsert_tx_history(c, rows)
                    hist = get_tx_history(c)
            except Exception:  # noqa: BLE001
                pass
        if len(hist) >= 20:
            out = kline.ohlc_candles(hist, interval)
            out["symbol"] = "tx"
            return out
        try:
            proxy = kline.fetch_index_kline("taiex", interval)
            proxy["symbol"] = "tx"
            proxy["proxy"] = True
            return proxy
        except Exception:  # noqa: BLE001
            return {"candles": [], "dates": [], "volumes": [], "symbol": "tx"}

    def _twse_rows():
        """官方指數 OHLC，逐日快取（一天最多一次網路呼叫）。"""
        c = conn()
        key = f"idxohlc:{datetime.now().strftime('%Y%m%d')}"
        rows = get_ai_cache(c, key)
        if rows is None:
            rows = twse.fetch_index_ohlc_history(12)
            if rows:
                set_ai_cache(c, key, rows)
        return rows or []

    try:
        out = kline.fetch_index_kline(symbol, interval)
        if len(out.get("candles") or []) > 5:
            # **主來源可用 ≠ 主來源是最新的。** yfinance 偶爾落後官方一天（實測
            # 2026-08-06 08:17，^TWII 只到 08-04，TWSE 已有 08-05），而原本這裡
            # 一旦拿到夠多根就直接 return、永不問官方——於是「大盤×籌碼對照」的
            # 籌碼窗格有最新一天、K 線卻沒有，最想看的那天反而是空的。
            # merge_tail 只補比主來源更新的日期，既有的一律不動。
            if symbol == "taiex":
                try:
                    out = kline.merge_tail(out, _twse_rows(), interval)
                    out["symbol"] = symbol
                except Exception:  # noqa: BLE001 — 補不到就用主來源原樣，不要因此整支失敗
                    pass
            return out
    except Exception:  # noqa: BLE001
        pass
    if symbol == "taiex":
        try:
            rows = _twse_rows()
            if rows:
                res = kline.ohlc_candles(rows, interval)
                res["symbol"] = "taiex"
                res["source"] = "twse"
                return res
        except Exception:  # noqa: BLE001
            pass
    return {"candles": [], "dates": [], "volumes": [], "symbol": symbol}

@router.get("/tx/volume-sessions")
def tx_volume_sessions(days: int = 60):
    """台指期日盤/夜盤量能每日比較。夜盤（15:00～次日05:00）成交依期交所規則計入次一營業日，
    故同一列 date 的 night_volume 是「前一晚的夜盤」——與當日日盤天然同列，供對照隔日開盤前情緒。"""
    c = conn()
    hist = get_tx_history(c)
    if len(hist) < 20:
        try:
            rows = taifex.fetch_tx_history()
            if rows:
                upsert_tx_history(c, rows)
                hist = get_tx_history(c)
        except Exception:  # noqa: BLE001
            pass
    rows = hist[-max(5, min(days, 400)):]
    ratio = [round(r["night_volume"] / r["volume"], 3)
             if r.get("night_volume") and r.get("volume") else None for r in rows]
    return {
        "dates": [r["date"] for r in rows],
        "day_volume": [r.get("volume") for r in rows],
        "night_volume": [r.get("night_volume") for r in rows],
        "ratio": ratio,
    }

@router.get("/stock/{code}/custody")
def stock_custody(code: str):
    c = conn()
    pure = code.split(".")[0]
    cur = get_ai_cache(c, "tdcc:current")
    stale = True
    if cur and cur.get("week_date"):
        try:
            stale = (datetime.now().date() - datetime.fromisoformat(cur["week_date"]).date()).days >= 7
        except Exception:  # noqa: BLE001
            stale = False
    if stale:
        try:
            fresh = tdcc.fetch_custody_distribution()
            if fresh.get("data"):
                set_ai_cache(c, "tdcc:current", fresh)
                cur = fresh
        except Exception:  # noqa: BLE001
            pass
    rec = (cur or {}).get("data", {}).get(pure)
    if rec and (cur or {}).get("week_date"):
        upsert_custody(c, cur["week_date"], pure, rec)
    trend = get_custody_trend(c, pure)
    filling = pure in _custody_autofill
    if not filling and _should_autofill(trend):
        # 補歷史的判斷本身（sqlite I/O ＋ 起執行緒）不能讓這支端點連帶掛掉——要回的
        # trend 早就算好了，端點的承諾是「立刻回現有資料」，補歷史只是順手做的加值。
        # 同上面 tdcc.fetch_custody_distribution() 那段一樣的理由。不可靜默吞掉：
        # 記下代號與例外，才看得出「補歷史一直沒有起來」是不是這裡在擋。
        try:
            filling = _start_custody_autofill(code)
        except Exception:  # noqa: BLE001
            _log.exception("custody autofill decision failed: %s", pure)
            filling = False
    return {"code": pure, "week": (cur or {}).get("week_date"), "current": rec,
            "trend": trend, "weeks": len(trend), "backfilling": filling}


@router.get("/stock/{code}/custody/backfill")
def stock_custody_backfill(code: str, weeks: int = 52):
    """回補該股集保大戶歷史週次（TDCC 智能網股權分散表，opendata 只給當週）。
    只補尚未存在的週次；重複呼叫直到 filled 為空。"""
    if not _custody_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        c = conn()
        pure = code.split(".")[0]
        weeks = max(4, min(weeks, 60))
        have = {t["week"] for t in get_custody_trend(c, pure)}
        avail = tdcc.fetch_custody_weeks()   # YYYYMMDD 新到舊
        want = [w for w in avail if f"{w[:4]}-{w[4:6]}-{w[6:8]}" not in have][:weeks]
        hist = tdcc.fetch_custody_history(pure, weeks=want, max_weeks=weeks) if want else {}
        for wk_iso, rec in hist.items():
            upsert_custody(c, wk_iso, pure, rec)
        return {"code": pure, "filled": sorted(hist.keys()),
                "stored": len(hist), "already": len(have)}
    finally:
        _custody_lock.release()

@router.get("/stock/{code}/chips")
def stock_chips(code: str, days: int = 10):
    c = conn()
    pure = code.split(".")[0]
    days = max(2, min(days, 60))
    rows = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()
    dlist = [r[0] for r in reversed(rows)]
    market = "twse"
    for ds in reversed(dlist):
        t = _insti_for(c, ds, "twse")
        if t:
            market = "twse" if pure in t else "tpex"
            break
    series = {"foreign": [], "trust": [], "dealer": [], "total": []}
    for ds in dlist:
        rec = _insti_for(c, ds, market).get(pure)
        for k in series:
            series[k].append(rec.get(k) if rec else None)
    return {"code": pure, "market": market, "dates": dlist, **series}

@router.get("/stock/{code}/profile")
def stock_profile(code: str):
    c = conn()
    dates = get_snapshot_dates(c)
    chip = None
    if dates:
        row = c.execute(
            "SELECT * FROM chip_snapshot WHERE snap_date=? AND code=?", (dates[-1], code)
        ).fetchone()
        chip = analysis.attach_mu(dict(row)) if row else None  # 補木質/木率（同選股表，後端算）
    return {"code": code, "snap_date": dates[-1] if dates else None,
            "chip": chip, "valuation": _valuation_for(c, code)}

@router.get("/watchlist")
def get_watchlist():
    return _get_watchlist(conn())

@router.post("/watchlist")
def add_watchlist(payload: dict = Body(...)):
    code = str(payload.get("code") or "").strip().upper()
    if code and "." not in code:
        code += ".TW"
    if code:
        add_watch(conn(), code, str(payload.get("name", "")).strip())
    return _get_watchlist(conn())

@router.delete("/watchlist/{code}")
def del_watchlist(code: str):
    remove_watch(conn(), code)
    return _get_watchlist(conn())

@router.put("/watchlist/{code}/estimate")
def put_watchlist_estimate(code: str, payload: dict = Body(...)):
    """存自選股「輸入預估」面板的原始輸入（最近三個月營收/毛利率/營業費用/所得稅/本益比低中高）。

    回傳整份自選股清單（跟其餘 watchlist 端點一致），前端已算好的 estimate/shares
    就在裡面，不必再開一個 GET 端點。
    """
    return _save_watch_estimate(conn(), code, payload)

@router.get("/patterns/cup-handle")
def cup_handle_screen(min_r: float = patterns.MIN_R_DEFAULT):
    return cup_handle_screen_logic(conn(), min_r=max(50.0, min(90.0, min_r)))

@router.get("/patterns/cup-handle/backtest")
def cup_backtest():
    c = conn()
    ods = ohlc_dates(c)
    need = patterns.LOOKBACK + 30
    if len(ods) < need:
        return {"note": f"目前歷史 {len(ods)} 天，回測至少需 {need} 天（訊號要能走出未來報酬）。"
                        f"請先回補更多：/api/ohlc/backfill?days=800（可重跑續補）",
                "bars": len(ods)}
    key = f"cupbt:{ods[-1]}:{len(ods)}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    data = get_all_ohlc(c, min_bars=patterns.LOOKBACK + 1)
    names = _ohlc_names(c)
    for code, s in data.items():
        s["name"] = names.get(code) or code
    result = backtest.backtest_cup(data)
    result.update({"date": ods[-1], "bars": len(ods)})
    set_ai_cache(c, key, result)
    return result
