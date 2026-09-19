# 週集保一公布就反映到自算選股 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新一週的 TDCC 集保一公布（週五晚上到週六）就寫入並重算自算選股；週六 Telegram 週報等新集保再送；前瞻紀錄不被收盤後才公布的資料改寫；頁面標出用的是哪一週集保。

**Architecture:** 新增排程 `custody_watch`（週五 17:00–23:30、週六 08:00–21:30，每 30 分鐘）只串流讀 TDCC 檔頭的資料日期，比資料庫最新「完整週」新才完整下載，並重算畫面上那一天的自算選股快取。週報 `picks_new_weekly` 改成週六 18:00–21:30 每 30 分鐘檢查，用 `custody_is_current` 與「本週已送」標記決定送不送。前瞻紀錄加「只在訊號日當天寫」的守衛。排程時段先擴充成支援多個分鐘。

**Tech Stack:** Python 3.11+、FastAPI、sqlite3、httpx（串流）、APScheduler、pytest；前端 vanilla JS（`web/app.js`）。

**Spec:** `docs/superpowers/specs/2026-09-19-custody-watch-design.md`

## Global Constraints

- 一律用繁體中文寫註解與文案；註解說明「為什麼」，比照檔案既有風格。
- 碰 TDCC（`opendata.tdcc.com.tw`）的每個請求都要 `verify=False`（憑證缺 SKI，既有規矩）。
- 排程邏輯放 `stocks_power_rich/api/helpers.py`，`main.py` 只呼叫；每支排程走既有 `run_job`（去重與執行紀錄）。
- 「現在」一律用 `helpers._now()`（測試用 monkeypatch 固定），排程邏輯不可直接呼叫 `datetime.now()` 判斷。
- 失敗要看得見：不可 `except: pass` 吞例外；排程函式讓例外往上拋給 `run_job` 記成 failed。
- 前瞻紀錄（`signal_ledger`）**只在訊號日當天**寫；收盤後才公布的集保不回頭改寫任何訊號日。
- 紅綠只給價格方向，琥珀只給「注意這格」與目前選擇；頁面新增的集保週用中性色。
- 快取版號（`web/index.html` 的 link 與 script、`stocks_power_rich/api/public.py` 兩處 replace、`tests/test_api.py` 四條斷言）改 app.js 時一起進版。本計畫建在 ui64 之上 → 進到 `20260817-ui65`。
- 改檔一律保留 CRLF；改版號用 Python 以位元組讀寫，**不可用 `sed -i`**（會把 CRLF 檔改成 LF）。
- pytest 一律單獨跑、讀摘要行，**不可接管線**（`pytest | tail` 會吃掉離開碼）。
- 測試指令：`.venv\Scripts\python -m pytest ...`；Windows 終端機會亂碼，要看中文輸出就寫檔再讀。
- CLAUDE.md 與 AGENTS.md 要一起更新。

## 前置作業（開始 Task 1 之前）

工作目錄在分支 `ssf-top30-price`，上面有**尚未提交**的 ui64（成交量前 30 加股價欄，測試 959 條已通過）。
1. 在 `ssf-top30-price` 只提交那 7 個檔：`AGENTS.md`、`CLAUDE.md`、`stocks_power_rich/api/public.py`、`tests/test_api.py`、`web/app.js`、`web/index.html`、`web/styles.css`（**不可 `git add .`**，工作目錄裡有不該提交的 `HANDOFF-*.md`、`CODEX_HANDOFF_*.md`）。不推送。
2. 從它開新分支 `custody-watch`。本計畫的提交都在這個分支上，**不推送**；推送要等使用者說「commit and push」。
3. 規格與計畫文件在 Task 1 的提交一起加入。

## 檔案結構

| 檔案 | 責任 | 動作 |
|---|---|---|
| `stocks_power_rich/db.py` | 完整週判定：`latest_complete_custody_week`、`custody_week_complete` | 修改 |
| `stocks_power_rich/updater.py` | `_accumulate_custody` 改用完整週判定、記下第一次取得時間 | 修改 |
| `stocks_power_rich/sources/tdcc.py` | `parse_custody_week_head`、`peek_custody_week`（只讀檔頭） | 修改 |
| `stocks_power_rich/api/helpers.py` | 多分鐘時段、`refresh_self_screen_cache(record_signals)` 與訊號日守衛、`custody_watch`、`custody_is_current`、週報等待 | 修改 |
| `stocks_power_rich/pick_push.py` | `compose_weekly_new_picks` 加 `custody_note` | 修改 |
| `stocks_power_rich/main.py` | 註冊 `custody_watch_fri`／`custody_watch_sat` | 修改 |
| `stocks_power_rich/selfcheck.py` | 覆蓋率帶 `custody_weeks`、`custody_fetched_at` | 修改 |
| `web/app.js` | 覆蓋率列顯示集保週 | 修改 |
| `tests/test_custody_watch.py` | 本功能的新測試 | 新增 |
| `tests/test_api.py`、`tests/test_job_runs.py`、`tests/test_scheduler.py` | 既有測試配合調整 | 修改 |
| `CLAUDE.md`、`AGENTS.md` | 文件 | 修改 |

---

### Task 1: 集保「完整週」判定，寫入不被逐檔回補擋住

**背景（實作前必讀）：** 個股頁「補歷史」按鈕會把**單一檔**寫進全市場還沒公布的那一週。`_accumulate_custody` 原本用 `latest_custody_week`（取 MAX）做 6 天節流、用 `custody_week_exists`（該週有任何一列就算）判斷已存在——只要週六早上有人點過一次補歷史，新一週全市場那一批就會被擋一整週。`db.custody_compare_weeks` 已經有「殘缺週」判定（某週檔數不到近 10 週最大週的 `CUSTODY_WEEK_MIN_FRAC`＝0.5 就略過），這裡沿用同一個門檻。

**Files:**
- Modify: `stocks_power_rich/db.py`（`custody_compare_weeks` 之後新增兩個函式）
- Modify: `stocks_power_rich/updater.py:10-22`（import）、`:27-44`（`_accumulate_custody`）
- Create: `tests/test_custody_watch.py`

**Interfaces:**
- Produces:
  - `db.latest_complete_custody_week(conn) -> str | None`：最新的完整週（ISO 日期）。
  - `db.custody_week_complete(conn, week: str) -> bool`。
  - `ai_cache` 鍵 `custody_fetched:{week}` → `{"at": "YYYY-MM-DDTHH:MM:SS"}`，只在該週**第一次**寫入時記。

- [ ] **Step 1: 建立測試檔（含整個檔案會用到的 import 與共用 helper）並寫失敗的測試**

新增 `tests/test_custody_watch.py`：

