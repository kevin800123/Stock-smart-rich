# 資訊安全評估與改善計畫

評估日期：2026-07-04（唯讀評估，未變更任何線上資料）
範圍：Zeabur 單一服務（FastAPI 後端 + 同源靜態前端）、SQLite（/data Volume）、
外部整合（TWSE/TPEx/TDCC/期交所/yfinance/Gemini/LINE）。

## 一、現況架構摘要（安全視角）

- 單一服務、同源部署：無 CORS 面、無跨網域問題；Zeabur 提供 TLS。
- 無任何身分驗證：所有 `/api/*` 與前端頁面公開可存取。
- 無使用者帳號系統：不存個資；敏感資料為金鑰（env）、個人選股 CSV（私有 repo + DB）。
- 金鑰管理正確：GEMINI_API_KEY / LINE_CHANNEL_ACCESS_TOKEN 僅存環境變數，
  API 只回傳 `*_configured: bool`；`.env` 已被 gitignore 且未入庫。
- SQL 全數參數化；動態欄名僅來自程式內常數（MARKET_COLS 等），無注入面。
- 上傳檔名已取 basename（防路徑跳脫）。

## 二、風險清單（依嚴重度排序）

### 高風險

**H1. 全站無身分驗證（最關鍵）**
任何知道網址的人都可以：
- `POST /api/line/test`：對你的 LINE 濫發訊息、耗盡每月 200 則免費額度
- `GET /api/market/summary?refresh=1`、`/api/analysis/summary?refresh=1`：燒 Gemini 額度
- `POST /api/csv/upload`：塞偽造選股資料汙染 DB（進而汙染 watchlist/AI 分析）
- `POST /api/update/run`、`GET /api/backfill`：觸發對官方站的出站抓取風暴（放大攻擊）
- `POST /api/settings`：改排程時間與 data_dir
- 讀取你的自選股、選股結果（個人投資隱私）

**H2. 儲存型 XSS（透過 CSV 上傳）**
前端 43 處 `innerHTML` 直接嵌入 API 回傳字串（股名、產業、細產業等皆源自 CSV/外部 API）。
無驗證的上傳端點可塞入 `<img onerror=...>` 之類股名 → 你開頁即執行攻擊者 JS。
與 H1 疊加後為實際可行攻擊鏈。

**H3. data_dir 可被改成任意伺服器路徑**
`POST /api/settings` 的 `data_dir` 未白名單化，`/api/csv/import-latest`、`/api/csv/import-all`
會對該目錄 listdir 並解析檔案 → 有限度的本機檔案內容洩漏管道。

### 中風險

**M1. 上傳無大小/型別限制**：超大檔或 xlsx 炸彈可耗盡記憶體/磁碟（DoS）。
**M2. 無速率限制**：昂貴端點（update/backfill/AI/LINE/未快取日期的 sectors 抓取）可被重放濫用。
**M3. 無資料庫備份**：Volume 單點；誤刪或平台事故即遺失全部累積（集保逐週資料不可重建）。
**M4. TDCC 抓取 `verify=False`**：停用 TLS 驗證（對方憑證缺 SKI 的已知怪癖），理論上可被
中間人竄改集保數據（僅影響資料正確性，不涉金鑰）。

### 低風險

**L1. 無安全性回應標頭**（CSP、X-Content-Type-Options、X-Frame-Options）。
**L2. 依賴套件未鎖版、無漏洞掃描**（requirements.txt 為 `>=` 浮動版本）。
**L3. 無 CSRF 防護**：目前無 cookie/session，實質風險極低；導入認證時需一併考慮。
**L4. 錯誤訊息偶帶內部細節**（如 AI 失敗訊息含例外字串）。

## 三、改善計畫

### 第一階段 P0（高風險止血，約半天）

1. **全站 Basic Auth**（修 H1，並讓 H2/H3 從「任何人」降為「僅本人」）
   - Starlette middleware 攔所有請求（含靜態頁），比對 `SPR_BASIC_USER/SPR_BASIC_PASS` env
     （`secrets.compare_digest` 防時序攻擊）；未設定 env 時不啟用（本機開發不受影響）。
   - 瀏覽器原生帳密視窗，記住密碼後日常無感；LINE 推播為出站不受影響。
   - 排程為進程內呼叫不經 HTTP，不受影響；`cli.py`/外部 cron 需帶帳密。
2. **前端統一 HTML escape**（修 H2）
   - 加 `esc()` 工具函數，對所有嵌入 innerHTML 的動態字串（股名/產業/檔名等）跳脫。
3. **data_dir 白名單**（修 H3）：限制為 repo 內 `Date/`（或以 env `SPR_DATA_DIR` 為根，
   settings 僅允許其子目錄；`os.path.realpath` 驗證）。
4. **上傳限制**（修 M1）：大小上限 10MB、副檔名白名單 .csv/.xlsx/.xlsm（讀入前先檢查）。

### 第二階段 P1（一週內）

5. **輕量速率限制**（修 M2）：程式內 in-memory 計數（單 worker 即可），
   對 update/backfill/line/test/AI refresh 設每分鐘上限；超限回 429。
6. **每日 DB 備份**（修 M3）：排程 job 內以 SQLite `VACUUM INTO` 產生
   `/data/backup/spr-YYYYMMDD.sqlite`，保留 7 份輪替；另提供（認證後的）下載端點以便異地備援。
7. **安全標頭 middleware**（修 L1）：`X-Content-Type-Options: nosniff`、
   `X-Frame-Options: DENY`、基本 CSP（`default-src 'self'` + ECharts CDN 白名單）。

### 第三階段 P2（持續性）

8. **依賴治理**（修 L2）：`pip freeze` 產生 lock 檔；定期 `pip-audit`（可進 CI）。
9. **TDCC 憑證**（修 M4）：改以固定 CA bundle 驗證；不可行則維持現狀並保留此已知風險記錄。
10. **錯誤訊息淨化**（修 L4）：對外回應改通用訊息，細節僅寫 log。
11. **金鑰輪替習慣**：LINE token / Gemini key 每半年重發一次；洩漏疑慮時立即重發
    （兩者皆可於原後台重發，服務只需更新 env）。

## 四、明確不做（與理由）

- 完整帳號系統 / OAuth：單一使用者，Basic Auth + TLS 已足夠，複雜度不划算。
- WAF / 雲端資安服務：面向單人的私人工具，成本效益不符。
- 資料庫加密：SQLite 位於平台管控的 Volume，威脅模型下優先序低於備份。

## 五、驗收清單

- [ ] 未帶帳密存取任何頁面/API → 401
- [ ] 上傳含 `<script>` 股名的 CSV → 頁面以純文字顯示，不執行
- [ ] settings 設 data_dir=/etc → 拒絕
- [ ] 上傳 20MB 檔 → 413/明確拒絕
- [ ] 1 分鐘內連打 /api/line/test 10 次 → 大部分 429，LINE 額度未被消耗
- [ ] /data/backup/ 每日出現新備份且自動輪替
- [ ] 全測試套件通過；LINE 排程推播、每日更新不受影響
