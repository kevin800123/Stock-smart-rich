"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
import time
from datetime import date as _date, datetime, timedelta

from .db import (
    bulk_upsert_custody,
    bulk_upsert_ohlc,
    custody_week_exists,
    latest_custody_week,
    ohlc_dates,
    upsert_market_daily,
    upsert_tx_history,
)
from . import analysis
from .sources import intl, taifex, tdcc, tpex, twse


def _accumulate_custody(conn) -> str | None:
    """偵測到新的一週才抓 TDCC 全市場集保大戶比並批次入庫（趨勢逐週累積）。

    若資料庫最近一週在 6 天內（同一週）即略過，連抓都免；跨到新一週才下載並 bulk 寫入。
    """
    last = latest_custody_week(conn)
    if last:
        try:
            if (_date.today() - _date.fromisoformat(last)).days < 6:
                return None
        except (TypeError, ValueError):
            pass
    cur = tdcc.fetch_custody_distribution()
    week, data = cur.get("week_date"), cur.get("data") or {}
    if not week or not data or custody_week_exists(conn, week):
        return None
    bulk_upsert_custody(conn, week, data)
    return week


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


def backfill_history(conn, days: int = 30) -> int:
    """回補近 days 天的加權指數＋三大法人現貨買賣超＋融資融券（逐日，供雲端冷啟動補歷史）。

    逐日 upsert 且各自 commit，即使中途逾時，已處理日期也會保存，重跑可續補。
    期貨籌碼（未平倉/多空比）來源較慢，不在此整月回補，改由每日更新逐步累積。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    seen: dict = {}
    # 近 3 個月錨點（本月、上月、上上月最後一天），避免月初漏掉整個上月
    anchor, anchors = _date.today(), []
    for _ in range(3):
        anchors.append(anchor)
        anchor = anchor.replace(day=1) - timedelta(days=1)
    for a in anchors:
        for r in twse.fetch_taiex_history(a):
            if r["date"] >= cutoff:
                seen[r["date"]] = r
    for iso in sorted(seen):
        r = seen[iso]
        row = {"date": iso, "updated_at": datetime.now().isoformat()}
        for k in ("taiex", "taiex_chg", "turnover"):
            if r.get(k) is not None:
                row[k] = r[k]
        d = _iso_to_date(iso)
        for fetch in (lambda: twse.fetch_institutional(date=d), lambda: twse.fetch_margin(date=d)):
            try:
                row.update({k: v for k, v in fetch().items() if v is not None})
            except Exception:  # noqa: BLE001
                pass
        upsert_market_daily(conn, row)
    return len(seen)


# 指標股：以其是否存在判斷某日「該市場已回補」（2330 上市必有；上櫃取三檔大型股任一）
_TW_BELL = ("2330",)
_OTC_BELL = ("8069", "5483", "3105")


def _dates_with(conn, codes) -> set:
    ph = ",".join("?" * len(codes))
    return {r[0] for r in conn.execute(
        f"SELECT DISTINCT date FROM stock_ohlc WHERE code IN ({ph})", list(codes))}


_FAIL_ABORT = 20     # 單一市場連續失敗 N 個日期 → 本輪熔斷該市場（歷史底線或來源異常）
                     # 需大於台股最長連續休市（農曆春節封關最多約 5~6 個工作日），
                     # 否則假期會被誤判成歷史底線：斷路器在該處觸發、下次呼叫又從今天
                     # 重新往回掃到同一個假期、又觸發熔斷，永遠卡在同一天數（實際踩過的坑）
_TIME_BUDGET = 25.0  # 單次呼叫時間上限（秒）：在反向代理逾時前先回傳部分進度，避免 502
_THROTTLE = 0.25     # 每處理一個日期的間隔，對官方來源溫柔


def backfill_ohlc(conn, target: int = 377, max_fetch: int = 60) -> dict:
    """回補全市場（上市＋上櫃）個股每日 OHLC 到 target 個交易日（分次可續補）。

    - 兩市場各自追蹤已存日期（指標股法），舊資料只補缺的那個市場。
    - 連續失敗熔斷：某市場連 5 個日期抓不到就本輪放棄它，配額讓給另一市場
      （TPEx dailyQuotes 僅提供約 1.4 年歷史，更舊必然失敗——不熔斷會卡死在同一批日期）。
    - done 語意：上市達標，且上櫃「達標或已到官方歷史底線」（以同輪上市抓取成功佐證連線正常，
      避免把單純斷線誤判成底線）。
    """
    have_tw = _dates_with(conn, _TW_BELL)
    have_otc = _dates_with(conn, _OTC_BELL)
    added = 0
    tw_fails = otc_fails = 0
    tw_ok_this_call = False
    tw_aborted = otc_aborted = False
    start = time.monotonic()
    anchor = _date.today()
    floor = anchor - timedelta(days=target * 2 + 40)  # 日曆下限，避免無限迴圈
    while (len(have_tw) < target or len(have_otc) < target) and added < max_fetch and anchor >= floor:
        if time.monotonic() - start > _TIME_BUDGET:
            break
        if anchor.weekday() >= 5:
            anchor -= timedelta(days=1)
            continue
        ds = anchor.isoformat()
        attempted = False
        if ds not in have_tw and len(have_tw) < target and not tw_aborted:
            attempted = True
            rows = twse.fetch_stock_ohlc(anchor)
            if rows:
                bulk_upsert_ohlc(conn, ds, rows)
                have_tw.add(ds)
                tw_fails, tw_ok_this_call = 0, True
            else:
                tw_fails += 1
                tw_aborted = tw_fails >= _FAIL_ABORT
        if ds not in have_otc and len(have_otc) < target and not otc_aborted:
            attempted = True
            rows = tpex.fetch_otc_ohlc(anchor)
            if rows:
                bulk_upsert_ohlc(conn, ds, rows)
                have_otc.add(ds)
                otc_fails = 0
            else:
                otc_fails += 1
                otc_aborted = otc_fails >= _FAIL_ABORT
        if attempted:
            added += 1  # 以「處理過的日數」計次，確保單次呼叫有界
            time.sleep(_THROTTLE)
        if tw_aborted and otc_aborted:
            break
        anchor -= timedelta(days=1)
    otc_exhausted = otc_aborted and (tw_ok_this_call or len(have_tw) >= target)
    done = len(have_tw) >= target and (len(have_otc) >= target or otc_exhausted)
    return {"stored_days": min(len(have_tw), len(have_otc)),
            "twse_days": len(have_tw), "otc_days": len(have_otc),
            "added": added, "otc_exhausted": otc_exhausted, "done": done}


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

    # 大盤融資維持率（需融資金額＋個股融資明細＋全市場收盤；約 21:00 融資公布後才算得出）
    try:
        if D and row.get("margin_value"):
            lots = twse.fetch_margin_detail(D)
            quotes = twse.fetch_stock_quotes(D)
            mm = analysis.margin_maintenance(
                lots, {c: q["close"] for c, q in quotes.items() if q.get("close")},
                row["margin_value"])
            if mm:
                row["margin_maintenance"] = mm
                success.append("margin_maintenance")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "margin_maintenance", "error": str(e)})

    upsert_market_daily(conn, row)
    # 清理：以「真實今天」為基準刪掉未來幽靈列，並清掉異常過舊(>400天)的髒列。
    # 不可用抓到的資料日期當基準——若來源偶爾回傳錯誤舊日期，會把正常歷史整批誤刪。
    now = datetime.now()
    conn.execute(
        "DELETE FROM market_daily WHERE date > ? OR date < ?",
        (now.strftime("%Y-%m-%d"), (now - timedelta(days=400)).strftime("%Y-%m-%d")),
    )
    # ai_cache 多為逐日鍵（sectors/t86/個股報價…），會無限累積；120 天前的直接清掉（都可重抓）
    conn.execute(
        "DELETE FROM ai_cache WHERE created_at < ?",
        ((now - timedelta(days=120)).isoformat(),),
    )
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

    # 集保大戶比：偵測到新的一週才抓，全市場批次累積
    try:
        wk = _accumulate_custody(conn)
        if wk:
            success.append(f"tdcc_custody:{wk}")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "tdcc", "name": "custody", "error": str(e)})

    # 當日全市場個股 OHLC（型態選股用，逐日累積；上市＋上櫃）
    try:
        if D:
            ohlc = twse.fetch_stock_ohlc(D)
            try:
                ohlc.update(tpex.fetch_otc_ohlc(D))  # 上櫃失敗不影響上市入庫
            except Exception:  # noqa: BLE001
                pass
            if ohlc:
                bulk_upsert_ohlc(conn, row["date"], ohlc)
                success.append("stock_ohlc")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "stock_ohlc", "error": str(e)})

    # 台指期歷史日K（期交所官方下載），刷新近期
    try:
        tx_hist = taifex.fetch_tx_history(days=40)
        if tx_hist:
            upsert_tx_history(conn, tx_hist)
            success.append("taifex_tx_history")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "taifex", "name": "tx_history", "error": str(e)})

    return {"date": today, "success": success, "failed": failed}
