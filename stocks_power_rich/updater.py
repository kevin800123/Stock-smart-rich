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

    tasks = [
        # 先抓加權指數定出「資料日期」，其餘對齊同一天
        ("twse_taiex", twse.fetch_taiex),
        ("twse_inst", lambda: twse.fetch_institutional(date=_iso_to_date(row.get("date")))),
        ("twse_margin", twse.fetch_margin),
        ("taifex_tx", taifex.fetch_tx_quote),
        ("taifex_retail", taifex.fetch_retail_ratios),
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
                # taiex 的 date 會覆蓋預設 today，作為全列的資料日期
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
