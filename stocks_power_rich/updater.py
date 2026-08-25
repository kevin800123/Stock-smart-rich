"""一鍵更新協調者：依序抓 TWSE → TAIFEX → 國際指數，寫入 market_daily。

容錯：每個來源獨立 try/except，單一來源失敗只記錄，不影響其餘；
回傳 {date, success: [...], failed: [{source, name, error}]}。
"""
import threading
import time
from datetime import date as _date, datetime, timedelta

from .db import (
    bulk_upsert_custody,
    bulk_upsert_financials,
    bulk_upsert_ohlc,
    bulk_upsert_revenue,
    custody_week_exists,
    get_setting,
    latest_custody_week,
    ohlc_dates,
    set_setting,
    upsert_market_daily,
    upsert_tx_history,
)
from . import analysis, stock_flow
from .sources import financials, fred, intl, nasdaq, revenue, taifex, tdcc, tpex, twse


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


def _financials_universe(conn) -> list:
    """季報回補的代號母體：優先用月營收表（bare code、上市櫃全含、與 mopsfin 一致），
    退回籌碼快照（去掉 .TW/.TWO 後綴）。回排序後的清單，讓分批順序穩定、可續傳。"""
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM stock_revenue_monthly").fetchall()]
    if not codes:
        codes = [str(r[0]).split(".")[0] for r in conn.execute(
            "SELECT DISTINCT code FROM chip_snapshot").fetchall() if r[0]]
    return sorted(set(codes))


def _financials_incomplete(conn, universe: list) -> list:
    """母體中「還缺至少一個 RATIO_ITEMS 指標」的代號（維持母體順序，分批穩定）。

    以「缺任一指標」判定 pending 而非「有任一列就算完成」——某指標暫時性回空時，該代號會
    留在 pending、下次呼叫再補，避免把暫時失敗永久化（本 codebase 一貫的教訓）。代價是
    金融業本就沒有的指標（如銀行的存貨週轉率）會讓那些代號永遠 pending，故端點契約是
    「重複呼叫直到 remaining 不再下降」而非「直到 0」（同 chips/margin 回補的既有慣例）。
    """
    n_ind = len(financials.RATIO_ITEMS)
    have = {code: cnt for code, cnt in conn.execute(
        "SELECT code, COUNT(DISTINCT indicator) FROM stock_financials GROUP BY code")}
    return [c for c in universe if have.get(c, 0) < n_ind]


def backfill_financials(conn, max_batches: int = 4, batch_size: int = 50) -> dict:
    """全市場逐檔季報財務回補（sub-task 1 的 8 個乾淨 JSON 指標）。

    母體＝_financials_universe；pending＝缺任一指標的代號（見 _financials_incomplete）。
    一次處理 max_batches 批、每批 batch_size 檔，每批對 RATIO_ITEMS 每個指標各打一次
    mopsfin（一次帶整批代號）；請求間 sleep 0.2 秒禮貌節流（實測 8 連打偶爾有單筆暫時性
    回空）。**重複呼叫直到 remaining 不再下降**（非直到 0——金融業缺週轉率等指標的代號會
    永遠 pending，屬預期）。

    mopsfin 一次可帶多代號（實測 50 檔一次到位），故全市場約 (1900/50)×8 ≈ 300 個請求；
    財報一季才更新一次，屬偶爾手動觸發的回補、不進每日 run_update。
    """
    universe = _financials_universe(conn)
    pending = _financials_incomplete(conn, universe)

    filled = 0
    for b in range(max(1, max_batches)):
        batch = pending[b * batch_size:(b + 1) * batch_size]
        if not batch:
            break
        for indicator in financials.RATIO_ITEMS:
            try:
                by_code = financials.fetch_financial_ratio(batch, indicator)
            except Exception:  # noqa: BLE001 — 單一指標失敗不影響其餘
                by_code = {}
            if by_code:
                bulk_upsert_financials(conn, indicator, by_code)
            time.sleep(0.2)  # 禮貌節流，降低連打造成的暫時性回空
        filled += len(batch)

    remaining = len(_financials_incomplete(conn, universe))
    return {"filled": filled, "remaining": remaining, "universe": len(universe)}


