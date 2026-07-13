from fastapi import APIRouter, Body
from datetime import datetime
from .deps import conn
from .helpers import _trades_payload
from ..db import add_trade, close_trade, delete_trade

router = APIRouter(prefix="/api")

@router.get("/trades")
def trades_list():
    return _trades_payload(conn())

@router.post("/trades")
def trades_add(payload: dict = Body(...)):
    c = conn()
    code = str(payload.get("code") or "").strip().split(".")[0]
    try:
        shares = int(payload.get("shares") or 0)
        entry_price = float(payload.get("entry_price") or 0)
    except (TypeError, ValueError):
        shares, entry_price = 0, 0.0
    if not code or shares <= 0 or entry_price <= 0:
        return {"ok": False, "error": "代號/股數/進場價必填且需為正數"}
    name = str(payload.get("name") or "").strip()
    if not name:
        from .helpers import _industry_map, _otc_names
        name = _industry_map(c).get(code, {}).get("name") or _otc_names(c).get(code) or code
    entry_date = str(payload.get("entry_date") or "").strip() or None
    note = str(payload.get("note") or "").strip() or None
    try:
        fee = float(payload["fee_pct"]) if "fee_pct" in payload else None
    except (TypeError, ValueError):
        fee = None
    add_trade(c, {"code": code, "name": name, "shares": shares, "entry_price": entry_price,
                  "entry_date": entry_date, "fee_pct": fee, "note": note})
    return _trades_payload(c)

@router.post("/trades/{id}/close")
def trades_close(id: int, payload: dict = Body(...)):
    c = conn()
    try:
        exit_price = float(payload.get("exit_price") or 0)
    except (TypeError, ValueError):
        exit_price = 0.0
    if exit_price <= 0:
        return {"ok": False, "error": "出場價必填且需為正數"}
    exit_date = str(payload.get("exit_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    if not close_trade(c, id, exit_date, exit_price):
        return {"ok": False, "error": f"找不到交易 #{id}"}
    return _trades_payload(c)

@router.delete("/trades/{id}")
def trades_delete(id: int):
    c = conn()
    if not delete_trade(c, id):
        return {"ok": False, "error": f"找不到交易 #{id}"}
    return _trades_payload(c)

@router.get("/signals/performance")
def signals_performance():
    c = conn()
    perf = {}
    for source in ("filtered_picks", "cup_handle"):
        perf[source] = {}
        for ret_col in ("ret5", "ret10", "ret20"):
            rows = c.execute(
                f"SELECT {ret_col} FROM signal_ledger WHERE source=? AND {ret_col} IS NOT NULL",
                (source,)
            ).fetchall()
            vals = [r[0] for r in rows]
            count = len(vals)
            if count > 0:
                avg_ret = sum(vals) / count
                wins = sum(1 for v in vals if v > 0)
                win_rate = wins / count * 100
                perf[source][ret_col] = {
                    "win_rate": round(win_rate, 1),
                    "avg_ret": round(avg_ret, 2),
                    "count": count
                }
            else:
                perf[source][ret_col] = {
                    "win_rate": None,
                    "avg_ret": None,
                    "count": 0
                }

    user_data = _trades_payload(c)
    user_stats = user_data.get("stats") or {}

    return {
        "ok": True,
        "performance": perf,
        "user_stats": user_stats
    }
