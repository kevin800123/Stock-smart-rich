# 個股頁三張圖合成一張 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 個股頁的 K 線、三大法人、集保三張圖合成同一張 Lightweight Charts（四個窗格共用時間軸與十字線），每格左上角一行讀數，集保加人均數箭頭，集保歷史改成背景自動補。

**Architecture:** 後端先補齊資料（`custody_dist.total_shares`、人均數、自動補歷史的節流與狀態），前端再把兩張 ECharts 圖併進既有的 Lightweight Charts 實例：新增「法人」與「集保」兩個窗格，法人用 LWC 的自訂 series 畫堆疊柱，集保用階梯線＋箭頭標記，四個窗格各有一行讀數列取代原本的浮動 tooltip。

**Tech Stack:** Python 3.11+／FastAPI／sqlite3／pytest；前端 vanilla JS ＋ Lightweight Charts v5.2.1（`web/vendor/lightweight-charts.standalone.production.js`，自架、CSP `script-src 'self'`）。

**Spec:** `docs/superpowers/specs/2026-09-20-stock-combo-chart-design.md`

## Global Constraints

- 一律用繁體中文寫註解與文案；註解說明「為什麼」，比照檔案既有風格。
- 碰 TDCC（`opendata.tdcc.com.tw`、智能網）的每個請求都要 `verify=False`（憑證缺 SKI，既有規矩）。
- 「算式只能有一份權威版本」：人均數在後端算，前端不得再算一份（同 bands／Elliott 的規矩）。
- 紅綠只給價格漲跌；法人沿用既有 `SER.foreign`／`SER.trust`／`SER.dealer`（藍／紫／橘）；集保沿用既有兩色；人均數箭頭上白下黃（使用者指定）。
- CSP `script-src 'self'`：不可寫 inline `on*=` 事件屬性，動態元素一律用事件委派。
- 前端無自動化測試：每一項前端行為都要在瀏覽器對真實資料實測，且要能反證（拿掉某段就看得到差異）。
- 後端照專案慣例寫 pytest：純函式用小樣本鎖契約、端點用 `TestClient` ＋ monkeypatch 假 fetch，每道守衛都要有反證。
- pytest 一律單獨跑、讀摘要行、**不可接管線**；全套在前景跑（timeout 600000 ms）。
- 既有 `.py`／`.js`／`.html`／`.css` 是 CRLF，用 Edit 工具編輯（保留換行），**不可用 `sed -i`**；`stocks_power_rich/ledger.py`、`tests/test_custody_watch.py` 等本來就是 LF 的維持 LF。
- 改 `web/app.js`／`web/styles.css` 要一起進快取版號：`web/index.html`（link 與 script 各一）、`stocks_power_rich/api/public.py`（兩處 replace）、`tests/test_api.py`（四條斷言），現值 `20260817-ui65` → 本計畫進到 `20260817-ui66`；用 Python 以位元組替換，不可 `sed -i`。
- 既有測試不可放寬斷言。
- 只提交自己改過的檔（不可 `git add .`，工作目錄有不該提交的 `HANDOFF-*.md`／`CODEX_HANDOFF_*.md`）；**不推送**（推送＝正式部署，要等使用者說）。
- 提交訊息結尾空一行加 `Co-Authored-By: Claude <model> <noreply@anthropic.com>`。

## 前置作業

分支 `stock-combo-chart` 已建立（從 main @ `c24054c`），上面只有一個提交：設計規格。所有工作都在這個分支上，不推送。

## 檔案結構

| 檔案 | 責任 | 動作 |
|---|---|---|
| `stocks_power_rich/sources/tdcc.py` | 兩個來源的分級解析多帶「股數」，`_aggregate_levels` 多算 `total_shares` | 修改 |
| `stocks_power_rich/db.py` | `custody_dist.total_shares` 欄位、`upsert_custody` 寫入人數與股數、`get_custody_trend` 多回欄位 | 修改 |
| `stocks_power_rich/api/stock.py` | 集保端點回人均數與 `backfilling` 狀態、背景自動補歷史（節流） | 修改 |
| `web/app.js` | 四窗格圖表、堆疊柱自訂 series、四行讀數列、集保箭頭、聚合與日期貼齊、移除兩張 ECharts 圖 | 修改 |
| `web/index.html` | 移除兩個舊圖容器與「補歷史」連結；`#stock-chart` 高度 | 修改 |
| `web/styles.css` | 讀數列樣式、`#stock-chart` 桌機／手機高度 | 修改 |
| `tests/test_custody_shares.py` | 本功能的後端新測試（解析、DB、端點、自動補歷史） | 新增 |
| `tests/test_api.py` | 快取版號斷言 | 修改 |
| `CLAUDE.md`、`AGENTS.md` | 文件 | 修改 |

---

### Task 1: 集保解析多帶「股數」，算出總股數

**背景：** TDCC 兩個來源的分級表本來就有「股數」欄（opendata CSV 第 5 欄 `r[4]`、智能網 HTML 第 4 格 `cells[3]`），但 `_aggregate_levels` 只吃 `(分級序, 人數, 占比%)` 三元組，股數整欄沒用到。人均數＝總股數 ÷ 總持股人數，所以要先把股數加總出來。加總範圍與 `total_holders` 一致（分級 1~15，明確排除合計列——兩個來源的合計列編號不同，一個是 17 一個是 16）。

**Files:**
- Modify: `stocks_power_rich/sources/tdcc.py`（`_aggregate_levels`、`parse_custody_distribution`、`parse_custody_ownership_html`）
- Create: `tests/test_custody_shares.py`

**Interfaces:**
- Produces: `_aggregate_levels(levels)` 的 `levels` 改成 `(分級序, 人數, 股數, 占比%)` 四元組；回傳的 dict 多一個 `total_shares: float`（分級 1~15 的股數加總，`round(..., 0)`）。`parse_custody_distribution` 與 `parse_custody_ownership_html` 的回傳因此每檔多一個 `total_shares`。

- [ ] **Step 1: 寫失敗的測試**

新增 `tests/test_custody_shares.py`：

```python
"""集保股數／人均數（docs/superpowers/specs/2026-09-20-stock-combo-chart-design.md）。"""
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db
from stocks_power_rich.sources import tdcc


CSV_HEAD = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
# 級 1（散戶）、級 12～15（400 張↑，其中 15 是千張大戶）、級 17（合計，兩來源編號不同，必須排除）
CSV_BODY = (
    "20260918,2330,1,1000,500000,5.00\n"
    "20260918,2330,12,20,300000,3.00\n"
    "20260918,2330,13,10,400000,4.00\n"
    "20260918,2330,14,5,600000,6.00\n"
    "20260918,2330,15,3,8200000,82.00\n"
    "20260918,2330,17,1038,10000000,100.00\n"
)


def test_parse_custody_distribution_sums_shares_of_levels_1_to_15():
    d = tdcc.parse_custody_distribution(CSV_HEAD + CSV_BODY)
    rec = d["data"]["2330"]
    assert d["week_date"] == "2026-09-18"
    assert rec["total_holders"] == 1038            # 1000+20+10+5+3，不含第 17 級合計列
    assert rec["total_shares"] == 10000000         # 同樣不含合計列（否則會是兩倍）
    assert rec["big1000_pct"] == 82.0 and rec["big400_pct"] == 95.0


def test_parse_custody_ownership_html_also_returns_total_shares():
    rows = [("1", "1-999", "1,000", "500,000", "5.00"),
            ("15", "1,000以上", "3", "8,200,000", "82.00"),
            ("16", "合計", "1,003", "8,700,000", "87.00")]   # 智能網的合計列是第 16 級
    html = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    rec = tdcc.parse_custody_ownership_html(html)
    assert rec["total_holders"] == 1003
    assert rec["total_shares"] == 8700000          # 只加 1 與 15 兩級，剛好等於合計，但不是讀合計列
    assert rec["big1000_pct"] == 82.0


def test_aggregate_levels_without_shares_degrades_to_zero():
    """股數欄解析不出來（來源改版）時不可整筆炸掉，總股數算 0、其餘照常。"""
    rec = tdcc._aggregate_levels([("1", 100, None, 1.0), ("15", 2, None, 80.0)])
    assert rec["total_shares"] == 0 and rec["total_holders"] == 102 and rec["big1000_pct"] == 80.0
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py -q -p no:cacheprovider`
Expected: FAIL，`KeyError: 'total_shares'`（前兩條）與 `TypeError`（第三條，四元組進不了舊的三元組解包）。