# sub-task 2：完整報表（比率清單沒有的原始科目，見 sources/financials.py::REPORT_ITEMS）。
# 深度取自各指標實際需要的季數上限：pretax_income 2 季／opex,income_tax 4 季／capex 8 季
# （對照 analysis._LAN_USED 與 Call_LE 的 highest(...,4)），同一報表只抓一次滿足其下所有指標。
_REPORT_DEPTH = {"IncomeStatement": 4, "CashflowStatement": 8}

# mopsfin 完整報表端點本身慢（實測單次 ~6 秒）且**在密集請求下會退化/限流**（本機連打
# 12 次總時間 >120 秒、每次爬升到 >10 秒，Zeabur 上更會拖到 30 秒逾時而近乎停住）。放慢
# 請求間隔反而更快——讓端點維持 ~6 秒基準、穩定前進不卡死（放慢節奏總吞吐大於狂打踩限流）。
_REPORT_THROTTLE = 1.0


def _default_report_anchor() -> tuple:
    """預設從「最近一個已結束的日曆季」往回抓——本季幾乎必然還沒公布（見 financials.py 的
    ys 說明：45 天申報期限，實測 2026Q2 在 8/12 仍查無資料）。鍵off 真實日曆 datetime.now()
    （同 run_update「刪未來列」的既有規矩，不受抓到的資料日期影響）。
    """
    today = datetime.now()
    cur_season = (today.month - 1) // 3 + 1
    year, season = today.year, cur_season - 1
    if season == 0:
        season, year = 4, year - 1
    return year, season


def _report_pending_codes(conn, universe: list) -> list:
    n_items = len(financials.REPORT_ITEMS)
    placeholders = ",".join("?" * n_items)
    have = dict(conn.execute(
        f"SELECT code, COUNT(DISTINCT indicator) FROM stock_financials "
        f"WHERE indicator IN ({placeholders}) GROUP BY code",
        list(financials.REPORT_ITEMS),
    ).fetchall())
    return [c for c in universe if have.get(c, 0) < n_items]


def backfill_report_financials(conn, anchor_year: int | None = None, anchor_season: int | None = None,
                               max_batches: int = 4, batch_size: int = 30) -> dict:
    """全市場逐檔完整報表回補（sub-task 2：pretax_income／opex／income_tax／capex）。

    母體與 pending 判定同 backfill_financials（缺任一指標仍算 pending）。每批代號依報表分組
    抓取（IncomeStatement 一次滿足 pretax_income/opex/income_tax，CashflowStatement 滿足
    capex），同一報表跨季重複呼叫，**先收集完整季度序列再一次性反推單季**
    （`financials.decumulate_quarterly`）——不能季度各自獨立處理，因為反推需要「上一季」
    的累計值。**實際抓取深度是 `_REPORT_DEPTH+1`**：要交付 N 季可用的單季值，最舊那一季
    的單季值＝該季累計－上一季累計，若只抓 N 季，最舊一季缺「上一季」基準，own 值必為
    None 被濾掉，實測 8 季只換到 6 季能用；多抓一季墊底才能把 N 季全部填滿（墊底那季自己
    通常也順便可用，屬額外資料非缺陷）。最新一季若尚未公布（見 fetch_report 的 ys 說明），
    該季自然拿不到資料、略過，不落地假值、也不當機。
    """
    ay, aseason = (anchor_year, anchor_season) if anchor_year and anchor_season else _default_report_anchor()
    universe = _financials_universe(conn)
    pending = _report_pending_codes(conn, universe)

    filled = 0
    for b in range(max(1, max_batches)):
        batch = pending[b * batch_size:(b + 1) * batch_size]
        if not batch:
            break
        by_indicator = compute_report_indicators(batch, ay, aseason)
        for key, by_code in by_indicator.items():
            bulk_upsert_financials(conn, key, by_code)
        filled += len(batch)

    remaining = len(_report_pending_codes(conn, universe))
    return {"filled": filled, "remaining": remaining, "universe": len(universe)}


