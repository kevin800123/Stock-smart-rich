# 股力智富交接文件（2026-08-03）

給下一位接手的 AI／工程師。本文是目前唯一有效的交接文件；先讀專案根目錄 `AGENTS.md`，再讀本文。

## 目前版本與部署

- Git branch：`main`
- 最新已推送 commit：`54cee34 fix: send only full evening line brief`
- Zeabur：推送 `main` 會自動部署。必須掛 Persistent Volume `/data`，並設 `SPR_DB_PATH=/data/spr.sqlite`。
- 專案是單一 FastAPI 服務：API 在 `/api/*`，前端是 `web/` 的 vanilla JS，沒有前端 build step。
- 日常執行：`.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000`

## 接手前必讀規則

1. `AGENTS.md` 的「單一資料日期 D」與兩個不可回歸 invariant 是最高優先。
2. 來源模組維持「純解析函式＋薄網路 wrapper」，新解析先補測試。
3. 前端所有外部／CSV 字串進 `innerHTML` 前必須走 `esc()`。
4. 不要把晚公布資料拿前一天值冒充當日資料。
5. 不要洩漏 `.env`、LINE token、Telegram token、Gemini key 或含 token 的 backup remote。
6. commit 結尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`（或依實際 agent 慣例）。

## 最近完成項目

### 深色財經介面

- `web/styles.css` 已有完整 CSS Variables：曜石黑／冷藍灰、台股紅漲綠跌、緊湊 spacing 與高資訊密度字級。
- Sidebar 固定、主畫面捲動；表格有 sticky header、zebra striping、hover highlight。
- ECharts 深色主題、K 線／法人圖、tooltip、dataZoom 已優化在 `web/app.js`。

### 每日財經新聞與 Telegram

- 入口：`/api/news`，前端：每日財經新聞頁。
- 排程：07:00 `morning`、12:00 `midday`、17:00 `afternoon`、21:10 `evening`，時區 Asia/Taipei。
- 每個時段的台股／日股／美股均配置 **6 則**；閱讀焦點按時段不同。
- 日股來源要求為株探（Kabutan），擷取後以繁中推播。不要改用未驗證的日文新聞源取代。
- 主要程式：`stocks_power_rich/api/news.py`、`stocks_power_rich/gemini.py`、`stocks_power_rich/telegram_push.py`、`stocks_power_rich/main.py`。
- Telegram 推播需有 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`；缺任一個不註冊 job。
- 推播輸出是精簡 Telegram 模板；不要再次輸出「來源未提供可驗證數據」這類系統字樣，也不要把標題重寫成摘要。

### LINE：只保留晚間完整版

- **已移除 16:00 的 LINE 速報 job**。`SPR_LINE_PUSH_TIME` 已不再讀取，即使 Zeabur 留著這個 env 也沒有作用。
- 每日完整 LINE 推播由 `main.py::scheduled_job` 在 `SPR_SCHEDULE_TIME`／設定頁的 schedule time 執行；預設必須是 **21:00**。部署後請在 Zeabur／設定頁確認沒有把它改成別的時間。
- 每日完整版本會先 `run_update`，再 `_push_line(c, full=True)`，包含融資／融券與維持率。
- 每週六週報與盤中杯柄突破警示仍各自存在；本次只停用每日 16:00 簡版，不是關閉所有 LINE 功能。
- `api/helpers.py::_check_update_result_and_alert` 已將下列「預期晚公布」排除 LINE 警告：
  - `margin_maintenance`
  - `otc_margin_maintenance`
  - `intl` 當日海外市場尚未收盤
  這些仍留在更新結果中並由後續回補，不可重新當成錯誤洗版。
- LINE 截圖中 `You have reached your monthly limit` 是 LINE Messaging API **broadcast/push 月額度**用盡，不是 Gemini 或 Telegram。程式會把前一次失敗記下，下一次成功時補發「前次推播失敗」通知；因此看到通知不代表當次也失敗。
- 下一個 LINE 改善候選：偵測 monthly limit 後，當月停止主動 broadcast，次月再恢復；LINE webhook reply 不耗 broadcast 額度。

### 高價股監控（本輪核心）