```python
"""週集保一公布就反映到自算選股（docs/superpowers/specs/2026-09-19-custody-watch-design.md）。"""
import json
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db, ledger, pick_push, selfcheck, telegram_push, updater
from stocks_power_rich.api import helpers
from stocks_power_rich.config import Config
from stocks_power_rich.sources import tdcc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


FULL = [f"{1000 + i}" for i in range(10)]


def _week(c, week, codes):
    db.bulk_upsert_custody(c, week, {code: {"big400_pct": 50.0, "total_holders": 100} for code in codes})


def test_complete_week_ignores_single_stock_backfill(conn):
    """逐檔回補把一檔寫進新週：那一週不算完整，最新完整週仍是上一週。"""
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])
    assert db.latest_complete_custody_week(conn) == "2026-09-11"
    assert db.custody_week_complete(conn, "2026-09-11") is True
    assert db.custody_week_complete(conn, "2026-09-18") is False
    assert db.custody_week_complete(conn, "2026-09-25") is False   # 完全沒有列


def test_accumulate_custody_is_not_blocked_by_a_partial_week(conn, monkeypatch):
    """新週只有逐檔回補的一檔時，全市場那一批仍要寫入（舊寫法會被 6 天節流與「已存在」擋一整週）。"""
    prev = (date.today() - timedelta(days=8)).isoformat()
    new = (date.today() - timedelta(days=1)).isoformat()
    _week(conn, prev, FULL)
    _week(conn, new, ["2330"])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    n = conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=?", (new,)).fetchone()[0]
    assert n == len(FULL) + 1          # 全市場 10 檔＋原本那一檔（2330 不在 FULL 裡）
    assert db.custody_week_complete(conn, new) is True


def test_accumulate_custody_records_first_fetch_time_once(conn, monkeypatch):
    new = date.today().isoformat()
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    stamp = db.get_ai_cache(conn, f"custody_fetched:{new}")
    assert stamp and datetime.fromisoformat(stamp["at"])
    db.set_ai_cache(conn, f"custody_fetched:{new}", {"at": "2000-01-01T00:00:00"})
    assert updater._accumulate_custody(conn) is None                    # 同一週：節流擋下
    assert db.get_ai_cache(conn, f"custody_fetched:{new}")["at"] == "2000-01-01T00:00:00"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider`
Expected: FAIL，`AttributeError: module 'stocks_power_rich.db' has no attribute 'latest_complete_custody_week'`。

- [ ] **Step 3: 實作 db 的兩個函式**

在 `stocks_power_rich/db.py` 的 `custody_compare_weeks` 函式之後（`custody_change_map` 之前）新增：

```python
def latest_complete_custody_week(conn: sqlite3.Connection) -> str | None:
    """最新的**完整**集保週（ISO 日期），略過逐檔回補造成的殘缺週。

    不可用 `latest_custody_week`（MAX）：個股頁「補歷史」會把單一檔寫進全市場還沒公布的
    那一週，MAX 會指到那個只有一檔的週，讓 `_accumulate_custody` 的節流與「已存在」判斷
    把全市場那一批擋一整週。判定門檻與 `custody_compare_weeks` 同一個。"""
    weeks = custody_compare_weeks(conn)
    return weeks[0] if weeks else None


def custody_week_complete(conn: sqlite3.Connection, week: str) -> bool:
    """這一週是不是完整的全市場批次（檔數達到近 10 週最大週的 CUSTODY_WEEK_MIN_FRAC）。"""
    counts = conn.execute(
        "SELECT week, COUNT(*) FROM custody_dist WHERE big400_pct IS NOT NULL "
        "GROUP BY week ORDER BY week DESC LIMIT 10").fetchall()
    if not counts:
        return False
    mx = max(n for _, n in counts)
    n = dict(counts).get(week)
    if n is None:
        n = conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=? AND big400_pct IS NOT NULL",
                         (week,)).fetchone()[0]
    return n > 0 and n >= mx * CUSTODY_WEEK_MIN_FRAC
```

- [ ] **Step 4: 改 `_accumulate_custody`**

`stocks_power_rich/updater.py` 的 `from .db import (...)` 區塊改成（依字母順序）：

```python
from .db import (
    bulk_upsert_custody,
    bulk_upsert_financials,
    bulk_upsert_ohlc,
    bulk_upsert_revenue,
    custody_week_complete,
    get_ai_cache,
    get_setting,
    latest_complete_custody_week,
    ohlc_dates,
    set_ai_cache,
    set_setting,
    upsert_market_daily,
    upsert_tx_history,
)
```

（`custody_week_exists`／`latest_custody_week` 在 updater.py 裡只有 `_accumulate_custody` 用；db.py 的定義保留，別的地方可能還在用。）

把 `_accumulate_custody` 整個換成：

```python
def _accumulate_custody(conn) -> str | None:
    """偵測到新的一週才抓 TDCC 全市場集保大戶比並批次入庫（趨勢逐週累積）。

    若資料庫最近一個**完整**週在 6 天內（同一週）即略過，連抓都免；跨到新一週才下載並 bulk 寫入。
    「完整週」而不是 MAX：個股頁「補歷史」會把單一檔寫進全市場還沒公布的那一週，用 MAX 或
    「該週有任何一列」判斷，全市場那一批會被擋一整週（見 db.latest_complete_custody_week）。
    第一次寫入某週時記下時間（ai_cache `custody_fetched:{week}`），用來量 TDCC 實際幾點公布。
    """
    last = latest_complete_custody_week(conn)
    if last:
        try:
            if (_date.today() - _date.fromisoformat(last)).days < 6:
                return None
        except (TypeError, ValueError):
            pass
    cur = tdcc.fetch_custody_distribution()
    week, data = cur.get("week_date"), cur.get("data") or {}
    if not week or not data or custody_week_complete(conn, week):
        return None
    bulk_upsert_custody(conn, week, data)
    key = f"custody_fetched:{week}"
    if not get_ai_cache(conn, key):
        set_ai_cache(conn, key, {"at": datetime.now().isoformat(timespec="seconds")})
    return week
```

- [ ] **Step 5: 跑新測試與既有測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_updater.py -q -p no:cacheprovider`
Expected: 全部 PASS（既有 `test_accumulate_custody_stores_new_week_then_skips` 也要過）。

- [ ] **Step 6: 反證**

用 Python 暫時把 `_accumulate_custody` 的 `last = latest_complete_custody_week(conn)` 換成舊行為 `last = conn.execute("SELECT MAX(week) FROM custody_dist").fetchone()[0]`（`try/finally` 保證還原），跑 `test_accumulate_custody_is_not_blocked_by_a_partial_week` 應 FAIL；還原後應 PASS，`git diff stocks_power_rich/updater.py` 只剩本任務的改動。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/db.py stocks_power_rich/updater.py tests/test_custody_watch.py docs/superpowers/specs/2026-09-19-custody-watch-design.md docs/superpowers/plans/2026-09-19-custody-watch.md
git commit -m "fix(custody): 集保寫入改看完整週，逐檔回補不再擋住全市場批次"
```

---

### Task 2: TDCC 檔頭輕量讀取

**Files:**
- Modify: `stocks_power_rich/sources/tdcc.py`（在 `fetch_custody_distribution` 之後新增）
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Produces:
  - `tdcc.parse_custody_week_head(raw: bytes) -> str | None`：純函式，檔頭 bytes → ISO 資料日期；表頭不是「資料日期」開頭或少於兩列回 `None`。
  - `tdcc.peek_custody_week(timeout: float = 30) -> str | None`：串流讀到第二列就關閉連線；HTTP 錯誤與連線錯誤往上拋；格式不符回 `None`。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
HEAD = "﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260918,000218,1,0,0,0.00\n20260918,0002".encode("utf-8")