def compute_report_indicators(codes: list, anchor_year: int, anchor_season: int,
                              on_fetch=None) -> dict:
    """抓 `codes` 的完整報表並反推單季 → `{indicator: {code: {季別: 單季值}}}`（**不碰 DB**）。

    從 backfill_report_financials 的批次內聯邏輯抽出，讓「本機抓 → 匯入 production」的本機
    腳本能直接呼叫（Zeabur 打不動 mopsfin 報表端點，故在本機抓好再 POST 上雲）。抓取深度
    `depth+1`（墊底一季供反推）、「持續往回抓到蒐集滿」而非固定季數、got 只計真的抓到目標
    科目的季度——這些取捨與踩過的坑見 backfill_report_financials 的 docstring。

    `on_fetch(report, q, hit, secs)` 每抓完一季就回呼一次（供本機腳本印即時進度——一批
    要十幾次 fetch、每次數秒，沒有回呼的話整批跑完前看起來像凍住）。
    """
    raw: dict = {}  # (code, key) -> {季別: 累計值}
    for report, depth in _REPORT_DEPTH.items():
        got = 0
        y, s = anchor_year, anchor_season
        for _ in range(depth + 6):
            if got >= depth + 1:
                break
            q = f"{y}Q{s}"
            s -= 1
            if s == 0:
                s, y = 4, y - 1
            t0 = time.time()
            try:
                _actual_q, parsed = financials.fetch_report(codes, report, int(q[:4]), int(q[5]))
            except Exception:  # noqa: BLE001 — 單季失敗不影響其餘
                parsed = {}
            hit = False
            for code, labels in parsed.items():
                for key, (rep2, label) in financials.REPORT_ITEMS.items():
                    if rep2 == report and label in labels:
                        raw.setdefault((code, key), {})[q] = labels[label]
                        hit = True
            if on_fetch is not None:
                on_fetch(report, q, hit, time.time() - t0)
            if hit:
                got += 1
            time.sleep(_REPORT_THROTTLE)  # 放慢避免 mopsfin 報表端點在密集請求下退化

    by_indicator: dict = {}
    for (code, key), series in raw.items():
        decum = financials.decumulate_quarterly(series)
        vals = {q: v for q, v in decum.items() if v is not None}
        if vals:
            by_indicator.setdefault(key, {})[code] = vals
    return by_indicator


def backfill_report_financials_until_plateau(conn, anchor_year: int | None = None,
                                             anchor_season: int | None = None,
                                             chunk_batches: int = 6, batch_size: int = 30,
                                             max_rounds: int = 40, patience: int = 2,
                                             progress: dict | None = None) -> dict:
    """反覆呼叫 backfill_report_financials 直到 remaining 連續 `patience` 輪不再下降（或達
    max_rounds 上限）。

    完整報表回補是「重、逐季逐批打 mopsfin」的同步工作，放在請求裡會被 Zeabur 反向代理
    逾時砍成 502（同 stock-flow/research 的教訓）；由端點在背景執行緒呼叫這支跑到底、
    請求本身恆為毫秒級。`progress` 給的話每輪就地更新（filled 累計、remaining、universe、
    rounds、done），端點即時讀得到目前進度。

    **不可在「第一輪沒下降」就停**（patience≥2）：mopsfin 報表端點在密集請求下會退化，
    某一輪可能整輪回空、committed=0（實測 production：round1 只 committed 30/180、round2
    整輪 0 → 舊版單輪判定就誤判 done、remaining 卡在 1947）；那多半是暫時性退化而非真的
    沒資料可補，要連續數輪都沒進展才算到底。remaining 不會歸零也正常——金融業等本就缺
    現金流量表科目的代號會永遠 pending（見 backfill_report_financials）。"""
    filled, remaining, universe = 0, None, 0
    best = float("inf")
    rounds, stale = 0, 0
    for _ in range(max(1, max_rounds)):
        res = backfill_report_financials(conn, anchor_year=anchor_year, anchor_season=anchor_season,
                                         max_batches=chunk_batches, batch_size=batch_size)
        rounds += 1
        filled += res["filled"]
        remaining, universe = res["remaining"], res["universe"]
        if progress is not None:
            progress.update({"filled": filled, "remaining": remaining,
                             "universe": universe, "rounds": rounds, "done": False})
        if remaining < best:           # 有進展 → 重置耐心
            best, stale = remaining, 0
        else:
            stale += 1
            if stale >= max(1, patience):   # 連續 patience 輪沒下降 → 到底
                break
    if progress is not None:
        progress["done"] = True
    return {"filled": filled, "remaining": remaining, "universe": universe, "rounds": rounds}