- [ ] **Step 3: 實作**

`stocks_power_rich/sources/tdcc.py` 的 `_aggregate_levels` 換成：

```python
def _aggregate_levels(levels) -> dict:
    """levels＝[(分級序, 人數, 股數, 占比%), ...] → {big1000_pct, big400_pct, big_holders,
    total_holders, total_shares}。千張大戶＝級15；400張↑＝級12~15；總持股人數與總股數＝級1~15加總。

    總持股人數與總股數刻意用「加總 1~15」而非讀取「合計」列——opendata 的合計列是第 17 級、
    智能網 HTML 是第 16 級，兩個來源編號不一致；加總法不依賴任一來源的合計列編號，
    對兩邊都成立（已用真實 2330 資料驗證：分級 1~15 加總＝合計列人數，見 CLAUDE.md）。
    股數是給人均數（總股數 ÷ 總持股人數）用的；某一級的股數解析不出來就當 0，
    不讓整筆集保資料因為多一欄而消失。
    """
    d = {"big1000_pct": 0.0, "big400_pct": 0.0, "big_holders": 0, "total_holders": 0, "total_shares": 0.0}
    for lvl, holders, shares, pct in levels:
        if lvl in _LOW_TO_HIGH_LEVELS:
            d["total_holders"] += int(holders)
            d["total_shares"] += float(shares or 0)
        if lvl == "15":               # 千張大戶
            d["big1000_pct"] += pct
            d["big400_pct"] += pct
            d["big_holders"] += int(holders)
        elif lvl in ("12", "13", "14"):  # 400~1000 張
            d["big400_pct"] += pct
    return {"big1000_pct": round(d["big1000_pct"], 2),
            "big400_pct": round(d["big400_pct"], 2),
            "big_holders": d["big_holders"],
            "total_holders": d["total_holders"],
            "total_shares": round(d["total_shares"], 0)}
```

`parse_custody_distribution` 裡蒐集 levels 的那段改成（股數是第 5 欄 `r[4]`）：

```python
        holders, shares, pct = _num(r[3]), _num(r[4]), _num(r[5])
        if holders is None or pct is None:
            continue
        by_code.setdefault(code, []).append((lvl, holders, shares, pct))
```

`parse_custody_ownership_html` 裡那段改成（表列是 分級序／級距／人數／股數／占比%）：

```python
        holders, shares, pct = _num(cells[2]), _num(cells[3]), _num(cells[4])
        if holders is not None and pct is not None:
            levels.append((cells[0], holders, shares, pct))
```

兩支函式的 docstring 把回傳鍵補上 `total_shares`。

- [ ] **Step 4: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py tests/test_tdcc.py -q -p no:cacheprovider`
Expected: 全部 PASS（既有 `tests/test_tdcc.py` 也要過；若它自己組 levels 三元組，跟著改成四元組並在報告說明）。

- [ ] **Step 5: 對真實 TDCC 驗一次**（只讀，不寫 DB）

Run: `.venv\Scripts\python -c "from stocks_power_rich.sources import tdcc; d=tdcc.fetch_custody_distribution(); r=d['data']['2330']; print(d['week_date'], r['total_holders'], r['total_shares'], round(r['total_shares']/r['total_holders']))"`
Expected: 印出當週日期、總人數（約 300 萬）、總股數與人均數（2330 人均約 8,000 股上下）。把實際數字寫進報告。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/tdcc.py tests/test_custody_shares.py
git commit -m "feat(tdcc): 集保分級解析帶出股數，算出總股數（人均數用）"
```

---

### Task 2: `custody_dist.total_shares` 欄位與人均數

**背景：** `upsert_custody`（逐檔路徑，集保端點與補歷史都用它）**目前連 `total_holders` 都沒寫**——只有全市場批次的 `bulk_upsert_custody` 有寫。人均數要逐檔算得出來，所以這兩欄都要補進逐檔路徑。

**Files:**
- Modify: `stocks_power_rich/db.py`（`init_db` 的 lazy migration、`upsert_custody`、`bulk_upsert_custody`、`get_custody_trend`）
- Test: `tests/test_custody_shares.py`

**Interfaces:**
- Produces:
  - `custody_dist` 新增欄位 `total_shares REAL`（lazy migration，同 `total_holders` 既有寫法）。
  - `upsert_custody(conn, week, code, rec)` 一併寫 `total_holders`、`total_shares`。
  - `get_custody_trend(conn, code)` 每筆多回 `total_holders`、`total_shares`、`avg_shares`（人均數＝總股數÷總持股人數，四捨五入到整數；任一為空或人數為 0 → `None`）。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_shares.py`：

```python
@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


def test_upsert_custody_stores_holders_and_shares_and_trend_returns_avg(conn):
    db.upsert_custody(conn, "2026-09-11", "2330", {"big1000_pct": 77.0, "big400_pct": 82.0,
                                                   "big_holders": 200, "total_holders": 1000,
                                                   "total_shares": 9000000})
    db.upsert_custody(conn, "2026-09-18", "2330", {"big1000_pct": 77.9, "big400_pct": 83.0,
                                                   "big_holders": 205, "total_holders": 1250,
                                                   "total_shares": 10000000})
    trend = db.get_custody_trend(conn, "2330")
    assert [t["week"] for t in trend] == ["2026-09-11", "2026-09-18"]
    assert trend[0]["total_holders"] == 1000 and trend[0]["total_shares"] == 9000000
    assert trend[0]["avg_shares"] == 9000 and trend[1]["avg_shares"] == 8000   # 人均數下降＝籌碼分散


def test_custody_trend_avg_is_none_when_inputs_are_missing(conn):
    """算不出來就回 None，不要拿 0 或舊值頂替（同全站『算不出回 None』的慣例）。"""
    db.upsert_custody(conn, "2026-09-04", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10})          # 舊資料：沒有人數與股數
    db.upsert_custody(conn, "2026-09-11", "1101", {"big1000_pct": 50.0, "big400_pct": 60.0,
                                                   "big_holders": 10, "total_holders": 0,
                                                   "total_shares": 500})        # 人數 0 不可除
    assert [t["avg_shares"] for t in db.get_custody_trend(conn, "1101")] == [None, None]


