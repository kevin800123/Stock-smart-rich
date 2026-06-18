"""FastAPI 入口：提供 JSON API 與前端靜態頁。"""
import os
import tempfile

from fastapi import FastAPI, File, UploadFile
from fastapi.staticfiles import StaticFiles

from . import analysis, csv_import, gemini, updater
from .config import load_config
from .db import get_connection, get_snapshot, get_snapshot_dates, init_db
from .sources import kline

WEB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "web"))


def create_app() -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    def conn():
        c = get_connection(cfg.db_path)
        init_db(c)
        return c

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
        rows = get_snapshot(c, snap_date)
        return {"snap_date": snap_date, "count": count, "daily_top": analysis.daily_signals(rows, 30)}

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
        rows = get_snapshot(c, dates[-1]) if dates else []
        top = analysis.daily_signals(rows, 30)
        ind = analysis.industry_aggregate(rows)
        return gemini.summarize_csv(top, {}, ind, cfg.gemini_api_key)

    @app.get("/api/market/summary")
    def market_summary():
        c = conn()
        row = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        return gemini.summarize_market(dict(row) if row else {}, cfg.gemini_api_key)

    @app.get("/api/stock/{code}/kline")
    def stock_kline(code: str, interval: str = "1d", period: str | None = None):
        if period is None:
            period = {"1d": "1y", "1wk": "2y", "1mo": "5y"}.get(interval, "1y")
        return kline.fetch_kline(code, period=period, interval=interval)

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


app = create_app()
