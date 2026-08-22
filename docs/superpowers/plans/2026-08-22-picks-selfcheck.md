# 選股自算對照（picks self-check）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一個只讀的「選股自算對照」頁，逐欄位比對 XQ CSV 匯入值 vs App 自算值，建立對零 CSV 選股的信心。

**Architecture:** 純函式 `selfcheck_compare`（容差判定，`analysis.py`）＋ 新模組 `selfcheck.py` 的組裝 `build_selfcheck(conn, date)`（重用既有 `revenue_yoy_map`/`custody_change_map`/`w55_signal`/`attach_mu`）＋ `api/admin.py` 的 `GET /api/picks/selfcheck`（`ai_cache` 快取）＋ 前端 command-bar 新頁。**只讀**：不改 `filtered_picks`、不寫 DB、無切換。

**Tech Stack:** Python 3.11 / FastAPI / stdlib sqlite3 / vanilla JS（無 build step）/ pytest / ECharts 不涉及。

## Global Constraints

- **只讀不變式**：本頁不寫任何 DB、不改 `filtered_picks`、不改任何選股結果、不加任何「改用自算」開關。
- **自算為 None ≠ 不一致**：資料未成熟/無來源時該欄回 `self_na`，前端標「尚無自算」，**絕不可**算成 `diff`。
- **容差是單一權威**：容差常數只寫在 `analysis.py`，經 API 揭露給前端唯讀；前端**不得**複製一份寫死（同 scoring-rules/bands 規矩）。
- **顏色語彙**：狀態記號**不用紅綠**（紅綠鎖給行情漲跌）；一致＝中性、有差異＝既有琥珀「注意這格」語彙。
- **差異走 hover**：cell 只放「自算值＋小狀態記號」，CSV-vs-自算 的細節放 `title`（hover）——cell 保持乾淨（使用者明確要求）。
- **前端不變式**：事件委派（無 inline `on*=`）；CSP `script-src 'self'`；狀態同時有文字與結構語意；觸控目標 ≥24px；無頁面級水平溢出（寬表在 `.table-wrap` 內橫捲、第一欄凍結）；命令列沿用既有 `.picks-command` class（規則集中、不新造版面系統）。
- **快取版本三處同步**：改 `web/app.js` 或 `web/index.html` 時 bump `?v=` 版本字串，並同步 `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py::test_public_overview_shares_internal_frontend`。目前版本 `20260817-ui13`。
- **測試紀律**：TDD（先寫失敗測試）；跑完整 `pytest -q` 前不得宣稱完成；**不可** `pytest | tail`（pipe 遮 exit code）；Windows CJK 輸出要寫 UTF-8 檔再讀。
- **commit 慣例**：結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`；**不要** `git add .`（有兩個未追蹤的 handoff 檔），一律明確列檔。

## 本案範圍界線（重要）

- **完整自算＋驗證**：`rev_yoy`（營收年增）、`w55`（W55）、`big_holder_ratio`（大戶增比）三欄——現在就有資料，逐檔算出並與 CSV 對照。
- **框架就位、值暫為「尚無自算」**：`est_profit`（推估EPS）、`mu_score`（木質）、`mu_value`（木率）三欄——這三欄的自算需要「6 個月月營收＋季報財務成熟」與「投信/外資三日自算來源（尚未做）」，且涉及未經真實資料驗證的單位換算。本案**不**實作它們的自算數值計算；改由組裝層回傳 `None` ＋ 明確原因字串，前端顯示「—」＋ hover 原因。它們的真正自算數值計算，留給資料成熟後的下一個 slice（見 spec「不在此案」）。

## 檔案結構

- Create `stocks_power_rich/selfcheck.py`：組裝層。`build_selfcheck(conn, date)` ＋ 私有 helper（`_latest_snap_date`、`_self_w55`、`_blocked` 原因常數）。import `db` 與 `analysis`（避免 `analysis`↔`db` 反向依賴，故不放 `analysis.py`）。
- Modify `stocks_power_rich/analysis.py`：新增 `SELFCHECK_TOL`、`SELFCHECK_REL` 常數與 `selfcheck_compare()` 純函式（接在 `estimate_quarterly_eps` 之後、木質/木率段之前）。
- Modify `stocks_power_rich/api/admin.py`：新增 `GET /api/picks/selfcheck` 端點（`ai_cache` 快取＋容差揭露）。
- Modify `web/index.html`：側欄「進階」加一個 `.nav`；新增 `#view-selfcheck` section；bump `?v=`。
- Modify `web/app.js`：`loadSelfcheck()` ＋ `showView` 掛載。
- Modify `web/styles.css`：狀態記號 `.sc-ok`/`.sc-diff`/`.sc-na` 極簡樣式。
- Modify `stocks_power_rich/api/public.py`、`tests/test_api.py`：版本字串同步。
- Create `tests/test_analysis_selfcheck.py`：`selfcheck_compare` 容差測試。
- Create `tests/test_selfcheck.py`：`build_selfcheck` 組裝測試。
- Modify `tests/test_api.py`：端點測試＋容差揭露測試。

