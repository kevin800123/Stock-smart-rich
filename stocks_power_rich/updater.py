"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
from datetime import date as _date, datetime

from .db import upsert_market_daily, upsert_tx_history
from .sources import intl, taifex, twse


def _iso_to_date(s):
    try:
        return _date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def run_update(conn, intl_tickers: dict) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    row = {"date": today, "updated_at": datetime.now().isoformat()}
    success, failed = [], []

    # 先以直連加權指數定出「資料日期」D（當日盤後即有），其餘來源全部依 D 直連抓取
    try:
        taiex = twse.fetch_taiex()
        row.update({k: v for k, v in taiex.items() if v is not None})
        success.append("twse_taiex")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "twse_taiex", "error": str(e)})
    D = _iso_to_date(row.get("date"))

    tasks = [
        ("twse_inst", lambda: twse.fetch_institutional(date=D)),
        ("twse_margin", lambda: twse.fetch_margin(date=D)),
        ("taifex_chips", lambda: taifex.fetch_chips_for_date(D)),
        ("intl", lambda: intl.fetch_intl_indices(intl_tickers)),
    ]

    for name, fn in tasks:
        try:
            data = fn()
            if name == "intl":
                for key, val in data.items():
                    if val.get("value") is not None:
                        row[key] = val["value"]
                        row[key + "_chg"] = val.get("chg_pct")  # 國際指數漲跌%
            else:
                # 只覆蓋有值的欄位；缺資料（None）保持空白，不以舊值或他日資料填充
                row.update({k: v for k, v in data.items() if v is not None})
            success.append(name)
        except Exception as e:  # noqa: BLE001 — 容錯：單一來源失敗不影響其餘
            failed.append({"source": name.split("_")[0], "name": name, "error": str(e)})

    upsert_market_daily(conn, row)
    # 清掉「比資料日期還新」的幽靈列（舊版以執行當天日期誤存所致）
    conn.execute("DELETE FROM market_daily WHERE date > ?", (row["date"],))
    conn.commit()

    # 台指期歷史日K（期交所官方下載），刷新近期
    try:
        tx_hist = taifex.fetch_tx_history(days=40)
        if tx_hist:
            upsert_tx_history(conn, tx_hist)
            success.append("taifex_tx_history")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "taifex", "name": "tx_history", "error": str(e)})

    return {"date": today, "success": success, "failed": failed}
