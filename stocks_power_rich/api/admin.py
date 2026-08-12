import os
import glob as _glob
import threading
from fastapi import APIRouter, Body, Request
from .deps import conn
from .helpers import (
    _latest_date,
    effective_schedule,
    effective_data_dir,
    _dir_within,
    _push_line,
    _intraday_scan,
    _insti_for,
    ai_calls_today,
    line_quota_paused,
    REPO_DIR
)
from ..db import get_setting, set_setting, get_snapshot_dates, get_tx_history, get_ai_cache, backup_db
from ..config import load_config
from .. import updater, gemini, analysis

router = APIRouter(prefix="/api")

_backfill_lock = threading.Lock()

@router.post("/update/run")
def run_update():
    cfg = load_config()
    c = conn()
    res = updater.run_update(c, cfg.intl_tickers)
    try:
        from ..ledger import record_daily_signals, update_ledger_returns
        record_daily_signals(c)
        update_ledger_returns(c)
    except Exception:  # noqa: BLE001
        pass
    return res

@router.get("/backfill")
def backfill(days: int = 30, max_fetch: int = 20):
    """回補加權指數＋現貨法人＋融資融券歷史（建列的唯一入口）。

    上限 400 天而非原本的 60——run_update 只保留近 400 天，60 是沒有理由的窄。
    每個日期約 2 個 TWSE 請求，故以 max_fetch 分批，重複呼叫直到 remaining 為 0
    （同 chips/margin 回補的慣例）。指數不受 max_fetch 限制，窗口內一次建滿。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        return updater.backfill_history(conn(), max(5, min(days, 400)),
                                       cap=max(1, min(max_fetch, 40)))
    finally:
        _backfill_lock.release()

@router.get("/chips/backfill")
def chips_backfill(days: int = 90, max_fetch: int = 15):
    """大範圍回補台指期籌碼歷史（外資未平倉/散戶多空比等）。

    每日更新的 _backfill_chips 只回看 10 天、每次 3 筆，功能上線前的歷史永遠補不到——
    此端點用同一支回補函式放大視窗與上限，重複呼叫直到 remaining 不再下降
    （連假日期交所無資料者留 NULL，屬預期）。每個日期約 4 個 TAIFEX CSV 請求，勿設過大 max_fetch。

    上限 200（原 120）：`/api/backfill` 修好後建列窗口是 200 天，而這支只填既有列——
    上限比它窄的話，最舊那段永遠只有大盤與法人、沒有期貨籌碼，「大盤×籌碼對照圖」的
    期貨窗格就比其他窗格短一截（實測 120 時是 81/130 列、自 03-30 才有值）。已實測
    期交所對 200 天前的日期仍取得到完整籌碼。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        from datetime import date, timedelta
        c = conn()
        days = max(5, min(days, 200))     # 200 對齊 /api/backfill 建列的窗口，見下方註記
        filled = updater._backfill_chips(c, days=days, cap=max(1, min(max_fetch, 30)))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        remaining = c.execute(
            "SELECT COUNT(*) FROM market_daily WHERE date >= ? "
            "AND (retail_ls_mtx IS NULL OR tx_foreign_oi IS NULL)", (cutoff,)).fetchone()[0]
        return {"filled": filled, "remaining": remaining}
    finally:
        _backfill_lock.release()

