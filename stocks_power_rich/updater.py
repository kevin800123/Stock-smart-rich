"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
from datetime import datetime

from .db import upsert_market_daily
from .sources import intl, taifex, twse


def run_update(conn, intl_tickers: dict) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    row = {"date": today, "updated_at": datetime.now().isoformat()}
    success, failed = [], []

    tasks = [
        ("twse_taiex", twse.fetch_taiex),
        ("twse_inst", twse.fetch_institutional),
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
            else:
                row.update({k: v for k, v in data.items() if v is not None})
            success.append(name)
        except Exception as e:  # noqa: BLE001 — 容錯：單一來源失敗不影響其餘
            failed.append({"source": name.split("_")[0], "name": name, "error": str(e)})

    upsert_market_daily(conn, row)
    return {"date": today, "success": success, "failed": failed}
