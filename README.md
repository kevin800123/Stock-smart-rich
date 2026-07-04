# STOCKS POWER RICH（股力智富）

本機股市每日更新與籌碼分析 App。盤後**同日**自動更新大盤＋籌碼＋國際儀表板、每日上傳籌碼 CSV 做跨週與選股分析、個股 K 線、當日漲跌族群。FastAPI + SQLite，前端為單頁 + ECharts，無建置流程。

## 功能（左側視圖）
1. **總覽**：
   - **台股大盤**：加權指數、外資／投信／自營買賣超、三大法人合計（億）、融資／融券餘額。
   - **期貨籌碼**：台指期、外資台指淨未平倉、散戶小台淨未平倉、小台／微台散戶多空比、VIX 恐慌指數。
   - **期權情緒・大額交易人**：P/C 未平倉／成交量比、前 5／10 大特定法人台指淨未平倉。
   - **國際市場**：費城半導體、日經 225、KOSPI、黃金、比特幣（值＋漲跌點數＋%）。
   - **當日漲跌族群**：證交所 37 個上市類股指數，依漲幅排序、紅漲綠跌。
   - **籌碼趨勢（近 60 日）**：三大法人買賣超／外資台指未平倉／散戶多空比／融資融券可切換走勢。
   - **法人買賣超排行**：外資／投信／三大法人 買超・賣超 Top，可切「張／金額(億)」。
   - **加權／台指期 K 線**（1 小時／日／週／月，MA5/20/60/120、成交量、可切換艾略特波浪）＋ **AI 盤勢摘要**。
2. **選股清單**：上傳當日 CSV／Excel → 依「W55 翻多 ＋ 大戶增比>0 ＋ 營收年增>0 ＋ 推估EPS>0」篩選、依**蘭值**排序；細產業統計可點擊聯動、欄位可排序、一鍵匯出 Excel。
3. **個股查詢**：輸入股號開日／週／月／時 K 線（上市 `.TW`、上櫃自動回退 `.TWO`）＋ 籌碼／基本面卡片、**個股三大法人近 10 日**（上市 T86／上櫃櫃買）、**集保大戶持股趨勢**、可切換艾略特波浪。
4. **族群輪動**：近數日類股漲跌熱力（依累計強弱排序）＋ **交叉選股**（你的選股清單依官方類股分組、附當日漲跌）。
5. **自選股**：加入關注股 → 追蹤是否在選股榜、在榜次數、進榜日、自進榜報酬。
6. **跨週 ＋ AI**：本週 vs 上週（新進榜／加速／退榜）＋ AI 籌碼分析師。
7. **設定**：每日排程時間、「讀取最新檔」資料夾、資料庫狀態、Gemini 金鑰狀態（**只顯示已/未設定，不顯示也不輸入金鑰**）。

> 大盤資料**無「一鍵更新」按鍵**：開頁時若非當日資料會自動更新，頂部只顯示「資料日期」與「更新時間」。