def test_parse_custody_week_head_reads_date_from_second_line():
    assert tdcc.parse_custody_week_head(HEAD) == "2026-09-18"


def test_parse_custody_week_head_rejects_unexpected_format():
    assert tdcc.parse_custody_week_head(b"") is None
    assert tdcc.parse_custody_week_head("資料日期,證券代號\n".encode("utf-8")) is None      # 只有表頭
    assert tdcc.parse_custody_week_head("<html>維護中</html>\n\n".encode("utf-8")) is None


class _FakeStream:
    def __init__(self, chunks, calls):
        self.chunks, self.calls = chunks, calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("closed")

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        for ch in self.chunks:
            self.calls.append("chunk")
            yield ch


def test_peek_custody_week_stops_after_second_line_and_skips_tls_verify(monkeypatch):
    calls, seen = [], {}
    rest = [b"x" * 100] * 50                                     # 後面還有很多塊，不應該讀到

    def fake_stream(method, url, **kw):
        seen.update(kw)
        return _FakeStream([HEAD[:20], HEAD[20:]] + rest, calls)
    monkeypatch.setattr(tdcc.httpx, "stream", fake_stream)
    assert tdcc.peek_custody_week() == "2026-09-18"
    assert seen["verify"] is False and seen["params"] == {"id": "1-5"}
    assert calls.count("chunk") == 2 and calls[-1] == "closed"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k "head or peek"`
Expected: FAIL，`AttributeError: module 'stocks_power_rich.sources.tdcc' has no attribute 'parse_custody_week_head'`。

- [ ] **Step 3: 實作**

在 `stocks_power_rich/sources/tdcc.py` 的 `fetch_custody_distribution` 之後新增：

```python
def parse_custody_week_head(raw: bytes) -> str | None:
    """opendata CSV 的開頭幾個位元組 → 資料日期（ISO）。第一列必須是「資料日期」開頭的表頭、
    第二列第一格是 YYYYMMDD；格式不符回 None（呼叫端當成改版處理，不可當成「沒有新週」）。"""
    text = raw.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or not lines[0].startswith("資料日期"):
        return None
    return _ymd(lines[1].split(",")[0])


def peek_custody_week(timeout: float = 30) -> str | None:
    """只讀 TDCC 集保檔的頭兩列就關閉連線，回傳資料日期——輪詢「新一週出來了沒」用，
    不必每 30 分鐘下載整份（數 MB）。verify=False 理由同 fetch_custody_distribution。"""
    buf = b""
    with httpx.stream("GET", TDCC_URL, params={"id": "1-5"}, timeout=timeout,
                      follow_redirects=True, verify=False) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            buf += chunk
            if buf.count(b"\n") >= 2 or len(buf) > 65536:
                break
    return parse_custody_week_head(buf)
```

- [ ] **Step 4: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider`
Expected: 全部 PASS。

- [ ] **Step 5: 對真實 TDCC 驗一次**（只讀檔頭，不寫任何資料）

Run: `.venv\Scripts\python -c "from stocks_power_rich.sources import tdcc; print(tdcc.peek_custody_week())"`
Expected: 印出一個 ISO 日期（2026-09-19 實測是 `2026-09-18`）。失敗就把錯誤原文記進報告，不要改成吞例外。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/tdcc.py tests/test_custody_watch.py
git commit -m "feat(tdcc): 只讀檔頭取得集保資料日期（輪詢用）"
```

---

### Task 3: 排程時段支援多個分鐘

**背景：** `helpers.slot_times` 用 `int(spec["minute"])`，分鐘寫成 `"0,30"` 會丟 `ValueError`；補跑與 run_key 都靠它。APScheduler 本身吃得下 `minute="0,30"`。

**Files:**
- Modify: `stocks_power_rich/api/helpers.py`（`_cron_hours` 之後新增 `_cron_minutes`；改 `slot_times`、`run_key_for`）
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Produces: `slot_times(spec, day)` 支援 `minute` 為逗號列表；`run_key_for(spec, slot)` 在「一天多場」（小時數×分鐘數 > 1）時回 `日期:HH:MM`。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
def test_slot_times_support_multiple_minutes():
    spec = {"id": "x", "family": "x", "hour": "18-21", "minute": "0,30", "dow": "sat"}
    sat = date(2026, 9, 19)
    got = [t.strftime("%H:%M") for t in helpers.slot_times(spec, sat)]
    assert got == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]
    assert helpers.run_key_for(spec, datetime(2026, 9, 19, 20, 30)) == "2026-09-19:20:30"
    assert helpers.scheduled_run_key(spec, datetime(2026, 9, 19, 20, 31, 5)) == "2026-09-19:20:30"
    one = {"id": "y", "family": "y", "hour": "21", "minute": "0", "dow": None}
    assert helpers.run_key_for(one, datetime(2026, 9, 19, 21, 0)) == "2026-09-19"   # 一天一場照舊
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k multiple_minutes`
Expected: FAIL，`ValueError: invalid literal for int() with base 10: '0,30'`。

- [ ] **Step 3: 實作**

在 `helpers.py` 的 `_cron_hours` 函式之後新增：

```python
def _cron_minutes(minute: str) -> list[int]:
    """分鐘欄：單一值或逗號列表（custody_watch／週報是 "0,30"）。"""
    return sorted({int(p) for p in str(minute).split(",")})
```

把 `slot_times` 的本體（docstring 之後）換成：

```python
    if not spec.get("catchup", True) or not _dow_matches(spec.get("dow"), day.weekday()):
        return []
    return sorted(datetime(day.year, day.month, day.day, h, m)
                  for h in _cron_hours(spec["hour"]) for m in _cron_minutes(spec["minute"]))
```

把 `run_key_for` 的本體（docstring 之後）換成：

```python
    multi = len(_cron_hours(spec["hour"])) * len(_cron_minutes(spec["minute"])) > 1
    return slot.strftime("%Y-%m-%d:%H:%M") if multi else slot.strftime("%Y-%m-%d")
```

docstring 裡「一天多場（self_screen_early 三次）」改成「一天多場（self_screen_early 三次、custody_watch 與週報每 30 分）」。

