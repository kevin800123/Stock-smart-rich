"""FastAPI 入口：提供 JSON API 與前端靜態頁。"""
import os
import tempfile

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from datetime import datetime

from . import analysis, csv_import, exporter, gemini, updater
from .config import load_config
from .sources import kline, taifex, twse
from .db import (
    get_ai_cache,
    get_connection,
    get_setting,
    get_snapshot,
    get_snapshot_dates,
    get_tx_history,
    init_db,
    set_ai_cache,
    set_setting,
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
        row = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 1").fetchone()
        if not row:
            return gemini.summarize_market({}, cfg.gemini_api_key)
        m = dict(row)
        key = f"market:{m.get('date')}:{m.get('updated_at')}"
        cached = get_ai_cache(c, key)
        if cached and not refresh:
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