---

### Task 1: `selfcheck_compare()` ＋ 容差常數（純函式）

**Files:**
- Modify: `stocks_power_rich/analysis.py`（接在 `estimate_quarterly_eps` 之後）
- Test: `tests/test_analysis_selfcheck.py`（新建）

**Interfaces:**
- Produces:
  - `analysis.SELFCHECK_TOL: dict[str, float]` = `{"rev_yoy":0.5, "big_holder_ratio":0.05, "mu_score":1.0, "mu_value":1.0}`
  - `analysis.SELFCHECK_REL: dict[str, float]` = `{"est_profit":0.05}`
  - `analysis.selfcheck_compare(field: str, csv_v, self_v) -> str`，回 `"match"|"diff"|"self_na"|"csv_na"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_selfcheck.py
from stocks_power_rich import analysis


def test_selfcheck_compare_absolute_tolerance():
    # rev_yoy 容差 ±0.5：差 0.4 → match、差 0.6 → diff
    assert analysis.selfcheck_compare("rev_yoy", 10.0, 10.4) == "match"
    assert analysis.selfcheck_compare("rev_yoy", 10.0, 10.6) == "diff"


def test_selfcheck_compare_w55_is_exact_binary():
    assert analysis.selfcheck_compare("w55", 1.0, 1.0) == "match"
    assert analysis.selfcheck_compare("w55", 1.0, 0.0) == "diff"


def test_selfcheck_compare_relative_tolerance_est_profit():
    # est_profit 相對 ±5%：|self-csv|/|csv| ≤ 0.05 → match
    assert analysis.selfcheck_compare("est_profit", 100.0, 104.0) == "match"
    assert analysis.selfcheck_compare("est_profit", 100.0, 106.0) == "diff"


def test_selfcheck_compare_self_none_is_self_na_not_diff():
    assert analysis.selfcheck_compare("rev_yoy", 10.0, None) == "self_na"
    assert analysis.selfcheck_compare("mu_score", 12.0, None) == "self_na"


def test_selfcheck_compare_csv_none_is_csv_na():
    assert analysis.selfcheck_compare("rev_yoy", None, 10.0) == "csv_na"
    assert analysis.selfcheck_compare("rev_yoy", None, None) == "csv_na"


def test_selfcheck_compare_mu_score_absolute_one():
    assert analysis.selfcheck_compare("mu_score", 12.0, 13.0) == "match"   # 差 1.0 = 邊界內
    assert analysis.selfcheck_compare("mu_score", 12.0, 13.01) == "diff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_selfcheck.py -q`
Expected: FAIL（`AttributeError: module ... has no attribute 'selfcheck_compare'`）

- [ ] **Step 3: Write minimal implementation**

在 `analysis.py` 的 `estimate_quarterly_eps` 函式後、`# ===... 木質 / 木率` 註解區塊前插入：