- [ ] **Step 4: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_job_runs.py -q -p no:cacheprovider`
Expected: 全部 PASS（既有 `test_slot_times_respect_day_of_week_and_multi_hours` 也要過）。

- [ ] **Step 5: Commit**

```bash
git add stocks_power_rich/api/helpers.py tests/test_custody_watch.py
git commit -m "feat(scheduler): 排程時段支援多個分鐘（每 30 分鐘）"
```

---

### Task 4: 前瞻紀錄只在訊號日當天寫

**背景：** 訊號的進場價是訊號日收盤。週六 21:00 的 `run_update` 會用新集保重算 09-18 的名單；若那天的前瞻紀錄因故還沒寫，舊碼會在週六用週六才公布的集保補寫 09-18，前瞻勝率被高估且補不回來。`refresh_self_screen_cache` 在函式內 `from ..ledger import record_self_screen_signals`，所以測試 monkeypatch `ledger.record_self_screen_signals` 就攔得到。

**Files:**
- Modify: `stocks_power_rich/api/helpers.py`（`refresh_self_screen_cache`）
- Modify: `tests/test_api.py`（兩條既有測試要固定「現在」在訊號日）
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Produces: `refresh_self_screen_cache(c, day: str | None = None, record_signals: bool = True) -> dict`；成功時回傳 dict 多一個鍵 `"recorded": bool`（這次有沒有寫前瞻紀錄）。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
def _ready(c, day):
    for market in ("TWSE", "TPEx"):
        for source in ("quotes", "institutional"):
            db.set_stock_source_coverage(c, day, market, source, "complete", 1, None)


def _universe(monkeypatch):
    monkeypatch.setattr(helpers, "_industry_map",
                        lambda c: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1e9}})
    monkeypatch.setattr(helpers, "_otc_industry",
                        lambda c: {"8069": {"sector": "光電業", "name": "元太", "shares": 1e9}})


@pytest.mark.parametrize("now,record_signals,expect", [
    (datetime(2026, 9, 18, 21, 0), True, True),     # 訊號日當天：寫
    (datetime(2026, 9, 19, 21, 0), True, False),    # 週六重算週五：不寫（收盤後才公布的集保）
    (datetime(2026, 9, 18, 21, 0), False, False),   # custody_watch 明確不寫
])
def test_refresh_records_signals_only_on_the_signal_day(conn, monkeypatch, now, record_signals, expect):
    db.upsert_market_daily(conn, {"date": "2026-09-18", "taiex": 20000.0})
    conn.commit()
    _ready(conn, "2026-09-18")
    _universe(monkeypatch)
    calls = []
    monkeypatch.setattr(ledger, "record_self_screen_signals", lambda *a, **k: calls.append(k.get("signal_date")))
    monkeypatch.setattr(helpers, "_now", lambda: now)
    res = helpers.refresh_self_screen_cache(conn, record_signals=record_signals)
    assert res["cached"] is True and res["date"] == "2026-09-18"
    assert res["recorded"] is expect
    assert calls == (["2026-09-18"] if expect else [])
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k signal_day`
Expected: FAIL，`TypeError: refresh_self_screen_cache() got an unexpected keyword argument 'record_signals'`。

- [ ] **Step 3: 實作**

簽名改成：

```python
def refresh_self_screen_cache(c, day: str | None = None, record_signals: bool = True) -> dict:
```

docstring 末段補一段：

```
    `record_signals=False`（custody_watch 用）只重算快取、不碰前瞻紀錄。就算為 True，也**只有在
    訊號日當天**（`_now()` 的日期＝day）才寫前瞻紀錄：進場價是訊號日收盤，週末重算週五名單時
    用的是收盤後才公布的集保，拿它補寫那天的訊號等於用未來資料回測。代價是週五排程整晚失敗時，
    週六補算不會補記那一天——少一天樣本可以接受，偏一天會讓結論失真（同 partial_universe 的取捨）。
```

把原本這兩行：

```python
    # 前瞻追蹤吃同一份，不重算；訊號日明講是哪一天（提早計算時 market_daily 還沒有今天）
    record_self_screen_signals(c, universe, vmin, smin, precomputed=pre, signal_date=day)
```

換成：

```python
    # 前瞻追蹤吃同一份，不重算；訊號日明講是哪一天（提早計算時 market_daily 還沒有今天）。
    # 只在訊號日當天寫（見 docstring）。
    recorded = bool(record_signals and _now().date().isoformat() == day)
    if recorded:
        record_self_screen_signals(c, universe, vmin, smin, precomputed=pre, signal_date=day)
```

最後的 `return {"cached": True, ...}` 加上 `"recorded": recorded,`。

- [ ] **Step 4: 調整兩條既有測試**

`tests/test_api.py` 的 `test_refresh_self_screen_cache_waits_until_the_days_data_is_in` 與 `test_refresh_self_screen_cache_refuses_half_a_market` 都斷言「到齊時會寫前瞻紀錄」（`recorded == [1]`），但沒有固定「現在」；新守衛下真實今天不是 2026-10-01 就不會寫。兩條測試都在 `monkeypatch.setattr(ledger, "record_self_screen_signals", ...)` 那一行之後加：

```python
    monkeypatch.setattr(H, "_now", lambda: datetime(2026, 10, 1, 21, 0))   # 訊號日當天才寫前瞻紀錄
```

並在兩條測試函式開頭的 import 區加 `from datetime import datetime`。**不可放寬斷言**——這兩條測的是「資料到齊／兩個市場都有才寫」，固定現在只是讓它們回到原本要測的條件。

- [ ] **Step 5: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_api.py -q -p no:cacheprovider -k "self_screen or signal_day"`
Expected: 全部 PASS（`test_early_self_screen_*` 也要過：它把 `_now` 固定在 2026-10-01 並算 10-01，照常記錄）。

- [ ] **Step 6: 反證**

暫時把 `recorded = bool(record_signals and _now().date().isoformat() == day)` 改成 `recorded = bool(record_signals)`，參數化的週六那一格應 FAIL；還原後 PASS。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/api/helpers.py tests/test_custody_watch.py tests/test_api.py
git commit -m "fix(ledger): 自算選股前瞻紀錄只在訊號日當天寫"
```

---

### Task 5: `custody_watch` 排程

**背景：** 重算的必須是**快取裡那一天**（畫面上正在顯示的名單），不是 `_latest_date`（`market_daily` 最新一列）。週五 17:30 名單已算好，但 `market_daily` 要到 21:00 的每日更新才有週五那一列；若週五 19:00 新集保進來、拿 `_latest_date` 重算，會算成週四、把週五的名單蓋掉。沒有快取時才退回最新交易日（`day=None`）。

**Files:**
- Modify: `stocks_power_rich/api/helpers.py`（新增 `custody_watch`；`job_schedule` 加兩筆）
- Modify: `stocks_power_rich/main.py`（新增 job 函式；`raw_jobs` 加兩筆）
- Modify: `tests/test_job_runs.py`（排程 id 集合、週五補跑集合）
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Consumes: `tdcc.peek_custody_week()`（Task 2）、`db.latest_complete_custody_week`（Task 1）、`updater._accumulate_custody`（Task 1）、`refresh_self_screen_cache(c, day=..., record_signals=False)`（Task 4）、`selfcheck.load_latest_precomputed(conn)`（既有，回快取 dict 或 None）。
- Produces: `helpers.custody_watch(c) -> dict`，回傳其中一種：
  - `{"skipped": "no_new_week", "tdcc": str, "local": str | None}`
  - `{"skipped": "not_stored", "tdcc": str, "local": str | None}`
  - `{"week": str, "fetched_at": str | None, "self_screen": {"cached": bool, "date": str | None, "skipped": str | None}}`
  - 檔頭讀不到日期時丟 `RuntimeError`（由 `run_job` 記成 failed）。
- 排程 id：`custody_watch_fri`、`custody_watch_sat`，family 都是 `custody_watch`。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
def test_custody_watch_does_nothing_when_tdcc_has_no_new_week(conn, monkeypatch):
    _week(conn, "2026-09-18", FULL)
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應該被呼叫"))
    monkeypatch.setattr(updater, "_accumulate_custody", boom)
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", boom)
    assert helpers.custody_watch(conn) == {"skipped": "no_new_week", "tdcc": "2026-09-18",
                                           "local": "2026-09-18"}


