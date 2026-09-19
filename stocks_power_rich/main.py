import os
import base64
import binascii
import logging
import secrets
import threading
from datetime import date

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
from . import csv_import, line_push, telegram_push, updater
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
    refresh_self_screen_cache as _refresh_self_screen_cache,
    line_quota_paused,
    _is_quota_exceeded,
    _note_line_quota_exceeded,
    data_is_stale,
    job_schedule,
    run_job,
    scheduled_run_key,
    WEB_DIR,
)
from .api import helpers as _helpers

# Import routers
from .api.market import router as market_router
from .api.stock import router as stock_router
from .api.trades import router as trades_router
from .api.csv import router as csv_router
from .api.public import router as public_router
from .api.admin import router as admin_router
from .api.line import router as line_router
from .api.news import router as news_router, news_logic
from .api.stock_flow import router as stock_flow_router

# 免帳密的前端靜態資產（精確比對）：/public/overview 與站內共用同一套前端，需能載入這些檔。
# 僅限程式碼與樣式，不含 index.html（站內入口維持鎖住）。
_PUBLIC_FILES = {"/styles.css", "/app.js"}

# logging 取代 print：Zeabur 收 stdout，每支排程 job 進出各一行（見 api/helpers.run_job）。
# basicConfig 在 root 已有 handler 時是 no-op，不會跟 uvicorn／pytest 的設定打架。
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("spr")


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
    app.include_router(news_router)
    app.include_router(stock_flow_router)

    # 註冊排程 job。每支 job 只做事、不自己吞例外：外層 run_job 負責寫 job_runs 與 log，
    # 例外進 failed、有步驟失敗回 partial——不再是 except: pass 的無聲失敗。
    def scheduled_job():
        """每日更新（預設 21:00）。各步驟獨立 try/except 是刻意的（一步失敗不拖垮其他步驟），
        但失敗要收進 failed_steps 回傳，run_job 據此標 partial，設定頁／health 才看得到。"""
        c = conn()
        failed_steps: list[str] = []
        summary: dict = {"failed_steps": failed_steps}

        def step(name, fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                log.exception("[daily_update] 步驟 %s 失敗", name)
                failed_steps.append(f"{name}: {type(e).__name__}: {e}")
                return None

        def _import_csv():
            path = csv_import.find_latest_file(effective_data_dir(c))
            if path:
                snap_date, _ = csv_import.import_csv(c, path)
                _clear_csv_cache(c, snap_date)
        step("csv_import", _import_csv)
        res = step("run_update", lambda: updater.run_update(c, cfg.intl_tickers))
        if res:
            summary["date"] = res.get("date")
            summary["failed_sources"] = len(res.get("failed") or [])
            step("alert", lambda: _check_update_result_and_alert(c, res))

        # 摘要生成
        from .api.market import market_summary_logic
        from .api.public import summary_logic
        step("market_summary", lambda: market_summary_logic(c, refresh=0))
        step("public_summary", lambda: summary_logic(c, refresh=0))
        step("push_line", lambda: _push_line(c, full=True))

        def _backup():
            dest = backup_db(cfg.db_path)
            if dest:
                from .offsite_backup import push_offsite
                push_offsite(dest)
        step("backup", _backup)

        def _ledger():
            from .ledger import record_daily_signals, update_ledger_returns
            record_daily_signals(c)
            update_ledger_returns(c)
        step("ledger", _ledger)
        # 自算選股：每日排程算一次、存快取，前瞻追蹤（signal_ledger）吃同一份。
        # 邏輯在 api/helpers（排程 Job 的既定分工），這裡只呼叫——寫在閉包裡就測不到。
        r = step("self_screen", lambda: _refresh_self_screen_cache(c))
        if isinstance(r, dict):
            summary["self_screen"] = r.get("skipped") or f"picked={r.get('picked')}"

        def _prune():
            from datetime import timedelta
            from .db import prune_job_runs
            prune_job_runs(c, (_helpers._now() - timedelta(days=60)).isoformat(timespec="seconds"))
        step("prune_job_runs", _prune)
        return summary

    def self_screen_early_job():
        """自算選股提早算（使用者要求最晚 20:00 更新好）。邏輯在 api/helpers.early_self_screen。
        失敗由 run_job 記成 failed；21:00 那次仍會照常算。"""
        from .api.helpers import early_self_screen
        return early_self_screen(conn())

    def osfut_job():
        """海期監控排程：一天固定兩次（07:30／21:30），取代舊的「每 2 分鐘輪詢」。

        那個輪詢頻率正是把 Zeabur 出站 IP 打到被 Yahoo 429 限流的主因（net-check 診斷
        實測 yfinance 與 chart API 備援皆遭拒）；改成排程後請求量大幅降低，且與 LINE
        是否設定無關——獨立掛兩個 cron，不搭在每日 21:00 完整推播工作上。
        """
        r = _os_futures(refresh=True)
        return {"has_remote": bool(r.get("has_remote")), "updated_at": r.get("updated_at")}

    def intraday_watch_job():
        now = _helpers._now()
        if now.hour == 13 and now.minute > 35:
            return {"skipped": "after_close"}
        r = _intraday_scan(conn(), push=True)
        return {"checked": r.get("checked"), "hits": len(r.get("hits") or [])}

    def weekly_line_job():
        """週六 17:00 籌碼週報：跨週變化（週對週）＋ AI 籌碼分析師 → LINE 廣播。"""
        from datetime import timedelta
        from .api.helpers import _weekly_messages
        from .db import get_snapshot_dates
        c = conn()
        if line_quota_paused(c):
            return {"skipped": "line_quota_paused"}
        dates = get_snapshot_dates(c)
        # staleness guard：最新快照距今 >7 天代表本週沒匯 CSV，別重複推舊內容
        if not dates or (_helpers._now().date() - date.fromisoformat(dates[-1])) > timedelta(days=7):
            return {"skipped": "snapshot_stale"}
        r = line_push.broadcast_messages(cfg.line_token, _weekly_messages(c))
        if not r.get("ok") and _is_quota_exceeded(r):
            _note_line_quota_exceeded(c)
        return {"ok": bool(r.get("ok"))}

    def news_job(slot: str):
        """每日財經新聞：四時段各自的必含主題不同，快取鍵也各自獨立（見 news_logic），
        所以每個時段是一個獨立的 job 而非同一支函式帶參數重複註冊。"""
        def _run():
            payload = news_logic(conn(), slot=slot, refresh=1)
            text = payload.get("telegram_text") or payload.get("summary")
            if not text:
                return {"sent": False, "reason": "empty"}
            r = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, text)
            return {"sent": bool(r.get("ok")), "parse_mode": r.get("parse_mode_used")}
        return _run

    def picks_new_job(kind: str):
        """自算選股新進榜推播（平日 21:40 daily／週六 18:00–21:30 weekly，等本週集保、最晚 21:30）。
        邏輯在 api/helpers。"""
        def _run():
            return _helpers.telegram_new_picks_job(conn(), cfg, kind)
        return _run

    def custody_watch_job():
        """週集保輪詢。邏輯在 api/helpers.custody_watch；失敗由 run_job 記成 failed。"""
        return _helpers.custody_watch(conn())

    # job id → 原始函式。補跑走這份（自己算 run_key），排程走包了 run_job 的版本。
    raw_jobs = {
        "daily_update": scheduled_job,
        "osfut_morning": osfut_job,
        "osfut_evening": osfut_job,
        "self_screen_early": self_screen_early_job,
        "ssf_daily": lambda: _helpers.refresh_ssf_daily(conn()),
        "intraday_watch": intraday_watch_job,
        "weekly_line": weekly_line_job,
        **{f"news_{slot}": news_job(slot) for slot in ("morning", "midday", "afternoon", "evening")},
        "picks_new_daily": picks_new_job("daily"),
        "picks_new_weekly": picks_new_job("weekly"),
        "custody_watch_fri": custody_watch_job,
        "custody_watch_sat": custody_watch_job,
    }
    app.state.jobs = raw_jobs

    if enable_scheduler:
        from .scheduler import start_scheduler

        # 排程規格只有一份（api/helpers.job_schedule）：註冊與啟動補跑都吃它。
        # daily_update 的時間讀設定頁（effective_schedule），可在設定頁 reschedule。
        specs = job_schedule(cfg, effective_schedule(conn()))
        by_id = {sp["id"]: sp for sp in specs}

        # 上一個程序留下的 running **必須在排程器啟動前同步標掉**，不能只交給背景補跑執行緒：
        # 排程器一起來就可能觸發同一支 job，那時若還看到舊的 running，run_job 會當成「已在
        # 執行」而略過（這一場就沒了）；反過來補跑執行緒若不設時間界線，又會把本程序剛開始
        # 跑的那列標掉、再跑一次。booted_at 就是那條界線，一併傳給補跑。
        from .db import mark_interrupted_job_runs
        booted_at = _helpers._now().isoformat(timespec="seconds")
        n_stale = mark_interrupted_job_runs(conn(), booted_at, started_before=booted_at)
        if n_stale:
            log.warning("啟動：%d 列 running 標成 interrupted（上一個程序被重啟）", n_stale)

        def wrapped(job_id: str):
            spec, fn = by_id[job_id], raw_jobs[job_id]

            def _job():
                return run_job(job_id, scheduled_run_key(spec, _helpers._now()), fn)
            _job.__name__ = job_id      # APScheduler 的 log 用函式名，不然全是 <lambda>
            return _job

        app.state.scheduler = start_scheduler(wrapped("daily_update"), effective_schedule(conn()))
        for sp in specs:
            if sp["id"] == "daily_update":
                continue
            kw = {"hour": sp["hour"], "minute": sp["minute"]}
            if sp.get("dow"):
                kw["day_of_week"] = sp["dow"]
            app.state.scheduler.add_job(wrapped(sp["id"]), "cron", id=sp["id"],
                                        replace_existing=True, **kw)

        # 啟動補跑：今天該觸發卻沒有成功紀錄的場次，在背景執行緒依序補（不阻塞啟動——
        # Zeabur 會把啟動太久當成失敗）。透過模組屬性呼叫，測試才樁得掉。
        def _catchup():
            try:
                r = _helpers.catchup_missed_jobs(conn(), specs, raw_jobs, booted_at=booted_at)
                log.info("啟動補跑完成：%s", r.get("ran") or "無")
            except Exception:  # noqa: BLE001
                log.exception("啟動補跑失敗")
        threading.Thread(target=_catchup, name="spr-catchup", daemon=True).start()

    if os.path.isdir(WEB_DIR):
        app.mount("/", _NoCacheStatic(directory=WEB_DIR, html=True), name="web")
    return app


class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER", "0") == "1")
