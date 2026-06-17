# STOCKS POWER RICH 實作計畫

> **For agentic workers:** 本流程在本 session 內逐任務手動執行（subagent-driven-development / executing-plans 未內附）。每個任務照 Red→Green→Refactor，步驟用 checkbox（`- [ ]`）追蹤。本機 git 做檢查點。

**Goal:** 打造本機網頁 App「STOCKS POWER RICH」：一鍵每日更新大盤+國際儀表板、每日上傳 CSV 做籌碼跨週分析、個股 K 線。

**Architecture:** FastAPI 後端提供 JSON API 與靜態前端；SQLite 存每日快照；pandas 解析 CSV 與跨週/產業分析；httpx 抓 TWSE/TAIFEX openapi、yfinance 抓國際指數與個股 K 線；APScheduler 可選排程；Gemini 做白話統整（無金鑰降級）。前端單頁 ECharts。

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, httpx, pandas, yfinance, APScheduler, google-genai, pytest, respx, ECharts(CDN)。

---

## 檔案結構與職責

| 檔案 | 職責 |
|---|---|
| `stocks_power_rich/config.py` | 讀 .env 設定（金鑰、排程時間、國際指數代碼表） |
| `stocks_power_rich/db.py` | SQLite 連線、建 schema、upsert/查詢 |
| `stocks_power_rich/csv_import.py` | 解析 Big5 CSV → 標準化 dict → 寫快照 |
| `stocks_power_rich/analysis.py` | 當日訊號榜、跨週比較、產業彙整、個股檔案 |
| `stocks_power_rich/sources/twse.py` | 加權指數、三大法人現貨、融資券、本益比 |
| `stocks_power_rich/sources/taifex.py` | 台指期、期貨三大法人未平倉、散戶多空比 |
| `stocks_power_rich/sources/intl.py` | yfinance 國際指數 |
| `stocks_power_rich/sources/kline.py` | yfinance 個股 OHLC → ECharts 格式 |
| `stocks_power_rich/gemini.py` | Gemini 統整 + 降級 |
| `stocks_power_rich/updater.py` | 一鍵更新協調者 + 容錯 |
| `stocks_power_rich/scheduler.py` | APScheduler 可選每日排程 |
| `stocks_power_rich/main.py` | FastAPI 入口、路由、開瀏覽器 |
| `web/index.html`,`web/app.js`,`web/styles.css` | 前端儀表板 + 榜單 + K 線 |
| `tests/*` | 各模組測試 + fixtures |

CSV 欄位對應（標頭在第 4 列，第 2 列含「資料日期」）：
`代碼→code, 商品→name, 成交→close, 漲幅%→change_pct, 總量→volume, 市值(億)→market_cap, 股本(億)→capital, 推估獲利→est_profit, LPE→lpe, W55→w55, 集保→custody, 大戶增比→big_holder_ratio, 人數降比→holder_drop_ratio, 月增→month_inc, 年增→rev_yoy, 累增→accum_inc, 投三→trust_3d, 外三→foreign_3d, 產業→industry, 細產業→sub_industry, 所有細產業→all_sub_industry, 產業地位→industry_position`

---

## Task 0: 專案骨架與環境