def test_custody_watch_stores_new_week_and_recomputes_the_listed_day_without_ledger(conn, monkeypatch):
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])                       # 逐檔回補的殘缺週不算
    selfcheck.save_precomputed(conn, {"date": "2026-09-18", "rows": [], "heatmap": [], "coverage": {}})
    db.upsert_market_daily(conn, {"date": "2026-09-17", "taiex": 20000.0})   # 21:00 前 market_daily 只到週四
    conn.commit()
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: "2026-09-18")
    monkeypatch.setattr(updater, "_accumulate_custody", lambda c: "2026-09-18")
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})
    seen = {}

    def fake_refresh(c, day=None, record_signals=True):
        seen.update(day=day, record_signals=record_signals)
        return {"cached": True, "date": day}
    monkeypatch.setattr(helpers, "refresh_self_screen_cache", fake_refresh)
    r = helpers.custody_watch(conn)
    assert r["week"] == "2026-09-18" and r["fetched_at"] == "2026-09-19T09:30:00"
    assert r["self_screen"] == {"cached": True, "date": "2026-09-18", "skipped": None}
    assert seen == {"day": "2026-09-18", "record_signals": False}   # 重算畫面上那一天，不是 09-17


def test_custody_watch_raises_when_the_head_cannot_be_read(conn, monkeypatch):
    monkeypatch.setattr(tdcc, "peek_custody_week", lambda: None)
    with pytest.raises(RuntimeError):
        helpers.custody_watch(conn)


def test_custody_watch_is_scheduled_friday_evening_and_saturday():
    cfg = Config()                                            # 不綁任何推播設定
    by_id = {s["id"]: s for s in helpers.job_schedule(cfg, "21:00")}
    fri, sat = date(2026, 9, 18), date(2026, 9, 19)
    f = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["custody_watch_fri"], fri)]
    s = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["custody_watch_sat"], sat)]
    assert (f[0], f[-1], len(f)) == ("17:00", "23:30", 14)
    assert (s[0], s[-1], len(s)) == ("08:00", "21:30", 28)
    assert helpers.slot_times(by_id["custody_watch_fri"], sat) == []
    assert by_id["custody_watch_fri"]["family"] == by_id["custody_watch_sat"]["family"] == "custody_watch"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k custody_watch`
Expected: FAIL，`AttributeError: module 'stocks_power_rich.api.helpers' has no attribute 'custody_watch'`。

- [ ] **Step 3: 實作 `custody_watch`**

在 `helpers.py` 的 `refresh_self_screen_cache` 之後新增：

```python
def custody_watch(c) -> dict:
    """週集保輪詢（排程 custody_watch：週五 17:00–23:30、週六 08:00–21:30，每 30 分鐘）。

    只讀 TDCC 檔頭的資料日期；比資料庫最新**完整**週新，才完整下載寫入並重算自算選股快取。
    重算**不寫前瞻紀錄**（record_signals=False）：新集保是收盤後才公布的，不可改寫任何訊號日。
    重算的是**快取裡那一天**（畫面上的名單），不是 market_daily 最新一列：週五 21:00 前
    market_daily 還沒有週五，拿它重算會算成週四、蓋掉 17:30 算好的週五名單。沒有快取才退回最新交易日。
    例外往上拋給 run_job 記成 failed，下一個 30 分鐘再試；21:00 的每日更新仍會照舊抓，漏不掉。
    """
    from .. import selfcheck, updater
    from ..db import latest_complete_custody_week
    from ..sources import tdcc

    remote = tdcc.peek_custody_week()
    if not remote:
        raise RuntimeError("TDCC 集保檔頭讀不到資料日期（格式可能改版）")
    local = latest_complete_custody_week(c)
    if local and remote <= local:
        return {"skipped": "no_new_week", "tdcc": remote, "local": local}
    week = updater._accumulate_custody(c)
    if not week:
        return {"skipped": "not_stored", "tdcc": remote, "local": local}
    fetched = (get_ai_cache(c, f"custody_fetched:{week}") or {}).get("at")
    listed = (selfcheck.load_latest_precomputed(c) or {}).get("date")
    ss = refresh_self_screen_cache(c, day=listed, record_signals=False)
    return {"week": week, "fetched_at": fetched,
            "self_screen": {k: ss.get(k) for k in ("cached", "date", "skipped")}}
```

- [ ] **Step 4: 註冊排程**

在 `job_schedule` 的 `specs` 清單裡、`ssf_daily` 那筆之後加：

```python
        # 週集保：TDCC 每週公布一次、確切時間未量到（2026-09-19 週六 13:23 實測已是 09-18 週；
        # 週五 21:00 那次還沒有）。每 30 分鐘只讀檔頭，新週一到就寫入並重算自算選股，
        # 第一次取得的時間記在 ai_cache custody_fetched:{week}，累積幾週後可收窄時段。
        {"id": "custody_watch_fri", "family": "custody_watch",
         "hour": "17-23", "minute": "0,30", "dow": "fri"},
        {"id": "custody_watch_sat", "family": "custody_watch",
         "hour": "8-21", "minute": "0,30", "dow": "sat"},
```

`main.py`：在 `picks_new_job` 定義之後新增

```python
    def custody_watch_job():
        """週集保輪詢。邏輯在 api/helpers.custody_watch；失敗由 run_job 記成 failed。"""
        return _helpers.custody_watch(conn())
```

並在 `raw_jobs` 加兩筆：

```python
        "custody_watch_fri": custody_watch_job,
        "custody_watch_sat": custody_watch_job,
```

`tests/test_job_runs.py`：
- `test_job_schedule_depends_on_configured_channels` 第一個斷言的集合加入 `"custody_watch_fri", "custody_watch_sat"`。
- `test_catchup_plan_only_today_latest_slot_per_family`（`FRI_22`＝週五 22:00，22:00 那一場算已到）的 `set(plan)` 集合加入 `"custody_watch_fri"`，並加一行 `assert plan["custody_watch_fri"]["run_key"] == "2026-09-11:22:00"`。

- [ ] **Step 5: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_job_runs.py tests/test_scheduler.py tests/test_health.py -q -p no:cacheprovider`
Expected: 全部 PASS。

- [ ] **Step 6: 反證**

