"""FastAPI 入口：提供 JSON API 與前端靜態頁。"""
import base64
import binascii
import os
import secrets
import tempfile

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from datetime import datetime

from . import analysis, csv_import, exporter, gemini, line_push, patterns, updater
from .config import load_config
from .sources import intl, kline, taifex, tdcc, tpex, twse
from .db import (
    add_watch,
    backup_db,
    get_ai_cache,
    get_all_ohlc,
    get_connection,
    get_custody_trend,
    get_ohlc_history,
    get_setting,
    get_snapshot,
    get_snapshot_dates,
    get_tx_history,
    init_db,
    list_watch,
    ohlc_dates,
    remove_watch,
    set_ai_cache,
    set_setting,
    upsert_custody,
    upsert_tx_history,
)

WEB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web"))
REPO_DIR = os.path.dirname(WEB_DIR)  # 專案根目錄（data_dir 白名單的預設允許根）

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # CSV 上傳大小上限 10MB（防記憶體/磁碟耗盡）
UPLOAD_EXTS = (".csv", ".xlsx", ".xlsm")


def _check_basic(auth_header: str, user: str, pw: str) -> bool:
    """驗證 HTTP Basic Auth 標頭；以 compare_digest 做等時間比較避免時序側錄。"""
    if not auth_header.startswith("Basic "):
        return False
    try:
        u, _, p = base64.b64decode(auth_header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    # 兩者都比對（避免因先短路而洩漏帳號是否正確）
    return secrets.compare_digest(u, user) & secrets.compare_digest(p, pw)


def _dir_within(candidate: str, roots: list[str]) -> bool:
    """candidate 解析後（跟隨符號連結）是否落在任一允許根目錄內；防目錄跳脫。"""
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
    """官方盤後資料是否「落後當日」。

    判定：資料日期早於今天，且今天是平日（週一~週五，weekday 0~4）。
    週末資料停在週五屬正常、不算延遲；平日落後代表官方 openapi 當日盤後尚未釋出。
    """
    return bool(data_date and data_date < today and weekday < 5)


def create_app(enable_scheduler: bool = False) -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    # 全站 HTTP Basic Auth：帳密兩者皆設定才啟用（本機開發未設即不啟用、無感）。
    # 覆蓋所有請求含靜態頁；出站的 LINE 推播與進程內排程不經 HTTP，不受影響。
    if cfg.basic_user and cfg.basic_pass:
        @app.middleware("http")
        async def _basic_auth(request, call_next):
            if _check_basic(request.headers.get("Authorization", ""), cfg.basic_user, cfg.basic_pass):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="SPR"'})

    # 安全性回應標頭（後註冊＝最外層，套用於所有回應含 401）。CSP 只允許自站與 ECharts CDN，
    # 無 unsafe-eval（app.js/ECharts 皆不用 eval）；inline style 屬性大量使用故 style 放行 inline。
    @app.middleware("http")
    async def _security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return resp

    initialized: set[str] = set()

    def conn():
        c = get_connection(cfg.db_path)
        if cfg.db_path not in initialized:  # schema 只需建一次，之後每請求免跑 DDL/PRAGMA
            init_db(c)
            initialized.add(cfg.db_path)
        return c

    def _latest_date(c) -> str | None:
        """最新一筆大盤資料日（多數端點的預設查詢日）。"""
        row = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def effective_data_dir():
        return get_setting(conn(), "data_dir") or cfg.data_dir

    def effective_schedule():
        return get_setting(conn(), "schedule_time") or cfg.schedule_time

    def scheduled_job():
        c = conn()
        path = csv_import.find_latest_file(effective_data_dir())
        if path:
            try:
                snap_date, _ = csv_import.import_csv(c, path)
                _clear_csv_cache(c, snap_date)  # 重匯同日檔時讓摘要/榜單重算
            except Exception:  # noqa: BLE001 — 排程容錯
                pass
        updater.run_update(c, cfg.intl_tickers)
        # 數據到齊後自動生成盤勢摘要與 CSV 籌碼分析；已生成（快取命中）就不重複扣費
        for gen in (lambda: market_summary(refresh=0), lambda: summary(refresh=0)):
            try:
                gen()
            except Exception:  # noqa: BLE001 — 摘要失敗不影響資料更新
                pass
        try:
            _push_line(c, full=True)  # 21:00 完整版（含融資券＋AI）；非當日資料自動略過
        except Exception:  # noqa: BLE001 — 推播失敗不影響資料更新
            pass
        try:
            backup_db(cfg.db_path)  # 每日備份輪替，防 Volume 故障/誤刪永久遺失
        except Exception:  # noqa: BLE001 — 備份失敗不影響資料更新
            pass
        try:
            _os_futures(refresh=True)  # 刷新海期監控快取
        except Exception:  # noqa: BLE001
            pass

    def line_brief_job():
        """16:00 盤後速報：先確保當日數據已抓，再推速報（無融資券）。"""
        c = conn()
        updater.run_update(c, cfg.intl_tickers)
        try:
            _push_line(c, full=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            _os_futures(refresh=True)  # 刷新海期監控快取
        except Exception:  # noqa: BLE001
            pass

    if enable_scheduler:
        from .scheduler import build_trigger_kwargs, start_scheduler

        app.state.scheduler = start_scheduler(scheduled_job, effective_schedule())
        if cfg.line_token:  # 16:00 盤後速報（平日）；21:00 完整版掛在每日更新 job 尾端
            app.state.scheduler.add_job(
                line_brief_job, "cron", **build_trigger_kwargs(cfg.line_push_time),
                day_of_week="mon-fri", id="line_brief", replace_existing=True)

    @app.get("/api/dashboard")
    def dashboard():
        c = conn()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM market_daily ORDER BY date DESC LIMIT 60"
        ).fetchall()]
        latest = rows[0] if rows else {}
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        return {
            "latest": latest,
            "history": list(reversed(rows)),
            "today": today,
            "data_stale": data_is_stale(latest.get("date"), today, now.weekday()),
        }

    @app.post("/api/update/run")
    def run_update():
        return updater.run_update(conn(), cfg.intl_tickers)

    @app.get("/api/backfill")
    def backfill(days: int = 30):
        """回補近 N 日歷史（加權／三大法人現貨／融資券）。雲端冷啟動補歷史用；逐日入庫，可重跑續補。"""
        n = updater.backfill_history(conn(), max(5, min(days, 60)))
        return {"backfilled_days": n}

    def _os_futures(refresh: bool = False) -> dict:
        """海期監控：批次抓國際期貨/美股（延遲），單一鍵快取；注入本地可靠的加權/台指期。"""
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

    @app.get("/api/os-futures")
    def os_futures(refresh: int = 0):
        return _os_futures(refresh=bool(refresh))

    @app.get("/api/breadth")
    def breadth(date: str | None = None):
        """台股漲跌家數（上市個股上漲/下跌/平盤與漲停/跌停家數），逐日快取。"""
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

    @app.get("/api/ohlc/backfill")
    def ohlc_backfill(days: int = 377, max_fetch: int = 60):
        """回補全市場個股每日 OHLC（型態選股用）。資料量大分次，回傳可重跑續補。"""
        return updater.backfill_ohlc(conn(), target=max(60, min(days, 400)),
                                     max_fetch=max(1, min(max_fetch, 120)))

    @app.get("/api/patterns/cup-handle")
    def cup_handle_screen():
        """全市場篩選『亞當／杯柄』型態；依收盤強度排序，逐日快取。"""
        c = conn()
        ods = ohlc_dates(c)
        if not ods:
            return {"date": None, "count": 0, "stocks": [],
                    "note": "尚未回補個股歷史，請先執行 /api/ohlc/backfill"}
        latest = ods[-1]
        key = f"cuphandle:{latest}:{len(ods)}"
        result = get_ai_cache(c, key)
        if result is None:
            data = get_all_ohlc(c, min_bars=patterns.LOOKBACK)
            imap = _industry_map(c)
            for code, s in data.items():
                s["name"] = (imap.get(code) or {}).get("name") or code
            matches = patterns.screen_cup_handle(data)
            result = {"date": latest, "bars": len(ods), "count": len(matches), "stocks": matches}
            set_ai_cache(c, key, result)
        # 疊加「籌碼/基本選股」標記（以最新 CSV 快照為準，即時計算不進快取，換檔即更新）
        picks = _picks_code_set(c)
        for m in result["stocks"]:
            m["in_picks"] = m["code"] in picks
        result["has_picks"] = bool(picks)
        result["picks_count"] = sum(1 for m in result["stocks"] if m["in_picks"])
        return result

    def _picks_code_set(c) -> set:
        """最新 CSV 快照的『籌碼/基本選股』代號集合（去 .TW，供杯柄交集）。"""
        dates = get_snapshot_dates(c)
        if not dates:
            return set()
        return {p["code"].split(".")[0] for p in analysis.filtered_picks(get_snapshot(c, dates[-1]))}

    @app.get("/api/stock/{code}/ohlc")
    def stock_ohlc(code: str, bars: int = 400):
        """個股已存 OHLC（型態畫線用），candles=[open,close,low,high]，附日期。"""
        pure = code.split(".")[0]
        rows = get_ohlc_history(conn(), pure)[-max(60, min(bars, 500)):]
        return {"code": pure, "dates": [r["date"] for r in rows],
                "candles": [[r["open"], r["close"], r["low"], r["high"]] for r in rows]}

    @app.get("/api/index-movers")
    def index_movers(date: str | None = None, top: int = 20):
        """權值股對加權指數的『點數貢獻』(盤後歸因：市值加權、正規化到實際指數漲跌)。"""
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
        scale = (taiex_chg / raw_total) if raw_total else 1.0  # 正規化：各股貢獻合計＝實際指數漲跌
        for i in items:
            i["contribution"] = round(i["_d"] / total_prev * prev_index * scale, 2)
            i["weight"] = round(i["_p"] / total_prev * 100, 2)
            del i["_d"], i["_p"]
        items.sort(key=lambda i: -abs(i["contribution"]))
        result = {"date": date, "index": taiex, "index_chg": taiex_chg, "movers": items[:top]}
        set_ai_cache(c, key, result)
        return result

    @app.get("/api/sectors")
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
            secs.sort(key=lambda s: (s.get("chg_pct") is None, -(s.get("chg_pct") or 0)))  # 漲幅大→小
            result = {"date": date, "sectors": secs}
            if secs:
                set_ai_cache(c, key, result)
        _attach_size(c, date, result.get("sectors") or [])
        return result

    def _quotes_for(c, date: str) -> dict:
        """某日全上市個股報價 {代號: {name, close, chg_pct}}（逐日快取）。"""
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

    # 彙總型/跨市場指數不給面積，避免母子類股重複計面積（市值依產業代碼彙總，天然只含葉節點）。
    _TURNOVER_EXCLUDE = {"化學生技醫療", "電子工業", "水泥窯製", "塑膠化工", "機電"}

    def _attach_size(c, date: str, secs: list) -> None:
        """附掛熱力圖面積數據：mcap＝成分股市值加總(億)，turnover＝成交值(元, 備援)。"""
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
            mmap = {k: round(v / 1e8, 1) for k, v in acc.items()}  # 億元
            if mmap:
                set_ai_cache(c, mkey, mmap)
        for s in secs:
            name = s.get("name")
            s["mcap"] = mmap.get(name)
            s["turnover"] = None if name in _TURNOVER_EXCLUDE else tmap.get(twse.norm_sector_name(name))

    def _sectors_for(c, ds: str) -> list:
        """取某日類股漲跌（先讀快取，否則直連並快取）。"""
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

    def _industry_map(c) -> dict:
        """上市個股基本資料 {代號: {sector, name, shares}}（近乎靜態，按月快取，新上市會更新）。"""
        key = f"listed_ind2:{datetime.now().strftime('%Y-%m')}"
        m = get_ai_cache(c, key)
        if not m:
            m = twse.fetch_listed_industry()
            if m:
                set_ai_cache(c, key, m)
        return m or {}

    def _otc_names(c) -> dict:
        """上櫃公司 {代號: 簡稱}（按月快取）；自選股補上櫃股名用。"""
        key = f"otc_names:{datetime.now().strftime('%Y-%m')}"
        m = get_ai_cache(c, key)
        if not m:
            m = tpex.fetch_otc_names()
            if m:
                set_ai_cache(c, key, m)
        return m or {}

    @app.get("/api/sectors/{sector}/stocks")
    def sector_stocks(sector: str, date: str | None = None):
        """點選熱力圖類股 → 回傳該類股成分股當日漲跌，依市值（發行股數×收盤）由大到小。"""
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
            mcap = round(shares * close / 1e8, 1) if (shares and close) else None  # 億元
            stocks.append({"code": code, "name": q.get("name"),
                           "chg_pct": q.get("chg_pct"), "close": close, "mcap": mcap})
        stocks.sort(key=lambda s: (s["mcap"] is None, -(s["mcap"] or 0)))  # 市值大→小
        return {"sector": sector, "date": date, "count": len(stocks), "stocks": stocks}

    @app.get("/api/sectors/rotation")
    def sectors_rotation(days: int = 5):
        c = conn()
        days = max(2, min(days, 15))
        rows = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()
        dlist = [r[0] for r in reversed(rows)]
        if not dlist:
            return {"dates": [], "sectors": []}
        ckey = f"rotation:{dlist[-1]}:{days}"
        cached = get_ai_cache(c, ckey)
        if cached is not None:
            return cached
        per = {ds: {s["name"]: s["chg_pct"] for s in _sectors_for(c, ds)} for ds in dlist}
        names = sorted({n for d in per.values() for n in d})
        sectors = [{
            "name": n,
            "series": [per[ds].get(n) for ds in dlist],
            "sum": round(sum(per[ds].get(n) or 0 for ds in dlist), 2),
        } for n in names]
        sectors.sort(key=lambda s: -s["sum"])  # 近期累計強→弱
        result = {"dates": dlist, "sectors": sectors}
        if names:
            set_ai_cache(c, ckey, result)
        return result

    def _picks_index(c, ds: str) -> dict:
        """某快照日選股榜的精簡索引 {code: {name, close}}。

        快照一經匯入即不變，故可長期快取；重匯入該日 CSV 時由 _clear_csv_cache 失效。
        避免 watchlist 每次請求都對全部快照日重跑 filtered_picks（隨快照累積越來越慢）。
        """
        key = f"watchpicks:{ds}"
        cached = get_ai_cache(c, key)
        if cached is not None:
            return cached
        idx = {p["code"]: {"name": p.get("name"), "close": p.get("close")}
               for p in analysis.filtered_picks(get_snapshot(c, ds))}
        set_ai_cache(c, key, idx)
        return idx

    @app.get("/api/watchlist")
    def get_watchlist():
        c = conn()
        wl = list_watch(c)
        dates = get_snapshot_dates(c)
        # 各快照日的選股榜（code→{name, close}），用於進出榜與自進榜報酬
        picks_by_date = {d: _picks_index(c, d) for d in dates}
        latest = dates[-1] if dates else None
        out = []
        imap = omap = None  # 補股名用（上市/上櫃基本資料，月快取）；快照都查不到名字才載入
        for w in wl:
            code = w["code"]
            on = [d for d in dates if code in picks_by_date[d]]
            entry = on[0] if on else None
            ec = picks_by_date[entry][code].get("close") if entry else None
            lc = picks_by_date[latest][code].get("close") if (latest and code in picks_by_date.get(latest, {})) else None
            ret = round((lc - ec) / ec * 100, 2) if (ec and lc) else None
            # 最新一筆快照的籌碼欄位（不限是否入選股榜，CSV 有此股即有資料）
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
                if not nm:  # 上市查不到 → 試上櫃
                    if omap is None:
                        omap = _otc_names(c)
                    nm = omap.get(pure) or ""
            out.append({**w, "name": nm, "in_latest": bool(latest and code in picks_by_date.get(latest, {})),
                        "times": len(on), "entry_date": entry, "ret_pct": ret, "chip": chip})
        return {"stocks": out, "latest": latest}

    @app.post("/api/watchlist")
    def add_watchlist(payload: dict = Body(...)):
        code = str(payload.get("code", "")).strip().upper()
        if code and "." not in code:
            code += ".TW"
        if code:
            add_watch(conn(), code, str(payload.get("name", "")).strip())
        return get_watchlist()

    @app.delete("/api/watchlist/{code}")
    def del_watchlist(code: str):
        remove_watch(conn(), code)
        return get_watchlist()

    @app.get("/api/options-sentiment")
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

    @app.get("/api/inst-ranking")
    def inst_ranking(who: str = "foreign", date: str | None = None, top: int = 20, unit: str = "shares"):
        c = conn()
        if who not in ("foreign", "trust", "dealer", "total"):
            who = "foreign"
        if unit not in ("shares", "value"):
            unit = "shares"
        date = date or _latest_date(c)
        if not date:
            return {"date": None, "who": who, "unit": unit, "buy": [], "sell": []}
        t = get_ai_cache(c, f"t86:{date}")
        if t is None:
            t = twse.fetch_t86(datetime.fromisoformat(date).date())
            if t:
                set_ai_cache(c, f"t86:{date}", t)
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
                continue  # 只取上市個股，排除 ETF/受益證券
            lots = v.get(who)
            if lots is None:
                continue
            if unit == "value":
                close = (prices or {}).get(code)
                if close is None:
                    continue
                net = round(lots * close / 1e5, 2)  # 張×元×1000 ÷1e8 = 億
            else:
                net = lots
            items.append({"code": code, "name": v.get("name") or code, "net": net})
        buy = sorted(items, key=lambda x: -x["net"])[:top]
        sell = sorted(items, key=lambda x: x["net"])[:top]
        return {"date": date, "who": who, "unit": unit, "buy": buy, "sell": sell}

    @app.get("/api/sectors/picks")
    def sectors_picks(date: str | None = None):
        c = conn()
        dates = get_snapshot_dates(c)
        snap = date if date in dates else (dates[-1] if dates else None)
        if not snap:
            return {"date": None, "groups": []}
        picks = analysis.filtered_picks(get_snapshot(c, snap))
        sector_chg = {s["name"]: s["chg_pct"] for s in _sectors_for(c, snap)}
        return {"date": snap, "groups": analysis.picks_by_sector(picks, sector_chg)}

    def _clear_csv_cache(c, snap_date: str) -> None:
        """重匯入某日 CSV 後，失效該日的衍生快取（AI 摘要、watchlist 選股榜索引）。"""
        c.execute("DELETE FROM ai_cache WHERE cache_key IN (?,?)",
                  (f"csv:{snap_date}", f"watchpicks:{snap_date}"))
        c.commit()

    @app.post("/api/csv/upload")
    async def upload(file: UploadFile = File(...)):
        fname = os.path.basename(file.filename or "upload.csv")  # 只取檔名，防 ../ 路徑跳脫
        if not fname.lower().endswith(UPLOAD_EXTS):
            return {"snap_date": None, "count": 0,
                    "error": f"僅接受 {'/'.join(UPLOAD_EXTS)} 檔案"}
        data = await file.read(MAX_UPLOAD_BYTES + 1)  # 有界讀取，避免超大檔耗盡記憶體
        if len(data) > MAX_UPLOAD_BYTES:
            return {"snap_date": None, "count": 0, "error": "檔案過大（上限 10MB）"}
        tmp = os.path.join(tempfile.gettempdir(), fname)
        with open(tmp, "wb") as f:
            f.write(data)
        c = conn()
        snap_date, count = csv_import.import_csv(c, tmp)
        _clear_csv_cache(c, snap_date)
        picks = analysis.filtered_picks(get_snapshot(c, snap_date))
        return {"snap_date": snap_date, "count": count, "picks": picks}

    @app.post("/api/csv/import-latest")
    def import_latest():
        data_dir = effective_data_dir()
        path = csv_import.find_latest_file(data_dir)
        if not path:
            return {"snap_date": None, "count": 0, "daily_top": [],
                    "error": f"資料夾找不到 CSV/Excel：{data_dir}"}
        c = conn()
        snap_date, count = csv_import.import_csv(c, path)
        _clear_csv_cache(c, snap_date)
        picks = analysis.filtered_picks(get_snapshot(c, snap_date))
        return {"snap_date": snap_date, "count": count, "file": os.path.basename(path),
                "picks": picks}

    @app.get("/api/csv/import-all")
    def import_all():
        """匯入資料夾內所有 CSV/Excel（雲端一次載入 repo 內全部歷史檔，跨週比較才有意義）。"""
        data_dir = effective_data_dir()
        c = conn()
        files = sorted(
            os.path.join(data_dir, f) for f in os.listdir(data_dir)
            if f.lower().endswith((".csv", ".xlsx", ".xlsm"))
        ) if os.path.isdir(data_dir) else []
        imported = []
        for path in files:
            try:
                snap_date, count = csv_import.import_csv(c, path)
                _clear_csv_cache(c, snap_date)
                imported.append({"file": os.path.basename(path), "snap_date": snap_date, "count": count})
            except Exception as e:  # noqa: BLE001 — 單檔失敗不影響其餘
                imported.append({"file": os.path.basename(path), "error": str(e)})
        return {"imported": imported, "dates": get_snapshot_dates(c)}

    @app.get("/api/snapshots")
    def snapshots():
        return {"dates": get_snapshot_dates(conn())}

    @app.post("/api/db/backup")
    def db_backup():
        """手動觸發一次資料庫備份（受全站認證保護）；回目前保留的備份清單。"""
        import glob as _glob
        try:
            dest = backup_db(cfg.db_path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        bdir = os.path.join(os.path.dirname(cfg.db_path) or ".", "backup")
        files = [os.path.basename(p) for p in sorted(_glob.glob(os.path.join(bdir, "spr-*.sqlite")))]
        return {"ok": bool(dest), "file": os.path.basename(dest) if dest else None, "backups": files}

    @app.get("/api/settings")
    def get_settings():
        c = conn()
        last_date = _latest_date(c)
        return {
            "gemini_configured": bool(cfg.gemini_api_key),  # 僅狀態，絕不回傳金鑰值
            "line_configured": bool(cfg.line_token),        # 僅狀態，絕不回傳 token
            "line_push_time": cfg.line_push_time,
            "schedule_time": effective_schedule(),
            "scheduler_running": bool(getattr(app.state, "scheduler", None)),
            "data_dir": effective_data_dir(),
            "snapshots": len(get_snapshot_dates(c)),
            "tx_history_days": len(get_tx_history(c)),
            "last_market_date": last_date,
            "nav_order": (get_setting(c, "nav_order") or "").split(",") if get_setting(c, "nav_order") else None,
        }

    @app.post("/api/settings")
    def update_settings(payload: dict = Body(...)):
        c = conn()
        st = payload.get("schedule_time")
        if st:
            set_setting(c, "schedule_time", str(st))
            sched = getattr(app.state, "scheduler", None)
            if sched:
                try:
                    from .scheduler import build_trigger_kwargs
                    sched.reschedule_job("daily_update", trigger="cron", **build_trigger_kwargs(st))
                except Exception:  # noqa: BLE001
                    pass
        dd = payload.get("data_dir")
        if dd:
            # 白名單：僅允許專案根或 env 指定的資料根之下，杜絕指向 /etc 等任意路徑做匯入
            if _dir_within(str(dd), [REPO_DIR, cfg.data_dir]):
                set_setting(c, "data_dir", str(dd))
            else:
                return {"ok": False, "error": "資料夾不在允許範圍（僅限專案目錄下）", **get_settings()}
        no = payload.get("nav_order")
        if isinstance(no, list) and no:
            # 分頁順序（view id）；只留簡單字母 slug，逗號分隔存字串
            ids = [str(x) for x in no if str(x).isalnum()]
            if ids:
                set_setting(c, "nav_order", ",".join(ids))
        return {"ok": True, **get_settings()}

    @app.get("/api/analysis/daily")
    def daily(date: str | None = None):
        c = conn()
        dates = get_snapshot_dates(c)
        if not dates:
            return {"snap_date": None, "picks": [], "subindustry": []}
        snap = date if date in dates else dates[-1]
        picks = analysis.filtered_picks(get_snapshot(c, snap))
        return {"snap_date": snap, "picks": picks,
                "subindustry": analysis.subindustry_counts(picks)}

    @app.get("/api/analysis/export")
    def export(date: str | None = None, sub: str | None = None):
        c = conn()
        dates = get_snapshot_dates(c)
        snap = date if date in dates else (dates[-1] if dates else None)
        picks = analysis.filtered_picks(get_snapshot(c, snap)) if snap else []
        if sub:
            picks = [p for p in picks if p.get("sub_industry") == sub]
        data = exporter.picks_to_xlsx(picks, snap or "")
        # 檔名僅用 ASCII（HTTP header 不可含非 latin-1 字元，故中文細產業不放檔名）
        fname = f"picks_{snap or 'empty'}.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.get("/api/analysis/weekly")
    def weekly():
        c = conn()
        dates = get_snapshot_dates(c)
        if len(dates) < 2:
            return {"stocks": [], "industry": [], "note": "需至少兩週快照才能比較"}
        this_rows = get_snapshot(c, dates[-1])
        last_rows = get_snapshot(c, dates[-2])
        result = analysis.weekly_comparison(this_rows, last_rows)
        result["industry"] = analysis.industry_aggregate(this_rows)
        result["this_date"] = dates[-1]
        result["last_date"] = dates[-2]
        return result

    @app.get("/api/analysis/summary")
    def summary(refresh: int = 0):
        c = conn()
        dates = get_snapshot_dates(c)
        if not dates:
            return gemini.summarize_csv([], {}, [], cfg.gemini_api_key)
        key = f"csv:{dates[-1]}"
        cached = get_ai_cache(c, key)
        if cached and not refresh:
            return cached
        picks = analysis.filtered_picks(get_snapshot(c, dates[-1]))
        result = gemini.summarize_csv(
            picks, {}, analysis.subindustry_counts(picks), cfg.gemini_api_key
        )
        if result.get("enabled"):
            set_ai_cache(c, key, result)
        return result

    @app.get("/api/market/summary")
    def market_summary(refresh: int = 0):
        c = conn()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM market_daily ORDER BY date DESC LIMIT 6").fetchall()]
        if not rows:
            return gemini.summarize_market({}, cfg.gemini_api_key)
        m = rows[0]
        # 快取鍵＝日期＋四項核心數據(大盤/法人/外資OI/散戶多空比)到位簽章：
        # 同日數據沒變就直接回快取，不因 updated_at 變動而重新生成（省 token）；
        # 數據補齊（簽章改變）才自動重生一次。
        sig = "".join("1" if m.get(k) is not None else "0"
                      for k in ("taiex", "inst_foreign", "tx_foreign_oi", "retail_ls_mtx"))
        key = f"market:{m.get('date')}:{sig}"
        cached = get_ai_cache(c, key)
        if cached and not refresh:
            return cached
        hist = list(reversed(rows))  # 由舊到新
        pv = rows[1] if len(rows) > 1 else {}

        def _streak(k: str) -> int:
            """由最新往回數的連續同向天數：+N=連 N 買、-N=連 N 賣（0=最新日無值或為 0）。"""
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

        # 欄位名帶單位、衍生指標先算好（連買賣天數/OI增減/多空比%/量比），AI 只解讀不換算
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
            "VIX": m.get("vix"), "VIX漲跌(%)": m.get("vix_chg"),
            "費半漲跌(%)": m.get("sox_chg"), "日經漲跌(%)": m.get("n225_chg"),
            "韓股漲跌(%)": m.get("kospi_chg"), "黃金漲跌(%)": m.get("gold_chg"),
            "美元兌日圓": m.get("jpy"), "美元兌日圓漲跌(%)": m.get("jpy_chg"),
            "比特幣漲跌(%)": m.get("btc_chg"),
        }
        latest = {k: v for k, v in latest.items() if v is not None}  # 缺值不餵，省 token
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
        result = gemini.summarize_market(payload, cfg.gemini_api_key)
        if result.get("enabled"):
            set_ai_cache(c, key, result)
        return result

    def _push_line(c, full: bool, force: bool = False) -> dict:
        """組當日盤後訊息並 broadcast 到 LINE 官方帳號好友（單人自用＝自己）。

        非當日資料（假日/尚未更新）自動略過不推，避免重複推前一交易日；force=True 供手動測試。
        """
        if not cfg.line_token:
            return {"ok": False, "error": "未設定 LINE_CHANNEL_ACCESS_TOKEN"}
        rows = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 2").fetchall()
        if not rows:
            return {"ok": False, "error": "尚無大盤資料"}
        m = dict(rows[0])
        prev_row = dict(rows[1]) if len(rows) > 1 else {}
        if not force and m["date"] != datetime.now().strftime("%Y-%m-%d"):
            return {"ok": False, "skipped": True, "error": f"資料日 {m['date']} 非今日，略過"}
        secs = [s for s in _sectors_for(c, m["date"]) if s.get("chg_pct") is not None]
        # 自選股：附股價/當日漲跌與是否在榜（上市用 TWSE 報價；上櫃補櫃買 dailyQuotes，
        # 都查無時以最新快照收盤替代、漲跌留空）
        watch, tsmc = [], None
        try:
            quotes = _quotes_for(c, m["date"])
            tsmc = quotes.get("2330")
            stocks = get_watchlist().get("stocks", [])
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
                watch.append({"code": s["code"], "name": s.get("name"),
                              "close": q.get("close") or chip.get("close"),
                              "chg_pct": q.get("chg_pct"), "in_latest": s.get("in_latest")})
        except Exception:  # noqa: BLE001 — 自選股失敗不影響推播主體
            pass
        ai = market_summary(refresh=0)
        ai_text = (ai.get("text") or "") if ai.get("enabled") else ""
        txt = line_push.compose_daily_brief(m, secs, watch, ai_text=ai_text, full=full,
                                            tsmc=tsmc, prev=prev_row)
        return line_push.broadcast_text(cfg.line_token, txt)

    @app.post("/api/line/test")
    def line_test():
        """手動觸發一則完整版推播（不限當日資料），驗證 LINE 設定。"""
        return _push_line(conn(), full=True, force=True)

    def _insti_for(c, ds: str, market: str) -> dict:
        """某日全市場個股三大法人（market='twse' 用 T86、'tpex' 用櫃買），依日期快取。"""
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

    @app.get("/api/stock/{code}/chips")
    def stock_chips(code: str, days: int = 10):
        c = conn()
        pure = code.split(".")[0]
        days = max(2, min(days, 20))
        rows = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()
        dlist = [r[0] for r in reversed(rows)]
        # 判斷上市/上櫃：以「最近一個 T86 有資料的交易日」為準（最新日盤後未公布時
        # 會回空表，若只看最新日會把所有股票誤判成上櫃、整條序列變空）
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

    @app.get("/api/stock/{code}/custody")
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
            upsert_custody(c, cur["week_date"], pure, rec)  # 逐週累積
        return {"code": pure, "week": (cur or {}).get("week_date"),
                "current": rec, "trend": get_custody_trend(c, pure)}

    @app.get("/api/stock/{code}/kline")
    def stock_kline(code: str, interval: str = "1d", period: str | None = None):
        if period is None:
            period = {"1d": "1y", "1wk": "2y", "1mo": "5y"}.get(interval, "1y")
        return kline.fetch_kline(code, period=period, interval=interval)

    def _valuation_for(c, code: str):
        key = f"valuation:{datetime.now().strftime('%Y-%m-%d')}"
        cached = get_ai_cache(c, key)
        if cached is None:
            try:
                cached = {v["code"]: v for v in twse.fetch_valuation()}
            except Exception:  # noqa: BLE001 — 抓不到就空
                cached = {}
            if cached:  # 失敗不快取，稍後重試（否則整天都拿到空值）
                set_ai_cache(c, key, cached)
        return cached.get(code)

    @app.get("/api/stock/{code}/profile")
    def stock_profile(code: str):
        c = conn()
        dates = get_snapshot_dates(c)
        chip = None
        if dates:
            row = c.execute(
                "SELECT * FROM chip_snapshot WHERE snap_date=? AND code=?", (dates[-1], code)
            ).fetchone()
            chip = dict(row) if row else None
        return {"code": code, "snap_date": dates[-1] if dates else None,
                "chip": chip, "valuation": _valuation_for(c, code)}

    @app.get("/api/index/kline")
    def index_kline(symbol: str = "taiex", interval: str = "1d"):
        if symbol == "tx":
            c = conn()
            hist = get_tx_history(c)
            if len(hist) < 20:  # 首次或不足 → 從期交所官方下載歷史並快取
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
            # 仍無法取得 → 以高度連動的加權指數近似
            try:
                proxy = kline.fetch_index_kline("taiex", interval)
                proxy["symbol"] = "tx"
                proxy["proxy"] = True
                return proxy
            except Exception:  # noqa: BLE001
                return {"candles": [], "dates": [], "volumes": [], "symbol": "tx"}
        # 加權指數等：先試 yfinance（本機可取整年）；雲端常被擋而回空/寥寥數筆 → 改用證交所多月 OHLC
        try:
            out = kline.fetch_index_kline(symbol, interval)
            if len(out.get("candles") or []) > 5:  # 有實質歷史才用；空/退化就往下補
                return out
        except Exception:  # noqa: BLE001
            pass
        if symbol == "taiex":
            try:
                c = conn()
                # 近 12 個月每日 OHLC，逐月直連較慢，故按日快取一份、各時間框架共用（週/月由此聚合）
                key = f"idxohlc:{datetime.now().strftime('%Y%m%d')}"
                rows = get_ai_cache(c, key)
                if rows is None:
                    rows = twse.fetch_index_ohlc_history(12)
                    if rows:
                        set_ai_cache(c, key, rows)
                if rows:
                    res = kline.ohlc_candles(rows, interval)
                    res["symbol"] = "taiex"
                    res["source"] = "twse"
                    return res
            except Exception:  # noqa: BLE001
                pass
        return {"candles": [], "dates": [], "volumes": [], "symbol": symbol}

    if os.path.isdir(WEB_DIR):
        app.mount("/", _NoCacheStatic(directory=WEB_DIR, html=True), name="web")
    return app


class _NoCacheStatic(StaticFiles):
    """前端靜態檔一律加 Cache-Control: no-cache，讓瀏覽器每次都向伺服器驗證（ETag 命中回 304），
    重新部署後能立即載到最新的 app.js/styles.css，避免使用者看到舊版而『點了沒反應』。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER", "0") == "1")