**Files:**
- Create: `requirements.txt`, `.env.example`, `.gitignore`, `stocks_power_rich/__init__.py`, `stocks_power_rich/sources/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: 建立目錄與 requirements.txt**

```
# requirements.txt
fastapi==0.115.*
uvicorn[standard]==0.30.*
httpx==0.27.*
pandas==2.2.*
yfinance==0.2.*
apscheduler==3.10.*
google-genai==1.*
python-dotenv==1.*
pytest==8.*
respx==0.21.*
```

- [ ] **Step 2: `.env.example` 與 `.gitignore`**

```
# .env.example
GEMINI_API_KEY=
SPR_SCHEDULE_TIME=15:30
SPR_DB_PATH=data/spr.sqlite
```
```
# .gitignore
__pycache__/
*.pyc
.env
data/*.sqlite
data/csv/
.venv/
```

- [ ] **Step 3: 寫 smoke 測試**

```python
# tests/test_smoke.py
import importlib

def test_package_imports():
    assert importlib.import_module("stocks_power_rich") is not None
```

- [ ] **Step 4: 建虛擬環境並安裝**

Run: `python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt`
Expected: 安裝成功

- [ ] **Step 5: 跑測試**

Run: `.\.venv\Scripts\python -m pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 6: git init + commit**

```bash
git init
git add -A
git commit -m "chore: project scaffold and smoke test"
```

---

## Task 1: config.py 設定載入

**Files:**
- Create: `stocks_power_rich/config.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from stocks_power_rich.config import load_config

def test_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SPR_SCHEDULE_TIME", raising=False)
    cfg = load_config()
    assert cfg.schedule_time == "15:30"
    assert cfg.gemini_api_key == ""
    assert "^SOX" in cfg.intl_tickers.values()

def test_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("SPR_SCHEDULE_TIME", "14:00")
    cfg = load_config()
    assert cfg.gemini_api_key == "abc"
    assert cfg.schedule_time == "14:00"
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_config.py -v` → FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/config.py
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

INTL_TICKERS = {"sox": "^SOX", "n225": "^N225", "kospi": "^KS11", "gold": "GC=F", "btc": "BTC-USD"}

@dataclass
class Config:
    gemini_api_key: str = ""
    schedule_time: str = "15:30"
    db_path: str = "data/spr.sqlite"
    intl_tickers: dict = field(default_factory=lambda: dict(INTL_TICKERS))

def load_config() -> Config:
    return Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        schedule_time=os.getenv("SPR_SCHEDULE_TIME", "15:30"),
        db_path=os.getenv("SPR_DB_PATH", "data/spr.sqlite"),
    )
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: config loader"`

---

## Task 2: db.py SQLite schema

**Files:**
- Create: `stocks_power_rich/db.py`, `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot, get_snapshot_dates, get_snapshot

def test_market_daily_upsert(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite")); init_db(conn)
    upsert_market_daily(conn, {"date": "2026-06-15", "taiex": 23000.0, "sox": 5000.0})
    upsert_market_daily(conn, {"date": "2026-06-15", "taiex": 23100.0})  # 同日覆蓋
    row = conn.execute("select taiex, sox from market_daily where date=?", ("2026-06-15",)).fetchone()
    assert row[0] == 23100.0 and row[1] == 5000.0

def test_chip_snapshot_roundtrip(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite")); init_db(conn)
    rows = [{"code": "2330.TW", "name": "台積電", "big_holder_ratio": 0.5, "holder_drop_ratio": -0.2, "industry": "上市半導體", "raw_json": "{}"}]
    insert_chip_snapshot(conn, "2026-06-15", rows)
    assert get_snapshot_dates(conn) == ["2026-06-15"]
    got = get_snapshot(conn, "2026-06-15")
    assert got[0]["code"] == "2330.TW" and got[0]["big_holder_ratio"] == 0.5
```

- [ ] **Step 2: Run test** → FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/db.py
import sqlite3, os

MARKET_COLS = ["date","taiex","taiex_chg","inst_foreign","inst_trust","inst_dealer","margin_balance","margin_chg","short_balance","short_chg","tx_price","tx_chg","fut_inst_net","retail_ls_mtx","retail_ls_tmf","sox","n225","kospi","gold","btc","updated_at"]
CHIP_COLS = ["snap_date","code","name","industry","sub_industry","close","big_holder_ratio","holder_drop_ratio","month_inc","rev_yoy","accum_inc","trust_3d","foreign_3d","custody","w55","market_cap","capital","est_profit","lpe","raw_json"]

def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn):
    conn.execute(f"CREATE TABLE IF NOT EXISTS market_daily (date TEXT PRIMARY KEY, {', '.join(c+' REAL' for c in MARKET_COLS if c not in ('date','updated_at'))}, updated_at TEXT)")
    conn.execute(f"CREATE TABLE IF NOT EXISTS chip_snapshot (snap_date TEXT, code TEXT, name TEXT, industry TEXT, sub_industry TEXT, close REAL, big_holder_ratio REAL, holder_drop_ratio REAL, month_inc REAL, rev_yoy REAL, accum_inc REAL, trust_3d REAL, foreign_3d REAL, custody REAL, w55 REAL, market_cap REAL, capital REAL, est_profit REAL, lpe REAL, raw_json TEXT, PRIMARY KEY(snap_date, code))")
    conn.execute("CREATE TABLE IF NOT EXISTS csv_files (id INTEGER PRIMARY KEY AUTOINCREMENT, snap_date TEXT, stored_path TEXT, imported_at TEXT)")
    conn.commit()

def upsert_market_daily(conn, row: dict):
    cols = [c for c in MARKET_COLS if c in row]
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "date")
    conn.execute(f"INSERT INTO market_daily ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(date) DO UPDATE SET {updates}", [row[c] for c in cols])
    conn.commit()

def insert_chip_snapshot(conn, snap_date: str, rows: list[dict]):
    for r in rows:
        cols = ["snap_date"] + [c for c in CHIP_COLS if c != "snap_date" and c in r]
        vals = [snap_date] + [r[c] for c in cols if c != "snap_date"]
        ph = ",".join("?" for _ in cols)
        upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("snap_date","code"))
        conn.execute(f"INSERT INTO chip_snapshot ({','.join(cols)}) VALUES ({ph}) ON CONFLICT(snap_date,code) DO UPDATE SET {upd}", vals)
    conn.commit()

def get_snapshot_dates(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT snap_date FROM chip_snapshot ORDER BY snap_date").fetchall()]

def get_snapshot(conn, snap_date: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM chip_snapshot WHERE snap_date=?", (snap_date,)).fetchall()]
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: sqlite schema and data layer"`

---

## Task 3: csv_import.py 解析 Big5 CSV

**Files:**
- Create: `stocks_power_rich/csv_import.py`, `tests/fixtures/sample.csv`(Big5), `tests/test_csv_import.py`

- [ ] **Step 1: 建立 Big5 fixture（用程式產生，3 行標頭 + 1 資料列）**

```python
# tests/conftest.py  (新增)
import pytest, pathlib
@pytest.fixture
def big5_csv(tmp_path):
    header = '"序號","代碼","商品","成交","漲幅%","總量","收盤價(06/15)","區間漲幅%","振幅","市值(億)","股本(億)","成交值(億)","推估獲利","蘭質","LPE","蘭值","有CB","W55","55高","55低","集保","評比值","大戶增比","人數降比","月增","年增","累增","投三","外三","TOTAL","55漲%","21跌%","產業","細產業","所有細產業",產業地位'
    row = '1\t,"2330.TW","台積電","1000","1.5","30000","985","1.5","2","250000","2593","500","30","6","20","20","1","1","0","0","75","0.1","0.8","-0.5","3","12.3","5","2.5","3.1","5.6","0.2","0","上市半導體","晶圓","晶圓代工",全球晶圓代工龍頭'
    content = "符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n" + header + "\n" + row + "\n"
    p = tmp_path / "sample.csv"
    p.write_bytes(content.encode("big5"))
    return str(p)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_csv_import.py
from stocks_power_rich.csv_import import parse_csv

def test_parse_extracts_date_and_row(big5_csv):
    snap_date, rows = parse_csv(big5_csv)
    assert snap_date == "2026-06-15"
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "2330.TW"
    assert r["name"] == "台積電"
    assert r["close"] == 1000.0
    assert r["big_holder_ratio"] == 0.8
    assert r["holder_drop_ratio"] == -0.5
    assert r["rev_yoy"] == 12.3
    assert r["w55"] == 1.0
    assert r["industry"] == "上市半導體"
    assert "raw_json" in r
```

- [ ] **Step 3: Run test** → FAIL

- [ ] **Step 4: Write minimal implementation**

```python
# stocks_power_rich/csv_import.py
import re, json, io, shutil, os
from datetime import datetime
import pandas as pd

COLMAP = {"代碼":"code","商品":"name","成交":"close","漲幅%":"change_pct","總量":"volume","市值(億)":"market_cap","股本(億)":"capital","推估獲利":"est_profit","LPE":"lpe","W55":"w55","集保":"custody","大戶增比":"big_holder_ratio","人數降比":"holder_drop_ratio","月增":"month_inc","年增":"rev_yoy","累增":"accum_inc","投三":"trust_3d","外三":"foreign_3d","產業":"industry","細產業":"sub_industry","所有細產業":"all_sub_industry","產業地位":"industry_position"}
NUMERIC = {"close","change_pct","volume","market_cap","capital","est_profit","lpe","w55","custody","big_holder_ratio","holder_drop_ratio","month_inc","rev_yoy","accum_inc","trust_3d","foreign_3d"}

def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None

def parse_csv(path: str):
    raw = open(path, "rb").read().decode("big5", errors="replace")
    lines = raw.splitlines()
    m = re.search(r"(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", lines[1])
    snap_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else datetime.now().strftime("%Y-%m-%d")
    df = pd.read_csv(io.StringIO("\n".join(lines[3:])), dtype=str, engine="python")
    df.columns = [c.strip().strip('"') for c in df.columns]
    rows = []
    for _, rec in df.iterrows():
        d = {}
        for src, dst in COLMAP.items():
            if src in df.columns:
                val = rec[src]
                d[dst] = _to_float(val) if dst in NUMERIC else (None if pd.isna(val) else str(val).strip())
        if not d.get("code"):
            continue
        d["raw_json"] = json.dumps({k: (None if pd.isna(v) else v) for k, v in rec.items()}, ensure_ascii=False)
        rows.append(d)
    return snap_date, rows

def import_csv(conn, path: str, store_dir: str = "data/csv"):
    from .db import insert_chip_snapshot
    snap_date, rows = parse_csv(path)
    insert_chip_snapshot(conn, snap_date, rows)
    os.makedirs(store_dir, exist_ok=True)
    stored = os.path.join(store_dir, f"{snap_date}.csv")
    shutil.copyfile(path, stored)
    conn.execute("INSERT INTO csv_files (snap_date, stored_path, imported_at) VALUES (?,?,?)", (snap_date, stored, datetime.now().isoformat()))
    conn.commit()
    return snap_date, len(rows)
```

- [ ] **Step 5: Run test** → PASS
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: Big5 CSV parser and import"`

---

## Task 4: analysis.py 當日訊號榜

**Files:**
- Create: `stocks_power_rich/analysis.py`, `tests/test_analysis_daily.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_daily.py
from stocks_power_rich.analysis import daily_signals

ROWS = [
    {"code":"A","name":"a","big_holder_ratio":0.9,"holder_drop_ratio":-0.5,"w55":1,"rev_yoy":10,"trust_3d":2,"foreign_3d":3,"industry":"半導體"},
    {"code":"B","name":"b","big_holder_ratio":0.1,"holder_drop_ratio":0.2,"w55":0,"rev_yoy":-3,"trust_3d":0,"foreign_3d":0,"industry":"水泥"},
    {"code":"C","name":"c","big_holder_ratio":0.6,"holder_drop_ratio":-0.3,"w55":1,"rev_yoy":5,"trust_3d":1,"foreign_3d":-1,"industry":"半導體"},
]

def test_ranks_big_holder_up_retail_down_first():
    out = daily_signals(ROWS, top_n=2)
    assert [r["code"] for r in out] == ["A","C"]
    assert out[0]["score"] >= out[1]["score"]
    assert out[0]["flags"]["w55_bull"] is True
    assert out[0]["flags"]["rev_growth"] is True
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/analysis.py
def _num(v):
    return v if isinstance(v, (int, float)) and v is not None else 0.0

def _score(r):
    # 大戶增比越高、人數降比越負（散戶減越多）得分越高
    return _num(r.get("big_holder_ratio")) - _num(r.get("holder_drop_ratio"))

def _flags(r):
    return {
        "w55_bull": _num(r.get("w55")) >= 1,
        "rev_growth": _num(r.get("rev_yoy")) > 0,
        "inst_buy": _num(r.get("trust_3d")) > 0 or _num(r.get("foreign_3d")) > 0,
    }

def daily_signals(rows: list[dict], top_n: int = 30) -> list[dict]:
    scored = []
    for r in rows:
        item = dict(r)
        item["score"] = round(_score(r), 4)
        item["flags"] = _flags(r)
        scored.append(item)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: daily signal ranking"`

---

## Task 5: analysis.py 跨週比較

**Files:**
- Modify: `stocks_power_rich/analysis.py`
- Create: `tests/test_analysis_weekly.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_weekly.py
from stocks_power_rich.analysis import weekly_comparison

LAST = [{"code":"A","name":"a","custody":70,"big_holder_ratio":0.3,"industry":"半導體"},
        {"code":"B","name":"b","custody":50,"big_holder_ratio":0.2,"industry":"水泥"}]
THIS = [{"code":"A","name":"a","custody":75,"big_holder_ratio":0.6,"industry":"半導體"},  # 大戶加碼
        {"code":"C","name":"c","custody":40,"big_holder_ratio":0.5,"industry":"航運"}]      # 新進榜

def test_weekly_marks_status_and_delta():
    out = weekly_comparison(THIS, LAST)
    by = {r["code"]: r for r in out["stocks"]}
    assert by["A"]["custody_delta"] == 5
    assert by["A"]["status"] == "加速"        # 上週有、本週大戶增比更高
    assert by["C"]["status"] == "新進榜"        # 上週無、本週入榜
    assert by["B"]["status"] == "退榜"          # 上週有、本週無
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write implementation (append to analysis.py)**

```python
def weekly_comparison(this_rows: list[dict], last_rows: list[dict]) -> dict:
    last = {r["code"]: r for r in last_rows}
    this = {r["code"]: r for r in this_rows}
    stocks = []
    for code, r in this.items():
        prev = last.get(code)
        custody_delta = round(_num(r.get("custody")) - _num(prev.get("custody")), 4) if prev else None
        if not prev:
            status = "新進榜"
        elif _num(r.get("big_holder_ratio")) > _num(prev.get("big_holder_ratio")):
            status = "加速"
        else:
            status = "持平"
        stocks.append({**r, "custody_delta": custody_delta, "status": status})
    for code, prev in last.items():
        if code not in this:
            stocks.append({**prev, "custody_delta": None, "status": "退榜"})
    return {"stocks": stocks}
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: weekly comparison with status"`

---

## Task 6: analysis.py 產業彙整

**Files:**
- Modify: `stocks_power_rich/analysis.py`
- Create: `tests/test_analysis_industry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_industry.py
from stocks_power_rich.analysis import industry_aggregate

ROWS = [
    {"code":"A","industry":"半導體","big_holder_ratio":0.9,"holder_drop_ratio":-0.5},
    {"code":"C","industry":"半導體","big_holder_ratio":0.6,"holder_drop_ratio":-0.3},
    {"code":"B","industry":"水泥","big_holder_ratio":0.1,"holder_drop_ratio":0.2},
]

def test_aggregates_and_ranks_industry():
    out = industry_aggregate(ROWS)
    assert out[0]["industry"] == "半導體"
    assert out[0]["count"] == 2
    assert round(out[0]["avg_score"], 2) == round(((0.9+0.5)+(0.6+0.3))/2, 2)
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write implementation (append to analysis.py)**

```python
def industry_aggregate(rows: list[dict]) -> list[dict]:
    groups = {}
    for r in rows:
        key = r.get("industry") or "未分類"
        groups.setdefault(key, []).append(_score(r))
    out = [{"industry": k, "count": len(v), "avg_score": round(sum(v)/len(v), 4)} for k, v in groups.items()]
    out.sort(key=lambda x: x["avg_score"], reverse=True)
    return out
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: industry aggregation"`

---

## Task 7: sources/intl.py yfinance 國際指數

**Files:**
- Create: `stocks_power_rich/sources/intl.py`, `tests/test_intl.py`

- [ ] **Step 1: Write the failing test (mock yfinance)**

```python
# tests/test_intl.py
import pandas as pd
from stocks_power_rich.sources import intl

def test_fetch_intl_indices(monkeypatch):
    def fake_download(tickers, period, **kw):
        idx = pd.to_datetime(["2026-06-12","2026-06-13"])
        cols = pd.MultiIndex.from_product([["Close"], tickers.split()])
        data = {("Close", t): [100.0, 110.0] for t in tickers.split()}
        return pd.DataFrame(data, index=idx)
    monkeypatch.setattr(intl.yf, "download", fake_download)
    out = intl.fetch_intl_indices({"sox": "^SOX", "btc": "BTC-USD"})
    assert out["sox"]["value"] == 110.0
    assert out["sox"]["chg_pct"] == 10.0
    assert out["btc"]["value"] == 110.0
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/sources/intl.py
import yfinance as yf

def fetch_intl_indices(tickers: dict) -> dict:
    symbols = " ".join(tickers.values())
    df = yf.download(symbols, period="5d", progress=False)["Close"]
    out = {}
    for key, sym in tickers.items():
        series = df[sym].dropna() if sym in df else df.dropna()
        if len(series) >= 2:
            last, prev = float(series.iloc[-1]), float(series.iloc[-2])
            out[key] = {"value": round(last, 2), "chg_pct": round((last - prev) / prev * 100, 2)}
        elif len(series) == 1:
            out[key] = {"value": round(float(series.iloc[-1]), 2), "chg_pct": None}
    return out
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: international indices via yfinance"`

---

## Task 8: sources/kline.py 個股 K 線

**Files:**
- Create: `stocks_power_rich/sources/kline.py`, `tests/test_kline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kline.py
import pandas as pd
from stocks_power_rich.sources import kline

def test_fetch_kline_echarts_shape(monkeypatch):
    def fake_history(self, period):
        idx = pd.to_datetime(["2026-06-12","2026-06-13"])
        return pd.DataFrame({"Open":[10,11],"High":[12,13],"Low":[9,10],"Close":[11,12],"Volume":[100,200]}, index=idx)
    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    out = kline.fetch_kline("2330.TW", period="1mo")
    assert out["dates"] == ["2026-06-12","2026-06-13"]
    # ECharts candlestick order: [open, close, low, high]
    assert out["candles"][0] == [10.0, 11.0, 9.0, 12.0]
    assert out["volumes"] == [100.0, 200.0]
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/sources/kline.py
import yfinance as yf

def fetch_kline(code: str, period: str = "1y") -> dict:
    df = yf.Ticker(code).history(period=period)
    if df.empty:
        return {"code": code, "dates": [], "candles": [], "volumes": []}
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(r.Volume) for r in df.itertuples()]
    return {"code": code, "dates": dates, "candles": candles, "volumes": volumes}
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: per-stock kline via yfinance"`

---

## Task 9: sources/twse.py TWSE 抓取

**Files:**
- Create: `stocks_power_rich/sources/twse.py`, `tests/test_twse.py`

- [ ] **Step 1: 先用真實呼叫確認端點欄位（執行時記錄到 docstring，非 placeholder）**

Run: `.\.venv\Scripts\python -c "import httpx; print(httpx.get('https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN').json()[0])"`
Expected: 印出第一筆 JSON，確認欄名（如 `融資(交易單位)` 等）。把實際欄名填入下方 KEYS 常數。

- [ ] **Step 2: Write the failing test (respx mock)**

```python
# tests/test_twse.py
import httpx, respx
from stocks_power_rich.sources import twse

@respx.mock
def test_fetch_margin():
    respx.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN").mock(
        return_value=httpx.Response(200, json=[{"Date":"20260615","MarginPurchaseTodayBalance":"1000","MarginPurchaseYesBalance":"900","ShortSaleTodayBalance":"200","ShortSaleYesBalance":"180"}]))
    out = twse.fetch_margin()
    assert out["margin_balance"] == 1000.0
    assert out["margin_chg"] == 100.0
    assert out["short_balance"] == 200.0
    assert out["short_chg"] == 20.0
```

- [ ] **Step 3: Run test** → FAIL

- [ ] **Step 4: Write minimal implementation**

```python
# stocks_power_rich/sources/twse.py
import httpx

BASE = "https://openapi.twse.com.tw/v1"

def _get(path: str):
    return httpx.get(f"{BASE}{path}", timeout=20).json()

def _f(v):
    try: return float(str(v).replace(",", ""))
    except (ValueError, TypeError): return None

def fetch_margin() -> dict:
    rec = _get("/exchangeReport/MI_MARGN")[0]
    mb, my = _f(rec["MarginPurchaseTodayBalance"]), _f(rec["MarginPurchaseYesBalance"])
    sb, sy = _f(rec["ShortSaleTodayBalance"]), _f(rec["ShortSaleYesBalance"])
    return {"margin_balance": mb, "margin_chg": round(mb - my, 2), "short_balance": sb, "short_chg": round(sb - sy, 2)}

def fetch_institutional() -> dict:
    rows = _get("/fund/BFI82U")
    m = {r.get("name") or r.get("單位名稱"): r for r in rows}
    def net(key_substr):
        for name, r in m.items():
            if key_substr in name:
                return _f(r.get("買賣差額") or r.get("difference"))
        return None
    return {"inst_foreign": net("外資"), "inst_trust": net("投信"), "inst_dealer": net("自營")}

def fetch_taiex() -> dict:
    rows = _get("/exchangeReport/MI_INDEX")
    for r in rows:
        name = r.get("指數") or r.get("Name") or ""
        if "發行量加權股價指數" in name:
            return {"taiex": _f(r.get("收盤指數") or r.get("ClosingIndex")), "taiex_chg": _f(r.get("漲跌點數") or r.get("Change"))}
    return {"taiex": None, "taiex_chg": None}

def fetch_valuation() -> list[dict]:
    rows = _get("/exchangeReport/BWIBBU_ALL")
    out = []
    for r in rows:
        out.append({"code": (r.get("Code") or "") + ".TW", "pe": _f(r.get("PEratio")), "yield": _f(r.get("DividendYield")), "pb": _f(r.get("PBratio"))})
    return out
```

> 註：`fetch_institutional`/`fetch_taiex` 的欄名以 Step 1 真實回應為準；測試先鎖定 `fetch_margin`，其餘在 Step 1 確認欄名後補對應 respx 測試（同模式）。

- [ ] **Step 5: 補 `fetch_institutional`、`fetch_taiex`、`fetch_valuation` 的 respx 測試**（用 Step 1 取得的真實 JSON 當 mock 樣本，斷言解析值）

- [ ] **Step 6: Run tests** → PASS
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: TWSE sources"`

---

## Task 10: sources/taifex.py 台指期 + 散戶多空比

**Files:**
- Create: `stocks_power_rich/sources/taifex.py`, `tests/test_taifex.py`

- [ ] **Step 1: 先列出 TAIFEX openapi 端點並記錄（執行時釘死 URL）**

Run: `.\.venv\Scripts\python -c "import httpx; r=httpx.get('https://openapi.taifex.com.tw/v1/DailyMarketReportFut', timeout=20); print(r.status_code); print(r.text[:300])"`
若 404，改試端點清單：`.\.venv\Scripts\python -c "import httpx; print(httpx.get('https://openapi.taifex.com.tw/swagger/v1/swagger.json', timeout=20).json()['paths'].keys())"`
Expected: 取得期貨每日行情與三大法人各契約端點實際路徑，填入下方 `Q_FUT`、`Q_INST` 常數。

- [ ] **Step 2: Write the failing test（散戶多空比公式，純函式先可測）**

```python
# tests/test_taifex.py
from stocks_power_rich.sources.taifex import retail_long_short_ratio

def test_retail_ls_inverse_of_institutional():
    # 三大法人小台未平倉淨額 = +600（偏多），總未平倉 = 3000
    # 散戶 ≈ -600，散戶多空比 = -600/3000 = -0.2
    assert retail_long_short_ratio(inst_net_oi=600, total_oi=3000) == -0.2

def test_retail_ls_zero_total():
    assert retail_long_short_ratio(inst_net_oi=100, total_oi=0) is None
```

- [ ] **Step 3: Run test** → FAIL

- [ ] **Step 4: Write minimal implementation**

```python
# stocks_power_rich/sources/taifex.py
import httpx

BASE = "https://openapi.taifex.com.tw/v1"
# Q_FUT / Q_INST：Step 1 確認後填入實際路徑字串
Q_FUT = "/DailyMarketReportFut"
Q_INST = "/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesByContractsByDate"

def _f(v):
    try: return float(str(v).replace(",", ""))
    except (ValueError, TypeError): return None

def retail_long_short_ratio(inst_net_oi: float, total_oi: float):
    if not total_oi:
        return None
    return round(-inst_net_oi / total_oi, 4)

def _get(path):
    return httpx.get(f"{BASE}{path}", timeout=20).json()

def fetch_tx_quote(contract: str = "TX") -> dict:
    rows = _get(Q_FUT)
    for r in rows:
        if (r.get("ContractCode") or r.get("契約") or "").strip() == contract:
            return {"tx_price": _f(r.get("LastPrice") or r.get("收盤價")), "tx_chg": _f(r.get("Change") or r.get("漲跌價"))}
    return {"tx_price": None, "tx_chg": None}

def fetch_retail_ratios() -> dict:
    """回傳小台(MTX)、微台(TMF)散戶多空比與期貨三大法人淨額。"""
    inst_rows = _get(Q_INST)
    def contract_net_and_oi(code):
        net = oi = None
        for r in inst_rows:
            if (r.get("ContractCode") or r.get("商品代號") or "").strip() == code:
                long_oi = _f(r.get("LongOpenInterest") or r.get("多方未平倉口數"))
                short_oi = _f(r.get("ShortOpenInterest") or r.get("空方未平倉口數"))
                total = _f(r.get("OpenInterest") or r.get("全市場未平倉量"))
                if long_oi is not None and short_oi is not None:
                    net = long_oi - short_oi
                oi = total
        return net, oi
    mtx_net, mtx_oi = contract_net_and_oi("MTX")
    tmf_net, tmf_oi = contract_net_and_oi("TMF")
    return {
        "fut_inst_net": mtx_net,
        "retail_ls_mtx": retail_long_short_ratio(mtx_net, mtx_oi) if mtx_net is not None and mtx_oi else None,
        "retail_ls_tmf": retail_long_short_ratio(tmf_net, tmf_oi) if tmf_net is not None and tmf_oi else None,
    }
```

- [ ] **Step 5: Step 1 釘死 URL/欄名後，補 `fetch_tx_quote`、`fetch_retail_ratios` 的 respx 測試（用真實 JSON 樣本）**
- [ ] **Step 6: Run tests** → PASS
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: TAIFEX sources and retail long-short ratio"`

---

## Task 11: updater.py 一鍵更新協調者

**Files:**
- Create: `stocks_power_rich/updater.py`, `tests/test_updater.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_updater.py
from stocks_power_rich import updater
from stocks_power_rich.db import get_connection, init_db

def test_run_update_collects_and_tolerates_failure(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path/"t.sqlite")); init_db(conn)
    monkeypatch.setattr(updater.twse, "fetch_taiex", lambda: {"taiex": 23000.0, "taiex_chg": 50.0})
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda: {"inst_foreign": 1.0, "inst_trust": 2.0, "inst_dealer": 3.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda: {"margin_balance": 1000.0, "margin_chg": 10.0, "short_balance": 200.0, "short_chg": 5.0})
    monkeypatch.setattr(updater.taifex, "fetch_tx_quote", lambda: {"tx_price": 23010.0, "tx_chg": 40.0})
    monkeypatch.setattr(updater.taifex, "fetch_retail_ratios", lambda: {"fut_inst_net": 600, "retail_ls_mtx": -0.2, "retail_ls_tmf": -0.1})
    def boom(t): raise RuntimeError("network down")
    monkeypatch.setattr(updater.intl, "fetch_intl_indices", boom)
    result = updater.run_update(conn, intl_tickers={"sox":"^SOX"})
    assert "twse_taiex" in result["success"]
    assert any(f["source"] == "intl" for f in result["failed"])
    row = conn.execute("select taiex, retail_ls_mtx from market_daily").fetchone()
    assert row[0] == 23000.0 and row[1] == -0.2
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/updater.py
from datetime import datetime
from .sources import twse, taifex, intl
from .db import upsert_market_daily

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
                for k, v in data.items():
                    row[k] = v.get("value")
            else:
                row.update({k: v for k, v in data.items() if v is not None})
            success.append(name)
        except Exception as e:  # noqa: BLE001 — 容錯：單一來源失敗不影響其餘
            failed.append({"source": name.split("_")[0], "name": name, "error": str(e)})
    upsert_market_daily(conn, row)
    return {"date": today, "success": success, "failed": failed}
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: one-click updater with fault tolerance"`

---

## Task 12: gemini.py 統整 + 降級

**Files:**
- Create: `stocks_power_rich/gemini.py`, `tests/test_gemini.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gemini.py
from stocks_power_rich import gemini

def test_degrades_without_key():
    out = gemini.summarize_market({"taiex": 23000}, api_key="")
    assert out["enabled"] is False
    assert "未啟用" in out["text"]

def test_uses_model_when_key(monkeypatch):
    class FakeResp: text = "盤勢偏多"
    class FakeModels:
        def generate_content(self, model, contents): return FakeResp()
    class FakeClient:
        def __init__(self, api_key): self.models = FakeModels()
    monkeypatch.setattr(gemini, "genai_client", lambda key: FakeClient(key))
    out = gemini.summarize_market({"taiex": 23000}, api_key="k")
    assert out["enabled"] is True
    assert out["text"] == "盤勢偏多"
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/gemini.py
import json
MODEL = "gemini-2.5-flash"

def genai_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)

def _run(prompt: str, api_key: str) -> dict:
    if not api_key:
        return {"enabled": False, "text": "（未啟用 AI 摘要：未設定 GEMINI_API_KEY）"}
    try:
        client = genai_client(api_key)
        resp = client.models.generate_content(model=MODEL, contents=prompt)
        return {"enabled": True, "text": resp.text}
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "text": f"（AI 摘要失敗：{e}）"}

def summarize_market(market_row: dict, api_key: str) -> dict:
    prompt = "你是台股分析師，依以下大盤數據用繁中三句話講盤勢與法人動向：\n" + json.dumps(market_row, ensure_ascii=False)
    return _run(prompt, api_key)

def summarize_csv(daily_top: list, weekly: dict, industry: list, api_key: str) -> dict:
    prompt = ("你是籌碼分析師，依下列資料用繁中條列『本週大戶進、散戶退』的重點類股與個股及選股理由：\n"
              + json.dumps({"daily_top": daily_top[:15], "weekly": weekly, "industry": industry[:10]}, ensure_ascii=False))
    return _run(prompt, api_key)
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: gemini summaries with graceful degrade"`

---

## Task 13: main.py FastAPI 路由

**Files:**
- Create: `stocks_power_rich/main.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api.py
from fastapi.testclient import TestClient
from stocks_power_rich.main import create_app

def test_dashboard_and_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path/"t.sqlite"))
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/dashboard").status_code == 200
    # 上傳一個 Big5 CSV
    header = '"序號","代碼","商品","成交","漲幅%","總量","收盤價","區間漲幅%","振幅","市值(億)","股本(億)","成交值(億)","推估獲利","蘭質","LPE","蘭值","有CB","W55","55高","55低","集保","評比值","大戶增比","人數降比","月增","年增","累增","投三","外三","TOTAL","55漲%","21跌%","產業","細產業","所有細產業",產業地位'
    row = '1\t,"2330.TW","台積電","1000","1.5","30000","985","1.5","2","250000","2593","500","30","6","20","20","1","1","0","0","75","0.1","0.8","-0.5","3","12.3","5","2.5","3.1","5.6","0.2","0","上市半導體","晶圓","晶圓代工",龍頭'
    content = ("符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n"+header+"\n"+row+"\n").encode("big5")
    r = client.post("/api/csv/upload", files={"file": ("a.csv", content, "text/csv")})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["daily_top"][0]["code"] == "2330.TW"
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/main.py
import tempfile, os
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import load_config
from .db import get_connection, init_db, get_snapshot_dates, get_snapshot
from . import csv_import, analysis, updater, gemini
from .sources import kline

def create_app() -> FastAPI:
    cfg = load_config()
    app = FastAPI(title="STOCKS POWER RICH")

    def conn():
        c = get_connection(cfg.db_path); init_db(c); return c

    @app.get("/api/dashboard")
    def dashboard():
        c = conn()
        rows = [dict(r) for r in c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 60").fetchall()]
        latest = rows[0] if rows else {}
        return {"latest": latest, "history": list(reversed(rows))}

    @app.post("/api/update/run")
    def run_update():
        return updater.run_update(conn(), cfg.intl_tickers)

    @app.post("/api/csv/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        tmp = os.path.join(tempfile.gettempdir(), file.filename)
        open(tmp, "wb").write(data)
        c = conn()
        snap_date, count = csv_import.import_csv(c, tmp)
        rows = get_snapshot(c, snap_date)
        return {"snap_date": snap_date, "count": count, "daily_top": analysis.daily_signals(rows, 30)}

    @app.get("/api/analysis/weekly")
    def weekly():
        c = conn(); dates = get_snapshot_dates(c)
        if len(dates) < 2:
            return {"stocks": [], "industry": [], "note": "需至少兩週快照"}
        this_rows, last_rows = get_snapshot(c, dates[-1]), get_snapshot(c, dates[-2])
        return {**analysis.weekly_comparison(this_rows, last_rows), "industry": analysis.industry_aggregate(this_rows)}

    @app.get("/api/analysis/summary")
    def summary():
        c = conn(); dates = get_snapshot_dates(c)
        rows = get_snapshot(c, dates[-1]) if dates else []
        top = analysis.daily_signals(rows, 30)
        ind = analysis.industry_aggregate(rows)
        return gemini.summarize_csv(top, {}, ind, cfg.gemini_api_key)

    @app.get("/api/stock/{code}/kline")
    def stock_kline(code: str, period: str = "1y"):
        return kline.fetch_kline(code, period)

    web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app

app = create_app()
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: FastAPI routes"`

---

## Task 14: scheduler.py 可選排程

**Files:**
- Create: `stocks_power_rich/scheduler.py`, `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
from stocks_power_rich.scheduler import parse_schedule_time, build_trigger_kwargs

def test_parse_time():
    assert parse_schedule_time("15:30") == (15, 30)

def test_build_trigger_kwargs():
    assert build_trigger_kwargs("09:05") == {"hour": 9, "minute": 5}
```

- [ ] **Step 2: Run test** → FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# stocks_power_rich/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

def parse_schedule_time(s: str):
    h, m = s.split(":")
    return int(h), int(m)

def build_trigger_kwargs(s: str) -> dict:
    h, m = parse_schedule_time(s)
    return {"hour": h, "minute": m}

def start_scheduler(job, schedule_time: str) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Asia/Taipei")
    sched.add_job(job, "cron", **build_trigger_kwargs(schedule_time), id="daily_update", replace_existing=True)
    sched.start()
    return sched
```

- [ ] **Step 4: Run test** → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: optional daily scheduler"`

---

## Task 15: 前端 web/ 儀表板 + K 線

**Files:**
- Create: `web/index.html`, `web/app.js`, `web/styles.css`

- [ ] **Step 1: 寫 index.html（載入 ECharts CDN、版面骨架、一鍵更新鈕、上傳鈕、K 線彈窗容器）**

完整 HTML：頂部標題「STOCKS POWER RICH」、`#btn-update`、`#last-updated`、大盤指標卡容器 `#cards`、趨勢圖 `#trend`、上傳 `<input type=file id=csv>`、榜單表 `#daily`、`#weekly`、`#industry`、Gemini 區 `#summary`、K 線彈窗 `#kline-modal`＋`#kline`。`<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js">`、`<script src="app.js">`。

- [ ] **Step 2: 寫 app.js**

實作：
- `loadDashboard()` → fetch `/api/dashboard`，渲染指標卡（加權指數/三大法人/融資券/台指期/散戶多空比/SOX/N225/KOSPI/Gold/BTC）與 ECharts 趨勢線。
- `#btn-update` click → POST `/api/update/run`，顯示 success/failed 來源，重載 dashboard。
- `#csv` change → POST `/api/csv/upload`（FormData），渲染 `daily_top` 到 `#daily`，每列股號可點。
- `loadWeekly()` → GET `/api/analysis/weekly`，渲染跨週榜（標 新進榜/加速/退榜）與產業榜。
- `loadSummary()` → GET `/api/analysis/summary`，填 `#summary`。
- `openKline(code,name)` → GET `/api/stock/{code}/kline`，用 ECharts candlestick + volume + MA5/MA20 畫在 `#kline`，開彈窗。

- [ ] **Step 3: 寫 styles.css（卡片網格、表格、彈窗）**

- [ ] **Step 4: 手動驗證（前端無單元測試，於 Task 17 端對端跑）**

Run: `.\.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --port 8000` 後瀏覽 `http://127.0.0.1:8000`，確認頁面載入、指標卡與空狀態正常。

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: frontend dashboard, tables, kline modal"`

---

## Task 16: 啟動.bat + README + 排程備援

**Files:**
- Create: `啟動.bat`, `README.md`

- [ ] **Step 1: 寫 `啟動.bat`**

```bat
@echo off
cd /d %~dp0
call .venv\Scripts\activate
start "" http://127.0.0.1:8000
python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: 寫 README.md**（安裝、設定 .env、啟動、每日用法：先一鍵更新→上傳 CSV→看榜單與 K 線；Windows 工作排程器設定：每日呼叫 `python -m stocks_power_rich.cli update` 之說明）

- [ ] **Step 3: 補 `stocks_power_rich/cli.py` 供排程器/工作排程器呼叫**

```python
# stocks_power_rich/cli.py
import sys
from .config import load_config
from .db import get_connection, init_db
from . import updater

def main():
    cfg = load_config()
    conn = get_connection(cfg.db_path); init_db(conn)
    result = updater.run_update(conn, cfg.intl_tickers)
    print(result)

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 測試 CLI**

Run: `.\.venv\Scripts\python -m stocks_power_rich.cli`（會嘗試真連網，確認不崩潰、印出 success/failed 結構）

- [ ] **Step 5: Commit** — `git add -A && git commit -m "chore: launcher, README, CLI for scheduling"`

---

## Task 17: 端對端整合驗證（對接第⑤階段）

**Files:** 無新檔（驗證）

- [ ] **Step 1: 跑全部單元測試** — `.\.venv\Scripts\python -m pytest -v`，預期全綠。
- [ ] **Step 2: 啟動 app**，按「一鍵更新」，確認 `market_daily` 有資料、儀表板指標卡有值、失敗來源有明確提示。
- [ ] **Step 3: 上傳使用者真實 `.常用.csv`**，確認當日榜出現、股號可開 K 線。
- [ ] **Step 4: 連續上傳兩個不同日期 CSV**，確認 `/api/analysis/weekly` 出現「新進榜/加速/退榜」與產業榜。
- [ ] **Step 5: 設 GEMINI_API_KEY 後**確認摘要啟用；清空後確認降級訊息。
- [ ] **Step 6: Commit** — `git add -A && git commit -m "test: end-to-end verification pass"`

---

## 自我檢查（Self-Review）結果

- **Spec 覆蓋**：§4 來源→Task 7-10；§5 schema→Task 2；§6 散戶多空比→Task 10；§7 分析→Task 4-6,12；§8 更新/排程→Task 11,14,16；§9 Gemini→Task 12；§10 前端→Task 15；§11 API→Task 13；K 線→Task 8,13,15。皆有對應任務。
- **Placeholder 掃描**：TWSE/TAIFEX 端點欄名以「先真實呼叫釘死」的具體步驟處理（Task 9 Step1、Task 10 Step1），非「之後再補」。
- **型別一致**：`run_update(conn, intl_tickers)`、`fetch_*` 回傳 dict、`daily_signals`/`weekly_comparison`/`industry_aggregate` 簽章跨 Task 一致；`fetch_kline` 回傳 `{dates,candles,volumes}` 與前端一致。