各自暫時修改、確認轉紅後還原：
1. `listed = ...` 那行改成 `listed = None` → `test_custody_watch_stores_new_week_and_recomputes_the_listed_day_without_ledger` FAIL（day 變 None）。
2. `record_signals=False` 改成 `record_signals=True` → 同一條 FAIL。
3. `local = latest_complete_custody_week(c)` 改成 `local = "2026-09-18"`（假裝被殘缺週誤導）→ 同一條 FAIL（回 no_new_week）。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/api/helpers.py stocks_power_rich/main.py tests/test_custody_watch.py tests/test_job_runs.py
git commit -m "feat(custody): custody_watch 排程，新一週集保一公布就寫入並重算自算選股"
```

---

### Task 6: 週六週報等新集保

**Files:**
- Modify: `stocks_power_rich/api/helpers.py`（新增 `custody_is_current`、`WEEKLY_PUSH_DEADLINE`；改 `telegram_new_picks_job`、`new_picks_push_payload`；`job_schedule` 的 `picks_new_weekly`）
- Modify: `stocks_power_rich/pick_push.py`（`compose_weekly_new_picks` 加 `custody_note`）
- Modify: `tests/test_scheduler.py`（週報觸發時間的斷言）
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Consumes: `db.latest_complete_custody_week`（Task 1）、多分鐘時段（Task 3）。
- Produces:
  - `helpers.custody_is_current(c) -> dict`：`{"current": bool, "week": str | None}`。current＝最新完整集保週 ≥ 最新交易日（`market_daily` 有 `taiex` 的最大日期）所在 ISO 週的週一。
  - `helpers.WEEKLY_PUSH_DEADLINE = (21, 30)`。
  - `ai_cache` 鍵 `picks_weekly_sent:{ISO年}-W{週:02d}`（例：2026-09-19 → `picks_weekly_sent:2026-W38`）→ `{"at": ISO 時間, "date": 名單日期}`，只在送出成功後寫。
  - `pick_push.compose_weekly_new_picks(..., custody_note: str | None = None)`。
  - `telegram_new_picks_job(c, cfg, "weekly")` 新增兩種略過：`{"kind": "weekly", "skipped": "already_sent"}`、`{"kind": "weekly", "skipped": "waiting_custody", "custody_week": str | None}`。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
def _cal(c, days):
    for d in days:
        db.upsert_market_daily(c, {"date": d, "taiex": 20000.0})
    c.commit()


def test_custody_is_current_by_iso_week(conn):
    _cal(conn, ["2026-09-17", "2026-09-18"])
    _week(conn, "2026-09-11", FULL)
    assert helpers.custody_is_current(conn) == {"current": False, "week": "2026-09-11"}
    _week(conn, "2026-09-18", FULL)
    assert helpers.custody_is_current(conn) == {"current": True, "week": "2026-09-18"}


def test_custody_is_current_when_friday_is_a_holiday(conn):
    """週五放假：當週最後交易日是週四、TDCC 的週日期也是週四，仍算本週已公布。"""
    _cal(conn, ["2026-09-16", "2026-09-17"])
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-17", FULL)
    assert helpers.custody_is_current(conn)["current"] is True


def _weekly_setup(c, custody_weeks):
    _cal(c, ["2026-09-11", "2026-09-14", "2026-09-18"])
    for wk in custody_weeks:
        _week(c, wk, FULL)
    selfcheck.save_precomputed(c, {"date": "2026-09-18", "ready_at": "2026-09-18T17:31:00",
                                   "heatmap": [], "coverage": {}, "rows": []})


def _tg():
    return Config(telegram_token="t", telegram_chat_id="c")


def test_weekly_push_waits_for_new_custody_then_sends_once(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    _weekly_setup(conn, ["2026-09-04", "2026-09-11"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 0))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r == {"kind": "weekly", "skipped": "waiting_custody", "custody_week": "2026-09-11"}
    assert sent == []
    _week(conn, "2026-09-18", FULL)                            # 新集保 18:10 進來
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1 and "集保仍為" not in sent[0]
    assert db.get_ai_cache(conn, "picks_weekly_sent:2026-W38")["date"] == "2026-09-18"
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 19, 0))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly") == {"kind": "weekly", "skipped": "already_sent"}
    assert len(sent) == 1


def test_weekly_push_sends_at_deadline_with_stale_note(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    _weekly_setup(conn, ["2026-09-04", "2026-09-11"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 21, 30))
    r = helpers.telegram_new_picks_job(conn, _tg(), "weekly")
    assert r["sent"] is True and len(sent) == 1
    assert "集保仍為 09\\-11 週" in sent[0]                   # MarkdownV2 跳脫後的樣子


def test_weekly_push_failed_send_is_retried(conn, monkeypatch):
    monkeypatch.setattr(telegram_push, "send_message", lambda *a: {"ok": False})
    _weekly_setup(conn, ["2026-09-11", "2026-09-18"])
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 0))
    assert helpers.telegram_new_picks_job(conn, _tg(), "weekly")["sent"] is False
    assert db.get_ai_cache(conn, "picks_weekly_sent:2026-W38") is None   # 沒送成功不標記，下一場重試


def test_compose_weekly_includes_custody_note_only_when_given():
    base = dict(day="2026-09-18", week_start="2026-09-14", total=0, n_week=0, items=[], basis=None,
                top_sectors=[], ready_at=None)
    assert "集保仍為" not in pick_push.compose_weekly_new_picks(**base)
    assert "集保仍為 09\\-11 週" in pick_push.compose_weekly_new_picks(
        **base, custody_note="集保仍為 09-11 週（本週尚未公布）")


def test_weekly_push_is_scheduled_every_half_hour_on_saturday_evening():
    by_id = {s["id"]: s for s in helpers.job_schedule(_tg(), "21:00")}
    got = [t.strftime("%H:%M") for t in helpers.slot_times(by_id["picks_new_weekly"], date(2026, 9, 19))]
    assert got == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k "weekly or current"`
Expected: FAIL，`AttributeError: module 'stocks_power_rich.api.helpers' has no attribute 'custody_is_current'`。

- [ ] **Step 3: `pick_push.compose_weekly_new_picks` 加註記**

簽名改成（在 `limit` 之前加參數）：

```python
def compose_weekly_new_picks(day: str, week_start: str, total: int, n_week: int, items: list,
                             basis: dict | None, top_sectors: list, ready_at: str | None,
                             custody_note: str | None = None,
                             limit: int = DEFAULT_LIMIT) -> str:
```

在函式裡 `if basis:` 那段之後加：

```python
    if custody_note:
        # 週報等新集保等到 21:30 仍沒有時照送，但要講清楚大戶增比還是上一週的（見 helpers）
        parts.append(_e(custody_note))
```

- [ ] **Step 4: `helpers` 的 `custody_is_current`、週報等待與排程**

在 `new_picks_push_payload` 之前新增：

```python
WEEKLY_PUSH_DEADLINE = (21, 30)   # 週六週報最晚這個時間照送（新集保還沒來也送，並註明）


def custody_is_current(c) -> dict:
    """本週集保進來了沒：最新**完整**集保週 ≥ 最新交易日所在 ISO 週的週一。

    以週為單位、不寫死週五：週五放假時 TDCC 用當週最後一個營業日（週四），照樣成立。"""
    from ..db import latest_complete_custody_week
    week = latest_complete_custody_week(c)
    row = c.execute("SELECT MAX(date) FROM market_daily WHERE taiex IS NOT NULL").fetchone()
    last = row[0] if row else None
    if not week or not last:
        return {"current": False, "week": week}
    d = date.fromisoformat(last)
    return {"current": week >= (d - timedelta(days=d.weekday())).isoformat(), "week": week}
```

`new_picks_push_payload` 的 weekly 分支（`else:` 之下），在 `text = pick_push.compose_weekly_new_picks(` 之前加：

```python
        cust = custody_is_current(c)
        custody_note = None if cust["current"] else (
            f"集保仍為 {cust['week'][5:]} 週（本週尚未公布）" if cust["week"] else "集保資料尚未取得")
```

