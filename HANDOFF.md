# 交接檔 — 股力智富（貼到新 session 用）

## 專案

`C:\Users\kevin\Desktop\AI\Claude\股力智富` — 台股籌碼分析 App（FastAPI 後端 + vanilla-JS 前端同一服務）。
專案規範全在 `CLAUDE.md`，新 session 會自動載入，**不必重貼**。

## 目前狀態（2026-07-22）

- 分支 `main`，與 `origin/main` 同步。
- 工作區乾淨；未追蹤的有 `.agents/`、`AGENTS.md`（Codex 鏡像檔，**永遠不要 commit**）
  與 `docs/line-richmenu/`（LINE 圖文選單圖，**使用者拍板不進 repo**，留本機即可）。
- 測試：`223 passed`（全綠）。

## 這一輪完成的三件事

### 1. 高價股監控獨立分頁（commit `4dda343`）

原本寄生在海期監控頁上半部的「台股高價股排行」拆成獨立分頁，排在側欄「市場」組的海期監控下面。

- 輪詢器拆兩支：高價股 10 秒、海期 120 秒，各自進頁啟動、離頁停止（原本共用一支計時器）。
- 表格 9 欄：# / 股票 / 成交價 / 漲跌 / 漲跌% / **成交量(張)** / **成交額(億)** / **成交額增減** / 時間。
- 成交額走**混合來源**（使用者拍板）：MIS 盤中快照只有成交量 `v`、**沒有成交金額欄位**，所以
  官方盤後值優先（新增 `twse.parse_stock_turnover` 具名取欄、`tpex.parse_otc_turnover` 位置取欄，
  依日期永久快取進 `ai_cache`），盤中官方未發布時退回 `量×1000×現價` 估算並標 `amount_est`。
- 成交額增減與「前一個拿得到官方量額的交易日」比；前一日無官方值 → 兩欄皆 None，**不拿估算值當基準**。

### 2. LINE webhook 主動查詢（commit `ed22fed`）

**起因**：免費額度 200 則被燒完。broadcast/push 按「收訊人數」計費（好友 6 人＝一則扣 6 則），
但 **reply（回覆使用者訊息）完全不計額度、無上限**。

- 新增 `POST /line/webhook`（`stocks_power_rich/api/line.py`）：傳關鍵字給 OA 即以 reply 回內容。
- 指令：`大盤`/`簡報`/`速報` → 速報、`完整`/`總結` → 含融資券、`週報`、`高價股` → Top10、其他 → 說明。
- **安全**：LINE 伺服器無法帶 Basic Auth，故該路徑列入 `main.py` 免帳密白名單，
  改以 `LINE_CHANNEL_SECRET` HMAC-SHA256 驗簽把關。secret 未設定＝整支關閉（503）、簽章不符 403。
  **簽章過就一律回 200**（回非 2xx 會被 LINE 判定 webhook 失效並停用）。
- 內容組裝抽成 `helpers._compose_daily_text` / `_weekly_text` / `_rank_text`，與排程推播共用不漂移。
- webhook 用 `force=True`：使用者自己問的就該回，staleness 略過規則只保護自動推播。

**已上線且驗證過**：Zeabur 已設 `LINE_CHANNEL_SECRET`，Webhook URL
`https://stock-power-rich.zeabur.app/line/webhook` 已 Verify，使用者手機實測四個指令都會回。

### 3. 高價股訊息排版（本次 commit）

使用者反映手機上折行很亂 → `line_push.compose_rank_brief()`（純函數，可單元測試）：

- **一檔一行**：` 1 信驊　　15,510　+10%　319張　▼12.2億`
- 價格取整數——`15,510.00` 這種 7 位數字串會被 **LINE 誤判成電話號碼自動加藍色連結**（實際踩到過）。
- 漲跌%取整數；成交額只留增減（絕對值由量×價可推，是重複資訊）。
- 盤中估算以行尾 `*` ＋末尾註腳表示，不用 `(估)`——那 4 格會把最長的一行撐到折行。
- 測試鎖住**顯示寬度 ≤44 半形單位**（LINE 訊息框約容 22 個全形字），日後加欄位會被測試擋下。

## LINE 圖文選單（已設定完成）

六格版型（2500×1686，3欄×2列），圖在 `docs/line-richmenu/`（不進 repo，用 medium 1200×810）。
產圖腳本沒有保留在 repo 裡；要重做的話重點是：格線 x=0/833/1667/2500、y=0/843/1686，
配色取自 `web/styles.css`（bg `#0f1419`、panel `#1a2029`、accent `#f0a500`），
字級要**明顯大於圖示**（主標 132px、圖示縮到 46%，使用者明確要求過）。

動作對應：A/B/C（上排）＝**文字**指令 `大盤`／`高價股`／`週報`；D/E/F（下排）＝三個 `/public/*` 連結。

## 這個專案的固定工作習慣（請沿用）

- **push 前一定要等使用者說「push」**，絕不自行推送，也不因為前面批准過就順帶推。
- **絕不用 `git add -A`**：一律列明檔案 `git add web/app.js ...`，staged 前後都用 `git status -s`
  確認 `.agents/`、`AGENTS.md`、`docs/line-richmenu/` 沒被掃進去。
- 後端改動走 TDD：先寫 parse/endpoint 測試再實作。
- 非小改動先進 Plan Mode：Explore 探索 → `AskUserQuestion` 問決策點 → 寫計畫檔 → `ExitPlanMode` 等批准。
- 前端改完要**實際在瀏覽器驗證**（`mcp__Claude_Browser__*`）。截圖管線偶爾卡住時，改用
  `javascript_tool` 跑 `getComputedStyle` / `getBoundingClientRect` / DOM 查詢回傳 JSON 驗證，效果更穩。
- 跑 pytest **不要接 `| tail`**（會吃掉 exit code）；全套約 70–130 秒。
- 終端機會亂碼 CJK，要看含中文的 API 輸出就寫成 UTF-8 檔再用 Read 讀。
- 動 `.claude/launch.json` 前先看內容——它已存在且路徑用 Windows 反斜線，別直接 Write 覆蓋。

## 本機啟動

```bash
.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000
```

測試：

```bash
.venv\Scripts\python -m pytest -q
```

## 待辦

無。三件事都已完成、驗證、commit 並 push。新 session 可直接接受新需求。
