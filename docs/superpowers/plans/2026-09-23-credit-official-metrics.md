# 總覽融資融券改用證交所官方定義 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 總覽的融資維持率改用證交所公布的全市場「整戶擔保維持率」，新增追繳壓力／融資占市值／信用交易占比三張官方定義的卡，並把自算維持率整條路徑刪掉，讓本站與證交所儀表板的定義一致。

**Architecture:** 六個官方欄位（`keep_rate`／`below_call_acc`／`call_acc`／`exe_acc`／`credit_amt`／`market_value`）跟著資料日 D 存進 `market_daily`，來源是證交所 `BFIJ3U`（單日）與 `MI_MARGN_TREND`（上市總市值，60 天一次）；兩個衍生比率讀取時算、不落地。兩平線改成以當日上市／上櫃融資金額加權的融資成數推得、每天算、經 `bands` 送前端。所有消費端（卡片、操盤手檢核表、LINE、對照圖、公開總覽、Gemini 輸入）改讀官方值；`_compute_margin_maintenance` 那條自算路徑與其測試刪除。

**Tech Stack:** FastAPI、SQLite（stdlib `sqlite3`）、httpx、vanilla JS、ECharts、pytest。

Spec：`docs/superpowers/specs/2026-09-23-credit-official-metrics-design.md`。

## Global Constraints

- 資料源常數：`BFIJ3U_RWD = "https://www.twse.com.tw/rwd/zh/marginTrading/BFIJ3U"`、`MARGIN_TREND_RWD = ".../MI_MARGN_TREND"`、`MARGIN_HISTORY_RWD = ".../MI_MARGN_HISTORY"`、`CREDIT_SINCE = "2026-08-03"`（官方 08-03 起才有，之前的日期**不打**）。
- 單位：`credit_amt` 存**億**（`crdAmt/1e8`，2 位小數）；`market_value` 存**億**（TREND 的 `marketValue` 本身就是億）；`keep_rate` 是 %。
- 兩個衍生比率**不落地**：`margin_mcap_pct = margin_value / market_value × 100`；`credit_ratio = credit_amt / (2 × turnover) × 100`（**分母乘 2**，證交所 JS 原式）。任一輸入缺或分母為 0 → `None`。
- 加權融資成數：`(tse_value×0.6 + otc_value×0.5) ÷ (tse_value+otc_value)`；兩邊皆缺回 `None`，只缺一邊就用另一邊的成數。`bands["keep_rate"] = {"breakeven": margin_breakeven(加權成數), "call": 130.0}`，**每天依最新列算**。
- 當日 BFIJ3U 尚未產製時**不往回找**（資料日 D 紀律），記進 `failed`：`{"source": "twse", "name": "twse_credit", "error": "信用交易概況尚未公布，稍後回補"}`；`expected_later` 只對 `name == "twse_credit"` 且 error 含「尚未」放行。
- 回補只填 NULL、絕不覆蓋既有值（同 `_backfill_intl`）。
- 顏色：四張新卡全部**不著紅綠**；對照圖維持率窗格用 `C.info`，追繳線 `markLine` 用 `C.muted` 虛線。
- 台股大盤組卡片順序（10 張、5 欄）：外資／投信／自營／融資餘額(張)／融券餘額(張)／整戶擔保維持率／追繳壓力／融資占市值／信用交易占比／10 日均量。
- `.stat-board--tw` 5 欄；斷點 ≤1400px 4 欄、≤1240px 3 欄、≤1100px 2 欄；≤600px 既有 2 欄不變。
- 快取版號 `20260817-ui69` → `20260817-ui70`，四處：`web/index.html`×2、`stocks_power_rich/api/public.py`×2 行、`tests/test_api.py`×4。
- **保留**：`twse.parse_margin_detail`／`fetch_margin_detail`（`stock_flow.update_day` 仍用）、`market_daily` 舊欄位（不刪欄、不寫值）、`otc_margin_value`／`otc_margin_balance`／`otc_short_balance` 三欄**仍要每天寫**（加權兩平線需要 `otc_margin_value`）。
- 所有 repo 檔案 **CRLF**；改完用 bytes 計數確認「`\n` 數＝`\r\n` 數」。**不要用 `sed -i`**。新檔若是 LF，用 Python 轉成 CRLF。
- **pytest 不接管線**：`.venv\Scripts\python -m pytest ... > file 2>&1; echo EXIT=$?; tail -3 file`。長跑（整套約 8 分鐘）用 Bash 工具的 `run_in_background: true` 等通知，不要 shell `&`。
- Windows 終端機會吃 CJK：含中文的檢查輸出寫 UTF-8 檔再讀。
- 提交訊息結尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。**不要 push**。工作分支 `credit-official-metrics`。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `stocks_power_rich/sources/twse.py` | 三支純解析（`parse_credit_summary`／`parse_margin_trend`／`parse_margin_history`）＋三支 thin fetch；常數 |
| `stocks_power_rich/db.py` | `MARKET_COLS` 加六欄、舊欄位加註 |
| `stocks_power_rich/analysis.py` | `credit_ratios()` 純函式；刪 `margin_maintenance()` |
| `stocks_power_rich/ss_trader.py` | `blended_margin_ratio()`；`market_checklist` 維持率兩項併一項 |
| `stocks_power_rich/updater.py` | `run_update` 抓 `twse_credit`、`_otc_margin_summary`、`_backfill_credit`、`_refresh_credit_history`；刪 `_maint`／`_compute_*`／`_heal_margin_maintenance` |
| `stocks_power_rich/api/market.py` | `dashboard()` 逐列注入比率、動態 `bands`、`credit_history`；Gemini 輸入鍵名 |
| `stocks_power_rich/api/admin.py` | 刪 `/margin-maintenance/heal`；新增 `/credit/backfill` |
| `stocks_power_rich/api/helpers.py` | `expected_later` 名單 |
| `stocks_power_rich/api/public.py` | 公開總覽 `margin.keep_rate`；版號 |
| `stocks_power_rich/line_push.py` | 純文字與 Flex 的維持率列改官方值＋追繳列 |
| `web/app.js`、`web/styles.css`、`web/index.html` | 四張新卡、對照圖窗格、5 欄、版號 |
| `tests/…` | 見各 Task |
| `CLAUDE.md`、`AGENTS.md` | 改寫維持率那節、記錄本次決定 |

---

### Task 1: 證交所信用交易三支解析與抓取

**Files:**
- Modify: `stocks_power_rich/sources/twse.py`（常數區與 `fetch_margin` 之後）
- Test: `tests/test_twse.py`

**Interfaces:**
- Produces:
  ```python
  CREDIT_SINCE = "2026-08-03"
  def parse_credit_summary(payload: dict) -> dict   # {"keep_rate","below_call_acc","call_acc","exe_acc","credit_amt"}；stat 非 OK 回 {}
  def fetch_credit_summary(date: datetime.date) -> dict
  def parse_margin_trend(payload: dict) -> dict     # {"YYYY-MM-DD": market_value_億}
  def fetch_margin_trend(date: datetime.date, days: int = 60) -> dict
  def parse_margin_history(payload: dict) -> dict   # {"margin_ratio": {"min","max","since","latest","latest_label"}, "credit_ratio": {...}}
  def fetch_margin_history() -> dict
  ```

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_twse.py` 尾端加：

```python
def test_parse_credit_summary_maps_official_fields_and_converts_amount_to_yi():
    payload = {"stat": "OK", "date": "20260922", "crdAmt": 143306121850, "keepRate": 193.92,
               "belowAccNum": 147, "callAccNum": 27, "callAmt": 16071673, "exeAccNum": 11,
               "exeAmt": 17705361, "marginAccNum": 78130, "marginAccRate": 0.29}
    out = twse.parse_credit_summary(payload)
    assert out == {"keep_rate": 193.92, "below_call_acc": 147, "call_acc": 27, "exe_acc": 11,
                   "credit_amt": 1433.06}          # 143,306,121,850 元 → 億，2 位


def test_parse_credit_summary_returns_empty_when_not_published():
    # 08-03 之前、或當日尚未產製，證交所回這一句而不是 404
    assert twse.parse_credit_summary({"stat": "很抱歉，沒有符合條件的資料!"}) == {}
    assert twse.parse_credit_summary({}) == {}
    assert twse.parse_credit_summary(None) == {}


def test_parse_margin_trend_keeps_market_value_by_iso_date():
    payload = {"stat": "OK", "date": "20260922", "days": 30, "data": [
        {"date": "20260921", "marginShr": 9330477, "marginAmt": 603037778, "shortShr": 231582, "marketValue": 1560748.24},
        {"date": "20260922", "marginShr": 9245371, "marginAmt": 604861415, "shortShr": 218839, "marketValue": 1563443.68}]}
    assert twse.parse_margin_trend(payload) == {"2026-09-21": 1560748.24, "2026-09-22": 1563443.68}
    assert twse.parse_margin_trend({"stat": "很抱歉，沒有符合條件的資料!"}) == {}


def test_parse_margin_history_summarises_yearly_range():
    payload = {"stat": "OK", "data": [
        {"year": "2000", "label": "2000", "marginRatio": 2.31, "creditRatio": 40.98},
        {"year": "2025", "label": "2025", "marginRatio": 0.36, "creditRatio": 5.78},
        {"year": "2026", "label": "2026/08", "marginRatio": 0.38, "creditRatio": 6.09}]}
    out = twse.parse_margin_history(payload)
    assert out["margin_ratio"] == {"min": 0.36, "max": 2.31, "since": "2000", "latest": 0.38, "latest_label": "2026/08"}
    assert out["credit_ratio"] == {"min": 5.78, "max": 40.98, "since": "2000", "latest": 6.09, "latest_label": "2026/08"}
    assert twse.parse_margin_history({"stat": "X"}) == {}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_twse.py -q --no-header -k "credit_summary or margin_trend or margin_history"`
Expected: 4 failed（`AttributeError: module ... has no attribute 'parse_credit_summary'`）

- [ ] **Step 3: 實作**

`stocks_power_rich/sources/twse.py` 常數區（`INDEX_OHLC_RWD` 那行之後）加：

```python
# 臺股儀表板「信用交易」（2026-08-03 起提供）：BFIJ3U＝單日全市場整戶擔保維持率／追繳處分戶數／
# 信用交易成交值；MI_MARGN_TREND＝逐日融資融券＋上市總市值（days 實測上限 60）；
# MI_MARGN_HISTORY＝2000 年起的年度「融資占市值」「信用交易占成交值」。
BFIJ3U_RWD = "https://www.twse.com.tw/rwd/zh/marginTrading/BFIJ3U"
MARGIN_TREND_RWD = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN_TREND"
MARGIN_HISTORY_RWD = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN_HISTORY"
CREDIT_SINCE = "2026-08-03"   # 之前的日期證交所回「沒有符合條件的資料」，不要打
_UA = {"User-Agent": "Mozilla/5.0"}
```

`fetch_margin` 函式之後加：

```python
def _i(v):
    f = _f(v)
    return int(round(f)) if f is not None else None


