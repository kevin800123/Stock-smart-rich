import threading

from fastapi import APIRouter

from .deps import conn
from .. import stock_flow

router = APIRouter(prefix="/api/stock-flow", tags=["stock-flow-research"])
_work_lock = threading.Lock()


@router.get("/coverage")
def stock_flow_coverage(days: int = 220):
    report = stock_flow.coverage_report(conn(), days=days, batch_size=3)
    report["disclaimer"] = stock_flow.DISCLAIMER
    return report


@router.get("/backfill")
def stock_flow_backfill(days: int = 220, max_fetch: int = 3):
    if not _work_lock.acquire(blocking=False):
        return {"busy": True, "note": "另一個法人資料工作仍在執行，請稍後再試。"}
    try:
        return stock_flow.backfill(conn(), days=days, max_fetch=max_fetch)
    finally:
        _work_lock.release()


@router.post("/research")
def stock_flow_research():
    if not _work_lock.acquire(blocking=False):
        return {"busy": True, "note": "法人資料回補或研究仍在執行，請稍後再試。"}
    try:
        return stock_flow.research(conn())
    finally:
        _work_lock.release()