def test_custody_total_shares_column_migrates_on_an_old_table(tmp_path, monkeypatch):
    """既有部署的 custody_dist 沒有 total_shares 欄，init_db 要能就地補上且不動既有資料。"""
    path = str(tmp_path / "old.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    c.execute("CREATE TABLE custody_dist (week TEXT, code TEXT, big1000_pct REAL, "
              "big400_pct REAL, big_holders REAL, PRIMARY KEY(week, code))")
    c.execute("INSERT INTO custody_dist VALUES ('2026-09-11','2330',77.0,82.0,200)")
    c.commit()
    db.init_db(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(custody_dist)")}
    assert "total_shares" in cols and "total_holders" in cols
    assert db.get_custody_trend(c, "2330")[0]["big1000_pct"] == 77.0   # 既有列還在
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py -q -p no:cacheprovider -k "upsert or trend or migrates"`
Expected: FAIL，`KeyError: 'total_shares'`／`KeyError: 'avg_shares'`。

- [ ] **Step 3: 實作**

`db.py` 的 lazy migration（`custody_existing` 那段）之後補一行：

```python
    if "total_shares" not in custody_existing:
        conn.execute("ALTER TABLE custody_dist ADD COLUMN total_shares REAL")
```

`upsert_custody` 換成（**補上 total_holders 與 total_shares**——這條逐檔路徑原本兩欄都沒寫，集保端點與補歷史都走它，人均數因此永遠算不出來）：

```python
def upsert_custody(conn: sqlite3.Connection, week: str, code: str, rec: dict) -> None:
    conn.execute(
        "INSERT INTO custody_dist (week, code, big1000_pct, big400_pct, big_holders, "
        "total_holders, total_shares) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(week, code) DO UPDATE SET big1000_pct=excluded.big1000_pct, "
        "big400_pct=excluded.big400_pct, big_holders=excluded.big_holders, "
        "total_holders=COALESCE(excluded.total_holders, custody_dist.total_holders), "
        "total_shares=COALESCE(excluded.total_shares, custody_dist.total_shares)",
        (week, code, rec.get("big1000_pct"), rec.get("big400_pct"), rec.get("big_holders"),
         rec.get("total_holders"), rec.get("total_shares")),
    )
    conn.commit()
```

（人數與股數用 `COALESCE`：舊來源沒帶這兩欄時不要把已經補好的值洗掉——同 `bulk_upsert_ohlc` 那條既有規矩。）

`bulk_upsert_custody` 的欄位清單與 `ON CONFLICT` 一併補 `total_shares`（照它既有的寫法，每列取 `v.get("total_shares")`）。

`get_custody_trend` 換成：

```python
def get_custody_trend(conn: sqlite3.Connection, code: str) -> list[dict]:
    """該股逐週集保。avg_shares＝人均持股（總股數÷總持股人數，股）——在這裡算，
    前端不得再算一份（同全站「算式只有一份權威版本」）。任一輸入缺或人數為 0 回 None。"""
    out = []
    for r in conn.execute(
            "SELECT week, big1000_pct, big400_pct, big_holders, total_holders, total_shares "
            "FROM custody_dist WHERE code=? ORDER BY week", (code,)):
        d = dict(r)
        h, s = d.get("total_holders"), d.get("total_shares")
        d["avg_shares"] = round(s / h) if h and s else None
        out.append(d)
    return out
```

- [ ] **Step 4: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py tests/test_db.py tests/test_custody_watch.py -q -p no:cacheprovider`
Expected: 全部 PASS。

- [ ] **Step 5: 反證**

用 Python 腳本＋`try/finally` 暫時把 `get_custody_trend` 的 `if h and s` 改成 `if s`，跑 `test_custody_trend_avg_is_none_when_inputs_are_missing` → 應 FAIL（除以 0 或 None）。還原後比對位元組。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/db.py tests/test_custody_shares.py
git commit -m "feat(db): 集保存總股數，逐週趨勢帶出人均數"
```

---

### Task 3: 集保端點回人均數與背景自動補歷史

**背景：** 目前 `/api/stock/{code}/custody` 回 `{code, week, current, trend}`；補歷史要使用者自己點連結（`/custody/backfill`，逐週抓智能網、CSRF token 每次輪替，52 週約半分到一分鐘）。改成：端點立刻回現有資料，順便判斷「這檔歷史夠不夠」，不夠就在背景補，並用 `backfilling` 告訴前端要不要回頭再問。

**Files:**
- Modify: `stocks_power_rich/api/stock.py`（`stock_custody`，新增背景補歷史的模組層狀態與函式）
- Test: `tests/test_custody_shares.py`

**Interfaces:**
- Produces:
  - `/api/stock/{code}/custody` 回傳多兩個鍵：`backfilling: bool`（背景是否正在補這一檔）、`weeks: int`（目前有幾週）。`trend` 每筆已含 `avg_shares`（Task 2）。
  - `stock.CUSTODY_MIN_WEEKS = 30`：週數少於它就自動補。
  - `stock._should_autofill(trend) -> bool`：週數 < `CUSTODY_MIN_WEEKS`，或**有股數的週數 < 已存週數的一半**（舊資料沒有股數欄，人均數會整片算不出來）。
  - `stock._start_custody_autofill(code) -> bool`：起一條 daemon 執行緒補歷史，回傳「這次有沒有真的起跑」。節流：`ai_cache` 鍵 `custodyauto:{code}:{YYYY-MM-DD}` 存在就不跑；`_custody_lock` 拿不到（別檔正在補）也不跑。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_shares.py`：

```python
def _client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "api.sqlite"))
    from stocks_power_rich.main import create_app
    return TestClient(create_app(enable_scheduler=False))


def _seed_weeks(c, code, n, with_shares=True, end="2026-09-18"):
    d = date.fromisoformat(end)
    for i in range(n):
        wk = (d - timedelta(days=7 * i)).isoformat()
        rec = {"big1000_pct": 70.0 + i * 0.1, "big400_pct": 80.0, "big_holders": 100,
               "total_holders": 1000 + i}
        if with_shares:
            rec["total_shares"] = 9000000
        db.upsert_custody(c, wk, code, rec)


def test_custody_endpoint_returns_avg_shares_and_week_count(monkeypatch, tmp_path):
    from stocks_power_rich.api import stock as S
    from stocks_power_rich.sources import tdcc as T
    monkeypatch.setattr(T, "fetch_custody_distribution", lambda: {"week_date": None, "data": {}})
    monkeypatch.setattr(S, "_start_custody_autofill", lambda code: False)
    client = _client(monkeypatch, tmp_path)
    c = db.get_connection(str(tmp_path / "api.sqlite"))
    _seed_weeks(c, "2330", 40)
    d = client.get("/api/stock/2330.TW/custody").json()
    assert d["weeks"] == 40 and d["backfilling"] is False
    assert d["trend"][-1]["avg_shares"] == round(9000000 / d["trend"][-1]["total_holders"])


@pytest.mark.parametrize("weeks,with_shares,expect", [
    (40, True, False),    # 夠長又有股數 → 不補
    (5, True, True),      # 太短 → 補
    (40, False, True),    # 夠長但整片沒有股數（舊資料）→ 補，否則人均數永遠算不出來
])
def test_autofill_decision(conn, weeks, with_shares, expect):
    from stocks_power_rich.api import stock as S
    _seed_weeks(conn, "2330", weeks, with_shares=with_shares)
    assert S._should_autofill(db.get_custody_trend(conn, "2330")) is expect


def test_autofill_runs_once_per_code_per_day(conn, monkeypatch):
    """同一天同一檔只補一次——手動連查很多檔時不可以連打 TDCC 智能網。"""
    from stocks_power_rich.api import stock as S
    started = []
    monkeypatch.setattr(S.threading, "Thread",
                        lambda target, daemon=None, name=None: type("T", (), {"start": lambda self: started.append(name)})())
    monkeypatch.setattr(S, "conn", lambda: conn)
    assert S._start_custody_autofill("2330") is True
    assert S._start_custody_autofill("2330") is False
    assert len(started) == 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py -q -p no:cacheprovider -k "endpoint or autofill"`
Expected: FAIL，`AttributeError: module 'stocks_power_rich.api.stock' has no attribute '_should_autofill'`。

- [ ] **Step 3: 實作**

`stocks_power_rich/api/stock.py`：`_custody_lock` 附近新增（`threading` 已 import；`get_ai_cache`／`set_ai_cache`／`get_custody_trend`／`upsert_custody` 都已 import，缺的補上）：

```python
CUSTODY_MIN_WEEKS = 30          # 少於這個週數就自動補歷史（集保一年約 52 週）
_custody_autofill = set()        # 本程序正在補的代號，避免同一檔重複起執行緒


def _should_autofill(trend: list) -> bool:
    """這檔的集保歷史夠不夠畫趨勢與人均數。

    兩種都要補：週數太少（新查的股票只有排程累積的那幾週），以及**有股數的週數不到一半**
    ——total_shares 是後加的欄位，既有部署的舊列全是空的，人均數會整片算不出來。"""
    if len(trend) < CUSTODY_MIN_WEEKS:
        return True
    return sum(1 for t in trend if t.get("total_shares")) < len(trend) / 2


def _start_custody_autofill(code: str) -> bool:
    """背景補這一檔的集保歷史；回傳這次有沒有真的起跑。

    節流：同一檔同一天只補一次（ai_cache `custodyauto:{code}:{date}`），同時間只允許一個
    補歷史在跑（`_custody_lock`，由執行緒內取得）。智能網是逐週抓、每次要帶上一次回應輪替的
    CSRF token，52 週約半分到一分鐘，所以只能背景做，絕不能卡住請求。"""
    pure = code.split(".")[0]
    key = f"custodyauto:{pure}:{datetime.now().date().isoformat()}"
    c = conn()
    if get_ai_cache(c, key) or pure in _custody_autofill:
        return False
    set_ai_cache(c, key, {"at": datetime.now().isoformat(timespec="seconds")})
    _custody_autofill.add(pure)
    threading.Thread(target=_custody_autofill_job, args=(pure,), daemon=True,
                     name=f"spr-custody-{pure}").start()
    return True


def _custody_autofill_job(pure: str) -> None:
    """背景執行緒本體：自己開一條 sqlite 連線（sqlite 預設不跨執行緒共用）。
    失敗只記 log，不重試——下一天使用者再查同一檔會再補一次。"""
    import logging
    log = logging.getLogger("spr")
    try:
        if not _custody_lock.acquire(blocking=False):
            log.info("custody autofill skipped (another backfill running): %s", pure)
            return
        try:
            c = get_connection(load_config().db_path)   # 自己開一條：請求那條屬於別的執行緒
            c.execute("PRAGMA busy_timeout=30000")       # 與同時進行的寫入短暫相撞時等待（同 api/stock_flow.py）
            have = {t["week"] for t in get_custody_trend(c, pure)}
            avail = tdcc.fetch_custody_weeks()
            want = [w for w in avail if f"{w[:4]}-{w[4:6]}-{w[6:8]}" not in have][:52]
            hist = tdcc.fetch_custody_history(pure, weeks=want, max_weeks=52) if want else {}
            for wk_iso, rec in hist.items():
                upsert_custody(c, wk_iso, pure, rec)
            log.info("custody autofill %s: +%d weeks", pure, len(hist))
        finally:
            _custody_lock.release()
    except Exception:  # noqa: BLE001  背景執行緒的例外沒有人接，記下來才看得見
        log.exception("custody autofill failed: %s", pure)
    finally:
        _custody_autofill.discard(pure)
```

（`get_connection` 從 `..db`、`load_config` 從 `..config` import——`api/deps.py::conn` 就是這樣取 db_path 的；背景執行緒不可共用請求那條連線，sqlite 預設 `check_same_thread=True`。）

`stock_custody` 的 return 之前加：

```python
    trend = get_custody_trend(c, pure)
    filling = pure in _custody_autofill
    if not filling and _should_autofill(trend):
        filling = _start_custody_autofill(code)
```

return 改成：

```python
    return {"code": pure, "week": (cur or {}).get("week_date"), "current": rec,
            "trend": trend, "weeks": len(trend), "backfilling": filling}
```

（**舊的手動端點 `/custody/backfill` 保留**，除錯與手動重補仍用得到。）

- [ ] **Step 4: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_shares.py tests/test_api.py -q -p no:cacheprovider -k "custody or autofill or endpoint"`
Expected: 全部 PASS。

- [ ] **Step 5: 反證**

暫時把 `_start_custody_autofill` 裡的 `if get_ai_cache(c, key) or pure in _custody_autofill:` 改成 `if False:`，跑 `test_autofill_runs_once_per_code_per_day` → 應 FAIL（起了兩次）。還原並比對位元組。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/api/stock.py tests/test_custody_shares.py
git commit -m "feat(api): 集保端點回人均數與週數，歷史不足時背景自動補"
```

---

### Task 4: 前端純函式——日期貼齊與週期聚合

**背景：** 三支 API 的頻率不同（K 線日/週/月、法人日、集保週）。Lightweight Charts 的時間軸是所有 series 時間的聯集，直接丟進不存在於 K 線的日期（集保週五遇假日、`stock_ohlc` 稀疏）會多出沒有 K 棒的刻度，看起來像破圖。所以先寫兩支純函式把外來資料貼到 K 棒上。

**Files:**
- Modify: `web/app.js`（在既有 `lwCandleData`／`toLwLineData` 那一區之後新增）
- Test: 無自動化測試（前端）；用瀏覽器 console 驗證，見 Step 3

**Interfaces:**
- Produces:
  - `snapToBars(barDates, dates, values)` → `{ byBar: Map }`：把 `dates[i]`／`values[i]` 貼到「≤ 該日期的最後一根 K 棒」上；同一根被貼到多筆時**取最新一筆**；早於第一根 K 棒的丟掉。值可以是任何型別（數字或整筆物件）；同一根取來源日期最晚的那一筆，與輸入順序無關。
  - `sumToBars(barDates, dates, valueArrays)` → `Array<Array<number|null>>`：同樣貼齊，但同一根 K 棒收到多筆時**相加**（週 K／月 K 的法人買賣超按期間加總）；整根都沒有資料回 `null`（不是 0——「沒有資料」與「買賣超剛好是 0」是兩件事）。`valueArrays` 是多條序列（外資／投信／自營），回傳同樣的條數，每條長度＝`barDates.length`。

- [ ] **Step 1: 寫程式**

在 `web/app.js` 的 `lwTimeLabel` 之後新增：

```js
// ===== 把外來的籌碼資料貼到 K 棒上 =====
// LWC 的時間軸是所有 series 時間的聯集：直接丟進「K 線沒有的日期」（集保週五遇假日、
// stock_ohlc 稀疏）會多出沒有 K 棒的刻度，看起來像破圖。所以一律往前貼到「≤ 該日期的
// 最後一根 K 棒」；貼不到（早於第一根）就丟掉。週K／月K 也靠這個規則落到對的那根棒子。
function barIndexFor(barDates, d) {
  let lo = 0, hi = barDates.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (barDates[mid] <= d) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}
// 同一根 K 棒收到多筆時取最新一筆（集保：月K 的一個月有四五週，規格說取最後一週）。
function snapToBars(barDates, dates, values) {
  const byBar = new Map();
  for (let i = 0; i < dates.length; i++) {
    const k = barIndexFor(barDates, dates[i]);
    if (k >= 0 && values[i] != null) byBar.set(barDates[k], values[i]);
  }
  return { byBar };
}
// 同一根 K 棒收到多筆時相加（法人：週K 按週加總、月K 按月加總）。整根都沒有資料回 null
// ——「沒有資料」與「買賣超剛好是 0」是兩件事，混在一起會讓空白日期畫出一根 0 的柱子。
function sumToBars(barDates, dates, valueArrays) {
  const out = valueArrays.map(() => new Array(barDates.length).fill(null));
  for (let i = 0; i < dates.length; i++) {
    const k = barIndexFor(barDates, dates[i]);
    if (k < 0) continue;
    valueArrays.forEach((arr, s) => {
      const v = arr[i];
      if (v == null) return;
      out[s][k] = (out[s][k] == null ? 0 : out[s][k]) + v;
    });
  }
  return out;
}
```

- [ ] **Step 2: 用瀏覽器實測這兩支純函式（含對不上的日期）**

先確認本機伺服器在跑（`.claude/launch.json` 的 `spr`；改了 Python 要重啟 preview），開首頁後在 console 執行：

```js
const bars = ["2026-09-14","2026-09-15","2026-09-16","2026-09-17","2026-09-18"];
// 貼齊：09-13（週末，貼到 09-12 以前→丟掉）、09-16 正常、09-19（晚於最後一根→貼到 09-18）
snapToBars(bars, ["2026-09-13","2026-09-16","2026-09-19"], [1, 2, 3]).byBar;
// 期望：Map { "2026-09-16" => 2, "2026-09-18" => 3 }（09-13 早於第一根，丟掉）
sumToBars(bars, ["2026-09-15","2026-09-15","2026-09-17"], [[10, 5, 7]]);
// 期望：[[null, 15, null, 7, null]]（同一根相加；沒有資料的是 null 不是 0）
```

把兩行的實際輸出貼進報告。**反證**：把 `sumToBars` 的 `null` 初值改成 0 再跑一次，第一個元素會變 0——證明這個區別是真的（看完改回來）。

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): 個股圖的籌碼日期貼齊與週期聚合純函式"
```

---

### Task 5: 三大法人堆疊柱（LWC 自訂 series）

**背景：** Lightweight Charts 沒有內建堆疊柱。v5 提供自訂 series 介面（`chart.addCustomSeries(view)`），要自己實作一個 pane view 來畫。正負混合要分別往上、往下疊。

**Files:**
- Modify: `web/app.js`（在 Task 4 的純函式之後新增）
- Test: 瀏覽器實測

**Interfaces:**
- Produces:
  - `StackedBarsSeries`：LWC 自訂 series 的 pane view 類別，資料點形狀 `{ time, values: [外資, 投信, 自營] }`（值可為 `null`），`options` 需要 `{ colors: [c1, c2, c3] }`。實作 `priceValueBuilder`（回傳 `[最高堆疊, 最低堆疊, 合計]` 供價格軸自動縮放與十字線用）、`isWhitespace`、`renderer`、`defaultOptions`。
  - 用法：`const s = chart.addCustomSeries(new StackedBarsSeries(), { colors: [SER.foreign, SER.trust, SER.dealer], priceLineVisible: false, lastValueVisible: false }, 2)`。

- [ ] **Step 1: 寫程式**

在 `web/app.js` 新增（放在 `sumToBars` 之後）：

```js
// ===== 三大法人堆疊柱（Lightweight Charts 自訂 series）=====
// LWC 沒有內建堆疊柱，v5 的自訂 series 介面讓我們自己畫一層：正值由 0 往上依序疊、
// 負值由 0 往下依序疊（外資→投信→自營），與原本 ECharts 堆疊柱的外觀一致。
// 資料點是 { time, values:[外資, 投信, 自營] }，null 當 0 畫、三個都 null 的那根不畫。
class StackedBarsRenderer {
  constructor() { this._data = null; this._options = null; }
  update(data, options) { this._data = data; this._options = options; }
  draw(target, priceConverter) {
    target.useBitmapCoordinateSpace((scope) => {
      if (!this._data || !this._data.bars.length) return;
      const ctx = scope.context;
      const ratio = scope.horizontalPixelRatio;
      // 柱寬：用 LWC 給的 barSpacing，留 30% 間隙，最少 1px（縮到很小時仍看得見）
      const width = Math.max(1, Math.floor(this._data.barSpacing * 0.7 * ratio));
      for (const bar of this._data.bars) {
        const vals = bar.originalData.values || [];
        const x = Math.round(bar.x * ratio) - Math.floor(width / 2);
        let up = 0, down = 0;    // 已經疊到哪（正、負各自累積）
        vals.forEach((v, i) => {
          if (v == null || v === 0) return;
          const from = v > 0 ? up : down;
          const to = from + v;
          const y1 = priceConverter(from) * scope.verticalPixelRatio;
          const y2 = priceConverter(to) * scope.verticalPixelRatio;
          ctx.fillStyle = this._options.colors[i];
          ctx.fillRect(x, Math.min(y1, y2), width, Math.max(1, Math.abs(y2 - y1)));
          if (v > 0) up = to; else down = to;
        });
      }
    });
  }
}
class StackedBarsSeries {
  constructor() { this._renderer = new StackedBarsRenderer(); }
  priceValueBuilder(d) {
    // 回傳 [最高, 最低, 收]：價格軸用前兩個自動縮放，十字線標籤用最後一個（合計）。
    const vals = (d.values || []).filter((v) => v != null);
    let up = 0, down = 0;
    vals.forEach((v) => { if (v > 0) up += v; else down += v; });
    return [up, down, vals.reduce((a, b) => a + b, 0)];
  }
  isWhitespace(d) { return !d.values || d.values.every((v) => v == null); }
  renderer() { return this._renderer; }
  update(data, options) { this._renderer.update(data, options); }
  defaultOptions() { return { colors: ["#4f9cf9", "#a07cff", "#f5b544"], lastValueVisible: false, priceLineVisible: false }; }
}
```

- [ ] **Step 2: 用真實資料在瀏覽器實測**

伺服器跑起來後，在 console 用真實 API 資料單獨建一張圖驗證（不動個股頁）：

```js
const el = Object.assign(document.createElement("div"), { style: "position:fixed;left:8px;bottom:8px;width:520px;height:160px;z-index:9999;background:#0f1419" });
document.body.appendChild(el);
const ch = LightweightCharts.createChart(el, { layout: { background: { type: "solid", color: "transparent" }, textColor: "#8a94a3" } });
const d = await (await fetch("/api/stock/2330/chips?days=60")).json();
const s = ch.addCustomSeries(new StackedBarsSeries(), { colors: [SER.foreign, SER.trust, SER.dealer] });
s.setData(d.dates.map((t, i) => ({ time: t, values: [d.foreign[i], d.trust[i], d.dealer[i]] })));
ch.timeScale().fitContent();
```

確認：柱子分三色堆疊、正負分別往上往下、與 `d.foreign/d.trust/d.dealer` 的數字對得起來（挑三根正負混合的逐一比對，把數字寫進報告）。**反證**：`s.setData(...)` 時把 `d.trust[i]` 換成 0 重畫，中間那一段顏色要消失。驗完 `el.remove()`。

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): 三大法人堆疊柱（Lightweight Charts 自訂 series）"
```

---

### Task 6: 四窗格與資料灌入

**背景：** 目前 `initStockChart` 建兩個窗格（價格、量能），`renderStockChart` 灌資料。這個任務把法人、集保兩格加進同一張圖，並把三支 API 的資料在前端合併（Task 4 的貼齊／聚合、Task 5 的堆疊柱）。

**Files:**
- Modify: `web/app.js`（`initStockChart`、`renderStockChart`、`disposeStockChart`、`loadStock`）
- Modify: `web/index.html`（`#stock-chart` 之後的兩個舊容器先留著，Task 8 才移除；本任務只改 `#stock-chart`）
- Modify: `web/styles.css`（`#stock-chart` 高度）
- Test: 瀏覽器實測

**Interfaces:**
- Consumes: `snapToBars`／`sumToBars`（Task 4）、`StackedBarsSeries`（Task 5）、`/api/stock/{code}/custody` 的 `trend[].avg_shares`（Task 2、3）。
- Produces:
  - 模組層變數 `lwInstSeries`（自訂 series）、`lwCustodySeries`（`{big1000, big400}` 兩條 LineSeries）、`lwCustodyMarkers`（箭頭圖層）、`lastStockChips`、`lastStockCustody`（兩支 API 的原始回應，給重畫與讀數列用）。
  - `renderStockPanes()`：用 `lastStockData`／`lastStockChips`／`lastStockCustody` 重算兩個窗格的資料並灌進去；三格的資料任一缺就那格留空。`loadStockChips`／`loadStockCustody` 改成只抓資料、存進上面兩個變數，然後呼叫它。

- [ ] **Step 1: 圖表高度**

`web/styles.css` 找到 `#stock-chart`（`contain: size` 那條規則）把高度改成 560px；手機段（`@media (max-width: 600px)`）加一條 `#stock-chart { height: 460px; }`。若既有規則是靠 `.chart-big` 的高度，改成在 `#stock-chart` 明確寫 `height: 560px`（保留既有的 `contain: size`——它擋住「flex 容器 × autoSize 互相把高度愈撐愈大」那個坑）。

- [ ] **Step 2: 建立兩個新窗格**

`initStockChart` 裡，量能 series 之後、`const panes = chart.panes();` 之前插入：

```js
  // 法人窗格（自訂堆疊柱）與集保窗格（兩條階梯線）：與價格、量能共用時間軸與十字線，
  // 這就是「合成一張」的重點——滑到哪一天，四格同時顯示那天的價格、法人、大戶。
  const inst = chart.addCustomSeries(new StackedBarsSeries(), {
    colors: [SER.foreign, SER.trust, SER.dealer], priceLineVisible: false, lastValueVisible: false,
  }, 2);
  // 集保兩條線沿用今天那張圖的顏色（千張大戶＝SER.foreign 冰藍、400張↑＝SER.trust 紫）。
  // 與法人窗格的外資／投信同色是刻意的取捨：兩者在不同窗格、各自的讀數列已標明名稱，
  // 使用者現在看到的就是這兩色，換色只會製造「顏色怎麼變了」的困惑。
  const cust1000 = chart.addSeries(LightweightCharts.LineSeries, {
    color: SER.foreign, lineWidth: 2, lineType: LightweightCharts.LineType.WithSteps,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  }, 3);
  const cust400 = chart.addSeries(LightweightCharts.LineSeries, {
    color: SER.trust, lineWidth: 2, lineType: LightweightCharts.LineType.WithSteps,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  }, 3);
```

（`loadStockCustody` 現在就是用 `SER.foreign`／`SER.trust` 畫這兩條線，直接沿用同一組，不新增色票。）

窗格比例改成（總高 560：主 300／量 70／法人 95／集保 95）：

```js
  const panes = chart.panes();
  // 比例＝各窗格高度佔比（主圖 300、量能 70、法人 95、集保 95，總高 560px）
  if (panes[1]) panes[1].setStretchFactor(0.23);
  if (panes[2]) panes[2].setStretchFactor(0.32);
  if (panes[3]) panes[3].setStretchFactor(0.32);
```

把三個新 series 存進模組層變數（跟 `lwCandleSeries` 同一組宣告處新增 `let lwInstSeries = null, lwCustodySeries = null, lwCustodyMarkers = null;`），在 `initStockChart` 結尾與既有的 `lwCandleSeries = candle; ...` 同一行區塊指派：`lwInstSeries = inst; lwCustodySeries = { big1000: cust1000, big400: cust400 };`（後面 `renderStockPanes`／`renderCustodyMarkers` 都吃 `lwCustodySeries.big1000`／`.big400` 這個形狀）。

- [ ] **Step 3: 灌資料**

在 `renderStockChart` 之後新增：

```js
// 用目前的三份資料（K 線／法人／集保）重算兩個籌碼窗格。K 線是主軸：籌碼一律貼到
// K 棒上（見 snapToBars/sumToBars 的註解）。時 K 沒有逐小時的籌碼資料，兩格清空。
function renderStockPanes() {
  if (!lwInstSeries || !lastStockData) return;
  const bars = lastStockData.dates || [];
  const hourly = stockInterval === "1h";   // 時K 的 data-iv 就是 "1h"（見 index.html 的 .ktf 按鈕）
  const chips = (!hourly && lastStockChips) || null;
  const cust = (!hourly && lastStockCustody) || null;
  if (chips && chips.dates && chips.dates.length) {
    const [f, t, dl] = sumToBars(bars, chips.dates, [chips.foreign, chips.trust, chips.dealer]);
    lwInstSeries.setData(bars.map((d, i) => ({ time: d, values: [f[i], t[i], dl[i]] })));
  } else lwInstSeries.setData([]);
  const trend = (cust && cust.trend) || [];
  if (trend.length) {
    const weeks = trend.map((x) => x.week);
    const b1000 = snapToBars(bars, weeks, trend.map((x) => x.big1000_pct)).byBar;
    const b400 = snapToBars(bars, weeks, trend.map((x) => x.big400_pct)).byBar;
    lwCustodySeries.big1000.setData([...b1000].map(([time, value]) => ({ time, value })));
    lwCustodySeries.big400.setData([...b400].map(([time, value]) => ({ time, value })));
    renderCustodyMarkers(bars, trend);
  } else {
    lwCustodySeries.big1000.setData([]); lwCustodySeries.big400.setData([]);
    if (lwCustodyMarkers) lwCustodyMarkers.setMarkers([]);
  }
}
```

`renderCustodyMarkers` 在 Task 7 實作；本任務先放一個空殼：

```js
function renderCustodyMarkers() { /* Task 7 實作人均數箭頭 */ }
```

`loadStockChips`／`loadStockCustody` 改成只抓資料（移除 ECharts 的部分留到 Task 8，本任務先讓它們額外存下資料並呼叫 `renderStockPanes()`）：在兩支函式取得 `d` 之後各加一行 `lastStockChips = d;`／`lastStockCustody = d;` 與 `renderStockPanes();`。`renderStockChart` 的最後也呼叫一次 `renderStockPanes()`（切週期／重新查詢時要跟著重畫）。

`disposeStockChart` 裡把新變數一併歸零：`lwInstSeries = null; lwCustodySeries = null; lwCustodyMarkers = null;`。

`loadStock` 在查新股票時把 `lastStockChips = null; lastStockCustody = null;`（否則切股票時會短暫看到上一檔的籌碼）。

- [ ] **Step 4: 預設視窗改成近 60 個交易日**

`renderStockChart` 裡設定可視範圍那段改成：

```js
  // 預設落在三格都有資料的區間：法人只有近 60 日（端點上限），所以預設就看近 60 根。
  // **用日期字串（setVisibleRange）不用邏輯索引**——邏輯索引會被 LWC 的最小柱寬悄悄改動
  // （既有教訓，見 CLAUDE.md）。
  const n = candles.length;
  if (n > 60) {
    stockChart.timeScale().setVisibleRange({ from: dates[n - 60], to: dates[n - 1] });
  } else {
    stockChart.timeScale().fitContent();
  }
```

- [ ] **Step 5: 瀏覽器實測**

重啟 preview，查 2330：四個窗格都出現、上下對齊、十字線一拉四格同時動；預設看到最近 60 根；切週K／月K 時法人柱變成加總（隨便挑一週，把該週的日資料相加與畫面上的柱高／讀數比對）；切時K 時兩格空白。截圖存證，數字寫進報告。**反證**：把 `renderStockPanes` 裡 `hourly` 的判斷拿掉，切到時K 會看到法人柱仍在（證明那道判斷有作用），看完改回來。

- [ ] **Step 6: Commit**

```bash
git add web/app.js web/styles.css
git commit -m "feat(ui): 個股圖加法人與集保兩個窗格，共用時間軸"
```

---

### Task 7: 人均數箭頭

**Files:**
- Modify: `web/app.js`（`renderCustodyMarkers`）
- Test: 瀏覽器實測

**Interfaces:**
- Consumes: `trend[].avg_shares`（Task 2）、`lwCustodySeries.big1000`（Task 6）。
- Produces: `renderCustodyMarkers(bars, trend)` 實作完成；箭頭圖層只建立一次，之後一律 `setMarkers` 換內容。

- [ ] **Step 1: 寫程式**

把 Task 6 的空殼換成：

```js
// 人均數（總股數÷總持股人數）箭頭：比上週高＝白色向上（籌碼往少數人集中）、
// 比上週低＝黃色向下（分散）。相等、任一週缺值、第一週（沒有前一週可比）都不標。
// **標記圖層只建一次、之後一律 setMarkers 換內容**——createSeriesMarkers 每呼叫一次就在
// series 上掛一個新的 primitive，丟掉舊參照並不會卸下它（艾略特波浪踩過的既有教訓）。
function renderCustodyMarkers(bars, trend) {
  const marks = [];
  for (let i = 1; i < trend.length; i++) {
    const cur = trend[i].avg_shares, prev = trend[i - 1].avg_shares;
    if (cur == null || prev == null || cur === prev) continue;
    const k = barIndexFor(bars, trend[i].week);
    if (k < 0) continue;
    marks.push({
      time: bars[k], position: cur > prev ? "aboveBar" : "belowBar",
      shape: cur > prev ? "arrowUp" : "arrowDown",
      color: cur > prev ? "#ffffff" : C.accent,
    });
  }
  if (lwCustodyMarkers) lwCustodyMarkers.setMarkers(marks);
  else if (marks.length) lwCustodyMarkers = LightweightCharts.createSeriesMarkers(lwCustodySeries.big1000, marks);
}
```

（黃色用既有的 `C.accent`——全站琥珀就是這個 token，不要另外寫死一個黃。）

- [ ] **Step 2: 瀏覽器實測**

查一檔有一年集保歷史的股票（若本機沒有，先手動打一次 `/api/stock/2330/custody/backfill?weeks=52`），確認：箭頭出現在集保窗格、方向與 `trend` 的 `avg_shares` 相鄰兩週大小關係一致（挑 5 週逐一比對，寫進報告）；白色向上、黃色向下。**反證**：把 `cur > prev` 改成 `cur < prev`，箭頭方向要整批反過來（看完改回來）。
同時記錄：52 週會有幾個箭頭、在 95px 高的窗格裡看起來密不密（截圖），留給使用者決定要不要改成「只標方向轉變」。

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): 集保窗格加人均數箭頭（上白下黃）"
```

---

### Task 8: 四行讀數列，移除舊圖與 tooltip

**Files:**
- Modify: `web/app.js`（`initStockChart` 的 tooltip 段、`disposeStockChart`、移除 `loadStockChips`／`loadStockCustody` 的 ECharts 部分與 `stockChipsChart`／`stockCustodyChart`、resize 掛勾、`trimEdges`、「補歷史」handler）
- Modify: `web/index.html`（移除 `#stock-chips-wrap`、`#stock-custody-wrap` 兩個區塊）
- Modify: `web/styles.css`（`.lw-readout` 樣式）
- Test: 瀏覽器實測

