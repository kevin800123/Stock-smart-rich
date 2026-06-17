# STOCKS POWER RICH — 設計規格

> App 顯示名稱：**STOCKS POWER RICH**（專案資料夾 `股力智富`、Python 套件 `stocks_power_rich`）

- 日期：2026-06-17
- 狀態：待使用者複審
- 專案類型：本機網頁 App（Python 後端 + 瀏覽器前端，單機單人使用）

## 1. 目標與範圍

打造一個跑在使用者本機的股市分析網頁 App，核心價值是「一鍵每日自動更新」。兩大功能：

1. **大盤儀表板**：呈現台股大盤與國際市場關鍵指標，可一鍵更新、可選每日排程自動更新。
2. **CSV 籌碼分析**：使用者每日上傳籌碼 CSV，系統找出「每週大戶增加／散戶減少」的類股與個股，與上週比較，並統整籌碼面／技術面／基本面／財務面，必要時用 Gemini 產生白話洞察。
3. **個股 K 線圖**：分析選出的任一個股，可點開看日 K 線（蠟燭圖＋成交量＋均線），方便對照籌碼訊號與技術走勢。

### 明確不做（YAGNI）
- 不做多人帳號／登入／雲端部署（單機單人）。
- 不做即時 tick 行情（只做每日／盤後資料）。
- 不做下單、不串券商交易。
- 第一版不做手機原生 App（瀏覽器響應式即可）。

## 2. 技術選型

| 層 | 選擇 | 理由 |
|---|---|---|
| 後端 | Python 3.11+、FastAPI、Uvicorn | 非同步抓 API、好測試、pip-only |
| 資料層 | SQLite（標準庫 sqlite3 + 薄資料存取層） | 單機單人最合適、零安裝、可回溯快照 |
| HTTP 抓取 | httpx | 同步/非同步皆可、好用 respx 做測試 |
| 資料分析 | pandas | CSV 解析、跨週 diff、產業彙整最自然 |
| 國際指數 | yfinance | 免費免金鑰，涵蓋 SOX/N225/KOSPI/Gold/BTC |
| 排程 | APScheduler（程式內） | 可選每日自動更新；另附 Windows 工作排程器備援 |
| AI | google-genai（Gemini 最新模型） | CSV 統整 + 大盤盤勢摘要；無金鑰時降級 |
| 前端 | 單頁 HTML + 原生 JS + ECharts(CDN) | 免 Node 建置；金融圖表豐富 |
| 測試 | pytest + respx | 不打真網路；以 fixture 驗證邏輯 |

## 3. 專案結構

```
股力智富/
├─ stocks_power_rich/
│  ├─ __init__.py
│  ├─ main.py            # FastAPI 入口、路由、啟動時開瀏覽器
│  ├─ config.py          # 讀 .env：GEMINI_API_KEY、排程時間、yfinance 代碼表
│  ├─ db.py              # SQLite 連線、schema 建立、通用 upsert
│  ├─ sources/
│  │   ├─ twse.py        # 加權指數、三大法人現貨、融資融券、本益比殖利率淨值比
│  │   ├─ taifex.py      # 台指期行情、期貨三大法人未平倉、散戶多空比
│  │   ├─ intl.py        # yfinance：費半/日經/KOSPI/黃金/BTC
│  │   └─ kline.py       # yfinance：個股日 OHLC 歷史（K 線用）
│  ├─ csv_import.py      # 解析 Big5 CSV → 標準化 → 寫入 chip_snapshot
│  ├─ analysis.py        # 當日訊號榜、跨週比較、產業彙整、多因子統整
│  ├─ gemini.py          # Gemini 統整（CSV 洞察 + 大盤摘要），無金鑰降級
│  ├─ updater.py         # 「一鍵更新」協調者：依序抓所有來源、容錯
│  └─ scheduler.py       # APScheduler 可選每日自動跑
├─ web/
│  ├─ index.html
│  ├─ app.js
│  └─ styles.css
├─ data/                 # spr.sqlite + 上傳 CSV 原檔留存（依日期）
├─ tests/                # 每模組對應測試 + fixtures（小型假 CSV、錄製 API 回應）
├─ .env.example
├─ requirements.txt
├─ README.md
└─ 啟動.bat              # 一鍵啟動：起 Uvicorn 並開預設瀏覽器
```

## 4. 資料來源對應