並在 `compose_weekly_new_picks(...)` 的呼叫參數最後加上 `custody_note=custody_note`。docstring 裡「weekly（週六 18:00）」改成「weekly（週六 18:00–21:30）」。

`telegram_new_picks_job` 整個換成：

```python
def telegram_new_picks_job(c, cfg, kind: str) -> dict:
    """排程 job 本體（main.py 只呼叫）。回傳值存進 job_runs.note：略過原因或送出結果都看得見。

    weekly（週六 18:00–21:30 每 30 分鐘）：本週已送過就略過；本週集保還沒進來、且還沒到
    WEEKLY_PUSH_DEADLINE 就先等（下一場再試）；到了截止時間照送，內文註明集保仍是上一週。
    送出成功才標記本週已送，失敗讓下一場重試。"""
    from .. import telegram_push
    sent_key = None
    if kind == "weekly":
        now = _now()
        iso = now.isocalendar()
        sent_key = f"picks_weekly_sent:{iso[0]}-W{iso[1]:02d}"
        if get_ai_cache(c, sent_key):
            return {"kind": kind, "skipped": "already_sent"}
        cust = custody_is_current(c)
        if not cust["current"] and (now.hour, now.minute) < WEEKLY_PUSH_DEADLINE:
            return {"kind": kind, "skipped": "waiting_custody", "custody_week": cust["week"]}
    payload = new_picks_push_payload(c, kind)
    if payload.get("skipped"):
        return {"kind": kind, "date": payload.get("date"), "skipped": payload["skipped"]}
    r = telegram_push.send_message(cfg.telegram_token, cfg.telegram_chat_id, payload["text"])
    if sent_key and r.get("ok"):
        set_ai_cache(c, sent_key, {"at": _now().isoformat(timespec="seconds"), "date": payload["date"]})
    return {"kind": kind, "date": payload["date"], "counts": payload["counts"],
            "sent": bool(r.get("ok")), "parse_mode": r.get("parse_mode_used")}
```

`job_schedule` 的 `picks_new_weekly` 那筆改成：

```python
            # 週六 18:00–21:30 每 30 分鐘：本週集保進來就送、同一週只送一次，21:30 仍沒有就照送並註明
            # （使用者決定「等新集保再送」；判斷在 telegram_new_picks_job）。
            {"id": "picks_new_weekly", "family": "picks_new_weekly", "hour": "18-21", "minute": "0,30",
             "dow": "sat"},
```

`tests/test_scheduler.py` 裡斷言週報觸發時間的那一行改成：

```python
        assert (w["day_of_week"], w["hour"], w["minute"]) == ("sat", "18-21", "0,30")
```

- [ ] **Step 5: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_pick_push.py tests/test_job_runs.py tests/test_scheduler.py -q -p no:cacheprovider`
Expected: 全部 PASS（既有 `test_weekly_payload_*` 也要過；它們的集保停在 09-11，內文會多一行註記，斷言都是「包含」不受影響）。

- [ ] **Step 6: 反證**

各自暫時修改、確認轉紅後還原：
1. 拿掉 `if not cust["current"] and ... < WEEKLY_PUSH_DEADLINE:` 那兩行 → `test_weekly_push_waits_for_new_custody_then_sends_once` FAIL。
2. 拿掉 `if get_ai_cache(c, sent_key):` 那兩行 → 同一條 FAIL（第二次又送）。
3. 把 `if sent_key and r.get("ok"):` 改成 `if sent_key:` → `test_weekly_push_failed_send_is_retried` FAIL。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/api/helpers.py stocks_power_rich/pick_push.py tests/test_custody_watch.py tests/test_scheduler.py
git commit -m "feat(push): 週六自算選股週報等新集保再送，最晚 21:30 照送並註明"
```

---

### Task 7: 自算選股頁標出集保週

**Files:**
- Modify: `stocks_power_rich/selfcheck.py`（`compute_self_screen` 的 coverage）
- Modify: `web/app.js`（`$("ss-coverage").innerHTML = [...]` 那段）
- Modify: 快取版號：`web/index.html`（link、script 共 2 處）、`stocks_power_rich/api/public.py`（2 條 replace）、`tests/test_api.py`（4 條斷言）：`20260817-ui64` → `20260817-ui65`
- Test: `tests/test_custody_watch.py`

**Interfaces:**
- Consumes: `ai_cache` 鍵 `custody_fetched:{week}`（Task 1）。
- Produces: `compute_self_screen(...)["coverage"]` 多兩個鍵：`custody_weeks: list[str]`（新到舊，最多 2 個，同 `db.custody_compare_weeks(conn, date)`）、`custody_fetched_at: str | None`。

- [ ] **Step 1: 寫失敗的測試**

附加到 `tests/test_custody_watch.py`：

```python
def test_self_screen_coverage_reports_the_custody_weeks_used(conn):
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", FULL)
    db.set_ai_cache(conn, "custody_fetched:2026-09-18", {"at": "2026-09-19T09:30:00"})
    cov = selfcheck.compute_self_screen(conn, "2026-09-18", {})["coverage"]
    assert cov["custody_weeks"] == ["2026-09-18", "2026-09-11"]
    assert cov["custody_fetched_at"] == "2026-09-19T09:30:00"
    json.dumps(cov)                                           # 要進 ai_cache 的 TEXT 欄


def test_self_screen_coverage_before_new_week_uses_previous_pair(conn):
    _week(conn, "2026-09-04", FULL)
    _week(conn, "2026-09-11", FULL)
    cov = selfcheck.compute_self_screen(conn, "2026-09-18", {})["coverage"]
    assert cov["custody_weeks"] == ["2026-09-11", "2026-09-04"] and cov["custody_fetched_at"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py -q -p no:cacheprovider -k coverage`
Expected: FAIL，`KeyError: 'custody_weeks'`。

- [ ] **Step 3: 實作後端**

`compute_self_screen` 的 `return` 之前加：

```python
    # 大戶增比用的是哪兩週集保（新到舊）、本週集保幾點取得——頁面要標出來。週六新一週公布前
    # 名單還是上一週的集保，只寫資料日的話，使用者只能憑「怎麼沒變」察覺（2026-09-19 回報）。
    cweeks = db.custody_compare_weeks(conn, date) if date else []
    cfetched = (db.get_ai_cache(conn, f"custody_fetched:{cweeks[0]}") or {}).get("at") if cweeks else None
```

回傳的 `coverage` dict 加上 `"custody_weeks": cweeks, "custody_fetched_at": cfetched`。

- [ ] **Step 4: 實作前端**

`web/app.js` 的 `$("ss-coverage").innerHTML = [` 陣列裡，`資料日` 那一格之後插入（`cov` 代表該段已有的 coverage 物件變數；若該段用的名稱不同，改用實際名稱）：

```js
      // 大戶增比＝這兩週集保相減。週六新一週公布前名單用的還是上一週，只寫資料日看不出來
      // （2026-09-19 使用者回報）；有取得時間就一併標出（custody_watch 一公布就抓）。
      ...((cov.custody_weeks || []).length
        ? [`<span title="大戶增比與人數降比＝這兩週集保相減">集保 <b>${
            esc((cov.custody_weeks[1] || "").slice(5))}→${esc(cov.custody_weeks[0].slice(5))}</b>${
            cov.custody_fetched_at ? `・${esc(cov.custody_fetched_at.slice(5, 16).replace("T", " "))} 取得` : ""}</span>`]
        : []),
```