**Interfaces:**
- Produces: 四個 `.lw-readout` 元素（每個窗格左上角一行），由 `subscribeCrosshairMove` 更新；滑鼠離開時顯示最新一根。浮動 tooltip（`.lw-tooltip`）移除。

- [ ] **Step 1: 樣式**

`web/styles.css` 在 `.lw-legend` 附近新增：

```css
/* 每個窗格左上角一行讀數（取代浮動 tooltip）。pointer-events:none 才不會擋住圖表操作；
   z-index 壓在圖表 canvas 之上、但不蓋住 TradingView 授權標誌（它在右下角）。 */
.lw-readout { position: absolute; left: 8px; z-index: 3; pointer-events: none;
  font-size: var(--fs-xs); color: var(--text-secondary); white-space: nowrap;
  text-shadow: 0 1px 2px rgba(0,0,0,.55); }
.lw-readout b { color: var(--text-primary); font-weight: 650; }
.lw-readout .up { color: var(--up); } .lw-readout .down { color: var(--down); }
@media (max-width: 600px) { .lw-readout { font-size: 11px; } }
```

- [ ] **Step 2: 建立四行讀數列並在十字線移動時更新**

`initStockChart` 裡把 tooltip（`const tip = ...` 到 `stockTipHtml` 那整段）換成：