def _refresh_monthly_revenue(conn) -> dict:
    """月營收（上市/上櫃），兩市場獨立抓取與寫入、互不影響。

    端點本身只給「當下最新已公告的月份」（見 sources/revenue.py），沒有歷史查詢，所以
    這裡每次呼叫都直接覆寫，不像 _accumulate_custody 那樣需要「偵測到新一週才抓」的節流
    ——同一個月被重複覆寫是無害的冪等操作，換來不必自己判斷「現在是否有新月份」。
    回傳 {"TWSE": 入庫檔數, "TPEx": 入庫檔數}，任一市場失敗記 0 而非讓例外中斷另一市場。
    """
    counts = {}
    for market, fetch in (("TWSE", revenue.fetch_twse_revenue), ("TPEx", revenue.fetch_otc_revenue)):
        try:
            rows = fetch()
            counts[market] = bulk_upsert_revenue(conn, market, rows) if rows else 0
        except Exception:  # noqa: BLE001 — 單一市場失敗不影響另一市場
            counts[market] = 0
    return counts


def _prev_calendar_month(today) -> tuple:
    """上一個日曆月 (西曆年, 月)；跨年邊界正確（1 月 → 去年 12 月）。"""
    y, m = today.year, today.month - 1
    if m == 0:
        m, y = 12, y - 1
    return y, m


def backfill_monthly_revenue_history(conn, months: int = 6, anchor: tuple | None = None) -> dict:
    """回補近 `months` 個月的全市場月營收（MOPS t21sc03，openapi 補不到的歷史月份）。

    openapi 只給「最新已公告月」，而 Call_LE 推估季EPS 需要「近3+前3」共 6 個月月營收，
    只靠每日累積要等半年。這條走 t21sc03 彙總報表可指定年月、一次整月全市場，直接補齊。
    anchor＝起算 (西曆年, 月)，預設上一個日曆月（本月營收多半尚未公告）；由此往回逐月抓
    上市＋上櫃，兩市場獨立、任一失敗或回空不影響其餘（同 _refresh_monthly_revenue 精神）。
    與 openapi 覆蓋到的最新月重疊時是冪等覆寫（revenue 同單位同值，僅 report_date 由精確
    出表日改為次月10日近似，對 report_date<=as_of 的判斷無影響）。

    重複呼叫是冪等；一季才需要一次、屬偶爾手動觸發的回補，不進每日 run_update。
    """
    ay, am = anchor if anchor else _prev_calendar_month(datetime.now())
    detail, filled = [], 0
    y, m = ay, am
    for _ in range(max(1, months)):
        roc = y - 1911
        ym = f"{y:04d}-{m:02d}"
        row = {"year_month": ym, "TWSE": 0, "TPEx": 0}
        for market, tag in (("twse", "TWSE"), ("otc", "TPEx")):
            try:
                rows = revenue.fetch_monthly_revenue_history(roc, m, market)
                n = bulk_upsert_revenue(conn, tag, rows) if rows else 0
            except Exception:  # noqa: BLE001 — 單一市場/月份失敗不影響其餘
                n = 0
            row[tag] = n
            filled += n
        detail.append(row)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return {"months": max(1, months), "filled": filled, "detail": detail}


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


def _maint(lots, shorts, closes, margin_value, prefix=""):
    """把「明細×收盤 → 維持率＋分子分母」收在一處，上市與上櫃共用。

    分子分母一併回傳（億），是為了讓卡片能把算式秀出來——維持率是個推導值，
    只給結果的話沒人能檢查它對不對。
    """
    mm = analysis.margin_maintenance(lots, closes, margin_value, shorts)
    if mm is None:
        return {}
    mv = sum(l * 1000 * closes[c] for c, l in lots.items() if c in closes and l)
    sv = sum(l * 1000 * closes[c] for c, l in (shorts or {}).items() if c in closes and l)
    return {f"{prefix}margin_maintenance": mm,
            f"{prefix}margin_mv": round(mv / 1e8, 1),
            f"{prefix}short_mv": round(sv / 1e8, 1)}


