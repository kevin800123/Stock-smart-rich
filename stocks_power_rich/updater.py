"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
from datetime import date as _date, datetime, timedelta

from .db import upsert_market_daily, upsert_tx_history
from .sources import intl, taifex, twse


def _iso_to_date(s):
    try:
        return _date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _refresh_recent(conn, days: int = 7) -> list:
    """以「指定日」官方資料校正/回補近 days 天各列的三大法人與融資券。

    白天更新時官方三大法人可能還是盤中初值、融資券（約 21:00）尚未公布；隔日或晚間再次
    更新時，依各列日期直連重抓 BFI82U／MI_MARGN 並覆蓋，使數值對齊正確日期且為定稿值
    （修正舊版「回退他日」造成的日期錯置，以及初值→定稿的差異）。只覆蓋有值的欄位。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    dates = [r[0] for r in conn.execute(
        "SELECT date FROM market_daily WHERE date >= ? ORDER BY date", (cutoff,),
    ).fetchall()]
    healed = []
    for ds in dates:
        d = _iso_to_date(ds)
        patch = {}
        for fetch in (lambda: twse.fetch_institutional(date=d), lambda: twse.fetch_margin(date=d)):
            try:
                patch.update({k: v for k, v in fetch().items() if v is not None})
            except Exception:  # noqa: BLE001 — 單項失敗略過
                pass
        if patch:
            upsert_market_daily(conn, {"date": ds, **patch})
            healed.append(ds)
    return healed


def _backfill_chips(conn, days: int = 10, cap: int = 3) -> list:
    """回補近 days 天內、期貨籌碼（多空比/未平倉）仍有缺的交易日。

    期交所下載較慢（每日約 4 個檔），故限 cap 天；只補「多空比或外資台指未平倉為空」者。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    pending = [r[0] for r in conn.execute(
        "SELECT date FROM market_daily WHERE date >= ? "
        "AND (retail_ls_mtx IS NULL OR tx_foreign_oi IS NULL) ORDER BY date DESC",
        (cutoff,),
    ).fetchall()][:cap]
    filled = []
    for ds in pending:
        try:
            chips = taifex.fetch_chips_for_date(_iso_to_date(ds))
            patch = {k: v for k, v in chips.items() if v is not None}
            if patch:
                upsert_market_daily(conn, {"date": ds, **patch})
                filled.append(ds)
        except Exception:  # noqa: BLE001 — 單日回補失敗略過
            pass
    return filled


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

    # 校正/回補近期各列的三大法人與融資券（修正日期錯置、初值→定稿、晚間才公布）
    try:
        if _refresh_recent(conn):
            success.append("twse_refresh_recent")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "refresh_recent", "error": str(e)})

    # 回補近期缺的期貨籌碼（多空比/未平倉）
    try:
        if _backfill_chips(conn):
            success.append("taifex_chips_backfill")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "taifex", "name": "chips_backfill", "error": str(e)})

    # 台指期歷史日K（期交所官方下載），刷新近期
    try:
        tx_hist = taifex.fetch_tx_history(days=40)
        if tx_hist:
            upsert_tx_history(conn, tx_hist)
            success.append("taifex_tx_history")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "taifex", "name": "tx_history", "error": str(e)})

    return {"date": today, "success": success, "failed": failed}