```js
  // 每個窗格左上角一行讀數（取代浮動 tooltip）：十字線移到哪根就顯示那根，滑鼠離開時
  // 顯示最新一根。位置用各窗格的高度比例推算，在 resize 後重算（LWC 沒有「給我某個窗格
  // 的 y 座標」的 API，窗格高度是 stretch factor 的比例）。
  const readouts = ["price", "vol", "inst", "cust"].map((k) => {
    const n = document.createElement("div");
    n.className = "lw-readout"; n.dataset.pane = k;
    el.appendChild(n); return n;
  });
  const layoutReadouts = () => {
    const h = el.clientHeight, f = [3.0, 0.7, 0.95, 0.95];   // 300/70/95/95
    const sum = f.reduce((a, b) => a + b, 0);
    let y = 0;
    f.forEach((x, i) => { readouts[i].style.top = Math.round(y) + 6 + "px"; y += (h * x) / sum; });
  };
  layoutReadouts();
  new ResizeObserver(layoutReadouts).observe(el);

  const fmtSigned = (v, dp) => (v == null ? "—" : (v > 0 ? "+" : "") + fmt(v, dp));
  const paintReadouts = (param) => {
    const dates = (lastStockData && lastStockData.dates) || [];
    const i = param && param.time
      ? dates.indexOf(lwTimeLabel(param.time))
      : dates.length - 1;                                  // 滑鼠離開＝顯示最新一根
    if (i < 0 || !lastStockData) { readouts.forEach((n) => (n.innerHTML = "")); return; }
    const c = lastStockData.candles[i] || [];
    const prev = i > 0 ? lastStockData.candles[i - 1][1] : null;
    const chg = prev != null ? c[1] - prev : null;
    const cls = chg == null ? "" : chg >= 0 ? "up" : "down";
    readouts[0].innerHTML = `${esc(dates[i].slice(5))}　開 <b>${fmt(c[0], 2)}</b> 高 <b>${fmt(c[3], 2)}</b> 低 <b>${fmt(c[2], 2)}</b> 收 <b class="${cls}">${fmt(c[1], 2)}</b>`
      + (chg == null ? "" : ` <span class="${cls}">${fmtSigned(chg, 2)}（${fmtSigned((chg / prev) * 100, 2)}%）</span>`);
    readouts[1].innerHTML = `量 <b>${fmt((lastStockData.volumes || [])[i] || 0, 0)}</b> 張`;
    readouts[2].innerHTML = stockInstReadout(dates[i]);
    readouts[3].innerHTML = stockCustodyReadout(dates[i]);
  };
  chart.subscribeCrosshairMove((param) => paintReadouts(param && param.point ? param : null));
  lwPaintReadouts = paintReadouts;   // 灌完資料後也要刷一次（顯示最新一根）
```