def _compute_margin_maintenance(D, margin_value, detail=None, quotes=None):
    """上市整戶擔保維持率＋分子分母。算不出回 {}。

    抽成函數是為了讓每日更新與 _heal_margin_maintenance 共用同一條計算路徑，
    否則兩邊各寫一份會漂移。
    """
    if not D or not margin_value:
        return {}
    detail = detail if detail is not None else twse.fetch_margin_detail(D)
    quotes = quotes if quotes is not None else twse.fetch_stock_quotes(D)
    closes = {c: q["close"] for c, q in quotes.items() if q.get("close")}
    return _maint(detail.get("margin", {}), detail.get("short"), closes, margin_value)


def _compute_otc_margin_maintenance(D, detail=None, quotes=None):
    """上櫃版。餘額與融資金額都在同一支櫃買端點，故連 otc_margin_value 一起回傳。

    上櫃融資成數 50%（上市 60%），損益兩平線因此是 200% 而非 166.7%——同一個數字
    在兩個市場意義不同，所以分開存、分開判讀，不併成單一「大盤」值。
    """
    if not D:
        return {}
    d = detail if detail is not None else tpex.fetch_otc_margin(D)
    if not d.get("value"):
        return {}
    quotes = quotes if quotes is not None else tpex.fetch_otc_quotes(D)
    closes = {c: q["close"] for c, q in quotes.items() if q.get("close")}
    out = _maint(d.get("margin", {}), d.get("short"), closes, d["value"], prefix="otc_")
    if not out:
        return {}
    out.update({"otc_margin_value": d["value"], "otc_margin_balance": d.get("balance"),
                "otc_short_balance": d.get("short_balance")})
    return out