```python
# ======================================================================
# 選股自算對照（picks self-check）：CSV 匯入值 vs App 自算值 的逐欄容差判定。
# 容差是「規則的單一權威版本」——經 /api/picks/selfcheck 揭露給設定/對照頁唯讀，
# 前端不得複製一份寫死（同 scoring-rules/bands 的規矩）。
# ======================================================================
SELFCHECK_TOL = {          # 絕對容差
    "rev_yoy": 0.5,        # 百分點
    "big_holder_ratio": 0.05,  # 百分點
    "mu_score": 1.0,       # 木質 0–19 小整數刻度
    "mu_value": 1.0,       # 木率
}
SELFCHECK_REL = {"est_profit": 0.05}   # 相對容差 |self-csv|/|csv| ≤ 5%
# w55 不入表 → 完全相等才算一致


def selfcheck_compare(field: str, csv_v, self_v) -> str:
    """逐欄位比對 CSV 值與自算值 → "match" / "diff" / "self_na" / "csv_na"。

    自算值為 None（資料未成熟/無來源）→ "self_na"（前端標「尚無自算」，不算不一致）。
    CSV 值為 None（該檔 CSV 端也沒這欄）→ "csv_na"（無從比對）。
    w55 為二元、完全相等才 match；est_profit 走相對容差；其餘走絕對容差。
    """
    if self_v is None:
        return "self_na"
    if csv_v is None:
        return "csv_na"
    if field == "w55":
        return "match" if csv_v == self_v else "diff"
    if field in SELFCHECK_REL:
        denom = abs(csv_v)
        if denom == 0:
            return "match" if csv_v == self_v else "diff"
        return "match" if abs(self_v - csv_v) / denom <= SELFCHECK_REL[field] else "diff"
    tol = SELFCHECK_TOL.get(field)
    if tol is None:
        return "match" if csv_v == self_v else "diff"
    return "match" if abs(self_v - csv_v) <= tol else "diff"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_selfcheck.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add stocks_power_rich/analysis.py tests/test_analysis_selfcheck.py
git commit -m "feat: selfcheck_compare 容差判定純函式（選股自算對照）"
```

---

### Task 2: 組裝 `build_selfcheck(conn, date)`（新模組 selfcheck.py）

**Files:**
- Create: `stocks_power_rich/selfcheck.py`
- Test: `tests/test_selfcheck.py`（新建）

**Interfaces:**
- Consumes: `analysis.selfcheck_compare`（Task 1）；`db.revenue_yoy_map`、`db.custody_change_map`、`db.get_all_ohlc`；`analysis.w55_signal`。
- Produces: `selfcheck.build_selfcheck(conn, date: str | None) -> dict`，結構：
  ```python
  {
    "date": "2026-08-22",
    "dates": ["2026-08-22", ...],          # 可選的已匯入 snap_date（新到舊）
    "tolerances": {"SELFCHECK_TOL": {...}, "SELFCHECK_REL": {...}},
    "fields": ["rev_yoy", "w55", "big_holder_ratio", "est_profit", "mu_score", "mu_value"],
    "blocked_reason": {"est_profit": "...", "mu_score": "...", "mu_value": "..."},
    "rows": [ {"code","name","fields": {f: {"csv","self","status"}}} ],
    "coverage": {f: {"computable": int, "total": int, "median_abs_diff": float | None}},
  }
  ```
- `est_profit`/`mu_score`/`mu_value` 三欄本案 `self` 一律 `None`、`status` 為 `self_na`（見範圍界線）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_selfcheck.py
from datetime import date, timedelta
from stocks_power_rich import selfcheck
from stocks_power_rich.db import get_connection, init_db


def _seed(conn):
    # 一天 CSV 快照（chip_snapshot），兩檔。W55 用 60 根遞增 OHLC → 站上中點。
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,rev_yoy,w55,big_holder_ratio,"
                 "est_profit,lan_score,lpe) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-08-20", "2330", "台積電", 44.0, 1.0, 0.30, 22.0, 6.0, 56.0))
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,rev_yoy,w55,big_holder_ratio,"
                 "est_profit,lan_score,lpe) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-08-20", "1101", "台泥", -5.0, 0.0, -0.10, 1.0, 3.0, 40.0))
    ds = [(date(2026, 6, 1) + timedelta(days=n)).isoformat() for n in range(60)]
    for i, d in enumerate(ds):        # 2330 遞增 → %R(55) 高 → w55 self = 1
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('2330',?,?,?,?)",
                     (d, 100 + i, 99 + i, 100 + i))
    for i, d in enumerate(ds):        # 1101 遞減 → %R(55) 低 → w55 self = 0
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('1101',?,?,?,?)",
                     (d, 200 - i, 199 - i, 200 - i))
    conn.commit()