@router.get("/margin-maintenance/heal")
def margin_maintenance_heal(days: int = 45, max_fetch: int = 15):
    """大範圍回補融資維持率歷史（上市＋上櫃）。

    每日更新的 _heal_margin_maintenance 只回看 7 天、每次最多 3 筆——上線前累積的洞
    （尤其上櫃：verify=False 修好前，Zeabur 上一天都沒補到過）永遠補不到。此端點用
    同一支自癒函式放大視窗與上限，重複呼叫直到 remaining 不再下降。

    上限 200（原 120）：理由同 chips/backfill——建列窗口是 200 天，上限比它窄會讓
    維持率窗格比其他窗格短一截（實測 120 時是 80/130 列）。每個日期 4 個請求，其中
    TWSE 全市場收盤約 1.7 萬列，故 max_fetch 勿設過大。已實測 TWSE 逐檔融資明細與
    櫃買融資對 200 天前的日期都仍有資料。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        from datetime import date, timedelta
        c = conn()
        days = max(5, min(days, 200))     # 200 對齊 /api/backfill 建列的窗口，見下方註記
        filled = updater._heal_margin_maintenance(c, days=days, cap=max(1, min(max_fetch, 30)))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        remaining = c.execute(
            "SELECT COUNT(*) FROM market_daily WHERE date >= ? "
            "AND ((margin_value IS NOT NULL AND margin_mv IS NULL) OR otc_margin_maintenance IS NULL)",
            (cutoff,)).fetchone()[0]
        return {"filled": filled, "remaining": remaining}
    finally:
        _backfill_lock.release()

@router.get("/intl/backfill")
def intl_backfill(days: int = 120):
    """大範圍回補國際指數歷史（費半/VIX/日經/KOSPI/黃金/日圓/台幣/比特幣）。

    每日更新的 _backfill_intl 只回看 10 天，補得到 yfinance 偶發失敗的洞，但補不到
    「代碼加入前」的空白——每個 ticker 都只從加入當天往後累積（vix 2026-06-25、
    jpy 07-02、twd 07-14），冷啟動歷史得靠這裡。yfinance 一次給數月，故不需分批。

    只填 NULL、不覆蓋既有值：既有值是舊行為「抓取當下的最新值」，語意與本端點寫入的
    「場次收盤」不同，覆寫等於默默改寫歷史。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        from datetime import date, timedelta
        c = conn()
        days = max(5, min(days, 365))
        tickers = load_config().intl_tickers
        yf_tickers = {k: v for k, v in tickers.items() if k not in updater.INTL_NON_YFINANCE_KEYS}
        filled = list(updater._backfill_intl(c, yf_tickers, days=days))
        filled += updater._backfill_intl_fred(c, days=days)
        filled += updater._backfill_intl_nasdaq(c, days=days)
        filled += updater._backfill_intl_tv(c, days=days)
        filled = sorted(set(filled))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        cond = " OR ".join(f"{k} IS NULL" for k in tickers)
        remaining = c.execute(
            f"SELECT COUNT(*) FROM market_daily WHERE date >= ? AND ({cond})",
            (cutoff,)).fetchone()[0]
        return {"filled": filled, "remaining": remaining}
    finally:
        _backfill_lock.release()

@router.get("/inst/backfill")
def inst_backfill(days: int = 60, max_fetch: int = 15):
    """預熱個股三大法人（T86/TPEx）整日快取，讓個股三大法人買賣超圖能秒載 6 月前歷史。

    資料逐日整日一次抓、快取跨股共用；此端點對 market_daily 最近 N 個交易日預熱兩市場，
    重複呼叫直到 remaining=0（連假日交易所無資料者留空、屬預期）。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        c = conn()
        days = max(5, min(days, 120))
        dlist = [r[0] for r in c.execute(
            "SELECT date FROM market_daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()]
        pending = [ds for ds in dlist if get_ai_cache(c, f"t86:{ds}") is None]
        filled = []
        for ds in pending[:max(1, min(max_fetch, 30))]:
            twse_ok = bool(_insti_for(c, ds, "twse"))
            _insti_for(c, ds, "tpex")   # 上櫃也預熱，任何股都秒載
            if twse_ok:
                filled.append(ds)
        remaining = sum(1 for ds in dlist if get_ai_cache(c, f"t86:{ds}") is None)
        return {"filled": filled, "remaining": remaining}
    finally:
        _backfill_lock.release()

@router.get("/financials/backfill")
def financials_backfill(max_batches: int = 4, batch_size: int = 50):
    """全市場逐檔季報財務回補（mopsfin，sub-task 1 的 8 個乾淨 JSON 指標）。

    母體取月營收表代號（需先跑過月營收，即每日 run_update 會做的）；已補過的代號跳過，
    重複呼叫直到 remaining=0。財報一季才更新一次，故非每日排程、屬偶爾手動觸發。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        return updater.backfill_financials(
            conn(), max_batches=max(1, min(max_batches, 20)),
            batch_size=max(1, min(batch_size, 50)))
    finally:
        _backfill_lock.release()

