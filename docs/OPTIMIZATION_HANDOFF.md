# 優化任務交接單（給 AI agent 執行）

專案：STOCKS POWER RICH（股力智富）。FastAPI + vanilla JS + SQLite，單一服務。
先讀 `CLAUDE.md` 了解「單一資料日期 D」核心設計與兩條不可回歸的 invariant。
慣例：TDD（先寫 parse/純函數測試）、純解析函數＋薄網路層、commit 尾端加 `Co-Authored-By: Claude` trailer。
執行環境：Windows，用 `.venv\Scripts\python`。測試：`.venv\Scripts\python -m pytest -q`（**不要** `| tail`，會吃掉 exit code）。

---

## 任務 1：資料更新失敗告警（最高優先，最便宜）

**現況**：`updater.run_update(conn, intl_tickers)`（`stocks_power_rich/updater.py`）**已經回傳** `{"date": str, "success": [str], "failed": [{"source","name","error"}]}`。各來源失敗是刻意靜默容錯（單一來源掛掉不拖垮整輪），但呼叫端 `main.py::daily_job`（約 line 147）和 `line_brief_job`（約 line 170）**丟棄了回傳值**，所以連續多日某來源失敗、或排程整個沒跑，使用者只能靠肉眼發現。

**要做**：讓失敗透過既有的 LINE 廣播浮出，並提供一個健康度端點。

### 1a. 排程 job 檢查 run_update 結果並在異常時推 LINE
- 位置：`stocks_power_rich/main.py`，`daily_job` 與 `line_brief_job` 兩處。
- 把 `updater.run_update(...)` 的回傳存下來，判斷是否「資料不健康」：
  - `failed` 非空，或
  - `result["date"]` 落後真實今天超過 N 個交易日（先簡化為：`date` 不是今天且今天是週間交易日 → 視為落後）。
- 不健康時呼叫 `line_push.broadcast_text(cfg.line_token, msg)`（簽名見 `line_push.py:189`，失敗不拋例外，token 空會回 `{"ok":False}` 直接略過，安全）。
- 訊息格式範例（純文字，`broadcast_text` 內部會截到 `MAX_LEN`）：
  ```
  ⚠️ 資料更新警告 2026-07-13
  失敗來源：twse_inst（連線逾時）、taifex_chips
  資料日期：2026-07-11（落後 2 個交易日）
  ```
- **去重**：避免每次排程都轟炸。用 `db.get_setting/set_setting` 存 `last_alert_key`（例如 `date|sorted(failed names)` 的字串），與本次相同就不重推。settings 表與 helper 已存在（`db.py`）。
- 整段包在 `try/except` 裡（比照 line 158~161 的既有容錯風格），告警本身失敗絕不可影響資料更新。

### 1b. 新增 `GET /api/health`（受既有 Basic Auth 保護即可，不必進 /public）
- 回傳各表最新資料日期與落後天數，方便 Zeabur/外部監控輪詢：
  ```json
  {
    "market_daily": {"latest": "2026-07-13", "lag_days": 0},
    "chip_snapshot": {"latest": "2026-07-13"},
    "stock_ohlc": {"latest": "2026-07-11"},
    "custody_dist": {"latest_week": "..."},
    "ok": true
  }
  ```
- 純 SQL `SELECT MAX(date) ...` 各表即可；`ok` = 所有關鍵表 lag 在容忍範圍內。

**測試**（先寫）：
- `tests/test_updater.py` 或新檔：`run_update` 在某 task 拋例外時，回傳的 `failed` 含該來源（用 `monkeypatch.setattr(sources.X, "fetch_...", raiser)`）。
- `tests/test_api.py`：`GET /api/health` 用 `TestClient` + tmp DB，塞一列 `market_daily` 後驗證 `latest`/`ok`。
- 告警去重邏輯：同 key 呼叫兩次，第二次不推（可把 `broadcast_text` monkeypatch 成計數器）。

---

## 任務 2：異地備份（防 Volume 單點故障）

**現況**：`db.backup_db(db_path, keep=7)`（`db.py:27`）用 SQLite 線上備份 API 複製到**同目錄** `backup/`，21:00 job 會呼叫（`main.py:159`）。但 Zeabur 上備份與主庫同在 `/data` volume — volume 故障就一起消失。交易帳本（`tx_history`）與累積的集保週資料（`custody_dist`）**重建不回來**，是全專案唯一「壞了無法復原」的資料。

**要做**：`backup_db` 成功後，把最新那份備份檔再送到 volume 以外的地方。選一種實作（依使用者現有帳號決定，預設走 GitHub）：

- **方案 A（推薦，零額外服務）**：push 到一個 private GitHub repo。用環境變數 `SPR_BACKUP_GIT_REMOTE`（含 token 的 https url）+ `SPR_BACKUP_GIT_BRANCH`。備份後在 `backup/` 內做一次淺 clone/commit/push（或用 GitHub Contents API PUT 單檔）。檔案是 SQLite binary，走 API 要 base64。
- **方案 B**：任何 S3 相容 object storage（`boto3`），env：`SPR_BACKUP_S3_BUCKET/ENDPOINT/KEY/SECRET`。
- 未設定對應 env → 靜默略過（維持純本地備份，本地開發不受影響）。