在模組層新增 `let lwPaintReadouts = null;`，並在 `renderStockPanes()` 結尾呼叫 `lwPaintReadouts && lwPaintReadouts(null)`。

兩支讀數字串（放在 `renderStockPanes` 之前）：

```js
// 法人／集保讀數：值取自貼齊後的資料（與畫出來的柱、線同一份，不另外算一次）。
let lwInstByBar = new Map(), lwCustByBar = new Map();
function stockInstReadout(bar) {
  const v = lwInstByBar.get(bar);
  if (!v) return `法人 <b>—</b>`;
  const [f, t, d] = v, sum = [f, t, d].reduce((a, b) => a + (b || 0), 0);
  const cell = (label, x) => `${label} <b class="${x > 0 ? "up" : x < 0 ? "down" : ""}">${x == null ? "—" : fmtSigned(x, 0)}</b>`;
  return `${cell("外資", f)}　${cell("投信", t)}　${cell("自營", d)}　${cell("合計", sum)} 張`;
}
function stockCustodyReadout(bar) {
  const v = lwCustByBar.get(bar);
  if (!v) return `集保 <b>—</b>`;
  return `千張大戶 <b>${fmt(v.big1000_pct, 2)}%</b>　400張↑ <b>${fmt(v.big400_pct, 2)}%</b>`
    + `　人均 <b>${v.avg_shares == null ? "—" : fmt(v.avg_shares, 0)}</b> 股（${esc(v.week.slice(5))}）`;
}
```