@router.get("/ohlc/backfill")
def ohlc_backfill(days: int = 377, max_fetch: int = 60, reset: int = 0):
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        c = conn()
        if reset:
            updater.reset_ohlc_progress(c)
        return updater.backfill_ohlc(c, target=max(60, min(days, 800)),
                                     max_fetch=max(1, min(max_fetch, 120)))
    finally:
        _backfill_lock.release()

@router.post("/db/backup")
def db_backup():
    cfg = load_config()
    try:
        dest = backup_db(cfg.db_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    bdir = os.path.join(os.path.dirname(cfg.db_path) or ".", "backup")
    files = [os.path.basename(p) for p in sorted(_glob.glob(os.path.join(bdir, "spr-*.sqlite")))]
    return {"ok": bool(dest), "file": os.path.basename(dest) if dest else None, "backups": files}

@router.get("/settings")
def get_settings(request: Request):
    cfg = load_config()
    c = conn()
    last_date = _latest_date(c)
    return {
        "gemini_configured": bool(cfg.gemini_api_key),
        # 免費層是每日 × 每專案 × 每模型 20 次，撞上限前完全沒有跡象可查（實際發生過，
        # 網頁突然整段吐 429 原文）。只回次數，不回金鑰。
        "gemini_calls_today": ai_calls_today(c),
        "gemini_model": gemini.MODEL,
        "line_configured": bool(cfg.line_token),
        "line_webhook_configured": bool(cfg.line_secret),   # 只回布林，絕不外洩 secret
        "line_quota_paused": line_quota_paused(c),          # 本月 broadcast 額度已用盡（reply 不受影響）
        "telegram_configured": bool(cfg.telegram_token and cfg.telegram_chat_id),
        "offsite_backup_configured": bool(cfg.backup_git_remote),
        "weekly_push_time": cfg.weekly_push_time,
        "schedule_time": effective_schedule(c),
        "scheduler_running": bool(getattr(request.app.state, "scheduler", None)),
        "data_dir": effective_data_dir(c),
        "snapshots": len(get_snapshot_dates(c)),
        "tx_history_days": len(get_tx_history(c)),
        "last_market_date": last_date,
        "nav_order": (get_setting(c, "nav_order") or "").split(",") if get_setting(c, "nav_order") else None,
        "intraday_picks_only": get_setting(c, "intraday_picks_only") == "1",
        "loss_tolerance": int(get_setting(c, "loss_tolerance") or 0) or None,
    }

@router.get("/scoring-rules")
def get_scoring_rules():
    """木質/木率 的評分規則（唯讀，供設定頁檢視）。

    規則文字一律來自 analysis.py 的常數——單一權威版本，前端不得複製一份寫死
    （同 bands/Elliott 的規矩）。木質是「財報體質 × 籌碼」的自家版分數，木率是它的
    估值化；15 項財報評分屬 Stage 2（待接季報源）故標為未啟用。
    """
    return {
        "mu_score": {
            "name": "木質",
            "desc": "財報體質 × 本站籌碼；刻度 0–%d 分。財報是主幹、籌碼是傾斜。" % (15 + len(analysis.MU_CHIP_ITEMS)),
            "base": "財報分（目前用匯入的蘭質 0–15；Stage 2 換成自算的 15 項）",
            "chip_items": [{"key": k, "label": lbl} for k, lbl, _op in analysis.MU_CHIP_ITEMS],
            "max": 15 + len(analysis.MU_CHIP_ITEMS),
        },
        "mu_value": {
            "name": "木率",
            "formula": "木質 ÷ 本業PE × 100（沿用蘭值公式，只換品質分子）",
            "quality_floor": analysis.MU_QUALITY_FLOOR,
            "gate": "木質未達 %d 分時不給「便宜」分，避開價值陷阱（raw 仍保留）。"
                    % analysis.MU_QUALITY_FLOOR,
        },
        "lan_score": {
            "name": "蘭質（15 項財報評分）",
            "status": "pending_source",
            "note": "忠實還原蘭弦；Stage 2 接季報源後啟用，屆時取代木質的財報分並可回算比對 CSV。",
            "max": 15,
            "items": [{"id": i, "label": lbl, "points": p} for i, lbl, p in analysis.LAN_SCORE_ITEMS],
        },
    }

@router.get("/stage2-sources")
def get_stage2_sources():
    """Stage 2：脫離 XQ 的公開資料來源與計算方式（唯讀，供設定頁檢視）。

    三片皆已完成、公式皆用真實資料交叉驗證過（非猜測），但都刻意尚未接進
    filtered_picks——選股結果目前仍讀 XQ CSV 匯入值（並存對照，見 CLAUDE.md 對應段落）。
    門檻文字引用 analysis.py 的常數，不在此複製一份寫死（同 scoring-rules 的規矩）。
    """
    return {
        "wired_to_picks": False,
        "note": "以下三項與 XQ CSV 並存對照，目前選股結果（filtered_picks）仍讀 CSV 匯入值，尚未切換。",
        "items": [
            {
                "key": "revenue", "name": "月營收年增",
                "sources": ["TWSE OpenAPI（t187ap05_L，上市）", "TPEx OpenAPI（mopsfin_t187ap05_O，上櫃）"],
                "formula": "官方公布的「去年同月增減(%)」，逐檔逐月存進 stock_revenue_monthly"
                           "（歷史累積，端點本身沒有回溯查詢，只給當下最新已公告的月份）",
                "verified": "實測兩市場合計 1959 檔一次到位；2330 台積電年增 44.69% 與官方數字一致",
            },
            {
                "key": "w55", "name": "W55 翻多訊號",
                "sources": ["stock_ohlc（本站已累積的官方 TWSE/TPEx 日 K 線）"],
                "formula": f"PercentR(55) > {analysis.W55_THRESHOLD:g}（收盤站上近 55 天高低區間中點）",
                "verified": "8 檔真實股票用歷史股價反推 PercentR(55)，與同日 XQ CSV 的 W55 值 8/8 全部對上",
            },
            {
                "key": "custody", "name": "大戶增比／人數降比",
                "sources": ["TDCC 集保戶股權分散表（custody_dist，逐週累積）"],
                "formula": "大戶增比＝400張以上大戶持股比例週對週百分點差；"
                           "人數降比＝全體股東人數（非大戶人數）週對週相對變化%",
                "verified": "4 檔真實股票用歷史集保週資料驗證：人數降比 4/4 精確吻合，"
                            "大戶增比 3/4 精確吻合（1 檔差 0.01，官方顯示位數捨入誤差）",
            },
        ],
    }


@router.post("/settings")
def update_settings(request: Request, payload: dict = Body(...)):
    cfg = load_config()
    c = conn()
    st = payload.get("schedule_time")
    if st:
        set_setting(c, "schedule_time", str(st))
        sched = getattr(request.app.state, "scheduler", None)
        if sched:
            try:
                from ..scheduler import build_trigger_kwargs
                sched.reschedule_job("daily_update", trigger="cron", **build_trigger_kwargs(st))
            except Exception:  # noqa: BLE001
                pass
    dd = payload.get("data_dir")
    if dd:
        if _dir_within(str(dd), [REPO_DIR, cfg.data_dir]):
            set_setting(c, "data_dir", str(dd))
        else:
            return {"ok": False, "error": "資料夾不在允許範圍（僅限專案目錄下）", **get_settings(request)}
    if "intraday_picks_only" in payload:
        set_setting(c, "intraday_picks_only", "1" if payload["intraday_picks_only"] else "0")
    if "loss_tolerance" in payload:
        try:
            v = int(payload["loss_tolerance"] or 0)
        except (TypeError, ValueError):
            v = 0
        set_setting(c, "loss_tolerance", str(v) if v > 0 else "")
    no = payload.get("nav_order")
    if isinstance(no, list) and no:
        ids = [str(x) for x in no if str(x).isalnum()]
        if ids:
            set_setting(c, "nav_order", ",".join(ids))
    return {"ok": True, **get_settings(request)}

@router.post("/line/test")
def line_test():
    return _push_line(conn(), full=True, force=True)

@router.post("/intraday/test")
def intraday_test(push: int = 0):
    return _intraday_scan(conn(), push=bool(push))