**接線點**：新增 `stocks_power_rich/offsite_backup.py`，函數 `push_offsite(local_path: str) -> dict`（回 `{ok, error?}`，**不拋例外**）。在 `main.py` 的 21:00 job `backup_db(cfg.db_path)` 之後呼叫，包 try/except。
**設定集中**：新 env 加進 `config.py`（比照現有欄位風格）。
**安全**：token/secret 絕不可出現在任何 API 回應或日誌；`/api/settings` 只回布林 `offsite_backup_configured`（比照 `gemini_configured`/`line_configured` 慣例）。
**測試**：`push_offsite` 在缺 env 時回 `{"ok": False, "skipped": True}`；有 env 時 monkeypatch 掉實際網路呼叫，驗證帶入正確路徑。

---

## 任務 3：ECharts 改本地 vendor（去掉唯一外部 CDN）

**現況**：`web/index.html:8` 從 `cdn.jsdelivr.net` 載 `echarts@5`。CSP 因此得為 jsdelivr 開洞（見 `main.py` 的 security headers middleware 與 `CLAUDE.md` 安全段）。斷網或 CDN 故障 → 全站圖表死。

**要做**：
1. 下載 `echarts.min.js`（v5，與現用同版）放到 `web/vendor/echarts.min.js`。
2. `index.html:8` 改為 `<script src="/vendor/echarts.min.js"></script>`（`web/` 已由 `StaticFiles` 掛在 `/`，故 `/vendor/...` 直接可服務，確認路徑）。
3. `public/overview` 等頁面若也引 echarts CDN，一併改本地。
4. 收緊 CSP：`script-src`/`connect-src` 移除 jsdelivr，改回 `'self'`（保留現有 no `unsafe-eval` 原則）。
**驗證**：用 preview 工具開站，`read_console_messages` 確認無 CSP violation、圖表正常渲染，`read_network_requests` 確認不再打 jsdelivr。

---

## 任務 4（中期，最高「產品價值」）：訊號追蹤帳本 / forward test

**動機**：`backtest.py` 只回測杯柄，且是歷史一次性回測（檔頭自承存活者偏差 + 未含手續費）。真正有力的是**無存活者偏差的前瞻測試**：每天把當日選股訊號快照存起來，日後自動補上實際報酬。

**要做**：
- 新表 `signal_ledger(signal_date, code, name, source, entry_ref_price, ret5, ret10, ret20, ...)`，`source` ∈ {`filtered_picks`, `cup_handle`}。用 `db.init_db` 的 lazy migration 慣例新增。
- 每日 job 資料到齊後：把 `analysis.filtered_picks(rows)`（`analysis.py:35`，W55 翻多∧大戶增比>0∧營收年增>0∧推估EPS>0）與 `patterns.cup_handle_signals`（`patterns.py:61`）的當日命中寫入，`entry_ref_price` = 當日收盤（記錄假設進場基準）。
- 補漲跌：每日 job 掃 `signal_ledger` 中 `ret5/10/20` 仍為 null 且已滿 N 交易日者，用 `stock_ohlc` 算實際報酬回填。
- 端點 `GET /api/signals/performance`：回各 source 的 5/10/20 日勝率、平均、樣本數；並可與**交易帳本的 alpha**（`analysis.trade_stats`）對照 —— 你「實際挑的」vs「訊號全買」的差距。
- 前端加一頁或一區塊呈現。誠實揭露：這是含手續費前的理論報酬、樣本自訊號建立日起算。
**測試**：純函數優先 —— 給定 ledger 列 + `stock_ohlc` 假資料，回填函數算出正確 `retN`；`performance` 聚合正確。

---

## 任務 5（維護性）：main.py 拆 APIRouter

`main.py` 1662 行、48 條路由全塞在 `create_app` 閉包。拆成 `stocks_power_rich/routers/`（`market`, `stock`, `trades`, `csv`, `public`, `admin`）。
**注意**：現在很多路由用閉包捕捉 `cfg`/`conn()`；拆分時改用 FastAPI `Depends` 提供 `conn`、或 `APIRouter` + `app.state`。這是**純重構、零行為變更** —— 拆完 `pytest` 全綠即算成功，不可改任何回應格式。建議在下一個大功能前做，越晚越痛。

---

## 任務 6（去地雷）：消除 Elliott 波浪雙實作

`elliott.py`（Python）與 `web/app.js` 內的 JS 版是同一演算法的兩份人工同步副本 —— `CLAUDE.md` 自己標為地雷。
**要做**：加端點 `GET /api/stock/{code}/elliott`（或併入既有 kline 回應）回傳 Python 版計算結果；前端改為取用 API，刪掉 `app.js` 內的 JS 實作。永久移除一個 regression 來源。
**測試**：端點回傳與 `elliott.py` 純函數一致；前端 preview 驗證波浪標記仍正確。

---

## 不要動
- SECURITY.md 中**刻意延後**的項目（M2 rate-limiting、M4 TDCC `verify=False`、L4 error detail）：文件已逐項評估為低殘餘風險，勿自行重開。
- 兩條 invariant：`run_update` 刪未來列必須 key off 真實 `datetime.now()`（非抓到的日期）；取「近一月」指數要錨在**上月最後一日**。
- 月界 / ROC 民國年 / TDCC 空白補齊 / TPEx 固定欄位位置解析等 quirk（見 CLAUDE.md「Data-source quirks」）。

## 建議執行順序
1 → 3 → 2（此三項一個下午可完成，直接補上「系統靜默失效」的最大風險）→ 4（產品價值最高）→ 6 → 5。
每項獨立 commit、`pytest` 全綠再進下一項。