| 儀表板項目 | 來源 | 端點/方法 | 備註 |
|---|---|---|---|
| 加權指數＋漲跌 | TWSE openapi | `exchangeReport/FMTQIK`、`exchangeReport/MI_INDEX` | 取最新交易日 |
| 三大法人現貨買賣超（大盤） | TWSE openapi | `fund/BFI82U` | 外資/投信/自營 |
| 三大法人買賣超（個股，輔助） | TWSE openapi | `fund/T86` | 供個股檔案 |
| 融資融券餘額/增減 | TWSE openapi | `exchangeReport/MI_MARGN` | 大盤融資券 |
| 本益比/殖利率/淨值比 | TWSE openapi | `exchangeReport/BWIBBU_ALL` | 基本面補充 |
| 台指期行情 | TAIFEX openapi | 期貨每日交易行情端點 | TX/MTX/TMF |
| 期貨三大法人未平倉淨額 | TAIFEX openapi | 三大法人－區分各期貨契約端點 | 算散戶多空比用 |
| 微台/小台散戶多空比 | 本系統計算 | 見 §6 公式 | |
| 費半/日經/KOSPI/黃金/BTC | yfinance | `^SOX ^N225 ^KS11 GC=F BTC-USD` | 漲跌與最新值 |
| 個股日 K 線（OHLC＋量） | yfinance | `<股號>.TW`（如 `2330.TW`） | 供蠟燭圖，預設近一年 |

> **實作第一步**（寫入計畫的第一個任務，非 placeholder）：實際呼叫 TWSE/TAIFEX openapi，把上表 TAIFEX 兩個端點的精確 URL 字串與回應欄位記錄到 `sources/taifex.py` 的常數與測試 fixture，確認後才往下做。資料本身確定存在（期交所每日公布），僅需釘死 URL 與欄位名。

## 5. 資料儲存（SQLite schema）

- **market_daily**（每交易日一列）：`date PK`、`taiex`、`taiex_chg`、`inst_foreign`、`inst_trust`、`inst_dealer`、`margin_balance`、`margin_chg`、`short_balance`、`short_chg`、`tx_price`、`tx_chg`、`fut_inst_net`、`retail_ls_mtx`（小台散戶多空比）、`retail_ls_tmf`（微台）、`sox`、`n225`、`kospi`、`gold`、`btc`、`updated_at`。
- **chip_snapshot**（每次上傳一批）：`snap_date`、`code`、`name`、`industry`、`sub_industry`、`close`、`big_holder_ratio`（大戶增比）、`holder_drop_ratio`（人數降比＝散戶減少）、`month_inc`（月增）、`rev_yoy`（年增＝營收年增率）、`accum_inc`（累增）、`trust_3d`（投三）、`foreign_3d`（外三）、`custody`（集保大戶持股）、`w55`（威廉55翻多旗標）、`market_cap`、`capital`、`est_profit`、`lpe`、`raw_json`（原始整列備查）；PK＝(`snap_date`,`code`)。
- **csv_files**：`id`、`snap_date`、`stored_path`、`imported_at`，保留原檔避免覆蓋。

## 6. 散戶多空比計算（微台/小台）

採玩股網式定義，期貨為零和，以三大法人反面近似散戶：

```
小台散戶多空比 = (散戶多單 − 散戶空單) / 小台未平倉總量
其中  散戶淨部位 ≈ −(三大法人小台未平倉淨額)
故    小台散戶多空比 ≈ −(三大法人小台未平倉淨額) / 小台未平倉總量
微台同理，取 TMF 契約。
```

- 正值＝散戶偏多，負值＝散戶偏空，作為市場反指標呈現。
- 此公式與分母（未平倉總量）來源欄位在實作時以 fixture 數據獨立單元測試驗證。

## 7. CSV 分析引擎（功能 #2 核心）

輸入：每日上傳的 Big5 編碼 CSV（欄位如附件，前三列為標頭資訊，第四列才是欄名）。

### 7.1 當日訊號榜（用 CSV 既有欄位）
- 個股榜：依「大戶增比」高 ＋「人數降比」（散戶減少）排序，取 Top N。
- 多因子加分（呈現於榜單欄位）：W55 翻多（技術面）、年增>0（營收成長）、投三/外三為正（法人同步）。

### 7.2 跨週趨勢（本週最新 vs 上週最新）
- 取本週最新 `snap_date` 與上週最新 `snap_date` 兩批快照。
- 逐檔計算 Δ：集保大戶持股、人數、投三/外三、大戶增比。
- 找出「大戶持續增加且散戶持續減少」之個股，標記狀態：`新進榜` / `加速` / `退榜`。

### 7.3 產業彙整
- 依「產業／細產業」分組，彙整各組大戶流入強度與散戶流出強度，排名出本週受大戶青睞的類股。