def _heal_margin_maintenance(conn, days: int = 7, cap: int = 3) -> list:
    """回補近 days 天維持率仍缺的交易日（上市＋上櫃）。

    存在的理由：margin_value（官方融資金額）約 21:00 才公布，而更新可能跑在那之前
    （16:00 推播、白天開頁的 autoUpdate），此時維持率整段算不出來。margin_value 之後
    會被 _refresh_recent 補上，但維持率原本只在當次 run 算一次、不會回頭重算——
    於是「被依賴的欄位自癒了，依賴它的沒有」，45 天只有 7 天有值。
    每天數支全市場請求，故限 cap 天。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    pending = conn.execute(
        "SELECT date, margin_value, margin_mv, otc_margin_maintenance FROM market_daily "
        "WHERE date >= ? AND ((margin_value IS NOT NULL AND margin_mv IS NULL) "
        "                     OR otc_margin_maintenance IS NULL) ORDER BY date DESC",
        (cutoff,),
    ).fetchall()[:cap]
    filled = []
    for ds, mval, mmv, otc_mm in pending:
        patch = {}
        D = _iso_to_date(ds)
        if mval is not None and mmv is None:
            try:
                patch.update(_compute_margin_maintenance(D, mval))
            except Exception:  # noqa: BLE001 — 單邊失敗不影響另一邊
                pass
        if otc_mm is None:
            try:
                patch.update(_compute_otc_margin_maintenance(D))
            except Exception:  # noqa: BLE001
                pass
        if patch:
            upsert_market_daily(conn, {"date": ds, **patch})
            filled.append(ds)
    return filled


def _backfill_intl(conn, intl_tickers: dict, days: int = 10) -> list:
    """回補近 days 天內國際指數為空的欄位（只填 NULL，絕不覆蓋既有值）。

    治兩種缺口：新加入的代碼沒有歷史、以及 yfinance 偶發失敗留下的洞。
    對齊規則見 intl.pick_close_for／INTL_SAME_DAY：亞股取 D 當日收盤，其餘取 D 之前
    最近一場——台北 D 日晚間檢視時，美股 D 當日尚未開盤。

    只填 NULL 的用意：既有值是舊行為「抓取當下的最新值」產生的，語意與本函數的
    「場次收盤」不同；覆寫等於默默改寫歷史，寧可讓新舊並存且各自有明確出處。

    intl_tickers 為空時直接回傳——production 目前一定會剩 gold/jpy/twd/btc 這幾檔
    （sox/n225/kospi/vix 都各自被 Nasdaq／FRED／TradingView 頂替），但呼叫端傳一份
    更窄的 ticker dict 並非不可能（測試就踩過一次）：cols 為空字串會讓下面的 SQL
    變成 `SELECT date,  FROM ...`，語法錯誤。
    """
    keys = list(intl_tickers)
    if not keys:
        return []
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
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


# vix/n225 的**歷史**改走 FRED、sox 改走 Nasdaq 公開 API、kospi 沒有可靠的免費歷史源
# ——呼叫端把 intl_tickers 傳給 _backfill_intl 前先排除這四個 key，其餘（gold/jpy/btc/twd）
# 仍走原本的 yfinance 路徑不動，Yahoo 哪天解封就自己好。
# 注意這裡管的是**歷史**來源。「今天那一格」另由 _backfill_intl_tv 統一補（sox 也在內），
# 所以 sox 同時吃 Nasdaq（歷史）與 TradingView（當日），兩者都只填 NULL，不會打架。
INTL_NON_YFINANCE_KEYS = set(fred.FRED_SERIES) | {"kospi", "sox"}


def _backfill_intl_fred(conn, days: int = 10) -> list:
    """vix／n225 走 FRED（免金鑰、逐日附日期，見 sources/fred.py 開頭說明）。

    洞掃描與「只填 NULL、絕不覆蓋」的邏輯跟 _backfill_intl 一致，只是資料源換了；
    FRED 沒有 sox/kospi 的 series，本函數只處理 fred.FRED_SERIES 涵蓋的 key。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    keys = list(fred.FRED_SERIES)
    cols = ", ".join(keys)
    rows = conn.execute(
        f"SELECT date, {cols} FROM market_daily WHERE date >= ? ORDER BY date", (cutoff,),
    ).fetchall()
    holes = [r for r in rows if any(r[i + 1] is None for i in range(len(keys)))]
    if not holes:
        return []
    hist = {}
    for key, series_id in fred.FRED_SERIES.items():
        raw = fred.fetch_fred_series(series_id, start_date=cutoff)
        if raw:
            hist[key] = intl.parse_history_closes(sorted(raw.items()))
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


def _backfill_intl_nasdaq(conn, days: int = 10) -> list:
    """sox（費半）走 Nasdaq 官方公開歷史 API（免金鑰、不受 Yahoo 那個 IP 封鎖影響，
    見 sources/nasdaq.py 開頭說明）。洞掃描與「只填 NULL、絕不覆蓋」的邏輯跟
    _backfill_intl/_backfill_intl_fred 一致，只是資料源換了。"""
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT date, sox FROM market_daily WHERE date >= ? ORDER BY date", (cutoff,),
    ).fetchall()
    holes = [r for r in rows if r[1] is None]
    if not holes:
        return []
    raw = nasdaq.fetch_sox_history(days=days)
    if not raw:
        return []
    hist = intl.parse_history_closes(sorted(raw.items()))
    filled = []
    for r in holes:
        ds = r[0]
        got = intl.pick_close_for(hist, ds, same_day=False)
        if got:
            upsert_market_daily(conn, {"date": ds, "sox": got["value"], "sox_chg": got["chg_pct"]})
            filled.append(ds)
    return filled


