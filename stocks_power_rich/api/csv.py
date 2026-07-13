import os
import tempfile
from fastapi import APIRouter, File, UploadFile
from .deps import conn
from .helpers import (
    _clear_csv_cache,
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
    return {"dates": get_snapshot_dates(conn())}