### 7.4 多因子統整（籌碼／技術／基本／財務）
被選中的個股組成檔案，整合：
- 籌碼面：大戶增比、人數降比、集保大戶、投三、外三。
- 技術面：W55 翻多。
- 基本面/財務面：年增（營收年增率）、市值、股本、推估獲利、LPE/本益比，並可併入 TWSE 殖利率/淨值比。

### 7.5 輸出
- 結構化榜單（個股榜、產業榜、跨週變化表）回前端表格。
- 連同個股檔案餵 Gemini → 白話「本週籌碼洞察＋選股理由」。

## 8. 一鍵更新與排程

- 前端「一鍵更新」→ `POST /api/update/run` → `updater.py` 依序：TWSE → TAIFEX → 計算散戶多空比 → yfinance → 寫入 `market_daily`。
- **容錯**：每來源獨立 try/except，單一來源失敗只記錄「哪個來源、為何失敗」，不影響其餘；回傳成功項與失敗項清單。
- **排程**：`scheduler.py` 用 APScheduler，設定開啟後每日指定時間跑同一流程；README 附 Windows 工作排程器設定（呼叫 CLI 入口），作為「程式沒開也能跑」的備援。

## 9. Gemini 整合
- `GEMINI_API_KEY` 置於 `.env`；模型用 Gemini 最新版。
- 用途一：CSV 分析白話統整（§7.5）。
- 用途二：依 `market_daily` 當日數據生成「每日盤勢摘要」。
- **降級**：無金鑰或呼叫失敗時，回傳「（未啟用 AI 摘要）」並照常顯示數據，不擋功能。

## 10. 前端儀表板
- 單頁兩區塊：
  - 上：大盤儀表板——指標卡（加權指數、三大法人、融資券、台指期、散戶多空比、國際指數）＋ ECharts 歷史趨勢圖；Gemini 盤勢摘要。
  - 下：CSV 分析——上傳鈕、當日訊號榜、跨週變化表、產業榜、Gemini 洞察。
- **個股 K 線**：榜單/表格中任一個股可點擊，於彈窗或下方面板開啟 ECharts 蠟燭圖（日 K＋成交量＋MA5/MA20），標題顯示股號名稱與當日籌碼訊號摘要。
- 頂部：「一鍵更新」按鈕 ＋ 上次更新時間 ＋ 來源成功/失敗提示。

## 11. API 介面（FastAPI）
| 方法/路徑 | 功能 |
|---|---|
| `GET /` | 回傳前端頁面 |
| `POST /api/update/run` | 執行一鍵更新，回傳各來源結果 |
| `GET /api/dashboard` | 回傳最新 `market_daily` ＋ 近 N 日趨勢 |
| `POST /api/csv/upload` | 上傳 CSV、解析存快照、回傳當日訊號榜 |
| `GET /api/analysis/weekly` | 回傳本週 vs 上週榜單、產業榜 |
| `GET /api/analysis/summary` | 回傳 Gemini 統整（或降級訊息） |
| `GET /api/stock/{code}/kline` | 回傳個股日 OHLC＋量（ECharts 蠟燭圖格式），參數 `period` 預設 1y |
| `GET/POST /api/schedule` | 讀取/設定每日排程時間 |

## 12. 錯誤處理原則
- 外部來源：個別容錯、明確回報失敗來源與原因。
- CSV：編碼（Big5）、欄位缺漏、空值以明確錯誤訊息回前端，不靜默吞掉。
- Gemini：失敗即降級，不影響數據功能。

## 13. 測試策略（給 TDD 用）
- `sources/*`：用 respx 餵錄製的假 API 回應，驗證解析正確；不打真網路。
- `taifex.py`：散戶多空比公式以已知 fixture 數字獨立單元測試。
- `csv_import.py`：小型 fixture CSV（Big5）驗證欄位對應與型別。
- `analysis.py`：兩週 fixture 快照驗證跨週榜單、產業彙整、`新進榜/加速/退榜` 標記。
- `updater.py`：模擬某來源失敗，驗證容錯與回報結構。
- `gemini.py`：mock 金鑰存在/不存在兩路徑。
- `kline.py`：以 fixture 驗證 yfinance OHLC → ECharts 蠟燭圖資料結構轉換正確。

## 14. 待釐清/風險
- TAIFEX openapi 精確端點 URL 與欄名：實作首個任務以真實呼叫釘死（§4 備註）。
- 散戶多空比分母定義：以未平倉總量為準，fixture 測試驗證。
- yfinance 偶發抓取失敗：納入容錯，失敗只略過該國際指標。

---
（本檔為設計規格，核可後進入 writing-plans 拆解實作計畫。）
