import os
import base64
import binascii
import secrets
from datetime import datetime

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .db import (
    get_connection,
    init_db,
    get_setting,
    get_snapshot_dates,
    get_snapshot,
    backup_db,
)
from . import csv_import, line_push, updater
from .api.deps import conn
from .api.helpers import (
    _check_basic,
    _dir_within,
    effective_data_dir,
    effective_schedule,
    _clear_csv_cache,
    _push_line,
    _check_update_result_and_alert,
    _os_futures,
    _intraday_scan,
    data_is_stale,
    WEB_DIR,
)

# Import routers
from .api.market import router as market_router
from .api.stock import router as stock_router
from .api.trades import router as trades_router
from .api.csv import router as csv_router
from .api.public import router as public_router
from .api.admin import router as admin_router
from .api.line import router as line_router

# 免帳密的前端靜態資產（精確比對）：/public/overview 與站內共用同一套前端，需能載入這些檔。
# 僅限程式碼與樣式，不含 index.html（站內入口維持鎖住）。
_PUBLIC_FILES = {"/styles.css", "/app.js"}


def create_app(enable_scheduler: bool = False) -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    # 全站 HTTP Basic Auth 中介層：帳密兩者皆設定才啟用
    if cfg.basic_user and cfg.basic_pass:
        @app.middleware("http")
        async def _basic_auth(request, call_next):
            # 免帳密白名單：
            #   /public/*      —— 公開頁與其唯讀 API（LINE 圖文選單開啟，無帳密）
            #   前端靜態資產   —— /public/overview 直接沿用站內同一套前端，故需放行；
            #                     內含的只是程式碼與字型，無任何機密（金鑰皆在伺服器端），
            #                     且所有 /api/* 仍受保護，資料不會外洩。
            # 注意：/ 與 /index.html 維持鎖住；前綴一律帶結尾斜線，避免 /publicx、/vendorx 誤放行。
            #   /line/webhook  —— LINE 伺服器無法帶 Basic Auth；改以 channel secret 簽章把關
            path = request.url.path
            if (path.startswith("/public/")
                    or path == "/line/webhook"
                    or path in _PUBLIC_FILES
                    or path.startswith("/vendor/")):
                return await call_next(request)
            if _check_basic(request.headers.get("Authorization", ""), cfg.basic_user, cfg.basic_pass):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="SPR"'})

    # 安全性回應標頭
    @app.middleware("http")
    async def _security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        return resp

    # 註冊模組化路由
    app.include_router(market_router)
    app.include_router(stock_router)
    app.include_router(trades_router)
    app.include_router(csv_router)
    app.include_router(public_router)
    app.include_router(admin_router)
    app.include_router(line_router)

    # 註冊排程 job
    def scheduled_job():
        c = conn()
        path = csv_import.find_latest_file(effective_data_dir(c))
        if path:
            try:
                snap_date, _ = csv_import.import_csv(c, path)
                _clear_csv_cache(c, snap_date)
            except Exception:  # noqa: BLE001
                pass
        res = None
        try:
            res = updater.run_update(c, cfg.intl_tickers)
        except Exception:  # noqa: BLE001
            pass
        if res:
            try:
                _check_update_result_and_alert(c, res)
            except Exception:  # noqa: BLE001
                pass
        
        # 摘要生成
        from .api.market import market_summary_logic
        from .api.public import summary_logic
        for gen in (lambda: market_summary_logic(c, refresh=0), lambda: summary_logic(c, refresh=0)):
            try:
                gen()
            except Exception:  # noqa: BLE001
                pass
        try:
            _push_line(c, full=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            dest = backup_db(cfg.db_path)
            if dest:
                from .offsite_backup import push_offsite
                push_offsite(dest)
        except Exception:  # noqa: BLE001
            pass
        try:
            _os_futures(refresh=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            from .ledger import record_daily_signals, update_ledger_returns
            record_daily_signals(c)
            update_ledger_returns(c)
        except Exception:  # noqa: BLE001
            pass

    def line_brief_job():
        c = conn()
        res = None
        try:
            res = updater.run_update(c, cfg.intl_tickers)
        except Exception:  # noqa: BLE001
            pass
        if res:
            try:
                _check_update_result_and_alert(c, res)
            except Exception:  # noqa: BLE001
                pass
        try:
            _push_line(c, full=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            _os_futures(refresh=True)
        except Exception:  # noqa: BLE001
            pass

    def intraday_watch_job():
        now = datetime.now()
        if now.hour == 13 and now.minute > 35:
            return
        try:
            _intraday_scan(conn(), push=True)
        except Exception:  # noqa: BLE001
            pass

    def weekly_line_job():
        """週六 17:00 籌碼週報：跨週變化（週對週）＋ AI 籌碼分析師 → LINE 廣播。"""
        try:
            from datetime import date, timedelta
            from .api.helpers import _weekly_messages
            from .db import get_snapshot_dates
            c = conn()
            dates = get_snapshot_dates(c)
            # staleness guard：最新快照距今 >7 天代表本週沒匯 CSV，別重複推舊內容
            if not dates or (date.today() - date.fromisoformat(dates[-1])) > timedelta(days=7):
                return
            line_push.broadcast_messages(cfg.line_token, _weekly_messages(c))
        except Exception:  # noqa: BLE001 — 推播失敗不影響其他排程
            pass

    if enable_scheduler:
        from .scheduler import build_trigger_kwargs, start_scheduler

        app.state.scheduler = start_scheduler(scheduled_job, effective_schedule(conn()))
        if cfg.line_token:
            app.state.scheduler.add_job(
                line_brief_job, "cron", **build_trigger_kwargs(cfg.line_push_time),
                day_of_week="mon-fri", id="line_brief", replace_existing=True)
            app.state.scheduler.add_job(
                intraday_watch_job, "cron", day_of_week="mon-fri",
                hour="9-13", minute="*/5", id="intraday_watch", replace_existing=True)
            app.state.scheduler.add_job(
                weekly_line_job, "cron", **build_trigger_kwargs(cfg.weekly_push_time),
                day_of_week="sat", id="weekly_line", replace_existing=True)

    if os.path.isdir(WEB_DIR):
        app.mount("/", _NoCacheStatic(directory=WEB_DIR, html=True), name="web")
    return app


class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER", "0") == "1")