- 前端：`web/app.js::loadRankPrice()`；API：`GET /api/rank/price`，後端 `stocks_power_rich/api/market.py::rank_price`。
- 表格目前欄位：股票、**股價／漲跌**、成交量、成交額、**成交額較 10 日均**、周轉率、法人淨額（億）、資金訊號。
- 已移除成交量增減、獨立 10 日均成交額、時間欄，避免表格過寬。
- 成交額較 10 日均：基準取 **前 10 個有官方成交額的交易日，不含今天**。放大 >=100% 顯示淡紅底與 🔥。
- 周轉率：`成交量 × 1000 / 已發行股數 × 100`，上市股數來自 TWSE 公司基本資料，上櫃來自 TPEx 公司基本資料。
- 法人淨額：上市取 TWSE `T86`、上櫃取 TPEx `dailyTrade` 的當日三大法人**淨買賣張數**，以現價換算億元。因此 UI 前綴 `~`，不可誤稱官方原始買賣金額；hover 可看外資／投信／自營商分項張數。
- 盤中／尚未公告法人資料一律顯示 `—`，不可回填昨日。
- 資金訊號的純資料分類（非投資建議）在 `_capital_signal()`：
  - `法人承接`：法人買超＋成交額放大至少 30%＋周轉率 <=2%
  - `放量分歧`：法人賣超＋成交額放大至少 30%
  - `低周轉偏多`：法人買超＋周轉率 <=2%
  - `高周轉觀察`：周轉率 >=5%
  - `放量觀察`：成交額放大至少 30%
- 資金訊號 badge 已放大至 15px（與表頭同級）。
- 未做／不可虛構：內外盤比、特大單占比、券商分點主力集中度。它們需要逐筆成交／分價或券商分點資料源，現有 MIS／T86 沒有可驗證資料。

## 重要檔案地圖

| 目的 | 檔案 |
|---|---|
| App 與排程 | `stocks_power_rich/main.py` |
| 設定／環境變數 | `stocks_power_rich/config.py` |
| 每日資料更新與回補 | `stocks_power_rich/updater.py` |
| LINE 內容與發送 | `stocks_power_rich/line_push.py`、`stocks_power_rich/api/line.py` |
| 共享 API 邏輯（LINE、快取、告警） | `stocks_power_rich/api/helpers.py` |
| 高價股／大盤 API | `stocks_power_rich/api/market.py` |
| 新聞 API | `stocks_power_rich/api/news.py` |
| Gemini 摘要與新聞 prompt | `stocks_power_rich/gemini.py` |
| SQLite schema／migration | `stocks_power_rich/db.py` |
| 前端邏輯 | `web/app.js` |
| 前端設計系統 | `web/styles.css` |
| 測試 | `tests/test_api.py`、`tests/test_health.py`、`tests/test_scheduler.py`、`tests/test_gemini.py`、`tests/test_news_api.py` |

## 驗證指令

Windows PowerShell：

```powershell
.venv\Scripts\python -m pytest -q
node --check web\app.js
.venv\Scripts\python -m py_compile stocks_power_rich\api\market.py
```

若 pytest 的預設 temp 目錄遇到 Windows 權限問題，使用：

```powershell
.venv\Scripts\python -m pytest tests\test_api.py -k "rank_price" -q --basetemp C:\tmp\spr-tests
```

不要用 `pytest | tail`，它會掩蓋 pytest 的失敗 exit code。

## 部署與操作提醒

- Zeabur 必須單 worker；多 worker 會重複註冊 scheduler 並競爭 SQLite。
- `SPR_ENABLE_SCHEDULER=1` 才會執行排程。
- 完整版 LINE 測試端點：`POST /api/line/test`（會真的消耗 LINE push 額度）。
- 新聞測試／手動推播也會真的發 Telegram；先檢查 token/chat id。
- `GET /api/health` 可確認資料新鮮度。

## 建議的下一步（依優先順序）

1. LINE monthly limit：遇到額度用盡後本月暫停主動 broadcast，保留 webhook reply。
2. 高價股：若使用者確定需要「外盤比／特大單」，先選定可授權且穩定的逐筆成交資料源，再做 parser、快取、資料延遲標示與測試；不要用 MIS 五檔委買賣推估成真實成交。
3. 檢視 Telegram 每時段台／日／美各 6 則的篇幅與 API／Gemini 成本；需求目前是 18 則，勿未經確認自行減量。
4. 做任何 schedule 變更時，更新 `README.md`、設定頁文字、`tests/test_scheduler.py`，三者不可漂移。