def _backfill_intl_tv(conn, days: int = 10) -> list:
    """sox／vix／n225／kospi 走 TradingView 帶日期快照（intl.fetch_dated_closes）。

    **這條路徑專治「今天」那一格**，補不到歷史：scanner 只給「現在」，一次最多填一個場次。
    歷史仍靠 _backfill_intl（yfinance）與 _backfill_intl_fred，三者都只填 NULL 不覆蓋，
    所以誰先跑到都不會破壞彼此，順序不重要。之所以需要它：yfinance 被 Zeabur 出站 IP 擋死
    （sox 長期全 NULL），FRED 則慢一天（實測 08-05 當下 VIXCLS 只到 08-03、NIKKEI225
    只到 08-04），兩者都補不到當日。

    日期怎麼對到台股資料日，兩種 key 規則不同，理由同 intl.pick_close_for：
    - same_day（n225/kospi）：這一欄的定義就是「該市場在 D 當天的收盤」→ **只填 D == S**。
    - 其餘（sox/vix）：定義是「台北 D 日晚間可得的最近一場」→ 填**所有 D > S 的洞**。
      這是安全的：S 是「當下最後一個**已收盤**的場次」，所以 (S, D) 之間不可能存在另一個
      已收盤的場次，任何 D > S 的正確答案就是 S。連假／美股休市時多列共用同一個 S 是對的。
      反過來 D == S 的洞不會被填（那格要的是 S 的**前**一場，快照給不了）——寧可留 NULL。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    keys = list(intl.TV_DATED)
    rows = conn.execute(
        f"SELECT date, {', '.join(keys)} FROM market_daily WHERE date >= ? ORDER BY date",
        (cutoff,),
    ).fetchall()
    holes = {key: {r[0] for r in rows if r[i + 1] is None} for i, key in enumerate(keys)}
    want = [k for k in keys if holes[k]]
    if not want:
        return []
    patches: dict[str, dict] = {}
    for key, snap in intl.fetch_dated_closes(want).items():
        s = snap["date"]
        targets = ([s] if s in holes[key] else []) if key in intl.INTL_SAME_DAY \
            else sorted(d for d in holes[key] if d > s)
        for ds in targets:
            patches.setdefault(ds, {})[key] = snap["value"]
            patches[ds][key + "_chg"] = snap["chg_pct"]
    for ds, patch in sorted(patches.items()):
        upsert_market_daily(conn, {"date": ds, **patch})
    return sorted(patches)


def backfill_history(conn, days: int = 30, cap: int = 20) -> dict:
    """回補近 days 天的加權指數＋三大法人現貨買賣超＋融資融券（逐日，供雲端冷啟動補歷史）。

    逐日 upsert 且各自 commit，即使中途逾時，已處理日期也會保存，重跑可續補。
    期貨籌碼（未平倉/多空比）來源較慢，不在此整月回補，改由每日更新逐步累積。

    **月度錨點數由 days 推得，不可寫死。** 原本是 `for _ in range(3)`，days 只當過濾條件
    用，所以無論傳多大都只補到 3 個月前；加上端點當時把 days 夾在 60，傳 180 等於完全沒
    多補（實測回報 backfilled_days=41 卻一列都沒新增，因為 60 天前正好是既有資料的起點）。

    **指數/成交金額來自月度批次抓取（每月一個請求），法人與融資券則是逐日兩個請求**——
    後者是成本大頭，所以只對「還缺」的日期發請求，並以 cap 限制每次呼叫處理幾天，回傳
    remaining 讓呼叫端重複呼叫直到 0（比照 chips/margin 回補的慣例）。指數不受 cap 限制，
    窗口內每一列都會建好，否則對照圖的 K 線窗格會缺列。
    """
    cutoff = (_date.today() - timedelta(days=days)).isoformat()
    # 錨點＝每月最後一天往回走，直到覆蓋 cutoff 所在月份（月初往回一天即上月底）
    anchor, anchors = _date.today(), []
    while len(anchors) < 14:          # 14 個月保險：run_update 只保留近 400 天
        anchors.append(anchor)
        first = anchor.replace(day=1)
        if first.isoformat() <= cutoff:
            break
        anchor = first - timedelta(days=1)
    seen: dict = {}
    for a in anchors:
        for r in twse.fetch_taiex_history(a):
            if r["date"] >= cutoff:
                seen[r["date"]] = r
    # 指數/成交金額：無額外請求，窗口內全部建列
    for iso in sorted(seen):
        r = seen[iso]
        row = {"date": iso, "updated_at": datetime.now().isoformat()}
        for k in ("taiex", "taiex_chg", "turnover"):
            if r.get(k) is not None:
                row[k] = r[k]
        upsert_market_daily(conn, row)
    # 法人＋融資券：已經兩者都有的日期不必再打請求
    done = {r[0] for r in conn.execute(
        "SELECT date FROM market_daily WHERE date >= ? "
        "AND inst_foreign IS NOT NULL AND margin_balance IS NOT NULL", (cutoff,)).fetchall()}
    todo = [iso for iso in sorted(seen, reverse=True) if iso not in done]   # 由新到舊
    filled = 0
    for iso in todo[:cap]:
        d = _iso_to_date(iso)
        row = {"date": iso, "updated_at": datetime.now().isoformat()}
        for fetch in (lambda: twse.fetch_institutional(date=d), lambda: twse.fetch_margin(date=d)):
            try:
                row.update({k: v for k, v in fetch().items() if v is not None})
            except Exception:  # noqa: BLE001
                pass
        upsert_market_daily(conn, row)
        filled += 1
    return {"backfilled_days": filled, "remaining": max(0, len(todo) - filled)}


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

    daily_flow = {}
    try:
        if D:
            daily_flow = stock_flow.update_day(conn, D)
            success.append("stock_flow_daily")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "stock_flow", "name": "stock_flow_daily", "error": str(e)})

    # 大盤整戶擔保維持率（需融資金額＋個股融資融券明細＋全市場收盤；約 21:00 融資公布後才算得出）
    # 跑在 21:00 前時 margin_value 還沒公布，這裡算不出來——記進 failed 而非靜默跳過，
    # 否則「今天為什麼沒維持率」在更新結果裡完全看不出來。缺的那天由 _heal_margin_maintenance 補。
    try:
        if D and row.get("margin_value"):
            twse_daily = daily_flow.get("TWSE", {})
            mm = _compute_margin_maintenance(D, row["margin_value"],
                                             twse_daily.get("margin"),
                                             twse_daily.get("quotes"))
            if mm:
                row.update(mm)
                success.append("margin_maintenance")
            else:
                failed.append({"source": "twse", "name": "margin_maintenance",
                               "error": "明細或收盤不足，算不出維持率"})
        elif D:
            failed.append({"source": "twse", "name": "margin_maintenance",
                           "error": "融資金額尚未公布（約 21:00），稍後回補"})
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "margin_maintenance", "error": str(e)})

    # 上櫃維持率（櫃買同一支端點就給餘額與融資金額，不必等 TWSE）
    try:
        otc_daily = daily_flow.get("TPEx", {})
        otc = _compute_otc_margin_maintenance(D, otc_daily.get("margin"),
                                              otc_daily.get("quotes"))
        if otc:
            row.update(otc)
            success.append("otc_margin_maintenance")
        elif D:
            failed.append({"source": "tpex", "name": "otc_margin_maintenance",
                           "error": "上櫃融資餘額尚未發布，稍後回補"})
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "tpex", "name": "otc_margin_maintenance", "error": str(e)})

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
        yf_tickers = {k: v for k, v in intl_tickers.items() if k not in INTL_NON_YFINANCE_KEYS}
        filled = list(_backfill_intl(conn, yf_tickers))
        filled += _backfill_intl_fred(conn)
        filled += _backfill_intl_nasdaq(conn)
        filled += _backfill_intl_tv(conn)
        filled = sorted(set(filled))
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

    # 月營收：兩市場獨立抓取，任一失敗不影響另一（見 _refresh_monthly_revenue docstring）
    rev_counts = _refresh_monthly_revenue(conn)
    for market, n in rev_counts.items():
        if n:
            success.append(f"revenue_{market.lower()}:{n}")
        else:
            failed.append({"source": market.lower(), "name": "revenue", "error": "查無資料或抓取失敗"})

    # 台指期歷史日K（期交所官方下載），刷新近期
    try:
        tx_hist = taifex.fetch_tx_history(days=40)
        if tx_hist:
            upsert_tx_history(conn, tx_hist)
            success.append("taifex_tx_history")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "taifex", "name": "tx_history", "error": str(e)})

    return {"date": today, "success": success, "failed": failed}
