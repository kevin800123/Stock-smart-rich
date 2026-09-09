import os
import tempfile
from datetime import date
from fastapi import APIRouter, File, UploadFile
from .deps import conn
from .helpers import (
    _clear_csv_cache,
    _latest_date,
    effective_data_dir,
    MAX_UPLOAD_BYTES,
    UPLOAD_EXTS
)
from ..db import get_snapshot_dates, get_snapshot
from .. import csv_import
from .. import analysis
from ..ledger import record_daily_signals, update_ledger_returns

router = APIRouter(prefix="/api")

@router.post("/csv/upload")
async def upload(file: UploadFile = File(...)):
    fname = os.path.basename(file.filename or "upload.csv")
    if not fname.lower().endswith(UPLOAD_EXTS):
        return {"snap_date": None, "count": 0,
                "error": f"僅接受 {'/'.join(UPLOAD_EXTS)} 檔案"}
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return {"snap_date": None, "count": 0, "error": "檔案過大（上限 10MB）"}
    tmp = os.path.join(tempfile.gettempdir(), fname)
    with open(tmp, "wb") as f:
        f.write(data)
    c = conn()
    snap_date, count = csv_import.import_csv(c, tmp)
    _clear_csv_cache(c, snap_date)
    try:
        record_daily_signals(c)
        update_ledger_returns(c)
    except Exception:  # noqa: BLE001
        pass
    picks = analysis.filtered_picks(get_snapshot(c, snap_date))
    return {"snap_date": snap_date, "count": count, "picks": picks}

@router.post("/csv/import-latest")
def import_latest():
    c = conn()
    data_dir = effective_data_dir(c)
    path = csv_import.find_latest_file(data_dir)
    if not path:
        return {"snap_date": None, "count": 0, "daily_top": [],
                "error": f"資料夾找不到 CSV/Excel：{data_dir}"}
    snap_date, count = csv_import.import_csv(c, path)
    _clear_csv_cache(c, snap_date)
    try:
        record_daily_signals(c)
        update_ledger_returns(c)
    except Exception:  # noqa: BLE001
        pass
    picks = analysis.filtered_picks(get_snapshot(c, snap_date))
    return {"snap_date": snap_date, "count": count, "file": os.path.basename(path),
            "picks": picks}

@router.get("/csv/import-all")
def import_all():
    c = conn()
    data_dir = effective_data_dir(c)
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
        except Exception as e:  # noqa: BLE001
            imported.append({"file": os.path.basename(path), "error": str(e)})
    try:
        record_daily_signals(c)
        update_ledger_returns(c)
    except Exception:  # noqa: BLE001
        pass
    return {"imported": imported, "dates": get_snapshot_dates(c)}

@router.get("/snapshots")
def snapshots():
    """CSV 快照日清單 ＋ **它落後市場多少天**。

    籌碼選股頁吃 CSV，而使用者已決定停止每日上傳、只保留這一頁。那一頁原本只有一個
    日期下拉選單——看得到日期，卻**沒有任何參照告訴你那是不是今天**，於是停止上傳後
    它會安靜地一直顯示同一份選股。有了市場最新日，前端才有東西可比。

    落差用**日曆天**而非交易日，沿用 renderFreshness 的既有決定：跨週末說「落後 3 天」
    是事實，硬換算成「落後 1 個交易日」會讓週一早上看起來像資料很新。
    任一邊缺就回 None，**不回 0 假裝很新**——那正是這條提示要防的那種安靜的錯。
    """
    c = conn()
    dates = get_snapshot_dates(c)
    market = _latest_date(c)
    behind = None
    if dates and market:
        behind = (date.fromisoformat(market) - date.fromisoformat(dates[-1])).days
    return {"dates": dates, "market_date": market, "behind_days": behind}