## 專案結構
```
股力智富/
├─ 啟動.bat                   # 一鍵啟動（首次建 venv＋裝套件、開瀏覽器、起伺服器＋排程）
├─ requirements.txt
├─ .env.example
├─ README.md
├─ .claude/launch.json        # 預覽伺服器設定
├─ Date/                      # 每日上傳的籌碼 CSV（YYYYMMDD.csv）
├─ data/                      # SQLite 資料庫 spr.sqlite（gitignore）
├─ docs/superpowers/          # 設計規格與開發計畫
├─ web/                       # 前端（無建置流程）
│  ├─ index.html              # 單頁；左側 5 視圖
│  ├─ app.js                  # 全部前端邏輯 + ECharts（K線/族群/卡片/波浪）
│  └─ styles.css
├─ stocks_power_rich/         # 後端（FastAPI）
│  ├─ main.py                 # API 入口、靜態頁掛載、選用排程
│  ├─ config.py               # .env / 環境變數設定
│  ├─ db.py                   # SQLite schema 與存取（market_daily / chip_snapshot / tx_history …）
│  ├─ updater.py              # 同日更新協調：以加權日期 D 為錨，各源依 D 直連；含近期校正回補
│  ├─ csv_import.py           # 籌碼 CSV/Excel 匯入（big5/cp950 編碼自動偵測、欄位對應）
│  ├─ analysis.py             # 選股篩選、細產業統計、跨週比較
│  ├─ elliott.py              # 艾略特波浪偵測（三大鐵律 + A-B-C 修正浪）
│  ├─ exporter.py             # 選股清單匯出 Excel（openpyxl）
│  ├─ gemini.py               # AI 盤勢/籌碼摘要（未設金鑰自動降級為純數據）
│  ├─ scheduler.py            # APScheduler 每日排程
│  ├─ cli.py                  # 命令列更新（給 Windows 工作排程器）
│  └─ sources/
│     ├─ twse.py              # 證交所：加權/融資券/三大法人現貨/類股指數/個股T86/個股收盤/指數OHLC
│     ├─ taifex.py            # 期交所：台指期/期貨三大法人未平倉/多空比/歷史日K/P-C ratio/大額交易人
│     ├─ tdcc.py             # 集保(TDCC)：個股集保大戶持股分散（當週，逐週累積）
│     ├─ tpex.py             # 櫃買(TPEx)：上櫃個股三大法人買賣超
│     ├─ intl.py              # yfinance 國際指數（含 VIX ^VIX）
│     └─ kline.py             # yfinance 個股/指數 K 線（含重試）+ 重採樣
└─ tests/                     # pytest（解析函式單元測試 + API 整合測試）
```

## 資料來源
為求「當日盤後即更新、且各數值同一天一致」，籌碼以**官方直連端點**為主（openapi 鏡像常延遲到晚間/隔日）。更新時先以加權指數定出資料日期 **D**，其餘來源全部依 D 抓取。

| 資料 | 來源 | 備註 |
|---|---|---|
| 加權指數收盤／漲跌（定 D） | TWSE 直連 `FMTQIK` | 當日盤後即有 |
| 三大法人現貨買賣超 | TWSE 直連 `BFI82U` | 指定日；只查該日不回退他日 |
| 融資融券餘額 | TWSE 直連 `MI_MARGN` | 指定日；約 **21:00** 才公布 |
| 各類股指數（漲跌族群） | TWSE 直連 `MI_INDEX?type=IND` | 37 個上市類股 |
| 本益比／殖利率／淨值比 | TWSE openapi `BWIBBU_ALL` | 個股基本面 |
| 台指期近月行情、歷史日K | TAIFEX 直連 `dlFutDataDown` | 近月、一般盤 |
| 期貨三大法人未平倉、散戶多空比 | TAIFEX 直連 `futContractsDateDown` + 全市場 OI | 外資台指/散戶小台/多空比 |
| P/C ratio、大額交易人未平倉 | TAIFEX openapi `PutCallRatio`／`OpenInterestOfLargeTradersFutures` | 期權情緒 |
| 個股三大法人買賣超（上市） | TWSE 直連 `T86` | 法人排行、個股籌碼 |
| 個股三大法人買賣超（上櫃） | TPEx `dailyTrade` | 上櫃股 |
| 個股集保大戶持股分散 | TDCC opendata `getOD?id=1-5` | 只給當週，逐週累積；需 `verify=False` |
| 加權指數 OHLC（K 線 fallback） | TWSE openapi `MI_5MINS_HIST` | yfinance 失敗時改用 |
| 國際指數、個股 K 線 | yfinance | 雲端偶爾限流 → 已加重試／fallback |

資料正確性處理：
- **不回退他日**：當日官方資料未出時留空，不以他日數值魚目混珠（避免「今日＝昨日」假象）。
- **近期校正回補**：每次更新依各列日期以官方定稿值重抓覆蓋近 7 天的三大法人與融資券（修正盤中初值→定稿、晚間才公布的融資券）。
- **融資券退顯**：當日尚未公布時，卡片退顯最近一筆有資料的交易日並標註「截至 MM-DD」。