`fmtSigned` 目前定義在 `initStockChart` 內，改成模組層函式（兩處共用，不要複製第二份）。

`renderStockPanes()` 裡填這兩個 Map：法人 `lwInstByBar.set(bars[i], [f[i], t[i], dl[i]])`（三者皆 null 的不塞）；集保用 `snapToBars(bars, weeks, trend)`（值直接放整筆 trend 物件）取得 `byBar` 後指派給 `lwCustByBar`。兩格沒資料時 `new Map()` 清空。

- [ ] **Step 3: 移除舊圖**

- `web/index.html`：刪掉 `#stock-chips-wrap` 與 `#stock-custody-wrap` 兩個 `<div>` 區塊（含裡面的 `pane-title`、`#stock-chips`、`#stock-custody`、「補歷史」連結）。
- `web/app.js`：`loadStockChips`／`loadStockCustody` 只留「抓資料 → 存變數 → `renderStockPanes()`」，失敗或查無時把 `$("stock-note")` 補一句原因（例如「（查無此股三大法人資料）」），**窗格本身留著、資料清空、讀數列顯示「—」**（LWC 的窗格是跟著 series 走的，動態移除 series 會讓下一檔有資料時無從恢復，所以不做「整格不建立」）；刪掉 `stockChipsChart`／`stockCustodyChart` 兩個變數、它們在 window resize 掛勾清單裡的位置、`#custody-backfill` 的 click handler；`trimEdges` 若沒有其他呼叫端一併刪掉（先 grep 確認）。
- `disposeStockChart` 的殘留清理改成 `el.querySelectorAll(".lw-legend, .lw-readout")`（tooltip 已不存在）。

- [ ] **Step 4: 瀏覽器實測**

