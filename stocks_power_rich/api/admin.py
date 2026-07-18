import os
import glob as _glob
import threading
from fastapi import APIRouter, Body, Request
from .deps import conn
from .helpers import (
    _latest_date,
    effective_schedule,
    effective_data_dir,
    _dir_within,
    _push_line,
    _intraday_scan,
    REPO_DIR
)
from ..db import get_setting, set_setting, get_snapshot_dates, get_tx_history, backup_db
from ..config import load_config
from .. import updater

router = APIRouter(prefix="/api")

_backfill_lock = threading.Lock()

@router.post("/update/run")
def run_update():
    cfg = load_config()
    c = conn()
    res = updater.run_update(c, cfg.intl_tickers)
    try:
        from ..ledger import record_daily_signals, update_ledger_returns
        record_daily_signals(c)
        update_ledger_returns(c)
    except Exception:  # noqa: BLE001
        pass
    return res

@router.get("/backfill")
def backfill(days: int = 30):
    n = updater.backfill_history(conn(), max(5, min(days, 60)))
    return {"backfilled_days": n}

@router.get("/ohlc/backfill")
def ohlc_backfill(days: int = 377, max_fetch: int = 60, reset: int = 0):
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        c = conn()
        if reset:
            updater.reset_ohlc_progress(c)
        return updater.backfill_ohlc(c, target=max(60, min(days, 800)),
                                     max_fetch=max(1, min(max_fetch, 120)))
    finally:
        _backfill_lock.release()

@router.post("/db/backup")
def db_backup():
    cfg = load_config()
    try:
        dest = backup_db(cfg.db_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    bdir = os.path.join(os.path.dirname(cfg.db_path) or ".", "backup")
    files = [os.path.basename(p) for p in sorted(_glob.glob(os.path.join(bdir, "spr-*.sqlite")))]
    return {"ok": bool(dest), "file": os.path.basename(dest) if dest else None, "backups": files}

@router.get("/settings")
def get_settings(request: Request):
    cfg = load_config()
    c = conn()
    last_date = _latest_date(c)
    return {
        "gemini_configured": bool(cfg.gemini_api_key),
        "line_configured": bool(cfg.line_token),
        "offsite_backup_configured": bool(cfg.backup_git_remote),
        "line_push_time": cfg.line_push_time,
        "weekly_push_time": cfg.weekly_push_time,
        "schedule_time": effective_schedule(c),
        "scheduler_running": bool(getattr(request.app.state, "scheduler", None)),
        "data_dir": effective_data_dir(c),
        "snapshots": len(get_snapshot_dates(c)),
        "tx_history_days": len(get_tx_history(c)),
        "last_market_date": last_date,
        "nav_order": (get_setting(c, "nav_order") or "").split(",") if get_setting(c, "nav_order") else None,
        "intraday_picks_only": get_setting(c, "intraday_picks_only") == "1",
        "loss_tolerance": int(get_setting(c, "loss_tolerance") or 0) or None,
    }

@router.post("/settings")
def update_settings(request: Request, payload: dict = Body(...)):
    cfg = load_config()
    c = conn()
    st = payload.get("schedule_time")
    if st:
        set_setting(c, "schedule_time", str(st))
        sched = getattr(request.app.state, "scheduler", None)
        if sched:
            try:
                from ..scheduler import build_trigger_kwargs
                sched.reschedule_job("daily_update", trigger="cron", **build_trigger_kwargs(st))
            except Exception:  # noqa: BLE001
                pass
    dd = payload.get("data_dir")
    if dd:
        if _dir_within(str(dd), [REPO_DIR, cfg.data_dir]):
            set_setting(c, "data_dir", str(dd))
        else:
            return {"ok": False, "error": "資料夾不在允許範圍（僅限專案目錄下）", **get_settings(request)}
    if "intraday_picks_only" in payload:
        set_setting(c, "intraday_picks_only", "1" if payload["intraday_picks_only"] else "0")
    if "loss_tolerance" in payload:
        try:
            v = int(payload["loss_tolerance"] or 0)
        except (TypeError, ValueError):
            v = 0
        set_setting(c, "loss_tolerance", str(v) if v > 0 else "")
    no = payload.get("nav_order")
    if isinstance(no, list) and no:
        ids = [str(x) for x in no if str(x).isalnum()]
        if ids:
            set_setting(c, "nav_order", ",".join(ids))
    return {"ok": True, **get_settings(request)}

@router.post("/line/test")
def line_test():
    return _push_line(conn(), full=True, force=True)

@router.post("/intraday/test")
def intraday_test(push: int = 0):
    return _intraday_scan(conn(), push=bool(push))