## 安裝與啟動
需求：Windows + Python 3.11 以上。

1. 設定金鑰（選用）：複製 `.env.example` 為 `.env`，填入 `GEMINI_API_KEY`（沒有也能跑，AI 摘要會降級）。
2. 雙擊 **`啟動.bat`**（首次會自動建虛擬環境、安裝套件、啟用排程），瀏覽器會開 `http://127.0.0.1:8000`。

手動方式：
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --port 8000
```

## 每日使用
1. 開啟頁面 → 大盤／籌碼／族群會在資料非當日時**自動更新**（盤後直連，約 20–30 秒）。
2. 到「選股清單」點「📤 上傳今日檔（CSV/Excel）」或「📂 讀取資料夾最新檔」載入當日籌碼。
3. 看選股清單／細產業／跨週變化，點任一股號開 K 線；可一鍵匯出 Excel。
4. 連續上傳不同交易日的 CSV 後，跨週比較才會出現（需至少兩個交易週的快照）。

## 每日自動更新與排程
- **App 內排程（APScheduler）**：預設 **21:00**（融資券公布後，當日資料較完整），需程式保持開啟；`啟動.bat` 會自動啟用。可在「設定」頁調整時間。
- **Windows 工作排程器**（關機/未開程式也能跑）：
  1. 工作排程器 → 建立基本工作 → 每日 21:00（或自訂）。
  2. 動作：啟動程式
     - 程式：`<專案路徑>\.venv\Scripts\python.exe`
     - 引數：`-m stocks_power_rich.cli`
     - 起始於：`<專案路徑>`

## 主要 API
| 方法 路徑 | 說明 |
|---|---|
| `GET /api/dashboard` | 最新大盤＋近 60 日歷史、資料延遲旗標 |
| `POST /api/update/run` | 執行同日更新（TWSE/TAIFEX/TDCC/國際） |
| `GET /api/backfill?days=N` | 回補近月加權／三大法人現貨／融資券（冷啟動補歷史，可重跑續補） |
| `GET /api/sectors`、`/api/sectors/rotation`、`/api/sectors/picks` | 當日漲跌族群、輪動熱力、交叉選股 |
| `GET /api/inst-ranking?who=&unit=` | 法人買賣超排行（張／金額） |
| `GET /api/options-sentiment` | P/C ratio ＋ 大額交易人 |
| `POST /api/csv/upload`、`GET /api/csv/import-latest`、`import-all` | 上傳／讀最新／匯入資料夾全部 CSV |
| `GET /api/analysis/daily`、`/weekly`、`/export` | 選股清單、跨週、匯出 Excel |
| `GET /api/market/summary`、`/api/analysis/summary` | AI 盤勢／籌碼摘要 |
| `GET /api/stock/{code}/kline`、`/profile`、`/chips`、`/custody` | 個股 K 線、籌碼/基本面、三大法人近10日、集保大戶趨勢 |
| `GET /api/index/kline` | 加權／台指期 K 線 |
| `GET/POST /api/watchlist`、`DELETE /api/watchlist/{code}` | 自選股 ＋ 進出選股榜追蹤 |
| `GET/POST /api/settings` | 讀取/更新排程時間、資料夾（金鑰只回狀態） |

## 測試
```
.venv\Scripts\python -m pytest -q
```

## 設定（.env）
| 變數 | 說明 | 預設 |
|---|---|---|
| `SPR_BASIC_USER` / `SPR_BASIC_PASS` | 全站登入帳密（兩者皆填才啟用；雲端建議設定） | （空＝不啟用） |
| `GEMINI_API_KEY` | Gemini 金鑰（空＝停用 AI 摘要） | （空） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE 官方帳號 Messaging API token（空＝停用推播） | （空） |
| `SPR_LINE_PUSH_TIME` | 平日盤後速報推播時間 HH:MM | 16:00 |
| `SPR_SCHEDULE_TIME` | 每日排程時間 HH:MM | 21:00 |
| `SPR_DB_PATH` | SQLite 路徑 | data/spr.sqlite |
| `SPR_DATA_DIR` | 「讀取最新檔」的資料夾 | Date |
| `SPR_ENABLE_SCHEDULER` | 程式內每日自動更新（1 開啟，需程式開著） | 0（啟動.bat 會設 1） |

AI 摘要會快取於當日，只在更新或上傳新檔後重新生成（省 token）。

### LINE 每日推播（選用）
LINE Notify 已停服，改走你自己的 LINE 官方帳號（LINE@）Messaging API：
1. 到 [LINE Developers Console](https://developers.line.biz/) 用官方帳號建立 **Messaging API** channel，
   在「Messaging API」頁籤發行 **Channel access token (long-lived)**。
2. 用手機把該官方帳號加為好友（訊息用 broadcast 推給全部好友；自用帳號＝只推給你）。
3. 設定 `LINE_CHANNEL_ACCESS_TOKEN`（本機 .env 或 Zeabur 環境變數）。
4. 開啟排程後：**平日 16:00** 推「盤後速報」（大盤/法人/期貨/類股/自選股＋AI 解讀），
   **21:00** 更新完推「完整版」（加融資券）。假日或資料未更新自動不推。
5. 設定頁有「📱 測試推播」按鈕可立即驗證。免費方案每月 200 則，每日 2 則綽綽有餘。

### 安全性（雲端務必設定）
服務預設無認證，任何人知道網址即可存取。**部署到公網前，請在環境變數設定
`SPR_BASIC_USER` 與 `SPR_BASIC_PASS`**（兩者皆設定才啟用），啟用全站 HTTP Basic 登入；
瀏覽器會跳一次帳密視窗，記住後日常無感。詳見 [docs/SECURITY.md](docs/SECURITY.md)。

## 雲端部署（Zeabur，整包前後端）
後端本身就 serve 前端，整包部署一個服務即可（不需 CORS／前後端分離）。

1. **啟動指令**：repo 內 `Procfile` 已設 `uvicorn stocks_power_rich.main:app --host 0.0.0.0 --port $PORT`（用平台給的 `$PORT`）。不要開多 worker（會重複跑排程、SQLite 競爭）。
2. **環境變數**：
   | 變數 | 值 | 說明 |
   |---|---|---|
   | `TZ` | `Asia/Taipei` | **必設**；容器預設 UTC，否則資料日期／排程判斷會差 8 小時 |
   | `SPR_BASIC_USER` / `SPR_BASIC_PASS` | （帳號 / 密碼） | **建議必設**；啟用全站登入保護，否則任何人可存取 |
   | `SPR_DB_PATH` | `/data/spr.sqlite` | 指到持久化 Volume |
   | `SPR_ENABLE_SCHEDULER` | `1` | 開每日自動更新 |
   | `SPR_SCHEDULE_TIME` | `21:00` | 排程時間 |
   | `GEMINI_API_KEY` | （金鑰） | 設為密鑰，絕不進前端 |
   | `LINE_CHANNEL_ACCESS_TOKEN` | （token） | 設為密鑰；LINE 每日推播用（選用） |
3. **持久化 Volume（務必）**：掛載到 `/data`（對應 `SPR_DB_PATH`）。**未掛 Volume 每次重新部署資料就會清空**（大盤歷史、集保逐週累積、自選股）。
4. **區域**：選離台灣近者（連 TWSE／TAIFEX／TDCC 較穩）。
5. **排程備援**：免費方案可能休眠導致 21:00 排程不觸發；可改用平台 Cron／外部排程每日 `POST /api/update/run`。
6. **冷啟動補資料**（首次部署或 Volume 剛掛好，DB 是空的）：瀏覽器打開一次
   - `…/api/backfill?days=35` → 回補近一個月大盤/現貨法人/融資券（可重跑續補）
   - `…/api/csv/import-all` → 匯入 repo `Date/` 內全部選股 CSV

注意：雲端上「讀取資料夾最新檔」只會讀到 repo 內的 `Date/`，每日請改用「上傳今日檔」。`集保（TDCC）` 憑證有瑕疵，程式對該主機停用 SSL 驗證（僅此主機）。
