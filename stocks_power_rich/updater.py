"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
import threading
import time
from datetime import date as _date, datetime, timedelta

from .db import (
    bulk_upsert_custody,
    bulk_upsert_ohlc,
    custody_week_exists,
    get_setting,
    latest_custody_week,
    ohlc_dates,
    set_setting,
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


def _compute_margin_maintenance(D, margin_value):
    """大盤整戶擔保維持率（需個股融資融券明細＋全市場收盤）。算不出回 None。

    抽成函數是為了讓每日更新與 _heal_margin_maintenance 共用同一條計算路徑，
    否則兩邊各寫一份會漂移。
    """
    if not D or not margin_value:
        return None
    detail = twse.fetch_margin_detail(D)
    quotes = twse.fetch_stock_quotes(D)
    closes = {c: q["close"] for c, q in quotes.items() if q.get("close")}
    return analysis.margin_maintenance(
        detail.get("margin", {}), closes, margin_value, detail.get("short"))


def _heal_margin_maintenance(conn, days: int = 7, cap: int = 3) -> list:
    """回補近 days 天「已有 margin_value 但維持率仍空」的交易日。

    存在的理由：margin_value（官方融資金額）約 21:00 才公布，而更新可能跑在那之前
    （16:00 推播、白天開頁的 autoUpdate），此時維持率整段算不出來。margin_value 之後
    會被 _refresh_recent 補上，但維持率原本只在當次 run 算一次、不會回頭重算——
    於是「被依賴的欄位自癒了，依賴它的沒有」，45 天只有 7 天有值。
    每天兩支 TWSE 全市場請求，故限 cap 天。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    pending = [(r[0], r[1]) for r in conn.execute(
        "SELECT date, margin_value FROM market_daily WHERE date >= ? "
        "AND margin_value IS NOT NULL AND margin_maintenance IS NULL ORDER BY date DESC",
        (cutoff,),
    ).fetchall()][:cap]
    filled = []
    for ds, mv in pending:
        try:
            mm = _compute_margin_maintenance(_iso_to_date(ds), mv)
            if mm:
                upsert_market_daily(conn, {"date": ds, "margin_maintenance": mm})
                filled.append(ds)
        except Exception:  # noqa: BLE001 — 單日回補失敗略過，下次再試
            pass
    return filled


def _backfill_intl(conn, intl_tickers: dict, days: int = 10) -> list:
    """回補近 days 天內國際指數為空的欄位（只填 NULL，絕不覆蓋既有值）。

    治兩種缺口：新加入的代碼沒有歷史、以及 yfinance 偶發失敗留下的洞。
    對齊規則見 intl.pick_close_for／INTL_SAME_DAY：亞股取 D 當日收盤，其餘取 D 之前
    最近一場——台北 D 日晚間檢視時，美股 D 當日尚未開盤。

    只填 NULL 的用意：既有值是舊行為「抓取當下的最新值」產生的，語意與本函數的
    「場次收盤」不同；覆寫等於默默改寫歷史，寧可讓新舊並存且各自有明確出處。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    keys = list(intl_tickers)
    cols = ", ".join(keys)
    rows = conn.execute(
        f"SELECT date, {cols} FROM market_daily WHERE date >= ? ORDER BY date", (cutoff,),
    ).fetchall()
    holes = [r for r in rows if any(r[i + 1] is None for i in range(len(keys)))]
    if not holes:
        return []
    hist = intl.fetch_intl_history(intl_tickers, days=max(days * 2, 30))
    filled = []
    for r in holes:
        ds = r[0]
        patch = {}
        for i, key in enumerate(keys):
            if r[i + 1] is not None or key not in hist:
                continue
            got = intl.pick_close_for(hist[key], ds, same_day=key in intl.INTL_SAME_DAY)
            if got:
                patch[key] = got["value"]
                patch[key + "_chg"] = got["chg_pct"]
        if patch:
            upsert_market_daily(conn, {"date": ds, **patch})
            filled.append(ds)
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


_FAIL_ABORT = 20     # 單一市場「累計」連續失敗 N 個日期 → 熔斷該市場（判定為歷史底線）
                     # 需大於台股最長連續休市（農曆春節封關最多約 5~6 個工作日），否則假期
                     # 會被誤判成歷史底線；也需容許「單次呼叫時間預算不足以一口氣試到門檻」
                     # 的情況——見下方游標/失敗計數皆持久化的設計說明
_TIME_BUDGET = 25.0  # 單次呼叫時間上限（秒）：在反向代理逾時前先回傳部分進度，避免 502
_THROTTLE = 0.25     # 每處理一個日期的間隔，對官方來源溫柔
_FETCH_DEADLINE = 40.0  # 單一對外抓取的硬性截止（秒）：來源 httpx 雖有 timeout(25~30s)，
                        # 但 DNS/TLS 等前置階段不在其涵蓋範圍，實務上仍可能無限掛死
                        # （2026-07-07 事故：一次掛死→回補鎖不釋放→整個服務卡死需人工重啟）


def _fetch_capped(fn, arg):
    """在獨立 daemon 執行緒跑對外抓取並強制截止：逾時視同「該日抓不到」回空
    （計入該市場失敗計數）。卡死的執行緒無法強殺、只能棄置不等——寧可漏掉
    一個日期，也不讓整個回補（乃至持鎖的服務）跟著掛死。例外原樣重拋，
    與直接呼叫行為一致。"""
    out: dict = {}

    def run():
        try:
            out["rows"] = fn(arg)
        except Exception as e:  # noqa: BLE001 — 帶回主執行緒重拋
            out["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(_FETCH_DEADLINE)
    if "err" in out:
        raise out["err"]
    return out.get("rows") or {}


def _get_date_setting(conn, key: str, fallback):
    v = get_setting(conn, key)
    try:
        return _date.fromisoformat(v) if v else fallback
    except ValueError:
        return fallback


def _get_int_setting(conn, key: str, fallback: int = 0) -> int:
    v = get_setting(conn, key)
    try:
        return int(v) if v is not None else fallback
    except ValueError:
        return fallback


def backfill_ohlc(conn, target: int = 377, max_fetch: int = 60) -> dict:
    """回補全市場（上市＋上櫃）個股每日 OHLC 到 target 個交易日（分次可續補、狀態持久化）。

    兩市場共用同一個日期游標一起往回掃（指標股法各自追蹤已存天數），但**游標位置與各市場
    連續失敗次數都持久化於 settings、跨越多次呼叫累積，不隨每次呼叫重新歸零**。

    這是關鍵設計：早期版本每次呼叫都從「今天」重新掃、失敗計數重算，若單次呼叫的時間預算
    (`_TIME_BUDGET`) 不足以撐到熔斷門檻（官方伺服器慢、一次只夠試個位數天數），就會每次都
    在同一批日期打轉、真實進度掛零（實測：連續 30+ 次呼叫卡在同一天數不動）。持久化後，
    即使每次只推進一點，累積終究會抵達目標或觸發熔斷；任一天成功即重置失敗計數，短暫的
    連續假期（如農曆春節封關）不會被誤判成官方歷史底線。
    殘餘限制：若來源發生跨越多次呼叫的長時間暫時性故障（非假期、非真底線），失敗計數仍可能
    累積到門檻而誤判熔斷；此情境機率低、且僅影響「提早放棄該市場」，非資料錯誤，故接受此權衡。
    """
    have_tw = _dates_with(conn, _TW_BELL)
    have_otc = _dates_with(conn, _OTC_BELL)
    added = 0
    start = time.monotonic()
    anchor = _get_date_setting(conn, "ohlc_cursor", _date.today())
    tw_fails = _get_int_setting(conn, "ohlc_fails_tw")
    otc_fails = _get_int_setting(conn, "ohlc_fails_otc")
    tw_aborted = get_setting(conn, "ohlc_exhausted_tw") == "1"
    otc_aborted = get_setting(conn, "ohlc_exhausted_otc") == "1"
    floor = _date.today() - timedelta(days=target * 2 + 40)  # 日曆下限，避免無限迴圈
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
            rows = _fetch_capped(twse.fetch_stock_ohlc, anchor)
            if rows:
                bulk_upsert_ohlc(conn, ds, rows)
                have_tw.add(ds)
                tw_fails = 0
            else:
                tw_fails += 1
                tw_aborted = tw_fails >= _FAIL_ABORT
        if ds not in have_otc and len(have_otc) < target and not otc_aborted:
            attempted = True
            rows = _fetch_capped(tpex.fetch_otc_ohlc, anchor)
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
    set_setting(conn, "ohlc_cursor", anchor.isoformat())
    set_setting(conn, "ohlc_fails_tw", str(tw_fails))
    set_setting(conn, "ohlc_fails_otc", str(otc_fails))
    if tw_aborted:
        set_setting(conn, "ohlc_exhausted_tw", "1")
    if otc_aborted:
        set_setting(conn, "ohlc_exhausted_otc", "1")
    done = len(have_tw) >= target and (len(have_otc) >= target or otc_aborted)
    return {"stored_days": min(len(have_tw), len(have_otc)),
            "twse_days": len(have_tw), "otc_days": len(have_otc),
            "added": added, "twse_exhausted": tw_aborted, "otc_exhausted": otc_aborted, "done": done}


def reset_ohlc_progress(conn) -> None:
    """清掉持久化的回補進度（游標／兩市場失敗計數／熔斷旗標），供誤判熔斷時強制重來一次。

    不刪除已存的 OHLC 資料本身，只重置「掃到哪裡、失敗幾次」的狀態；下次呼叫會從今天
    重新往回掃，已存日期仍會被快速跳過（見 _dates_with），故不會重工，只是重新給熔斷
    判定一次機會（例如懷疑先前是暫時性問題被誤判成永久底線時使用）。
    """
    for key in ("ohlc_cursor", "ohlc_fails_tw", "ohlc_fails_otc",
                "ohlc_exhausted_tw", "ohlc_exhausted_otc"):
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()


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

    # 國際指數不在這裡抓——見下方 _backfill_intl，它是唯一寫入點。
    tasks = [
        ("twse_inst", lambda: twse.fetch_institutional(date=D)),
        ("twse_margin", lambda: twse.fetch_margin(date=D)),
        ("taifex_chips", lambda: taifex.fetch_chips_for_date(D)),
    ]

    for name, fn in tasks:
        try:
            data = fn()
            # 只覆蓋有值的欄位；缺資料（None）保持空白，不以舊值或他日資料填充
            row.update({k: v for k, v in data.items() if v is not None})
            success.append(name)
        except Exception as e:  # noqa: BLE001 — 容錯：單一來源失敗不影響其餘
            failed.append({"source": name.split("_")[0], "name": name, "error": str(e)})

    # 大盤整戶擔保維持率（需融資金額＋個股融資融券明細＋全市場收盤；約 21:00 融資公布後才算得出）
    # 跑在 21:00 前時 margin_value 還沒公布，這裡算不出來——記進 failed 而非靜默跳過，
    # 否則「今天為什麼沒維持率」在更新結果裡完全看不出來。缺的那天由 _heal_margin_maintenance 補。
    try:
        if D and row.get("margin_value"):
            mm = _compute_margin_maintenance(D, row["margin_value"])
            if mm:
                row["margin_maintenance"] = mm
                success.append("margin_maintenance")
            else:
                failed.append({"source": "twse", "name": "margin_maintenance",
                               "error": "明細或收盤不足，算不出維持率"})
        elif D:
            failed.append({"source": "twse", "name": "margin_maintenance",
                           "error": "融資金額尚未公布（約 21:00），稍後回補"})
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

    # 補算近期缺的融資維持率（21:00 前跑的那些 run 算不出來，margin_value 事後才補上）
    try:
        if _heal_margin_maintenance(conn):
            success.append("twse_margin_maint_heal")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "margin_maint_heal", "error": str(e)})

    # 國際指數的唯一寫入點（含當日）。刻意不在上面的 tasks 裡抓「當下最新值」——
    # 那個值取決於更新程式幾點跑，不是任何一場的收盤：實測同一個 sox 數字被寫進
    # 2026-07-20 與 07-21 兩列，等於把別場的價格貼上 D 的標籤，違反本檔頂部的資料日 D 原則。
    # 改由 _backfill_intl 以 pick_close_for 的場次規則寫入，當日算不出就留 NULL——
    # NULL 會被下次更新回補，寫錯的值則因「只填 NULL 不覆蓋」而永遠留著，所以寧可留空。
    try:
        filled = _backfill_intl(conn, intl_tickers)
        today_ds = D.isoformat() if D else None
        if today_ds and today_ds in filled:
            success.append("intl")
        elif today_ds:
            failed.append({"source": "intl", "name": "intl",
                           "error": "當日場次收盤尚未取得，下次更新自動回補"})
        past = [f for f in filled if f != today_ds]
        if past:
            success.append(f"intl_backfill:{len(past)}")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "intl", "name": "intl", "error": str(e)})

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