查 2330：四行讀數各自出現在自己的窗格左上角、不擋線；十字線移動時四行同步變、離開時回到最新一根；把某一天的四行數字與三支 API 的原始 JSON 逐欄比對（寫進報告）。切到時K：法人與集保兩行顯示「—」。反覆「查無資料的代號 → 有資料的代號」三輪，確認容器裡永遠只有一份 `.lw-legend` 與四個 `.lw-readout`（`document.querySelectorAll('#stock-chart .lw-readout').length === 4`）。1560／1280／375px 三個寬度各截一張圖，確認沒有水平溢出、讀數不重疊。

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/index.html web/styles.css
git commit -m "feat(ui): 四個窗格各一行讀數，移除舊的兩張圖與浮動 tooltip"
```

---

### Task 9: 自動補歷史接到前端

**Files:**
- Modify: `web/app.js`（`loadStockCustody`）
- Test: 瀏覽器實測

**Interfaces:**
- Consumes: `/api/stock/{code}/custody` 的 `backfilling`、`weeks`（Task 3）。
- Produces: `loadStockCustody(code)` 在 `backfilling` 為真時每 5 秒重問一次，週數變多就重畫集保窗格；最多 24 次（2 分鐘）後放棄。計時器記在模組層 `custodyPollTimer`，換股票或重查時先清掉（避免疊出多條輪詢——同 `irPollTimer` 的既有作法）。

- [ ] **Step 1: 寫程式**

```js
let custodyPollTimer = null;
// 集保歷史不足時後端會在背景補（約半分到一分鐘），這裡每 5 秒問一次、補完就重畫那一格。
// 計時器只能有一條：換股票、重新查詢都先清掉，否則會疊出多條輪詢（同法人研究頁的既有作法）。
async function loadStockCustody(code, tries = 0) {
  if (tries === 0 && custodyPollTimer) { clearTimeout(custodyPollTimer); custodyPollTimer = null; }
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/custody`);
    const before = lastStockCustody && lastStockCustody.weeks;
    lastStockCustody = d;
    renderStockPanes();
    if (d.backfilling && tries < 24 && code === stockCode) {
      custodyPollTimer = setTimeout(() => loadStockCustody(code, tries + 1), 5000);
    } else if (before != null && d.weeks > before) {
      custodyPollTimer = null;   // 補完了，畫面已在上面重畫
    }
  } catch (e) { /* 集保載入失敗只影響那一格，其餘照畫 */ }
}
```

`loadStock` 換股票時 `lastStockCustody = null;` 已在 Task 6 加過；這裡多一行 `if (custodyPollTimer) { clearTimeout(custodyPollTimer); custodyPollTimer = null; }`。

- [ ] **Step 2: 瀏覽器實測**

挑一檔本機沒有集保歷史的股票（先用 `sqlite3` 或一段 Python 刪掉某檔的 `custody_dist` 列，或直接查一檔從沒查過的），確認：圖先畫出來、集保格只有一兩點；約半分鐘後那一格自己變長（不必重新整理）。同一天再查同一檔，`/api/health` 不會再看到新的補歷史（或觀察後端 log 沒有第二次 `custody autofill`）。**反證**：把輪詢那段 `if (d.backfilling && ...)` 拿掉，補完後畫面不會自己更新（要手動重查才看得到），看完改回來。把兩次觀察寫進報告。

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): 集保歷史背景補完後自動重畫（取代手動補歷史連結）"
```

---

### Task 10: 快取版號、文件與整體驗證

**Files:**
- Modify: `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py`（版號 `20260817-ui65` → `20260817-ui66`）
- Modify: `CLAUDE.md`、`AGENTS.md`

- [ ] **Step 1: 版號**

用 Python 以位元組把三個檔裡的 `20260817-ui65` 換成 `20260817-ui66`（**不可 `sed -i`**）。換完確認四個檔沒有裸 LF：

```bash
.venv\Scripts\python -c "[print(p,(lambda b: b.count(b'\n')-b.count(b'\r\n'))(open(p,'rb').read())) for p in ['web/index.html','stocks_power_rich/api/public.py','tests/test_api.py','web/app.js']]"
```

Expected: 四個檔都印 `0`。

- [ ] **Step 2: CLAUDE.md 新增一節**（放在「### 個股 K 線改 Lightweight Charts（2026-09）」那節之後）

```markdown
### 個股頁三張圖合成一張（2026-09）

使用者要「K 線＋三大法人＋集保」用同一條時間軸看轉折（參考 XQ 的價量累計圖）。改成一張
Lightweight Charts、四個窗格（價格 300／量能 70／法人 95／集保 95，手機 240/55/80/85）：

- **籌碼一律貼到 K 棒上**（`snapToBars`／`sumToBars`）：LWC 的時間軸是所有 series 時間的聯集，
  丟進「K 線沒有的日期」（集保週五遇假日、`stock_ohlc` 稀疏）會多出沒有 K 棒的刻度＝破圖。
  貼到「≤ 該日期的最後一根」，早於第一根就丟掉。同一根多筆時：集保取最新一筆、法人相加
  （這就是週K／月K 的聚合）。**整根沒資料是 `null` 不是 0**——0 會畫出一根實心的零柱。
- **法人堆疊柱是自訂 series**（`StackedBarsSeries`）：LWC 沒有內建堆疊柱。正值由 0 往上、
  負值由 0 往下依序疊（外資→投信→自營）。`priceValueBuilder` 要回 `[最高, 最低, 合計]`，
  價格軸才自動縮放得對。
- **每個窗格左上角一行讀數**取代浮動 tooltip（使用者要求，同 XQ）：窗格的 y 位置靠 stretch
  factor 的比例推算（LWC 沒有「給我某窗格 y 座標」的 API），並掛 ResizeObserver 重算。
  滑鼠離開時顯示最新一根。**`disposeStockChart` 要清 `.lw-readout`**——只清 canvas 會讓
  反覆查詢疊出多份覆蓋層（既有教訓，原本清的是 `.lw-tooltip`）。
- **集保人均數＝總股數 ÷ 總持股人數**：`custody_dist` 新增 `total_shares`（lazy migration），
  `_aggregate_levels` 加總分級 1~15 的股數（合計列在 opendata 是第 17 級、智能網是第 16 級，
  所以一律用加總、不讀合計列）。**`upsert_custody` 原本連 `total_holders` 都沒寫**（只有
  `bulk_upsert_custody` 有），逐檔路徑因此永遠算不出人均數，一併補上、並用 `COALESCE`
  不讓沒帶欄位的來源洗掉既有值。人均數在後端算，前端不得再算一份。
- **人均數箭頭**：每週一個，比上週高＝白色向上（集中）、低＝黃色（`--accent`）向下（分散）；
  相等、缺值、第一週不標。標記圖層只建一次、之後 `setMarkers` 換內容（艾略特波浪踩過的
  primitive 累積坑）。
- **集保歷史改成背景自動補**（`_should_autofill`／`_start_custody_autofill`）：週數 < 30、
  或有股數的週數不到一半（舊資料沒有股數欄）就補；同一檔同一天只補一次（`custodyauto:{code}:{date}`）、
  同時只允許一個補歷史在跑（`_custody_lock`）。端點立刻回現有資料並帶 `backfilling`，
  前端每 5 秒問一次、最多 2 分鐘。手動端點 `/custody/backfill` 保留供除錯。
- 預設視窗是最近 60 根（法人端點上限 60 日，這樣打開就三格都有東西），用 `setVisibleRange`
  給日期字串、不用邏輯索引（既有教訓）。
```

- [ ] **Step 3: AGENTS.md 精簡段落**（放在個股 K 線那段附近）

```markdown
**個股頁三張圖合成一張（2026-09）**：K 線／量能／三大法人／集保四個窗格在同一張 Lightweight Charts，
共用時間軸與十字線。籌碼一律貼到 K 棒（`snapToBars`／`sumToBars`；週K/月K 的聚合就是貼齊時相加，
整根沒資料是 null 不是 0）。法人堆疊柱是自訂 series（LWC 沒有內建）。每格左上角一行讀數取代 tooltip，
`disposeStockChart` 要清 `.lw-readout`。集保加 `total_shares` 算人均數（後端算），每週一個箭頭：
比上週高＝白上、低＝黃下。集保歷史改背景自動補（每檔每天一次、同時只一個），端點回 `backfilling`，
前端每 5 秒輪詢、最多 2 分鐘。
```

- [ ] **Step 4: 全套測試**

Run: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`（前景、timeout 600000）
Expected: 摘要行 `N passed`、0 failed（N ＝ 1019 ＋ 本計畫新增的測試數）。

- [ ] **Step 5: 桌機與手機逐項檢查**

1560／1280／375px：四個窗格高度合計 560（手機 460）、頁面無水平溢出、讀數不重疊、箭頭看得見；
切走再切回個股頁四個窗格尺寸正確（既有的手動 resize 那段要涵蓋新窗格）；console 全程無錯誤與 CSP 警告。
各截一張圖。

- [ ] **Step 6: Commit**

```bash
git add web/index.html stocks_power_rich/api/public.py tests/test_api.py CLAUDE.md AGENTS.md
git commit -m "docs: 個股頁三張圖合成一張（ui66）"
```

- [ ] **Step 7: 回報**：列出每個任務的提交、全套測試摘要、瀏覽器截圖與逐項比對的數字，並提醒：推送要等使用者說「commit and push」；箭頭密度請使用者看過截圖再決定要不要改成「只標方向轉變」。
