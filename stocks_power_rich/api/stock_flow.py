import threading

from fastapi import APIRouter

from .deps import conn
from .. import stock_flow
from ..config import load_config
from ..db import get_connection

router = APIRouter(prefix="/api/stock-flow", tags=["stock-flow-research"])
_work_lock = threading.Lock()

# 全市場研究是 CPU-bound、隨資料量成長的同步計算（實測本機滿載約 2.5 秒，Zeabur 便宜
# 方案的共享 CPU 被節流後可拉到數十秒），放在請求裡同步跑會被反向代理逾時砍成 502——
# 而且第一次算不完就永遠寫不進快取，於是每次點擊都 502、卡死。改成背景執行緒計算、
# 請求立刻回 status，前端輪詢到 status:"ready" 為止。這樣不管機器多慢都不會再 502。
_research_lock = threading.Lock()
_research_running: set[str] = set()          # 正在背景計算的 data_version
_research_errors: dict[str, str] = {}        # data_version -> 上次背景計算的錯誤訊息（回報一次即清）


def _run_research_job(version: str, db_path: str) -> None:
    """在自己的連線裡跑完整研究並寫進 ai_cache（見 stock_flow.research）。

    背景執行緒不能共用請求那條 sqlite 連線（sqlite 預設 check_same_thread=True），所以
    自己開一條、用完關掉。錯誤不能直接吞掉就消失——記進 _research_errors，讓下一次請求
    能把失敗如實回報並允許重試（不寫進快取，避免把失敗永久化，同全站快取守則）。"""
    wc = get_connection(db_path)
    try:
        wc.execute("PRAGMA busy_timeout=30000")  # 與同時進行的回補寫入短暫相撞時等待而非直接失敗
        stock_flow.research(wc)
    except Exception as exc:  # noqa: BLE001 — 背景錯誤要留給下次請求回報
        with _research_lock:
            _research_errors[version] = str(exc)
    finally:
        with _research_lock:
            _research_running.discard(version)
        wc.close()


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
    """啟動/輪詢全市場研究。永遠立刻回應，計算在背景執行緒進行：
      status=ready    → 附上完整報告（快取命中，或背景已算完）
      status=computing → 背景計算中，前端每幾秒再打一次直到 ready
      status=error     → 背景計算失敗，附錯誤訊息；已清除，再按一次即重試
    """
    c = conn()
    version = stock_flow.data_version(c)
    cached = stock_flow.cached_research(c)
    if cached is not None:
        return {"status": "ready", **cached}
    with _research_lock:
        err = _research_errors.pop(version, None)
        if err is not None:
            return {"status": "error", "error": err,
                    "note": "上一次研究計算失敗，已清除；再按一次可重試。"}
        if version in _research_running:
            return {"status": "computing",
                    "note": "研究計算進行中（首次全市場約需數十秒），完成後會自動顯示。"}
        _research_running.add(version)
    threading.Thread(target=_run_research_job, args=(version, load_config().db_path),
                     daemon=True).start()
    return {"status": "computing",
            "note": "研究計算已開始（首次全市場約需數十秒），完成後會自動顯示。"}
