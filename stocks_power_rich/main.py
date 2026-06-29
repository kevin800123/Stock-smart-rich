"""FastAPI 入口：提供 JSON API 與前端靜態頁。"""
import os
import tempfile

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from datetime import datetime

from . import analysis, csv_import, exporter, gemini, updater
from .config import load_config
from .sources import kline, taifex, tdcc, twse
from .db import (
    add_watch,
    get_ai_cache,
    get_connection,
    get_custody_trend,
    get_setting,
    get_snapshot,
    get_snapshot_dates,
    get_tx_history,
    init_db,
    list_watch,
    remove_watch,
    set_ai_cache,
    set_setting,
    upsert_custody,
    upsert_tx_history,
)

WEB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web"))


def data_is_stale(data_date, today: str, weekday: int) -> bool:
    """官方盤後資料是否「落後當日」。

    判定：資料日期早於今天，且今天是平日（週一~週五，weekday 0~4）。
    週末資料停在週五屬正常、不算延遲；平日落後代表官方 openapi 當日盤後尚未釋出。
    """
    return bool(data_date and data_date < today and weekday < 5)


def create_app(enable_scheduler: bool = False) -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    def conn():
        c = get_connection(cfg.db_path)
        init_db(c)
        return c

    def effective_data_dir():
        return get_setting(conn(), "data_dir") or cfg.data_dir

    def effective_schedule():
        return get_setting(conn(), "schedule_time") or cfg.schedule_time

    def scheduled_job():
        c = conn()
        path = csv_import.find_latest_file(effective_data_dir())
        if path:
            try:
                csv_import.import_csv(c, path)
            except Exception:  # noqa: BLE001 — 排程容錯
                pass
        updater.run_update(c, cfg.intl_tickers)

    if enable_scheduler:
        from .scheduler import start_scheduler

        app.state.scheduler = start_scheduler(scheduled_job, effective_schedule())

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

    @app.get("/api/sectors")
    def sectors(date: str | None = None):
        c = conn()
        if not date:
            last = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
            date = last[0] if last else None
        if not date:
            return {"date": None, "sectors": []}
        key = f"sectors:{date}"
        cached = get_ai_cache(c, key)
        if cached is not None:
            return cached
        try:
            secs = twse.fetch_sector_indices(datetime.fromisoformat(date).date())
        except Exception:  # noqa: BLE001
            secs = []
        secs.sort(key=lambda s: (s.get("chg_pct") is None, -(s.get("chg_pct") or 0)))  # 漲幅大→小
        result = {"date": date, "sectors": secs}
        if secs:
            set_ai_cache(c, key, result)
        return result

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

    @app.get("/api/watchlist")
    def get_watchlist():
        c = conn()
        wl = list_watch(c)
        dates = get_snapshot_dates(c)
        # 各快照日的選股榜（code→pick），用於進出榜與自進榜報酬
        picks_by_date = {d: {p["code"]: p for p in analysis.filtered_picks(get_snapshot(c, d))}
                         for d in dates}
        latest = dates[-1] if dates else None
        out = []
        for w in wl:
            code = w["code"]
            on = [d for d in dates if code in picks_by_date[d]]
            entry = on[0] if on else None
            ec = picks_by_date[entry][code].get("close") if entry else None
            lc = picks_by_date[latest][code].get("close") if (latest and code in picks_by_date.get(latest, {})) else None
            ret = round((lc - ec) / ec * 100, 2) if (ec and lc) else None
            nm = w["name"] or next((picks_by_date[d][code].get("name") for d in reversed(dates) if code in picks_by_date[d]), "")
            out.append({**w, "name": nm, "in_latest": bool(latest and code in picks_by_date.get(latest, {})),
                        "times": len(on), "entry_date": entry, "ret_pct": ret})
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
        last = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        key = f"optsent:{last[0] if last else 'na'}"
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
        if not date:
            last = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
            date = last[0] if last else None
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

    @app.post("/api/csv/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        tmp = os.path.join(tempfile.gettempdir(), file.filename or "upload.csv")
        with open(tmp, "wb") as f:
            f.write(data)
        c = conn()
        snap_date, count = csv_import.import_csv(c, tmp)
        c.execute("DELETE FROM ai_cache WHERE cache_key=?", (f"csv:{snap_date}",))
        c.commit()
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
        c.execute("DELETE FROM ai_cache WHERE cache_key=?", (f"csv:{snap_date}",))
        c.commit()
        picks = analysis.filtered_picks(get_snapshot(c, snap_date))
        return {"snap_date": snap_date, "count": count, "file": os.path.basename(path),
                "picks": picks}

    @app.get("/api/snapshots")
    def snapshots():
        return {"dates": get_snapshot_dates(conn())}

    @app.get("/api/settings")
    def get_settings():
        c = conn()
        last = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        return {
            "gemini_configured": bool(cfg.gemini_api_key),  # 僅狀態，絕不回傳金鑰值
            "schedule_time": effective_schedule(),
            "scheduler_running": bool(getattr(app.state, "scheduler", None)),
            "data_dir": effective_data_dir(),
            "snapshots": len(get_snapshot_dates(c)),
            "tx_history_days": len(get_tx_history(c)),
            "last_market_date": last[0] if last else None,
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
        if payload.get("data_dir"):
            set_setting(c, "data_dir", str(payload["data_dir"]))
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
            "SELECT * FROM market_daily ORDER BY date DESC LIMIT 10").fetchall()]
        if not rows:
            return gemini.summarize_market({}, cfg.gemini_api_key)
        m = rows[0]
        key = f"market:{m.get('date')}:{m.get('updated_at')}"
        cached = get_ai_cache(c, key)
        if cached and not refresh:
            return cached
        hist = list(reversed(rows))  # 由舊到新
        keys = [("inst_foreign", "外資買賣超(億)"), ("inst_trust", "投信買賣超(億)"),
                ("inst_dealer", "自營買賣超(億)"), ("tx_foreign_oi", "外資台指淨未平倉(口)"),
                ("retail_ls_mtx", "小台散戶多空比"), ("margin_balance", "融資餘額(張)"),
                ("taiex", "加權指數")]
        trend = {"日期": [r.get("date") for r in hist]}
        trend.update({label: [r.get(k) for r in hist] for k, label in keys})
        secs = [s for s in _sectors_for(c, m["date"]) if s.get("chg_pct") is not None]
        secs.sort(key=lambda s: -s["chg_pct"])
        sectors = {"領漲": [[s["name"], s["chg_pct"]] for s in secs[:5]],
                   "領跌": [[s["name"], s["chg_pct"]] for s in secs[-5:][::-1]]}
        payload = {"latest": m, "trend": trend, "sectors": sectors}
        result = gemini.summarize_market(payload, cfg.gemini_api_key)
        if result.get("enabled"):
            set_ai_cache(c, key, result)
        return result

    @app.get("/api/stock/{code}/chips")
    def stock_chips(code: str, days: int = 10):
        c = conn()
        pure = code.split(".")[0]
        days = max(2, min(days, 20))
        rows = c.execute("SELECT date FROM market_daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()
        dlist = [r[0] for r in reversed(rows)]
        series = {"foreign": [], "trust": [], "dealer": [], "total": []}
        for ds in dlist:
            t = get_ai_cache(c, f"t86:{ds}")
            if t is None:
                t = twse.fetch_t86(datetime.fromisoformat(ds).date())
                if t:
                    set_ai_cache(c, f"t86:{ds}", t)
            rec = (t or {}).get(pure)
            for k in series:
                series[k].append(rec.get(k) if rec else None)
        return {"code": pure, "dates": dlist, **series}

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
            proxy = kline.fetch_index_kline("taiex", interval)
            proxy["symbol"] = "tx"
            proxy["proxy"] = True
            return proxy
        return kline.fetch_index_kline(symbol, interval)

    if os.path.isdir(WEB_DIR):
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER", "0") == "1")