def test_build_selfcheck_live_fields_and_blocked_fields(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed(conn)
    out = selfcheck.build_selfcheck(conn, "2026-08-20")

    assert out["date"] == "2026-08-20"
    assert out["fields"] == ["rev_yoy", "w55", "big_holder_ratio", "est_profit", "mu_score", "mu_value"]
    by_code = {r["code"]: r for r in out["rows"]}

    # rev_yoy：自算來自 revenue_yoy_map（本測試沒建月營收 → self None → self_na），不炸
    assert by_code["2330"]["fields"]["rev_yoy"]["status"] in ("match", "diff", "self_na")

    # w55：2330 遞增 → self=1、與 CSV(1) 一致；1101 遞減 → self=0、與 CSV(0) 一致
    assert by_code["2330"]["fields"]["w55"]["self"] == 1.0
    assert by_code["2330"]["fields"]["w55"]["status"] == "match"
    assert by_code["1101"]["fields"]["w55"]["self"] == 0.0
    assert by_code["1101"]["fields"]["w55"]["status"] == "match"

    # 三個 blocked 欄：self 恆 None、status 恆 self_na（本案不自算）
    for f in ("est_profit", "mu_score", "mu_value"):
        assert by_code["2330"]["fields"][f]["self"] is None
        assert by_code["2330"]["fields"][f]["status"] == "self_na"
        assert f in out["blocked_reason"]

    # 容差揭露來自 analysis 常數（防前端另寫一份）
    from stocks_power_rich import analysis
    assert out["tolerances"]["SELFCHECK_TOL"] == analysis.SELFCHECK_TOL

    # coverage：w55 兩檔皆可自算
    assert out["coverage"]["w55"]["computable"] == 2
    assert out["coverage"]["w55"]["total"] == 2


def test_build_selfcheck_defaults_to_latest_snap_date(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name) VALUES('2026-08-21','2317','鴻海')")
    conn.commit()
    out = selfcheck.build_selfcheck(conn, None)     # 不帶 date → 最新
    assert out["date"] == "2026-08-21"
    assert out["dates"][0] == "2026-08-21"           # 新到舊
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_selfcheck.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'stocks_power_rich.selfcheck'`）

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/selfcheck.py
"""選股自算對照組裝：逐欄位比對 CSV 匯入值 vs App 自算值。只讀、不改 filtered_picks。

三個「已成熟」欄（rev_yoy/w55/big_holder_ratio）逐檔自算並與 CSV 對照；三個「待資料」欄
（est_profit/mu_score/mu_value）本案不自算、一律回 None＋原因（見 blocked_reason），前端顯示
「尚無自算」——正好把「要全放自己的還缺什麼」顯示出來。放獨立模組（import db + analysis）
避免 analysis↔db 反向依賴。
"""
import statistics

from . import analysis, db

FIELDS = ["rev_yoy", "w55", "big_holder_ratio", "est_profit", "mu_score", "mu_value"]
LIVE_FIELDS = ["rev_yoy", "w55", "big_holder_ratio"]
BLOCKED_REASON = {
    "est_profit": "需 6 個月月營收累積（尚在累積中）",
    "mu_score": "需季報財務成熟 ＋ 投信/外資三日自算來源（尚未建立）",
    "mu_value": "同木質，另需自算本業PE",
}


def _snap_dates(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT snap_date FROM chip_snapshot ORDER BY snap_date DESC")]


def _self_w55(ohlc_code: dict | None, as_of: str):
    """該檔 OHLC 切到 as_of（含）之前，取序列餵 w55_signal；不足或無資料回 None。"""
    if not ohlc_code:
        return None
    ds, hs, ls, cs = ohlc_code["dates"], ohlc_code["highs"], ohlc_code["lows"], ohlc_code["closes"]
    hi, lo, cl = [], [], []
    for i, d in enumerate(ds):
        if d <= as_of:
            hi.append(hs[i]); lo.append(ls[i]); cl.append(cs[i])
    return analysis.w55_signal(hi, lo, cl)


def build_selfcheck(conn, date: str | None) -> dict:
    dates = _snap_dates(conn)
    if date is None:
        date = dates[0] if dates else None
    rows_csv = conn.execute(
        "SELECT code, name, rev_yoy, w55, big_holder_ratio, est_profit, mu_score_placeholder "
        "FROM (SELECT code, name, rev_yoy, w55, big_holder_ratio, est_profit, NULL "
        "AS mu_score_placeholder FROM chip_snapshot WHERE snap_date=?) ORDER BY code",
        (date,)).fetchall() if date else []

    yoy = db.revenue_yoy_map(conn, as_of=date) if date else {}
    custody = db.custody_change_map(conn, as_of=date) if date else {}
    ohlc = db.get_all_ohlc(conn, min_bars=55)

    out_rows = []
    for code, name, csv_yoy, csv_w55, csv_bhr, csv_est, _ in rows_csv:
        self_yoy = yoy.get(code)
        self_w55 = _self_w55(ohlc.get(code), date)
        self_bhr = (custody.get(code) or {}).get("big_holder_ratio")
        vals = {
            "rev_yoy": (csv_yoy, self_yoy),
            "w55": (csv_w55, self_w55),
            "big_holder_ratio": (csv_bhr, self_bhr),
            "est_profit": (csv_est, None),      # blocked（本案不自算）
            "mu_score": (None, None),           # blocked
            "mu_value": (None, None),           # blocked
        }
        fields = {}
        for f, (cv, sv) in vals.items():
            fields[f] = {"csv": cv, "self": sv, "status": analysis.selfcheck_compare(f, cv, sv)}
        out_rows.append({"code": code, "name": name, "fields": fields})

    coverage = {}
    for f in FIELDS:
        diffs, computable = [], 0
        for r in out_rows:
            cell = r["fields"][f]
            if cell["self"] is not None:
                computable += 1
                if cell["csv"] is not None and f != "w55":
                    diffs.append(abs(cell["self"] - cell["csv"]))
        coverage[f] = {"computable": computable, "total": len(out_rows),
                       "median_abs_diff": round(statistics.median(diffs), 4) if diffs else None}

    return {
        "date": date, "dates": dates, "fields": FIELDS,
        "blocked_reason": dict(BLOCKED_REASON),
        "tolerances": {"SELFCHECK_TOL": analysis.SELFCHECK_TOL, "SELFCHECK_REL": analysis.SELFCHECK_REL},
        "rows": out_rows, "coverage": coverage,
    }
```

> 註：上面 `SELECT ... mu_score_placeholder` 的子查詢只是為了讓欄位數固定、可讀；實作時若
> `chip_snapshot` 無 `est_profit` 以外欄位問題可直接 `SELECT code,name,rev_yoy,w55,big_holder_ratio,est_profit FROM chip_snapshot WHERE snap_date=? ORDER BY code`。以實際 schema 為準（`CHIP_COLS` 有這些欄）。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_selfcheck.py -q`
Expected: PASS（2 passed）。若 `chip_snapshot` INSERT 欄位對不上，改用實際 `CHIP_COLS` 欄位名。

- [ ] **Step 5: Commit**

```bash
git add stocks_power_rich/selfcheck.py tests/test_selfcheck.py
git commit -m "feat: build_selfcheck 組裝（rev_yoy/w55/大戶增比自算＋三欄待資料）"
```

---

### Task 3: 端點 `GET /api/picks/selfcheck`（ai_cache 快取）

**Files:**
- Modify: `stocks_power_rich/api/admin.py`（接在 `/financials/backfill-report` 之後）
- Test: `tests/test_api.py`（新增兩個測試）

**Interfaces:**
- Consumes: `selfcheck.build_selfcheck`（Task 2）；`api.deps.conn`；`db.get_ai_cache`/`set_ai_cache`（既有 per-key cache）。
- Produces: `GET /api/picks/selfcheck?date=` → `build_selfcheck` 的 dict（快取命中直接回）。

- [ ] **Step 1: Write the failing test**

```python
# 附加到 tests/test_api.py
def test_picks_selfcheck_endpoint(tmp_path, monkeypatch):
    import stocks_power_rich.selfcheck as sc
    monkeypatch.setattr(sc, "build_selfcheck",
                        lambda conn, date: {"date": date or "2026-08-20", "fields": sc.FIELDS,
                                            "rows": [], "coverage": {}, "dates": ["2026-08-20"],
                                            "blocked_reason": {}, "tolerances": {}})
    client = _client(tmp_path)   # 既有 helper：帶 SPR_DB_PATH 的 TestClient
    r = client.get("/api/picks/selfcheck?date=2026-08-20")
    assert r.status_code == 200
    assert r.json()["date"] == "2026-08-20"
    assert r.json()["fields"] == sc.FIELDS


def test_picks_selfcheck_tolerances_come_from_analysis(tmp_path):
    from stocks_power_rich import analysis
    client = _client(tmp_path)
    r = client.get("/api/picks/selfcheck")
    assert r.status_code == 200
    assert r.json()["tolerances"]["SELFCHECK_TOL"] == analysis.SELFCHECK_TOL
```

> `_client` 用 `tests/test_api.py` 既有的 TestClient 建法（`SPR_DB_PATH`=tmp 檔）；若既有 helper 名稱不同，沿用該檔慣例。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_api.py::test_picks_selfcheck_endpoint -q`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: Write minimal implementation**

在 `api/admin.py` import 區加 `from .. import selfcheck`（若尚未 import `db` 的 cache helper 也一併加），並在 `/financials/backfill-report` 端點之後加：

```python
@router.get("/picks/selfcheck")
def picks_selfcheck(date: str = ""):
    """選股自算對照：逐欄位 CSV 值 vs 自算值。只讀。W55 逐檔掃 OHLC 較重 → ai_cache 快取。

    不帶 date → 用最新 snap_date（由 build_selfcheck 決定）。快取鍵含 date 與內容雜湊，
    資料沒變直接回快取；本頁不寫任何業務資料，只寫這個計算快取。
    """
    c = conn()
    d = date or None
    result = selfcheck.build_selfcheck(c, d)
    # 以「實際採用的 date」＋ rows 長度＋ coverage 當輕量版本；資料變了鍵就變。
    from ..db import get_ai_cache, set_ai_cache
    ver = f"{result.get('date')}:{len(result.get('rows', []))}"
    key = f"selfcheck:{ver}"
    cached = get_ai_cache(c, key)
    if cached is not None:
        return cached
    set_ai_cache(c, key, result)
    return result
```

> 註：`build_selfcheck` 本身已算完才決定 date/rows，所以這裡的「快取」主要吸收同一 date 的重複請求；若實測單次 `build_selfcheck` 偏久（W55 全市場），下一輪把「先查快取鍵、miss 才 build」改成以 `date`＋`coverage data_version` 為鍵並在 build 前判斷（同 stock_flow research 的 data_version 作法）。先求正確與只讀，效能為次階段。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest tests/test_api.py::test_picks_selfcheck_endpoint tests/test_api.py::test_picks_selfcheck_tolerances_come_from_analysis -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add stocks_power_rich/api/admin.py tests/test_api.py
git commit -m "feat: GET /api/picks/selfcheck 端點（ai_cache 快取＋容差揭露）"
```

---

### Task 4: 前端「選股自算對照」頁（進階群組）

**Files:**
- Modify: `web/index.html`（側欄「進階」加 nav；新 `#view-selfcheck`；bump `?v=`）
- Modify: `web/app.js`（`loadSelfcheck()` ＋ `showView` 掛載）
- Modify: `web/styles.css`（狀態記號極簡樣式）
- Modify: `stocks_power_rich/api/public.py`、`tests/test_api.py`（版本字串同步）

**Interfaces:**
- Consumes: `GET /api/picks/selfcheck?date=`（Task 3）。

- [ ] **Step 1: 側欄 nav（進階群組）＋ section**

在 `web/index.html` 的「進階」`.nav-group` 內（跨週/AI 那組）加一個 nav（沿用既有 `.nav`＋`data-short`），並在某個 view 區塊後新增：

```html
<section id="view-selfcheck" class="view picks-view">
  <div class="picks-command">
    <div class="picks-command-copy">
      <div class="picks-title-row"><h2>選股自算對照</h2></div>
      <p>逐欄位比對「CSV 匯入值」與「App 自算值」，驗證零 CSV 選股的可信度；差異滑鼠移過去看細節。此頁只讀，不影響選股。</p>
    </div>
    <div class="picks-actions toolbar" aria-label="對照設定">
      <label class="date-pick">基準日期 <select id="selfcheck-date"></select></label>
    </div>
    <div class="picks-command-foot">
      <div id="selfcheck-coverage" class="picks-summary" aria-label="覆蓋率" aria-live="polite"></div>
    </div>
  </div>
  <div id="selfcheck-table" class="table-wrap fill"><div class="table-empty">載入中…</div></div>
</section>
```

- [ ] **Step 2: `loadSelfcheck()` ＋ 掛載**

在 `web/app.js` 的 `showView` 中，比照既有慣例加：`if (name === "selfcheck") loadSelfcheck();`。新增函式（欄位值＋小狀態記號，差異走 `title` hover；委派、無 inline handler；日期下拉 change 重載）：

```javascript
const SC_FIELDS = [
  ["rev_yoy", "營收年增"], ["w55", "W55"], ["big_holder_ratio", "大戶增比"],
  ["est_profit", "推估EPS"], ["mu_score", "木質"], ["mu_value", "木率"],
];
const SC_MARK = { match: ["✓", "sc-ok"], diff: ["~", "sc-diff"], self_na: ["—", "sc-na"], csv_na: ["·", "sc-na"] };
let scLoaded = false, scBlocked = {};

async function loadSelfcheck() {
  const el = $("selfcheck-table"); if (!el) return;
  const dsel = $("selfcheck-date");
  try {
    const d = await getJSON(`/api/picks/selfcheck${dsel.value ? "?date=" + dsel.value : ""}`);
    scBlocked = d.blocked_reason || {};
    if (!dsel.dataset.filled) {
      dsel.innerHTML = (d.dates || []).map((x) => `<option>${x}</option>`).join("");
      dsel.value = d.date; dsel.dataset.filled = "1";
    }
    // 覆蓋率摘要
    $("selfcheck-coverage").innerHTML = SC_FIELDS.map(([k, label]) => {
      const c = (d.coverage || {})[k] || {};
      const md = c.median_abs_diff == null ? "" : `・中位差 ${fmt(c.median_abs_diff, 2)}`;
      return `<span>${label} <b>${c.computable || 0}/${c.total || 0}</b>${md}</span>`;
    }).join("");
    if (!d.rows || !d.rows.length) {
      el.innerHTML = '<div class="table-empty"><strong>尚無資料</strong><span>選一個已匯入 CSV 的日期；或先在「籌碼／基本選股」上傳當日檔。</span></div>';
      return;
    }
    const head = "<tr><th>股票</th>" + SC_FIELDS.map(([, l]) => `<th class="num">${l}</th>`).join("") + "</tr>";
    const body = d.rows.map((r) => "<tr><td>" + stockLink(r.code, r.name) + "</td>" +
      SC_FIELDS.map(([k]) => {
        const cell = r.fields[k] || {}; const [glyph, cls] = SC_MARK[cell.status] || SC_MARK.csv_na;
        const selfTxt = cell.self == null ? "—" : fmt(cell.self, 2);
        let tip;
        if (cell.status === "self_na") tip = scBlocked[k] || "尚無自算資料";
        else tip = `CSV: ${cell.csv == null ? "—" : fmt(cell.csv, 2)} ／ 自算: ${selfTxt}` +
          (cell.csv != null && cell.self != null ? ` ／ 差 ${fmt(cell.self - cell.csv, 2)}` : "");
        return `<td class="num" title="${esc(tip)}">${selfTxt} <span class="sc-badge ${cls}">${glyph}</span></td>`;
      }).join("") + "</tr>").join("");
    el.innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
  } catch (e) {
    el.innerHTML = '<div class="table-empty"><strong>載入失敗</strong><span>' + esc(e.message) + "</span></div>";
  }
}
```

並在既有委派區加日期下拉監聽（比照其他 change 監聽）：
```javascript
$("selfcheck-date").addEventListener("change", loadSelfcheck);
```

- [ ] **Step 3: 狀態記號樣式（styles.css）**

```css
/* 選股自算對照的狀態記號：不用紅綠（鎖給行情）；一致＝中性、有差異＝既有琥珀語彙 */
.sc-badge { display:inline-block; min-width:1.2em; text-align:center; font-weight:700; }
.sc-ok  { color: var(--muted); }
.sc-diff{ color: #e0a23c; }         /* 琥珀＝注意這格 */
.sc-na  { color: var(--border-strong); }
```

- [ ] **Step 4: bump 版本三處**

把 `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py` 的 `20260817-ui13` 全改為 `20260817-ui14`（`replace_all`）。

- [ ] **Step 5: 本機驗證（瀏覽器）**

啟本機 server，切到「選股自算對照」頁，確認：
- command bar h2「選股自算對照」、日期下拉有值、覆蓋率摘要出現；
- 表格一列一檔、每格有值＋記號，滑過去 `title` 顯示 CSV/自算/差；
- 三個 blocked 欄顯示「—」＋ hover 顯示原因；
- 1280 / 390 皆無頁面級水平溢出、股票欄凍結、0 console error；
- `node --check web/app.js` 通過。

- [ ] **Step 6: 跑完整測試 + Commit**

Run: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`
Expected: 全數 passed（含更新的 `test_public_overview_shares_internal_frontend`）。

```bash
git add web/index.html web/app.js web/styles.css stocks_power_rich/api/public.py tests/test_api.py
git commit -m "feat: 選股自算對照頁（進階群組，command-bar＋逐欄 hover 差異）"
```

---

## Self-Review

- **Spec coverage**：只讀不變式（Global Constraints＋Task 範圍界線）✅；6 欄（Task 2 FIELDS）✅；
  基準日期可選（Task 2 `_snap_dates`＋Task 4 下拉）✅；容差具名常數＋API 揭露＋防前端複製
  （Task 1＋Task 3 測試）✅；hover 差異（Task 4 `title`）✅；覆蓋率摘要（Task 2 coverage＋Task 4）✅；
  木質/木率取代蘭值（FIELDS 無 lan_value、有 mu_score/mu_value）✅；「還缺哪一片」透明化
  （blocked_reason 文字）✅；快取（Task 3 ai_cache）✅；命令列標準＋前端不變式（Task 4）✅。
- **與 spec 的差異（已在計畫開頭「範圍界線」明列並對使用者揭露）**：est_profit/mu_score/mu_value
  的**自算數值計算本案不做**（回 None＋原因），只交付框架與三個現可對的欄。spec 原寫「wire 這三欄」，
  本計畫因「單位換算未經真實資料驗證＋投信/外資三日無自算來源」而降級為 blocked＋原因——這是
  YAGNI/正確性取捨，執行前已請使用者確認。
- **Placeholder scan**：無 TBD/TODO；每個 code step 有實際程式碼。
- **Type consistency**：`selfcheck_compare(field, csv_v, self_v)->str` 於 Task 1 定義、Task 2/3 一致使用；
  `build_selfcheck(conn, date)->dict` 結構於 Task 2 定義、Task 3/4 一致消費；`SELFCHECK_TOL`/`SELFCHECK_REL`
  於 Task 1 定義、Task 2/3 引用一致。
