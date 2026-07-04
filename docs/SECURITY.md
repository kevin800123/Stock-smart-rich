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

> 進度：**P0 + P1 + P2 已於 2026-07-04 實作完成**（含測試，全套 115 passed）。
> - P0：H1 全站 Basic Auth、H2 前端 XSS 跳脫、H3 data_dir 白名單、M1 上傳限制。
>   啟用方式：Zeabur 環境變數設 `SPR_BASIC_USER` 與 `SPR_BASIC_PASS`（已啟用）。
> - P1：M3 每日 DB 備份輪替、L1 安全性回應標頭（CSP 等）。M2 速率限制經評估後降級暫緩
>   （Basic Auth 已擋掉未授權濫用者，主要風險已消除）。
> - P2：L2 依賴鎖版 + pip-audit 掃描（無已知漏洞）。M4、L4 經評估後維持現狀並記錄理由
>   （詳見下方 P2 段落）；L3 CSRF 隨 P0 導入認證時已一併評估、風險仍低。

### 第一階段 P0（高風險止血，約半天）— ✅ 已完成

1. ✅ **全站 Basic Auth**（修 H1，並讓 H2/H3 從「任何人」降為「僅本人」）
   - `@app.middleware("http")` 攔所有請求（含靜態頁），`_check_basic` 以 `secrets.compare_digest`
     等時間比對 `SPR_BASIC_USER/SPR_BASIC_PASS`；兩者皆設定才啟用（未設定＝本機開發不受影響）。
   - 瀏覽器原生帳密視窗，記住密碼後日常無感；LINE 推播為出站、排程為進程內呼叫，皆不受影響。
   - 平台健檢即使收到 401 仍代表服務存活（Zeabur 預設只檢查連線）。
2. ✅ **前端統一 HTML escape**（修 H2）
   - `esc()` 跳脫 `& < > " '`，套用於 `stockLink()` 及所有嵌入 innerHTML 的股名/產業/類股/
     細產業/檔名/錯誤訊息與 ECharts tooltip/label。已於瀏覽器實測：惡意股名以純文字顯示、不執行。
3. ✅ **data_dir 白名單**（修 H3）：`_dir_within` 以 `realpath` 驗證，settings 僅允許
   專案根 `REPO_DIR` 或 env `SPR_DATA_DIR` 之下的路徑；根外（如 /etc、C:\Windows）一律拒絕。
4. ✅ **上傳限制**（修 M1）：副檔名白名單 .csv/.xlsx/.xlsm、大小上限 10MB（`read(N+1)` 有界讀取）。

### 第二階段 P1（一週內）

5. ⏸️ **輕量速率限制**（修 M2）— **暫緩**：Basic Auth 啟用後，未授權者無法觸達昂貴端點，
   放大/濫用風險已消除；登入使用者自我重放的殘餘風險極低，故降級暫不實作。
6. ✅ **每日 DB 備份**（修 M3）：`db.backup_db` 以 SQLite 線上備份 API 複製整庫到
   DB 同目錄 `backup/spr-YYYYMMDD.sqlite`，輪替保留最近 7 份；掛在 21:00 排程 job，
   另有（認證保護的）`POST /api/db/backup` 可手動觸發。
7. ✅ **安全標頭 middleware**（修 L1）：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
   `Referrer-Policy: no-referrer`、CSP（`default-src 'self'`、script 限自站+jsdelivr、無 unsafe-eval、
   style 放行 inline、`frame-ancestors 'none'`）。已於瀏覽器實測 ECharts 正常、無 CSP 違規。

### 第三階段 P2（持續性）— ✅ 已評估／已完成

8. ✅ **依賴治理**（修 L2）：`requirements-lock.txt` 為 `pip freeze` 完整解析快照（63 個套件，
   供重現性對照）；`pip-audit -r requirements.txt` 掃描結果為 **無已知漏洞**
   （2026-07-04；稽核工具用完即解除安裝，避免其相依套件混入 app 環境/鎖定檔）。
   定期做法：往後每次改動 requirements.txt 或每季，重跑上述兩步驟。
9. ⏸️ **TDCC 憑證**（M4）— **維持現狀，經評估記錄**：實查該憑證為合法 TWCA 簽發
   （CN=epassbook.tdcc.com.tw，效期至 2026-09-04），僅缺 Subject Key Identifier 擴充欄位
   導致 Python 嚴格鏈驗證失敗。評估兩種釘選方案皆不划算：釘葉憑證 2 個月後到期即失效、
   需人工追蹤更新；釘 CA 需自刻手動鏈驗證，複雜度與潛在 bug 風險更高。且該資料為 TDCC
   每週公開發布的集保戶股權分散統計（非帳密、非個資），遭竄改頂多分析數字失準。
   決策：維持 `verify=False`（已窄範圍限定僅此一處），理由已寫入 `tdcc.py` 程式碼註解。
10. ⏸️ **錯誤訊息淨化**（L4）— **維持現狀，經評估記錄**：這些回應（如「AI 摘要失敗：…」、
    updater 各來源失敗訊息）皆已被 P0 的全站 Basic Auth 保護，僅認證後的擁有者本人可見；
    淨化細節在此威脅模型下安全效益低，反而犧牲日常除錯能力（判斷哪個資料源、為何失敗）。
    與 M2 的降級理由一致：認證上線後，風險評估基準已改變。
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
- [x] /data/backup/ 每日出現新備份且自動輪替（21:00 排程 + `POST /api/db/backup`，保留 7 份）
- [x] 回應帶 CSP / X-Frame-Options / X-Content-Type-Options，且 ECharts 正常無 CSP 違規
- [x] `pip-audit -r requirements.txt` 無已知漏洞；`requirements-lock.txt` 與目前環境一致
- [x] 全測試套件通過（115）；LINE 排程推播、每日更新不受影響