快取版號：用 Python 以位元組把 `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py` 裡的 `20260817-ui64` 全部換成 `20260817-ui65`（**不可用 `sed -i`**）。換完確認四個檔沒有裸 LF：

```bash
.venv\Scripts\python -c "[print(p, (lambda b: b.count(b'\n') - b.count(b'\r\n'))(open(p,'rb').read())) for p in ['web/index.html','stocks_power_rich/api/public.py','tests/test_api.py','web/app.js']]"
```

Expected: 四個檔都印 `0`。

- [ ] **Step 5: 跑測試**

Run: `.venv\Scripts\python -m pytest tests/test_custody_watch.py tests/test_api.py tests/test_selfcheck.py -q -p no:cacheprovider`
Expected: 全部 PASS。

- [ ] **Step 6: 瀏覽器驗證**（dev server：`.claude/launch.json` 的 `spr`；改了 Python 要重啟 preview 才吃得到）

在本機 DB 確認 `custody_dist` 最近兩個完整週存在，跑一次 `helpers.refresh_self_screen_cache(conn, record_signals=False)` 讓快取帶新欄位，打開自算選股頁：覆蓋率列出現「集保 MM-DD→MM-DD」；有 `custody_fetched:{week}` 時附「MM-DD HH:MM 取得」。375px 寬時頁面沒有水平溢出。截圖留證。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/selfcheck.py web/app.js web/index.html stocks_power_rich/api/public.py tests/test_api.py tests/test_custody_watch.py
git commit -m "feat(ui): 自算選股頁標出大戶增比用的集保週與取得時間（ui65）"
```

---

### Task 8: 文件與整體驗證

**Files:**
- Modify: `CLAUDE.md`（「### 排程補跑 ＋ 執行紀錄表 ＋ logging（2026-09）」之前新增一節；排程補跑那一節補一句）
- Modify: `AGENTS.md`（自算選股相關段落附近）

- [ ] **Step 1: CLAUDE.md 新增一節**（放在「### 排程補跑 ＋ 執行紀錄表 ＋ logging（2026-09）」之前）

```markdown
### 週集保一公布就反映到自算選股（custody_watch，2026-09）

使用者回報：週六新一週的集保已經公布，自算選股（資料日 09-18、17:30 算好）還沒更新。原因是
集保只在每晚 21:00 的 `run_update` 裡抓，週五那次 TDCC 還沒放出新週；實測 2026-09-19 週六 13:23
TDCC 已是 09-18 週。週六 18:00 的 Telegram「本週新進榜＋大戶買進前三」排在 21:00 之前，必然用舊集保。

- **`custody_watch` 排程**（週五 17:00–23:30、週六 08:00–21:30，每 30 分鐘，family `custody_watch`）：
  `tdcc.peek_custody_week` 只串流讀檔頭兩列取資料日期，比資料庫最新**完整**週新才完整下載
  （`_accumulate_custody`）並重算自算選股快取（`record_signals=False`）。**重算快取裡那一天**，不用
  `_latest_date`：週五 21:00 前 market_daily 還沒有週五，會算成週四、蓋掉 17:30 的名單。
  第一次取得某週的時間記在 `ai_cache custody_fetched:{week}`，累積幾週後可以收窄輪詢時段。
- **完整週判定**（`db.latest_complete_custody_week`／`custody_week_complete`，門檻同
  `custody_compare_weeks` 的 0.5）：個股頁「補歷史」會把單一檔寫進全市場還沒公布的那一週，舊碼用
  MAX 與「該週有任何一列」判斷，**全市場那一批會被 6 天節流與「已存在」擋一整週**。
- **週六週報等新集保**：`picks_new_weekly` 改成 18:00–21:30 每 30 分鐘；本週已送（`picks_weekly_sent:
  {ISO年週}`）就略過、本週集保沒進來且未到 21:30 就等、21:30 照送並註明「集保仍為 MM-DD 週」。
  送出成功才標記，失敗讓下一場重試。「本週集保進來了」＝最新完整週 ≥ 最新交易日所在 ISO 週的週一
  （`custody_is_current`，週五放假時 TDCC 用週四也成立）。
- **前瞻紀錄只在訊號日當天寫**（`refresh_self_screen_cache` 的守衛）：進場價是訊號日收盤，週末用收盤
  後才公布的集保重算週五名單，不可拿它補寫那天的訊號。頁面上的名單會換成新集保的版本，前瞻紀錄
  維持週五當天記下的名單。
- **排程時段支援多個分鐘**（`_cron_minutes`）：`slot_times` 原本 `int(minute)`，`"0,30"` 會炸。
- **頁面標出集保週**：自算選股覆蓋率列「集保 09-11→09-18・09-19 09:30 取得」（`coverage.custody_weeks`
  ／`custody_fetched_at`）。
```

「### 排程補跑 ＋ 執行紀錄表 ＋ logging」那一節講排程規格（`job_schedule`）的那一條末尾補一句：`custody_watch_fri`／`custody_watch_sat` 同屬 family `custody_watch`，補跑只補最近錯過的那一場（重跑無害）；`picks_new_weekly` 一天 8 場、run_key 是 `日期:HH:MM`。

- [ ] **Step 2: AGENTS.md 對應精簡段落**（放在自算選股相關段落附近）

```markdown
**週集保一公布就反映（custody_watch）**：週五 17:00–23:30、週六 08:00–21:30 每 30 分鐘只讀 TDCC
檔頭（`tdcc.peek_custody_week`），比最新**完整**週新才抓（`_accumulate_custody`）並重算快取裡那一天的
自算選股（不寫前瞻紀錄）；完整週判定避開個股「補歷史」寫進的殘缺週（舊碼會被它擋一整週）。週六週報
18:00–21:30 每 30 分鐘，本週集保到了就送、同週只送一次、21:30 照送並註明集保仍為上一週。前瞻紀錄
只在訊號日當天寫。`slot_times` 支援 `minute="0,30"`。頁面覆蓋率列標「集保 前週→本週・取得時間」。
```

- [ ] **Step 3: 全套測試**（單獨跑、讀摘要行，不接管線）

Run: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`
Expected: 摘要行 `N passed`，0 failed（N ＝ 959 ＋ 本計畫新增的測試數）。

- [ ] **Step 4: 換行格式檢查**

Run: `.venv\Scripts\python -c "import subprocess;fs=subprocess.run(['git','diff','--name-only','ssf-top30-price'],capture_output=True,text=True).stdout.split();[print(f, (lambda b: b.count(b'\n') - b.count(b'\r\n'))(open(f,'rb').read())) for f in fs]"`
Expected: 既有檔案都印 `0`（新建的測試檔與 docs 若是 LF 可以接受）。

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: 週集保一公布就反映到自算選股（custody_watch）"
```

- [ ] **Step 6: 回報**：列出每個任務的提交、全套測試摘要、瀏覽器截圖，並提醒：推送要等使用者說「commit and push」；部署後的週五晚上／週六，用 `/api/health` 的 `jobs` 看 `custody_watch` 哪一場第一次回傳 `week`，就是 TDCC 實際公布時間。