def parse_credit_summary(payload: dict) -> dict:
    """BFIJ3U → 本站欄位。整戶擔保維持率是**券商申報的真實帳戶合計、全市場一個數字**
    （TPEx dashboardOtc 的 keepRate 逐日相同）。crdAmt 是元，轉成億對齊 turnover。"""
    if not payload or payload.get("stat") != "OK":
        return {}
    crd = _f(payload.get("crdAmt"))
    return {"keep_rate": _f(payload.get("keepRate")),
            "below_call_acc": _i(payload.get("belowAccNum")),
            "call_acc": _i(payload.get("callAccNum")),
            "exe_acc": _i(payload.get("exeAccNum")),
            "credit_amt": round(crd / 1e8, 2) if crd is not None else None}


def fetch_credit_summary(date: datetime.date) -> dict:
    """單日信用交易概況。當日尚未產製或早於 CREDIT_SINCE 都回 {}（呼叫端據此記「尚未公布」）。"""
    if date.isoformat() < CREDIT_SINCE:
        return {}
    r = httpx.get(BFIJ3U_RWD, params={"response": "json", "date": date.strftime("%Y%m%d")},
                  headers=_UA, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return parse_credit_summary(r.json())


def parse_margin_trend(payload: dict) -> dict:
    """MI_MARGN_TREND → {ISO 日期: 上市總市值(億)}。只取 marketValue（餘額本站已有官方值）。"""
    if not payload or payload.get("stat") != "OK":
        return {}
    out = {}
    for row in payload.get("data") or []:
        d, mv = str(row.get("date") or ""), _f(row.get("marketValue"))
        if len(d) == 8 and mv is not None:
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = mv
    return out


def fetch_margin_trend(date: datetime.date, days: int = 60) -> dict:
    r = httpx.get(MARGIN_TREND_RWD, params={"response": "json", "date": date.strftime("%Y%m%d"),
                                            "days": min(int(days), 60)},
                  headers=_UA, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return parse_margin_trend(r.json())


def parse_margin_history(payload: dict) -> dict:
    """MI_MARGN_HISTORY（年度）→ 兩個比率的歷史區間，給卡片 tooltip 的「近 N 年 min～max」。"""
    if not payload or payload.get("stat") != "OK":
        return {}
    rows = [r for r in (payload.get("data") or []) if r.get("year")]
    if not rows:
        return {}
    out = {}
    for key, field in (("margin_ratio", "marginRatio"), ("credit_ratio", "creditRatio")):
        vals = [(r["year"], r.get("label") or r["year"], _f(r.get(field))) for r in rows]
        vals = [v for v in vals if v[2] is not None]
        if not vals:
            continue
        out[key] = {"min": min(v[2] for v in vals), "max": max(v[2] for v in vals),
                    "since": vals[0][0], "latest": vals[-1][2], "latest_label": vals[-1][1]}
    return out


def fetch_margin_history() -> dict:
    r = httpx.get(MARGIN_HISTORY_RWD, params={"response": "json"}, headers=_UA,
                  timeout=20, follow_redirects=True)
    r.raise_for_status()
    return parse_margin_history(r.json())
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_twse.py -q --no-header`
Expected: 全部 passed（既有 `test_all_tpex_www_fetchers_use_verify_false` 在 `test_tpex.py`，不受影響）

- [ ] **Step 5: 對真實端點打一次（非 mock），把結果寫檔看**

Run:
```bash
.venv\Scripts\python -c "import datetime,json; from stocks_power_rich.sources import twse; d=datetime.date(2026,9,22); out={'credit':twse.fetch_credit_summary(d),'trend_n':len(twse.fetch_margin_trend(d)),'trend_0922':twse.fetch_margin_trend(d).get('2026-09-22'),'hist':twse.fetch_margin_history(),'pre_since':twse.fetch_credit_summary(datetime.date(2026,7,31))}; open('credit_probe.txt','w',encoding='utf-8').write(json.dumps(out,ensure_ascii=False,indent=1))"
```
Read `credit_probe.txt`。Expected：`credit.keep_rate == 193.92`、`credit_amt == 1433.06`、`trend_n == 60`、`trend_0922 == 1563443.68`、`hist.margin_ratio.min == 0.36`、`pre_since == {}`（沒有連外，`CREDIT_SINCE` 守住）。看完刪掉 `credit_probe.txt`。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv\Scripts\python -c "for p in ('stocks_power_rich/sources/twse.py','tests/test_twse.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
git add stocks_power_rich/sources/twse.py tests/test_twse.py
git commit -m "feat(twse): 證交所信用交易概況 BFIJ3U／融資趨勢 TREND／年度 HISTORY 解析與抓取" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `market_daily` 六欄 ＋ `analysis.credit_ratios`

**Files:**
- Modify: `stocks_power_rich/db.py:12-24`（`MARKET_COLS`）
- Modify: `stocks_power_rich/analysis.py`（`margin_maintenance` 那個位置）
- Test: `tests/test_analysis_daily.py`、`tests/test_db.py`

**Interfaces:**
- Produces: `analysis.credit_ratios(margin_value, market_value, credit_amt, turnover) -> {"margin_mcap_pct": float|None, "credit_ratio": float|None}`；`MARKET_COLS` 含 `keep_rate, below_call_acc, call_acc, exe_acc, credit_amt, market_value`。
- Removes: `analysis.margin_maintenance`（本 Task 刪函式與它的兩條測試；呼叫端在 Task 3 刪）。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_analysis_daily.py`：**刪掉** `test_margin_maintenance_ratio` 與 `test_margin_maintenance_full_formula_includes_short` 兩個函式（自算維持率退場，見 spec §5），並在檔尾加：

```python
def test_credit_ratios_follow_twse_dashboard_formulas():
    from stocks_power_rich.analysis import credit_ratios

    # 2026-09-22 證交所頁面：融資金額 6048.6 億 ÷ 上市總市值 1,563,443.68 億 = 0.39%；
    # 信用交易成交值 1433.06 億 ÷ (2 × 市場總成交值 10,787.8 億) = 6.64%——分母乘 2 是
    # 證交所 JS 的原式（買賣兩邊各算一次成交值）。不乘 2 會得到 13.3%。
    out = credit_ratios(6048.6, 1563443.68, 1433.06, 10787.8)
    assert out == {"margin_mcap_pct": 0.39, "credit_ratio": 6.64}


def test_credit_ratios_return_none_per_field_when_inputs_missing_or_zero():
    from stocks_power_rich.analysis import credit_ratios

    assert credit_ratios(None, 1563443.68, 1433.06, 10787.8) == {"margin_mcap_pct": None, "credit_ratio": 6.64}
    assert credit_ratios(6048.6, 0, 1433.06, 10787.8)["margin_mcap_pct"] is None
    assert credit_ratios(6048.6, 1563443.68, None, 10787.8)["credit_ratio"] is None
    assert credit_ratios(6048.6, 1563443.68, 1433.06, 0)["credit_ratio"] is None
```

`tests/test_db.py` 尾端加：

```python
def test_market_daily_has_official_credit_columns(tmp_path):
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    upsert_market_daily(c, {"date": "2026-09-22", "keep_rate": 193.92, "below_call_acc": 147,
                            "call_acc": 27, "exe_acc": 11, "credit_amt": 1433.06, "market_value": 1563443.68})
    r = c.execute("SELECT keep_rate, below_call_acc, call_acc, exe_acc, credit_amt, market_value "
                  "FROM market_daily WHERE date='2026-09-22'").fetchone()
    assert tuple(r) == (193.92, 147, 27, 11, 1433.06, 1563443.68)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_daily.py tests/test_db.py -q --no-header -k "credit"`
Expected: 3 failed（`ImportError ... credit_ratios`；`sqlite3.OperationalError: table market_daily has no column named keep_rate`）

- [ ] **Step 3: 實作**

`stocks_power_rich/db.py` 的 `MARKET_COLS`：把

```python
    "margin_value", "margin_value_chg", "margin_maintenance",
    # 維持率的分子分母（億）——存下來卡片才能把「怎麼算出來的」秀給人看
    "margin_mv", "short_mv",
    # 上櫃自成一組：融資成數 50%（上市 60%），損益兩平線 200% vs 166.7%，
    # 併成單一「大盤」數字會把兩個市場的反向訊號互相抵銷掉
    "otc_margin_balance", "otc_short_balance", "otc_margin_value",
    "otc_margin_mv", "otc_short_mv", "otc_margin_maintenance",
```

改成

```python
    "margin_value", "margin_value_chg",
    # 2026-09 起維持率改用證交所公布的全市場「整戶擔保維持率」（BFIJ3U），下面五欄是它
    # 的同伴；margin_mcap_pct／credit_ratio 兩個衍生比率讀取時算、不落地（analysis.credit_ratios）。
    "keep_rate", "below_call_acc", "call_acc", "exe_acc", "credit_amt", "market_value",
    # ---- 已停用（2026-09）：自算維持率整條路徑已移除，欄位留在 schema 但不再寫值 ----
    "margin_maintenance", "margin_mv", "short_mv",
    "otc_margin_mv", "otc_short_mv", "otc_margin_maintenance",
    # 上櫃融資餘額／融券餘額／融資金額仍每天寫（加權兩平線需要 otc_margin_value）
    "otc_margin_balance", "otc_short_balance", "otc_margin_value",
```

`stocks_power_rich/analysis.py`：把整支 `def margin_maintenance(...)`（含 docstring 到 `return round(numerator / denominator * 100, 1)`）**刪掉**，原位換成：

```python
def credit_ratios(margin_value, market_value, credit_amt, turnover) -> dict:
    """證交所儀表板「信用交易」頁的兩個比率，照它的 JS 原式（2026-09-23 對過）：

    - 融資餘額占市值比重 ＝ 融資金額(億) ÷ 上市總市值(億) × 100
    - 信用交易占成交值比重 ＝ 信用交易成交值(億) ÷ (2 × 市場總成交值(億)) × 100
      **分母乘 2**：買賣兩邊各算一次成交值。2026-09-22 實測 1433.06 ÷ (2×10787.8) ＝ 6.64%，
      與頁面一致；不乘 2 是 13.3%。
    純衍生值不落地（同 turnover_ma10），任一輸入缺或分母為 0 該欄回 None，各自獨立。
    """
    def pct(num, den):
        return round(num / den * 100, 2) if (num is not None and den) else None
    return {"margin_mcap_pct": pct(margin_value, market_value),
            "credit_ratio": pct(credit_amt, (turnover * 2) if turnover else None)}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_daily.py tests/test_db.py -q --no-header`
Expected: 全部 passed

- [ ] **Step 5: 確認沒有別的 import 還在用 `analysis.margin_maintenance`**

Run: `grep -rn "margin_maintenance(" stocks_power_rich tests`
Expected: 只剩 `stocks_power_rich/updater.py:434`（`_maint` 裡那一行，Task 3 會連函式一起刪）。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv\Scripts\python -c "for p in ('stocks_power_rich/db.py','stocks_power_rich/analysis.py','tests/test_analysis_daily.py','tests/test_db.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
git add stocks_power_rich/db.py stocks_power_rich/analysis.py tests/test_analysis_daily.py tests/test_db.py
git commit -m "feat(db,analysis): market_daily 六個官方信用交易欄位；credit_ratios 純函式；刪自算 margin_maintenance" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `updater`——抓官方值、回補、上櫃摘要，刪自算維持率

**Files:**
- Modify: `stocks_power_rich/updater.py`（`_maint`～`_heal_margin_maintenance` 整段、`run_update` 兩段、heal 呼叫）
- Test: `tests/test_updater.py`

**Interfaces:**
- Consumes: Task 1 的 `twse.fetch_credit_summary`／`fetch_margin_trend`／`fetch_margin_history`／`CREDIT_SINCE`。
- Produces:
  ```python
  def _otc_margin_summary(D, detail=None) -> dict      # {"otc_margin_value","otc_margin_balance","otc_short_balance"} 或 {}
  def _backfill_credit(conn, days: int = 10, cap: int = 5) -> list   # 回補到的日期（升冪）
  def _refresh_credit_history(conn) -> bool            # 本月已存就 False
  ```
  `run_update` 的 `success` 新名字：`twse_credit`／`otc_margin`／`twse_credit_backfill`／`credit_history`；`failed.name`：`twse_credit`／`otc_margin`。
- Removes: `_maint`、`_compute_margin_maintenance`、`_compute_otc_margin_maintenance`、`_heal_margin_maintenance`。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_updater.py`：**刪掉** `test_heal_margin_maintenance_fills_days_that_had_no_margin_value_yet` 與 `test_heal_computes_otc_independently_of_tse` 兩個函式。在檔尾加：

```python
def test_backfill_credit_fills_only_nulls_and_never_before_credit_since(tmp_path, monkeypatch):
    """官方信用交易概況的洞掃描：只填 NULL、CREDIT_SINCE 之前不打、市值一次 TREND 補整段。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.twse, "CREDIT_SINCE", "2026-08-03")
    monkeypatch.setattr(updater._date, "today", classmethod(lambda cls: date(2026, 8, 6)))
    for ds in ("2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"):
        upsert_market_daily(conn, {"date": ds, "taiex": 1.0})
    upsert_market_daily(conn, {"date": "2026-08-04", "keep_rate": 183.79, "market_value": 999.0})  # 已有值，不可覆蓋

    asked = []
    def fake_credit(D):
        asked.append(D.isoformat())
        return {"2026-08-03": {"keep_rate": 178.76, "below_call_acc": 102, "call_acc": 19, "exe_acc": 8, "credit_amt": 1206.97},
                "2026-08-05": {}}[D.isoformat()]          # 08-05 尚未公布
    monkeypatch.setattr(updater.twse, "fetch_credit_summary", fake_credit)
    trend_calls = []
    monkeypatch.setattr(updater.twse, "fetch_margin_trend",
                        lambda D, days=60: trend_calls.append(D) or {"2026-08-03": 1487845.76, "2026-08-04": 1.0, "2026-08-05": 1500000.0})
    monkeypatch.setattr(updater, "_otc_margin_summary", lambda D, detail=None: {"otc_margin_value": 1900.0})

    filled = updater._backfill_credit(conn, days=10)

    assert asked == ["2026-08-05", "2026-08-03"]           # 新→舊；07-31 早於 CREDIT_SINCE，一次都沒打
    assert len(trend_calls) == 1                           # 市值只打一次 TREND
    got = {r[0]: r[1:] for r in conn.execute(
        "SELECT date, keep_rate, market_value, otc_margin_value FROM market_daily ORDER BY date")}
    assert got["2026-08-03"] == (178.76, 1487845.76, 1900.0)
    assert got["2026-08-04"] == (183.79, 999.0, 1900.0)     # 既有 keep_rate／market_value 沒被 1.0 蓋掉
    assert got["2026-08-05"] == (None, 1500000.0, 1900.0)   # 尚未公布 → keep_rate 留 NULL、市值照補
    assert got["2026-07-31"] == (None, None, None)
    assert filled == ["2026-08-03", "2026-08-04", "2026-08-05"]


def test_backfill_credit_respects_cap_newest_first(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater._date, "today", classmethod(lambda cls: date(2026, 9, 10)))
    for i in range(1, 8):
        upsert_market_daily(conn, {"date": f"2026-09-0{i}", "taiex": 1.0, "market_value": 1.0, "otc_margin_value": 1.0})
    asked = []
    monkeypatch.setattr(updater.twse, "fetch_credit_summary", lambda D: asked.append(D.isoformat()) or {"keep_rate": 190.0})
    monkeypatch.setattr(updater.twse, "fetch_margin_trend", lambda D, days=60: {})
    updater._backfill_credit(conn, days=10, cap=3)
    assert asked == ["2026-09-07", "2026-09-06", "2026-09-05"]


def test_otc_margin_summary_keeps_balances_without_computing_maintenance():
    d = {"balance": 2335144, "short_balance": 35783, "value": 2085.2, "margin": {"8069": 10}, "short": {}}
    assert updater._otc_margin_summary(date(2026, 9, 22), d) == {
        "otc_margin_value": 2085.2, "otc_margin_balance": 2335144, "otc_short_balance": 35783}
    assert updater._otc_margin_summary(date(2026, 9, 22), {"value": None, "margin": {}, "short": {}}) == {}
    assert updater._otc_margin_summary(None) == {}


def test_refresh_credit_history_is_monthly(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    calls = []
    monkeypatch.setattr(updater.twse, "fetch_margin_history",
                        lambda: calls.append(1) or {"margin_ratio": {"min": 0.36, "max": 2.42}})
    assert updater._refresh_credit_history(conn) is True
    assert updater._refresh_credit_history(conn) is False        # 同月第二次不再連外
    assert len(calls) == 1
    from stocks_power_rich.db import latest_ai_cache_with_prefix
    assert latest_ai_cache_with_prefix(conn, "credit_hist:")["margin_ratio"]["max"] == 2.42
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_updater.py -q --no-header -k "credit or otc_margin_summary"`
Expected: 4 failed（`AttributeError: module ... has no attribute '_backfill_credit'` 等）

- [ ] **Step 3: 實作**

`stocks_power_rich/updater.py`：

(a) 把從 `def _maint(` 開始、到 `_heal_margin_maintenance` 的 `return filled` 為止的**四支函式整段刪掉**，原位換成：

```python
def _otc_margin_summary(D, detail=None):
    """上櫃融資餘額／融券餘額／融資金額（億），櫃買 margin/balance 同一支端點。

    以前這裡順便算上櫃維持率；2026-09 起維持率改用證交所公布的全市場整戶擔保維持率，
    但 otc_margin_value 仍要每天寫——加權兩平線（ss_trader.blended_margin_ratio）靠它。
    """
    if not D:
        return {}
    d = detail if detail is not None else tpex.fetch_otc_margin(D)
    if not d or not d.get("value"):
        return {}
    return {"otc_margin_value": d["value"], "otc_margin_balance": d.get("balance"),
            "otc_short_balance": d.get("short_balance")}


def _backfill_credit(conn, days: int = 10, cap: int = 5) -> list:
    """回補近 days 天官方信用交易欄位的洞（只填 NULL，絕不覆蓋既有值）。

    三種洞、三個來源：上市總市值一次 MI_MARGN_TREND（60 天）補整段；keep_rate 等五欄逐日
    BFIJ3U（新→舊、最多 cap 個日期，當日尚未產製的明天再補）；otc_margin_value 逐日櫃買。
    CREDIT_SINCE 之前的日期證交所沒有資料，掃描視窗直接截在那裡、一次都不打。
    """
    cutoff = max((_date.today() - timedelta(days=days)).isoformat(), twse.CREDIT_SINCE)
    rows = conn.execute(
        "SELECT date, keep_rate, market_value, otc_margin_value FROM market_daily "
        "WHERE date >= ? ORDER BY date DESC", (cutoff,)).fetchall()
    if not rows:
        return []
    filled = set()
    mv_holes = [r[0] for r in rows if r[2] is None]
    if mv_holes:
        try:
            trend = twse.fetch_margin_trend(_iso_to_date(rows[0][0]), days=60)
        except Exception:  # noqa: BLE001 — 市值補不到不影響其餘欄位
            trend = {}
        for ds in mv_holes:
            if trend.get(ds) is not None:
                upsert_market_daily(conn, {"date": ds, "market_value": trend[ds]})
                filled.add(ds)
    attempts = 0
    for ds, kr, _mv, omv in rows:
        if kr is not None and omv is not None:
            continue
        if attempts >= cap:
            break
        attempts += 1
        D = _iso_to_date(ds)
        patch = {}
        if kr is None:
            try:
                patch.update({k: v for k, v in twse.fetch_credit_summary(D).items() if v is not None})
            except Exception:  # noqa: BLE001 — 單日失敗略過，下次再補
                pass
        if omv is None:
            try:
                patch.update(_otc_margin_summary(D))
            except Exception:  # noqa: BLE001
                pass
        if patch:
            upsert_market_daily(conn, {"date": ds, **patch})
            filled.add(ds)
    return sorted(filled)


def _refresh_credit_history(conn) -> bool:
    """年度「融資占市值／信用交易占成交值」（2000 年起）月更一次，給卡片 tooltip 的歷史區間。
    鍵帶年月，dashboard 用 latest_ai_cache_with_prefix 讀最新一份、絕不連外。"""
    key = f"credit_hist:{_date.today():%Y-%m}"
    if get_ai_cache(conn, key):
        return False
    data = twse.fetch_margin_history()
    if not data:
        return False
    set_ai_cache(conn, key, data)
    return True
```

(b) `run_update` 裡從註解「`# 大盤整戶擔保維持率（需融資金額＋…`」開始、到上櫃那段的 `failed.append({"source": "tpex", "name": "otc_margin_maintenance", "error": str(e)})` 為止**整段刪掉**，換成：

```python
    # 證交所官方「信用交易概況」（整戶擔保維持率／低於130%戶數／追繳／處分／信用交易成交值）。
    # 產製時間不固定、常晚於 21:00；當日沒有就記進 failed（看得見、不告警），由 _backfill_credit 隔天補。
    try:
        credit = twse.fetch_credit_summary(D) if D else {}
        if credit:
            row.update({k: v for k, v in credit.items() if v is not None})
            success.append("twse_credit")
        elif D:
            failed.append({"source": "twse", "name": "twse_credit", "error": "信用交易概況尚未公布，稍後回補"})
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "twse_credit", "error": str(e)})

    # 上櫃融資餘額／融券餘額／融資金額（櫃買同一支端點；加權兩平線需要 otc_margin_value）
    try:
        otc = _otc_margin_summary(D, daily_flow.get("TPEx", {}).get("margin"))
        if otc:
            row.update(otc)
            success.append("otc_margin")
        elif D:
            failed.append({"source": "tpex", "name": "otc_margin", "error": "上櫃融資餘額尚未發布，稍後回補"})
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "tpex", "name": "otc_margin", "error": str(e)})
```

(c) `run_update` 後段那個「補算近期缺的融資維持率」try 區塊（呼叫 `_heal_margin_maintenance`）換成：

```python
    # 回補近期缺的官方信用交易欄位（BFIJ3U 常晚於 21:00 產製）＋ 年度歷史月更
    try:
        if _backfill_credit(conn):
            success.append("twse_credit_backfill")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "credit_backfill", "error": str(e)})
    try:
        if _refresh_credit_history(conn):
            success.append("credit_history")
    except Exception as e:  # noqa: BLE001
        failed.append({"source": "twse", "name": "credit_history", "error": str(e)})
```

- [ ] **Step 4: 跑測試確認通過（含整個 test_updater）**

Run: `.venv\Scripts\python -m pytest tests/test_updater.py -q --no-header > pt_t3.txt 2>&1; echo EXIT=$?; tail -2 pt_t3.txt`
Expected: `EXIT=0`。若有既有測試 monkeypatch `_compute_margin_maintenance`／`_compute_otc_margin_maintenance`（grep 確認），把那些 setattr 改成 `_otc_margin_summary`／`fetch_credit_summary` 的樁，斷言語意不變。

- [ ] **Step 5: 全 repo 確認四支舊函式無殘留**

Run: `grep -rn "_compute_margin_maintenance\|_compute_otc_margin_maintenance\|_heal_margin_maintenance\|def _maint\b" stocks_power_rich tests`
Expected: 只剩 `stocks_power_rich/api/admin.py`（Task 4 刪）與 `tests/test_api.py` 一處（Task 4 改）。

- [ ] **Step 6: CRLF 檢查、刪暫存檔、提交**

```bash
rm -f pt_t3.txt
.venv\Scripts\python -c "for p in ('stocks_power_rich/updater.py','tests/test_updater.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
git add stocks_power_rich/updater.py tests/test_updater.py
git commit -m "feat(updater): 每日抓官方信用交易概況＋洞掃描回補；上櫃只留餘額摘要；刪自算維持率整條路徑" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: 加權兩平線、檢核表、dashboard、admin、公開總覽、告警名單

**Files:**
- Modify: `stocks_power_rich/ss_trader.py`（常數註解、`market_checklist` 第 1 項）
- Modify: `stocks_power_rich/api/market.py`（`dashboard()`、`_BANDS`、Gemini 輸入 `"融資維持率(%)"` 那行）
- Modify: `stocks_power_rich/api/admin.py`（刪 `/margin-maintenance/heal`、加 `/credit/backfill`）
- Modify: `stocks_power_rich/api/helpers.py`（`expected_later`）
- Modify: `stocks_power_rich/api/public.py:131`
- Test: `tests/test_ss_trader.py`、`tests/test_api.py`、`tests/test_health.py`

**Interfaces:**
- Produces: `ss_trader.blended_margin_ratio(tse_value, otc_value) -> float|None`；`/api/dashboard` 回應 `history[i].margin_mcap_pct`／`credit_ratio`、`bands.keep_rate = {"breakeven", "call"}`、`credit_history`；`/public/api/overview` 的 `margin.keep_rate`／`keep_rate_prev`；`GET /api/credit/backfill?days=60`。
- Removes: `_BANDS["margin_maintenance"]`／`["otc_margin_maintenance"]`、checklist 的 `margin_maint_otc`、`/api/margin-maintenance/heal`。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_ss_trader.py`：把 `test_margin_maintenance_low_is_bull`、`test_margin_maintenance_missing_is_na`、`test_margin_verdict_reads_each_market_against_its_own_breakeven`、`test_market_checklist_lists_both_margin_markets` 四個函式**刪掉**，換成：

```python
def test_blended_margin_ratio_weights_by_margin_value():
    # 2026-09-22：上市融資金額 6048.6 億、上櫃 2085.2 億 → 成數 (6048.6×0.6+2085.2×0.5)/8133.8 = 0.5744
    assert round(ss_trader.blended_margin_ratio(6048.6, 2085.2), 4) == 0.5744
    assert ss_trader.margin_breakeven(ss_trader.blended_margin_ratio(6048.6, 2085.2)) == 174.1
    assert ss_trader.blended_margin_ratio(6048.6, None) == 0.6      # 只缺一邊就用另一邊
    assert ss_trader.blended_margin_ratio(None, 2085.2) == 0.5
    assert ss_trader.blended_margin_ratio(None, None) is None
    assert ss_trader.blended_margin_ratio(0, 0) is None


def test_market_checklist_has_one_official_keep_rate_item():
    rows = [_row(keep_rate=193.92, margin_value=6048.6, otc_margin_value=2085.2)]
    items = {i["key"]: i for i in ss_trader.market_checklist(rows)}
    assert "margin_maint_otc" not in items
    it = items["margin_maint"]
    assert it["name"] == "整戶擔保維持率" and it["value"] == 193.9
    assert it["status"] == "neutral" and "獲利 11%" in it["note"]     # 193.92 vs 兩平 174.1 → +11%


def test_market_checklist_keep_rate_low_is_contrarian_bull_and_missing_is_na():
    it = _find(ss_trader.market_checklist([_row(keep_rate=133.0, margin_value=100.0)]), "margin_maint")
    assert it["status"] == "bull" and "133" in str(it["value"])
    assert _find(ss_trader.market_checklist([_row()]), "margin_maint")["status"] == "na"
    # 有維持率但兩邊融資金額都缺 → 算不出兩平線，也是 na（不拿預設成數硬判）
    assert _find(ss_trader.market_checklist([_row(keep_rate=190.0)]), "margin_maint")["status"] == "na"
```

`test_margin_verdict_treats_low_maintenance_as_contrarian_bull` 保留不動（它直接測 `margin_verdict(v, ratio)`，介面沒變）。

`tests/test_api.py`：把 `test_dashboard_bands_come_from_ss_trader` 改成：

```python
def test_dashboard_bands_come_from_ss_trader(tmp_path, monkeypatch):
    """總覽卡片的「異常讀數」門檻必須是 ss_trader 的那一份，不得在前端另寫一組。

    維持率的兩平線 2026-09 起是**每天算的**：以最新列的上市／上櫃融資金額加權融資成數
    （官方整戶維持率是全市場單一數字，沒有單一成數）。所以這裡種一列真的值，斷言 breakeven
    等於 ss_trader 對同一組輸入算出來的數。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import ss_trader
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    upsert_market_daily(c, {"date": "2026-09-22", "taiex": 47800.17, "margin_value": 6048.6,
                            "otc_margin_value": 2085.2, "keep_rate": 193.92})

    client = TestClient(create_app())
    bands = client.get("/api/dashboard").json()["bands"]

    even = ss_trader.margin_breakeven(ss_trader.blended_margin_ratio(6048.6, 2085.2))
    assert bands["keep_rate"] == {"breakeven": even, "call": ss_trader.MARGIN_CALL_LINE}
    assert even == 174.1
    assert "margin_maintenance" not in bands and "otc_margin_maintenance" not in bands
    assert bands["vix"] == {"low": ss_trader.VIX_COMPLACENT, "high": ss_trader.VIX_PANIC}
    assert bands["turnover_ma10"] == {"low": ss_trader.VOL_QUIET_YI}
    # 免密碼的公開總覽走同一個 handler，門檻也必須跟著出現
    assert client.get("/public/api/dashboard").json()["bands"] == bands


def test_dashboard_bands_keep_rate_breakeven_is_none_without_margin_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import ss_trader
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    upsert_market_daily(c, {"date": "2026-09-22", "taiex": 1.0, "keep_rate": 193.92})
    bands = TestClient(create_app()).get("/api/dashboard").json()["bands"]
    assert bands["keep_rate"] == {"breakeven": None, "call": ss_trader.MARGIN_CALL_LINE}


def test_dashboard_injects_credit_ratios_per_row_and_credit_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, set_ai_cache
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    upsert_market_daily(c, {"date": "2026-09-21", "taiex": 1.0, "turnover": 8668.2, "margin_value": 6030.4})   # 沒市值
    upsert_market_daily(c, {"date": "2026-09-22", "taiex": 1.0, "turnover": 10787.8, "margin_value": 6048.6,
                            "market_value": 1563443.68, "credit_amt": 1433.06})
    set_ai_cache(c, "credit_hist:2026-09", {"margin_ratio": {"min": 0.36, "max": 2.42, "since": "2000"}})
    d = TestClient(create_app()).get("/api/dashboard").json()
    by = {r["date"]: r for r in d["history"]}
    assert by["2026-09-22"]["margin_mcap_pct"] == 0.39 and by["2026-09-22"]["credit_ratio"] == 6.64
    assert by["2026-09-21"]["margin_mcap_pct"] is None and by["2026-09-21"]["credit_ratio"] is None
    assert d["latest"]["credit_ratio"] == 6.64          # latest 與 history 最後一列是同一個 dict
    assert d["credit_history"]["margin_ratio"]["max"] == 2.42


def test_public_overview_reports_official_keep_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    upsert_market_daily(c, {"date": "2026-09-21", "taiex": 1.0, "keep_rate": 193.11})
    upsert_market_daily(c, {"date": "2026-09-22", "taiex": 1.0, "keep_rate": 193.92, "margin_balance": 1})
    m = TestClient(create_app()).get("/public/api/overview").json()["margin"]
    assert m["keep_rate"] == 193.92 and m["keep_rate_prev"] == 193.11
    assert "maintenance" not in m


def test_credit_backfill_endpoint_reports_remaining_within_official_window(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich import updater
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    from datetime import date, timedelta
    for i in (1, 2):
        upsert_market_daily(c, {"date": (date.today() - timedelta(days=i)).isoformat(), "taiex": 1.0})
    monkeypatch.setattr(updater, "_backfill_credit", lambda conn, days=10, cap=5: ["2026-09-22"])
    r = TestClient(create_app()).get("/api/credit/backfill?days=60").json()
    assert r["filled"] == ["2026-09-22"] and r["remaining"] == 2
    assert TestClient(create_app()).get("/api/margin-maintenance/heal").status_code == 404
```

並把既有 `test_backfill_windows...`（`tests/test_api.py` 約 570-597 行、呼叫 `/api/margin-maintenance/heal` 那條）裡的四行——`maint = client.get("/api/margin-maintenance/heal?days=200&max_fetch=1").json()`、`assert maint["remaining"] >= 1`、以及最後 `assert client.get("/api/margin-maintenance/heal?days=60&max_fetch=1").json()["remaining"] == 0`——**刪掉**，該測試只留 chips 那兩條斷言，docstring 補一句「維持率自 2026-09 改用官方值，heal 端點已移除」。

`tests/test_health.py` 179-188 那條：`failed` 清單裡兩筆 `margin_maintenance`／`otc_margin_maintenance` 換成一筆 `{"name": "twse_credit", "error": "信用交易概況尚未公布，稍後回補"}`，`intl` 那筆不動，斷言仍是 `sent == []`。

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ss_trader.py tests/test_api.py tests/test_health.py -q --no-header -k "blended or keep_rate or credit or bands or alert_expected or expected_later"`
Expected: 新增與改寫的測試 failed（`AttributeError: blended_margin_ratio`、`KeyError: 'keep_rate'`、404 未出現等）

- [ ] **Step 3: 實作 `ss_trader`**

常數區：把 `MARGIN_RATIO_TSE`／`MARGIN_RATIO_OTC` 兩行的註解改成 `# 上市融資成數（加權兩平線的權重之一）`／`# 上櫃融資成數`。在 `margin_breakeven` 之前加：

```python
def blended_margin_ratio(tse_value, otc_value):
    """全市場的融資成數：以當日上市／上櫃**融資金額（億）**為權重加權 0.6／0.5。

    2026-09 起維持率改用證交所公布的全市場整戶擔保維持率——它是券商申報的真實帳戶合計、
    上市上櫃同一個數字，沒有單一成數可推兩平線。兩平線是這張卡唯一可判讀的錨點
    （沒有它 193.9% 無從判斷是賺是賠），所以用兩市場融資金額的比例加權出一個近似成數，
    每天重算（2026-09-22：6048.6／2085.2 億 → 0.5744 → 兩平 174.1%）。
    只缺一邊就用另一邊的成數；兩邊都缺（或都是 0）回 None，讓呼叫端顯示 na，不硬套預設。
    """
    t = tse_value if tse_value and tse_value > 0 else 0
    o = otc_value if otc_value and otc_value > 0 else 0
    if not t and not o:
        return None
    return (t * MARGIN_RATIO_TSE + o * MARGIN_RATIO_OTC) / (t + o)
```

`market_checklist` 第 1 項那個 `for key, name, col, ratio in (...)` 迴圈整段換成：

```python
    # 1) 整戶擔保維持率（證交所公布的全市場數字）。兩平線用兩市場融資金額加權的成數推得，
    #    見 blended_margin_ratio；成數算不出（兩邊融資金額都缺）就 na，不拿單一市場的成數硬判。
    kr = _last_valid(rows, "keep_rate")
    ratio = blended_margin_ratio(_last_valid(rows, "margin_value"), _last_valid(rows, "otc_margin_value"))
    if kr is None or ratio is None:
        out.append(_item("margin_maint", "整戶擔保維持率", "na", note="尚無資料"))
    else:
        status, rel, note = margin_verdict(kr, ratio)
        out.append(_item("margin_maint", "整戶擔保維持率", status, round(kr, 1), note))
```

- [ ] **Step 4: 實作 `api/market.py`**

`dashboard()`：在 `for r, ma in zip(asc, mas): r["turnover_ma10"] = ma` 之後加：

```python
    # 官方定義的兩個衍生比率（融資占市值／信用交易占成交值）同樣逐列注入、不落地
    for r in asc:
        r.update(analysis.credit_ratios(r.get("margin_value"), r.get("market_value"),
                                        r.get("credit_amt"), r.get("turnover")))
```

回傳 dict 的 `"bands": _BANDS,` 改成 `"bands": _bands_for(asc),`，並加 `"credit_history": latest_ai_cache_with_prefix(c, "credit_hist:") or {},`（`from ..db import` 那行補 `latest_ai_cache_with_prefix`）。

`_BANDS` 刪掉 `margin_maintenance`／`otc_margin_maintenance` 兩個鍵與它們上方那 2 行註解，並在 `_BANDS` 之後加：

```python
def _bands_for(asc: list) -> dict:
    """固定門檻＋一個每天算的：整戶維持率的兩平線由最新列的上市／上櫃融資金額加權成數推得
    （ss_trader.blended_margin_ratio 是唯一出處，前端不得複寫）。算不出時 breakeven 為 None，
    前端只剩追繳線可用、副標留白。"""
    def last(col):
        for r in reversed(asc):
            if r.get(col) is not None:
                return r[col]
        return None
    ratio = ss_trader.blended_margin_ratio(last("margin_value"), last("otc_margin_value"))
    return {**_BANDS, "keep_rate": {"breakeven": ss_trader.margin_breakeven(ratio) if ratio else None,
                                    "call": ss_trader.MARGIN_CALL_LINE}}
```

Gemini 輸入那行 `"融資維持率(%)": m.get("margin_maintenance"),` 改成 `"整戶擔保維持率(%)": m.get("keep_rate"),`。

- [ ] **Step 5: 實作 `api/admin.py`、`api/helpers.py`、`api/public.py`**

`api/admin.py`：整支 `margin_maintenance_heal`（含 `@router.get("/margin-maintenance/heal")` 到 `_backfill_lock.release()`）刪掉，原位換成：

```python
@router.get("/credit/backfill")
def credit_backfill(days: int = 60):
    """一次性回補證交所官方信用交易欄位（整戶維持率／追繳處分戶數／信用交易成交值／上市總市值）。

    每日更新的 _backfill_credit 只回看 10 天、每次最多 5 個日期；官方 2026-08-03 才開始提供，
    上線那次要一口氣把 08-03 起補齊。BFIJ3U 每個日期一個請求，cap 放大到 90。
    """
    if not _backfill_lock.acquire(blocking=False):
        return {"busy": True, "note": "回補進行中，請稍候再呼叫"}
    try:
        from datetime import date, timedelta
        from ..sources import twse
        c = conn()
        days = max(5, min(days, 120))
        filled = updater._backfill_credit(c, days=days, cap=90)
        cutoff = max((date.today() - timedelta(days=days)).isoformat(), twse.CREDIT_SINCE)
        remaining = c.execute(
            "SELECT COUNT(*) FROM market_daily WHERE date >= ? AND keep_rate IS NULL", (cutoff,)).fetchone()[0]
        return {"filled": filled, "remaining": remaining, "since": twse.CREDIT_SINCE}
    finally:
        _backfill_lock.release()
```

`api/helpers.py` 的 `expected_later`：

```python
    def expected_later(f: dict) -> bool:
        name, error = f.get("name") or "", f.get("error") or ""
        if name in ("twse_credit", "otc_margin"):
            return "尚未" in error or "稍後回補" in error
        return name == "intl" and ("尚未取得" in error or "自動回補" in error)
```

`api/public.py:131`：`"maintenance": m.get("margin_maintenance"), "maintenance_prev": pv.get("margin_maintenance")}` 改成 `"keep_rate": m.get("keep_rate"), "keep_rate_prev": pv.get("keep_rate")}`。

- [ ] **Step 6: 跑測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ss_trader.py tests/test_api.py tests/test_health.py -q --no-header > pt_t4.txt 2>&1; echo EXIT=$?; tail -2 pt_t4.txt`
Expected: `EXIT=0`（`test_api.py` 整檔約 8 分鐘，用 `run_in_background: true` 等通知）

- [ ] **Step 7: 反證一條（做完還原）**

暫時把 `_bands_for` 裡的 `last("otc_margin_value")` 改成 `None`，跑 `-k test_dashboard_bands_come_from_ss_trader` → 應紅（breakeven 變 166.7 ≠ 174.1）。改回後再跑一次綠。

- [ ] **Step 8: CRLF 檢查、刪暫存檔、提交**

```bash
rm -f pt_t4.txt
.venv\Scripts\python -c "for p in ('stocks_power_rich/ss_trader.py','stocks_power_rich/api/market.py','stocks_power_rich/api/admin.py','stocks_power_rich/api/helpers.py','stocks_power_rich/api/public.py','tests/test_ss_trader.py','tests/test_api.py','tests/test_health.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
git add stocks_power_rich/ss_trader.py stocks_power_rich/api/market.py stocks_power_rich/api/admin.py stocks_power_rich/api/helpers.py stocks_power_rich/api/public.py tests/test_ss_trader.py tests/test_api.py tests/test_health.py
git commit -m "feat(api): 加權兩平線、檢核表單一整戶維持率、dashboard 注入官方比率與動態 bands、/credit/backfill 取代 heal" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: LINE 卡片與純文字改官方維持率

**Files:**
- Modify: `stocks_power_rich/line_push.py`（`compose_daily_brief` 融資券段、`compose_daily_flex` 融資段）
- Test: `tests/test_line_push.py`

**Interfaces:**
- Consumes: `row["keep_rate"]`／`pv["keep_rate"]`／`row["below_call_acc"]`／`call_acc`／`exe_acc`。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_line_push.py`：`_ROW` 裡 `"margin_maintenance": 165.2,` 改成 `"keep_rate": 165.2, "below_call_acc": 147, "call_acc": 27, "exe_acc": 11,`。
`test_compose_full_margin_three_lines_and_handles_missing` 裡 `assert "融資維持率 165.2%" in txt` 改成：

```python
    assert "整戶維持率 165.2%" in txt          # prev 空 → 無(昨…)
    assert "低於130% 147戶(追繳27/處分11)" in txt
```

`test_compose_daily_flex_margin_falls_back_and_marks_as_of` 裡 `"margin_maintenance": 173.6}` 改 `"keep_rate": 173.6}`、`margin_prev={"margin_maintenance": 174.9}` 改 `margin_prev={"keep_rate": 174.9}`（斷言 `"昨174.9%"` 不變）。

檔尾加：

```python
def test_flex_margin_shows_one_official_keep_rate_row_and_call_pressure_only_in_full():
    row = dict(_ROW)
    brief = str(_sect(line_push.compose_daily_flex(row, [], [], full=False), "融資"))
    full = str(_sect(line_push.compose_daily_flex(row, [], [], full=True), "融資"))
    assert "整戶維持率" in brief and "165.2%" in brief
    assert "維持率(上市)" not in brief and "維持率(上櫃)" not in brief
    assert "低於130%" not in brief                 # 16:00 精簡版不放
    assert "低於130%" in full and "147" in full and "追繳 27" in full and "處分 11" in full
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_line_push.py -q --no-header -k "margin"`
Expected: 3 failed

- [ ] **Step 3: 實作**

`compose_daily_brief` 融資券段：把

```python
        if row.get("margin_maintenance") is not None:
            line = f"融資維持率 {_fmt(row['margin_maintenance'], 1)}%"
            if pv.get("margin_maintenance") is not None:
                line += f"(昨{_fmt(pv['margin_maintenance'], 1)}%)"
            g.append(line)
```

改成

```python
        if row.get("keep_rate") is not None:      # 證交所公布的全市場整戶擔保維持率
            line = f"整戶維持率 {_fmt(row['keep_rate'], 1)}%"
            if pv.get("keep_rate") is not None:
                line += f"(昨{_fmt(pv['keep_rate'], 1)}%)"
            g.append(line)
            if row.get("below_call_acc") is not None:
                g.append(f"低於130% {_fmt(row['below_call_acc'], 0)}戶"
                         f"(追繳{_fmt(row.get('call_acc'), 0)}/處分{_fmt(row.get('exe_acc'), 0)})")
```

`compose_daily_flex` 融資段：`if any(mrow.get(k) is not None for k in ("margin_balance", "margin_maintenance")):` 改成 `("margin_balance", "keep_rate")`；把那個 `for lb, k in (("維持率(上市)", ...` 迴圈（連同上方兩行「兩個市場的融資成數不同…」註解）換成：

```python
        # 2026-09 起改證交所公布的全市場整戶擔保維持率（券商申報的真實帳戶合計、上市上櫃同值）
        if mrow.get("keep_rate") is not None:
            mg.append(_kv("整戶維持率", f"{_fmt(mrow['keep_rate'], 1)}%",
                          note="" if mprev.get("keep_rate") is None else f"昨{_fmt(mprev['keep_rate'], 1)}%"))
```

並在 `if full:` 區塊最後（融券餘額那個 if 之後）加：

```python
            if mrow.get("below_call_acc") is not None:      # 分布的尾巴：斷頭潮來臨時先動的是這三個數
                mg.append(_kv("低於130%", f"{_fmt(mrow['below_call_acc'], 0)}戶",
                              note=f"追繳 {_fmt(mrow.get('call_acc'), 0)}／處分 {_fmt(mrow.get('exe_acc'), 0)}"))
```

- [ ] **Step 4: 跑測試確認通過（含 bubble 上限測試）**

Run: `.venv\Scripts\python -m pytest tests/test_line_push.py -q --no-header`
Expected: 全部 passed（`test_compose_daily_flex_stays_under_line_bubble_limit` 仍綠——多一列約 150 B）

- [ ] **Step 5: CRLF 檢查並提交**

```bash
.venv\Scripts\python -c "for p in ('stocks_power_rich/line_push.py','tests/test_line_push.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
git add stocks_power_rich/line_push.py tests/test_line_push.py
git commit -m "feat(line): 融資段改官方整戶維持率一列，完整版加低於130%／追繳／處分戶數" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: 前端——四張新卡、5 欄、對照圖窗格、版號 ui70

**Files:**
- Modify: `web/app.js`（`maintTip`／`marginMaintCard` 改寫、新增三支卡、`renderCards` 清單、`CHIP_PANES.maint`、`loadDashboard` 存 `credit_history`）
- Modify: `web/styles.css:741`、`:766-768`、`:776-778`
- Modify: `web/index.html`×2、`stocks_power_rich/api/public.py`×2 行、`tests/test_api.py`×4（版號）

**Interfaces:**
- Consumes: `/api/dashboard` 的 `history[].keep_rate`／`below_call_acc`／`call_acc`／`exe_acc`／`margin_mcap_pct`／`credit_ratio`／`credit_amt`、`bands.keep_rate`、`credit_history`。
- Produces: 全域 `lastCreditHistory`；函式 `keepRateCard`／`callPressureCard`／`ratioCard`。

- [ ] **Step 1: 版號進版（四處）**

```bash
.venv\Scripts\python -c "
OLD,NEW='20260817-ui69','20260817-ui70'
for p in ('web/index.html','stocks_power_rich/api/public.py','tests/test_api.py'):
    s=open(p,'rb').read().decode('utf-8'); n=s.count(OLD); s=s.replace(OLD,NEW)
    assert s.count('\n')==s.count('\r\n'), p
    open(p,'wb').write(s.encode('utf-8')); print(p, n)
"
```
Expected: `web/index.html 2`、`stocks_power_rich/api/public.py 4`、`tests/test_api.py 4`

- [ ] **Step 2: CSS 改 5 欄與斷點**

`web/styles.css`：
- 第 741 行 `.stat-board--tw { grid-template-columns: repeat(4, minmax(0, 1fr)); }` 改成 `repeat(5, minmax(0, 1fr))`。
- `@media (max-width: 1240px) { .stat-board--tw { ... repeat(3, ...) } }` 那段**之前**加：

```css
/* 台股大盤 10 張卡、5 欄。中間寬度先退 4 欄再退 3 欄——ui68 的教訓：只測 1904/1280/375 會漏掉
   1400 左右那一段，每個斷點都要量 .card-val 有沒有溢出。 */
@media (max-width: 1400px) {
  .stat-board--tw { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
```

（1240px→3 欄、1100px→2 欄兩段維持原樣。）

- [ ] **Step 3: app.js——維持率卡改官方版、三張新卡**

(a) 第 65 行 `let lastLatest = null, lastBands = {};` 改成 `let lastLatest = null, lastBands = {}, lastCreditHistory = {};`；`loadDashboard` 裡 `lastHistory = hist; lastLatest = d.latest || null; lastBands = d.bands || {};` 改成 `lastHistory = hist; lastLatest = d.latest || null; lastBands = d.bands || {}; lastCreditHistory = d.credit_history || {};`。

(b) 把 `function maintTip(...)` 到 `function marginMaintCard(...)` 結尾（`return card(lbl, fmt(v, 1) + "%", chg, pctOf(v, chg), "", tip, rk, alert, extra, trend);\n}`）**整段刪掉**（`relToBreakeven`／`MAINT_NEAR_CALL`／`MAINT_DEEP_LOSS`／`isMaintAlert` 四個定義保留），原位換成：

```js
// 整戶擔保維持率：證交所公布的全市場數字（券商申報的真實帳戶合計，上市上櫃同值，2026-08-03 起）。
// 兩平線由後端每天用兩市場融資金額加權融資成數推得（bands.keep_rate.breakeven），前端不複寫。
function keepRateTip(even, call) {
  return `證交所定義：全市場融資融券擔保品市值加計融券保證金，與融資金加計融券標的市值之比例；`
    + `代表整體市場擔保能力，不代表個別投資人或個別證券商狀況。`
    + (even ? `\n損益兩平約 ${even}%（以當日上市／上櫃融資金額加權融資成數 60%／50% 推得，為近似值）；` : `\n`)
    + `低於 ${call}% 會被追繳、限期未補即斷頭。`
    + `\n資料：臺股儀表板「信用交易」，2026-08-03 起提供。`;
}
function keepRateCard(hist, srcRow, curDate) {
  const label = "整戶擔保維持率", col = "keep_rate";
  const band = lastBands.keep_rate || {};
  const even = band.breakeven, call = band.call;
  if (!srcRow || srcRow[col] == null) return emptyStatCard(label);
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const idx = hist.findIndex((r) => r && r.date === srcRow.date);
  const priorRow = idx > 0 ? [...hist.slice(0, idx)].reverse().find((r) => r && r[col] != null) : null;
  const chg = priorRow ? srcRow[col] - priorRow[col] : null;
  const v = srcRow[col];
  const rk = pctile(hist, col, v);
  const rel = relToBreakeven(v, even);
  const extra = rel == null ? "" : `相對兩平 ${rel > 0 ? "+" : ""}${fmt(rel, 1)}%（${rel >= 0 ? "獲利" : "套牢"}）`;
  const alert = isMaintAlert(v, call, rel) ? {
    key: col, label, display: `${fmt(v, 1)}%`,
    reason: `追繳線 ${fmt(call, 0)}%` + (rel == null ? "" : `；相對兩平 ${rel > 0 ? "+" : ""}${fmt(rel, 1)}%`),
    tier: 1, rank: rk, group: "tw",
  } : false;
  const trend = trendHtml(hist, col, { delta: true, label: "近7日增減", unit: "%", digits: 1 });
  return card(lbl, fmt(v, 1) + "%", chg, pctOf(v, chg), "", keepRateTip(even, call), rk, alert, extra, trend);
}
// 追繳壓力：維持率是平均值，這三個戶數是分布的尾巴——斷頭潮來臨時它們先動。
function callPressureCard(hist, srcRow, curDate) {
  const label = "追繳壓力", col = "below_call_acc";
  if (!srcRow || srcRow[col] == null) return emptyStatCard(label);
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const idx = hist.findIndex((r) => r && r.date === srcRow.date);
  const priorRow = idx > 0 ? [...hist.slice(0, idx)].reverse().find((r) => r && r[col] != null) : null;
  const v = srcRow[col];
  const chg = priorRow ? v - priorRow[col] : null;
  const rk = pctile(hist, col, v);
  const extra = `追繳 ${fmt(srcRow.call_acc ?? null, 0)} 戶　處分 ${fmt(srcRow.exe_acc ?? null, 0)} 戶`;
  const tip = `證交所定義：截至當日止，整戶擔保維持率低於 130% 之戶數（主數字）。`
    + `\n追繳＝當日證券商通知應補繳之戶數；處分＝未依期限補足差額或屆期未清償，次一營業日應由證券商處分信用交易部位之戶數。`
    + `\n位階條落在近期分布最高 10% 時標琥珀外框。資料：臺股儀表板「信用交易」，2026-08-03 起。`;
  const alert = cardAlert(col, label, v, `${fmt(v, 0)} 戶`, rk, "tw");
  const trend = trendHtml(hist, col, { delta: true, label: "近7日增減", unit: "戶", digits: 0 });
  return card(lbl, fmt(v, 0), chg, null, '<span class="card-unit">戶</span>', tip, rk, alert, extra, trend, " 戶");
}
// 兩個官方比率共用一張卡型：融資占市值（上市）、信用交易占比（上市）。值由後端逐列算好（不落地）。
function ratioCard(hist, srcRow, curDate, opts) {
  const { label, col, tip, extra } = opts;
  if (!srcRow || srcRow[col] == null) return emptyStatCard(label);
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const idx = hist.findIndex((r) => r && r.date === srcRow.date);
  const priorRow = idx > 0 ? [...hist.slice(0, idx)].reverse().find((r) => r && r[col] != null) : null;
  const v = srcRow[col];
  const chg = priorRow ? v - priorRow[col] : null;
  const rk = pctile(hist, col, v);
  const alert = cardAlert(col, label, v, `${fmt(v, 2)}%`, rk, "tw");
  const trend = trendHtml(hist, col, { delta: true, label: "近7日增減", unit: "%", digits: 2 });
  return card(lbl, fmt(v, 2) + "%", chg, null, "", tip, rk, alert, extra, trend);
}
function histRangeText(key, unit = "%") {
  const h = (lastCreditHistory || {})[key];
  if (!h || h.min == null || h.max == null) return "";
  const since = h.since ? `${new Date().getFullYear() - Number(h.since)} 年` : "歷史";
  return `近 ${since} ${fmt(h.min, 2)}${unit}～${fmt(h.max, 2)}${unit}`;
}
```

(c) `renderCards`：把 `const otcRow = ...` 那行（連同上方註解「上櫃走櫃買自己的發布時程…」）刪掉，改成：

```js
  // 官方信用交易概況（BFIJ3U）常晚於 21:00 產製，同樣退到最近一筆有值的列
  const creditRow = [...hist].reverse().find((r) => r && r.keep_rate != null) || m;
```

`$("cards-tw").innerHTML = [...]` 清單裡兩張 `marginMaintCard(...)` 換成：

```js
    keepRateCard(hist, creditRow, m.date),
    callPressureCard(hist, creditRow, m.date),
    ratioCard(hist, creditRow, m.date, { label: "融資占市值（上市）", col: "margin_mcap_pct",
      tip: `證交所定義：融資餘額占上市股票總市值的比率，用以觀察市場使用融資槓桿的相對程度。\n＝融資金額 ÷ 上市總市值（MI_MARGN_TREND 的 marketValue）。`
        + (histRangeText("margin_ratio") ? `\n${histRangeText("margin_ratio")}（年度資料，證交所 MI_MARGN_HISTORY）` : ""),
      extra: histRangeText("margin_ratio") }),
    ratioCard(hist, creditRow, m.date, { label: "信用交易占比（上市）", col: "credit_ratio",
      tip: `證交所定義：信用交易成交值占市場總成交值的比率。\n＝信用交易成交值 ÷ (2 × 市場總成交值)——買賣兩邊各算一次成交值，故分母乘 2（證交所 JS 原式）。`
        + (histRangeText("credit_ratio") ? `\n${histRangeText("credit_ratio")}（年度資料）` : ""),
      extra: creditRow.credit_amt != null ? `成交 ${fmt(creditRow.credit_amt, 0)} 億` : "" }),
```

(d) `CHIP_PANES` 的 `maint` 項換成：

```js
  { key: "maint", label: "整戶維持率", unit: "%", series: (at) => [
      { ...LP, name: "整戶擔保維持率", data: at((r) => r.keep_rate), lineStyle: { color: C.info }, itemStyle: { color: C.info },
        markLine: { silent: true, symbol: "none", label: { show: true, position: "insideEndTop", formatter: "追繳 130%", color: C.muted, fontSize: 10 },
          lineStyle: { color: C.muted, type: "dashed" }, data: [{ yAxis: 130 }] } }] },
```

- [ ] **Step 4: 靜態檢查**

Run: `node --check web/app.js && grep -n "marginMaintCard\|otc_margin_maintenance\|margin_maintenance" web/app.js`
Expected: `node --check` 無輸出；grep **無輸出**（舊鍵全部消失）。

Run: `.venv\Scripts\python -m pytest tests/test_api.py -q --no-header -k "public or frontend or overview" > pt_t6.txt 2>&1; echo EXIT=$?; tail -1 pt_t6.txt`
Expected: `EXIT=0`（版號四處一致）

- [ ] **Step 5: 瀏覽器實測**

先用本機 DB 把官方欄位補進來（一次性）：`preview_start {name:"spr"}`（若 `reused: true` 先 `preview_stop` 再 start，讓 Task 3 的 Python 生效），然後 `javascript_tool` 執行 `fetch('/api/credit/backfill?days=60').then(r=>r.json())` → 期望 `filled` 含 08-03 起約 35 個日期、`remaining` 0 或 1（今天）。

接著在總覽逐項用 `javascript_tool` 量、把數字寫進報告：
1. `document.querySelectorAll('#cards-tw > .stat-cell').length === 10`；四張新卡的 `.card-label` 文字依序是「整戶擔保維持率」「追繳壓力」「融資占市值（上市）」「信用交易占比（上市）」。
2. **數字對證交所**：整戶維持率卡主數字 ＝ `lastHistory` 最後一列 `keep_rate`（09-22 為 193.9%）；追繳壓力主數字 147、副標含「追繳 27 戶　處分 11 戶」；融資占市值 0.39%；信用交易占比 6.64%、副標「成交 1,433 億」（若本機最新列不是 09-22，用該日與 `BFIJ3U` 實際回應比）。
3. 整戶維持率卡副標「相對兩平 +11.x%（獲利）」，且 `lastBands.keep_rate.breakeven` 介於 166.7 與 200 之間。
4. **顏色**：掃 `#cards-tw` 四張新卡內所有元素的 computed `color`／`background-color`，不得出現 `--up`／`--down` 的色值。
5. **五個寬度零溢出**：`resize_window` 1904／1600／1400／1280／1181／768／375，每個寬度量 `[...document.querySelectorAll('#cards-tw .card-val, #cards-tw .card-note')].filter(e=>e.scrollWidth>e.clientWidth+1).length === 0` 與 `document.documentElement.scrollWidth <= clientWidth`；並記錄各寬度 `getComputedStyle($('cards-tw')).gridTemplateColumns.split(' ').length`（期望 1904/1600 → 5、1400/1280 → 4、1181 → 3、768 → 2、375 → 2）。
6. **降級**：暫時覆寫 `window.getJSON`，把 `/api/dashboard` 回應最後一列的 `keep_rate` 等六欄設成 `null`，重跑 `loadDashboard()` → 四張卡標「截至 MM-DD」（前一列）；再把所有列都設 null → 四張卡顯示 `—`（`emptyStatCard`）。還原。
7. 大盤×籌碼對照圖勾「融資維持率」窗格：`chipChart.getOption().series` 裡名為「整戶擔保維持率」的 series 存在、只有一條、`lineStyle.color` 等於 `C.info`，且有 `markLine`。
8. 切到操盤手頁：檢核表只有一項「整戶擔保維持率」、沒有「（上櫃）」。
9. 最後 `resize_window` preset `desktop`。

任一項不符就修到符合再往下；量到的數字寫進任務報告，不要只寫「看起來正常」。

- [ ] **Step 6: CRLF 檢查、刪暫存檔、提交**

```bash
rm -f pt_t6.txt
.venv\Scripts\python -c "for p in ('web/app.js','web/styles.css','web/index.html','stocks_power_rich/api/public.py','tests/test_api.py'): b=open(p,'rb').read(); print(p, 'loneLF', b.count(b'\n')-b.count(b'\r\n'))"
git add web/app.js web/styles.css web/index.html stocks_power_rich/api/public.py tests/test_api.py
git commit -m "feat(ui): 台股大盤組改 10 張 5 欄——官方整戶維持率、追繳壓力、融資占市值、信用交易占比（ui70）" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: 文件與全套測試

**Files:**
- Modify: `CLAUDE.md`（「融資維持率：兩個市場，兩條基準線」整節改寫；`_BANDS`／heal 相關句子）
- Modify: `AGENTS.md`（對應段落）

- [ ] **Step 1: CLAUDE.md**

把 `### 融資維持率：兩個市場，兩條基準線` 整節（到「…方向與 VIX 一致（低維持率＝斷頭清洗＝反指標偏多，不是利空）。」那段結束）**替換**為：

```markdown
### 融資維持率改用證交所官方「整戶擔保維持率」（ui70，2026-09）

證交所 2026-08-03 上線「臺股儀表板 › 信用交易」，使用者要求總覽的融資融券**定義與它一致**。
逐項對過：融資餘額（張）／融資金額／融券餘額三個數字本站與證交所**完全相同**（同源 MI_MARGN），
差的只有維持率——本站原本用「Σ融資明細×收盤 ÷ 融資金額」自算、還拆成上市／上櫃兩個數字；
證交所公布的是**券商申報的真實帳戶合計、全市場一個數字**（TWSE `BFIJ3U_TREND` 與 TPEx
`dashboardOtc/marginTrend` 30 個交易日逐日相同），08-03 起 19 天實測自算值比官方低 0.3%～5.9%、
比例還會跳。使用者拍板：**改用官方值，自算路徑刪除**（`_compute_margin_maintenance`／`_heal_*`／
`analysis.margin_maintenance`／`/api/margin-maintenance/heal` 都沒了；`market_daily` 舊欄位留在 schema
不寫值）。舊的「兩個市場、兩條基準線」那節因此整段作廢——那套推理的前提（自算、分市場）已不存在。

- **資料源**：`BFIJ3U`（單日：`keepRate`／`belowAccNum`／`callAccNum`／`exeAccNum`／`crdAmt` 元）、
  `MI_MARGN_TREND`（逐日上市總市值 `marketValue` 億，**`days` 上限 60**）、`MI_MARGN_HISTORY`（2000 起年度）。
  **08-03 之前沒有資料**（`twse.CREDIT_SINCE`，回補一律截在那裡）。產製時間不固定、常晚於 21:00：
  當日沒有就記 `failed name=twse_credit「尚未公布」`（`expected_later` 放行、不告警），`_backfill_credit`
  隔天只填 NULL。一次性回補 `GET /api/credit/backfill?days=60`。
- **兩個衍生比率不落地**（`analysis.credit_ratios`，同 `turnover_ma10` 規矩）：融資占市值＝融資金額÷上市
  總市值；**信用交易占比＝信用交易成交值÷(2×市場總成交值)——分母乘 2 是證交所 JS 的原式**（買賣兩邊各
  算一次成交值；實測 1433.06÷(2×10787.8)＝6.64% 與頁面一致，不乘 2 是 13.3%）。
- **兩平線改成每天算的加權值**（`ss_trader.blended_margin_ratio`）：官方維持率沒有單一融資成數，
  以當日上市／上櫃融資金額加權 0.6／0.5（09-22：6048.6／2085.2 億 → 0.5744 → 174.1%），經
  `bands.keep_rate.breakeven` 送前端；兩邊融資金額都缺就 `None`，卡片副標留白、檢核表 na。
  `otc_margin_value` 因此仍要每天寫（`_otc_margin_summary`），只是不再算上櫃維持率。
- **新增三張卡**：追繳壓力（主數字＝低於 130% 戶數、副標追繳／處分戶——維持率是平均，這三個數是分布的
  尾巴）、融資占市值（上市）、信用交易占比（上市）。四張新卡都不著紅綠。台股大盤組 10 張改 **5 欄**，
  斷點 ≤1400 4 欄／≤1240 3 欄／≤1100 2 欄，七個寬度逐一量過。
- 消費端一致：檢核表兩項併一項、LINE 卡片一列＋完整版追繳列、對照圖窗格一條線＋130 追繳線、
  公開總覽 `margin.keep_rate`、Gemini 輸入鍵名「整戶擔保維持率(%)」。
- 刻意不做：不存上櫃的信用交易概況（同值）、不做信用交易戶數（與追繳壓力重疊、分母未說明）、
  不用自算值補 08-03 之前的對照圖（兩個定義不能接在同一條線上）。

**不要拿外部數字校準這一欄**這條舊教訓保留一半：MacroMicro 那次證明**第三方**的口徑對不上；
這次換成**官方**數字則是定義本身就該一致——兩者不衝突，差別在來源是誰。
```

另外全文 grep `_heal_margin_maintenance`／`margin-maintenance/heal`／`otc_margin_maintenance`，其餘提到的地方（Data-source quirks 的 TPEx 段、`_BANDS` 段、Cloud deploy 段）各補一句「（2026-09 起自算維持率已移除，見 ui70 那節）」，不改寫整段。

- [ ] **Step 2: AGENTS.md**

在對應「融資維持率」段落改寫成一段：

```markdown
## 融資維持率改用證交所官方整戶擔保維持率（ui70，2026-09）
自算的上市／上櫃維持率整條路徑刪除（`_compute_margin_maintenance`／heal／`analysis.margin_maintenance`／heal 端點），改存 `BFIJ3U` 的 `keep_rate`／`below_call_acc`／`call_acc`／`exe_acc`／`credit_amt` 與 `MI_MARGN_TREND` 的 `market_value`（2026-08-03 起、TREND 60 天上限、只填 NULL 回補、當日沒有記 `twse_credit` 尚未公布不告警）。融資占市值＝融資金額÷上市總市值、**信用交易占比分母乘 2**（證交所 JS 原式），兩者讀取時算不落地。兩平線改每日加權（`ss_trader.blended_margin_ratio`，上市 0.6／上櫃 0.5 依融資金額加權），`otc_margin_value` 仍每天寫。台股大盤組 10 張 5 欄（≤1400 4／≤1240 3／≤1100 2）。四張新卡不著紅綠。
```

- [ ] **Step 3: CRLF 檢查**

```bash
.venv\Scripts\python -c "for p in ('CLAUDE.md','AGENTS.md'): b=open(p,'rb').read(); print(p, 'loneLF', b.count(b'\n')-b.count(b'\r\n'))"
```

- [ ] **Step 4: 全套測試（背景跑、不接管線）**

Run: `.venv\Scripts\python -m pytest -q --no-header > pt_full.txt 2>&1; echo EXIT=$? >> pt_full.txt`（`run_in_background: true`，等通知後 `tail -3 pt_full.txt`）
Expected: `EXIT=0`，passed 數 ≥ 1063 − 6（刪 6 條）＋ 16（新增）＝ 1073 左右；沒有 warning 以外的雜訊。

- [ ] **Step 5: 刪暫存檔並提交**

```bash
rm -f pt_full.txt
git add CLAUDE.md AGENTS.md
git commit -m "docs: 融資維持率改官方整戶擔保維持率——記下定義差異、分母乘 2、加權兩平線、刪自算路徑的理由" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review（計畫作者已做）

- **Spec 覆蓋**：§資料源→Task 1；§1 資料層（六欄、衍生不落地、run_update、回補、年度歷史）→Task 2＋3＋4；§2 卡片→Task 6；§3 加權兩平線→Task 4；§4 消費端（檢核表／LINE／對照圖／公開總覽／Gemini／expected_later）→Task 4＋5＋6；§5 移除→Task 2＋3＋4（保留清單在 Global Constraints）；§6 版面→Task 6；§7 版號與文件→Task 6＋7；§8 測試→各 Task 的 Step 1 與 Task 6 Step 5；§9 刻意不做→Task 7 文件。
- **型別一致**：`credit_ratios` 回 `{"margin_mcap_pct","credit_ratio"}`（Task 2）→ Task 4 `dashboard` 注入 → Task 6 卡片讀同名鍵；`blended_margin_ratio` 在 Task 4 定義、同 Task 的 `_bands_for` 與 checklist 使用；`bands.keep_rate` 在 Task 4 產出、Task 6 `keepRateCard` 讀；`_otc_margin_summary`／`_backfill_credit` 在 Task 3 定義、Task 4 admin 端點呼叫；`failed.name` 為 `twse_credit`／`otc_margin`，Task 4 `expected_later` 與 `test_health` 同名。
- **無佔位**：每一步都有實際程式碼或實際指令與期望輸出。
