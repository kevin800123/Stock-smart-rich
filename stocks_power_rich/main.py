"""FastAPI 入口：提供 JSON API 與前端靜態頁。"""
import os
import tempfile

from fastapi import FastAPI, File, UploadFile
from fastapi.staticfiles import StaticFiles

from datetime import datetime

from . import analysis, csv_import, gemini, updater
from .config import load_config
from .sources import twse
from .db import (
    get_ai_cache,
    get_connection,
    get_snapshot,
    get_snapshot_dates,
    init_db,
    set_ai_cache,
)
from .sources import kline

WEB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web"))


def create_app(enable_scheduler: bool = False) -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    def conn():
        c = get_connection(cfg.db_path)
        init_db(c)
        return c

    def scheduled_job():
        c = conn()
        path = csv_import.find_latest_file(cfg.data_dir)
        if path:
            try:
                csv_import.import_csv(c, path)
            except Exception:  # noqa: BLE001 — 排程容錯
                pass
        updater.run_update(c, cfg.intl_tickers)

    if enable_scheduler:
        from .scheduler import start_scheduler

        app.state.scheduler = start_scheduler(scheduled_job, cfg.schedule_time)

    @app.get("/api/dashboard")
    def dashboard():
        c = conn()
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM market_daily ORDER BY date DESC LIMIT 60"
        ).fetchall()]
        latest = rows[0] if rows else {}
        return {"latest": latest, "history": list(reversed(rows))}

    @app.post("/api/update/run")
    def run_update():
        return updater.run_update(conn(), cfg.intl_tickers)

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
        rows = get_snapshot(c, snap_date)
        return {"snap_date": snap_date, "count": count, "daily_top": analysis.daily_signals(rows, 30)}

    @app.post("/api/csv/import-latest")
    def import_latest():
        path = csv_import.find_latest_file(cfg.data_dir)
        if not path:
            return {"snap_date": None, "count": 0, "daily_top": [],
                    "error": f"資料夾找不到 CSV/Excel：{cfg.data_dir}"}
        c = conn()
        snap_date, count = csv_import.import_csv(c, path)
        c.execute("DELETE FROM ai_cache WHERE cache_key=?", (f"csv:{snap_date}",))
        c.commit()
        rows = get_snapshot(c, snap_date)
        return {"snap_date": snap_date, "count": count, "file": os.path.basename(path),
                "daily_top": analysis.daily_signals(rows, 30)}

    @app.get("/api/analysis/daily")
    def daily():
        c = conn()
        dates = get_snapshot_dates(c)
        if not dates:
            return {"snap_date": None, "daily_top": []}
        rows = get_snapshot(c, dates[-1])
        return {"snap_date": dates[-1], "daily_top": analysis.daily_signals(rows, 30)}

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
    def summary():
        c = conn()
        dates = get_snapshot_dates(c)
        if not dates:
            return gemini.summarize_csv([], {}, [], cfg.gemini_api_key)
        key = f"csv:{dates[-1]}"
        cached = get_ai_cache(c, key)
        if cached:
            return cached
        rows = get_snapshot(c, dates[-1])
        result = gemini.summarize_csv(
            analysis.daily_signals(rows, 30), {}, analysis.industry_aggregate(rows), cfg.gemini_api_key
        )
        if result.get("enabled"):
            set_ai_cache(c, key, result)
        return result

    @app.get("/api/market/summary")
    def market_summary():
        c = conn()
        row = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        if not row:
            return gemini.summarize_market({}, cfg.gemini_api_key)
        m = dict(row)
        key = f"market:{m.get('date')}:{m.get('updated_at')}"
        cached = get_ai_cache(c, key)
        if cached:
            return cached
        result = gemini.summarize_market(m, cfg.gemini_api_key)
        if result.get("enabled"):
            set_ai_cache(c, key, result)
        return result

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
            rows = [dict(r) for r in c.execute(
                "SELECT date, tx_open, tx_high, tx_low, tx_price FROM market_daily ORDER BY date"
            ).fetchall()]
            return kline.tx_candles_from_rows(rows, interval)
        return kline.fetch_index_kline(symbol, interval)

    if os.path.isdir(WEB_DIR):
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER", "0") == "1")
