# STOCKS POWER RICH（股力智富）

股市每日更新與籌碼分析 App。盤後**同日**自動更新大盤＋籌碼＋國際儀表板、每日上傳籌碼 CSV 做跨週與選股分析、個股 K 線、當日漲跌族群、全市場**杯柄型態選股**（附 ATR 停損與建議部位、歷史回測）、**交易帳本**績效追蹤、**盤中突破 LINE 警示**、免帳密**公開頁**供好友查看。FastAPI + SQLite，前端為單頁 + ECharts，無建置流程；支援本機或 Zeabur 雲端部署。

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
2. **籌碼/基本選股**：上傳當日 CSV／Excel → 依「W55 翻多 ＋ 大戶增比>0 ＋ 營收年增>0 ＋ 推估EPS>0」篩選、依**蘭值**排序；細產業統計可點擊聯動、欄位可排序、一鍵匯出 Excel。
3. **個股查詢**：輸入股號開日／週／月／時 K 線（上市 `.TW`、上櫃自動回退 `.TWO`）＋ 籌碼／基本面卡片、**個股三大法人近 10 日**（上市 T86／上櫃櫃買）、**集保大戶持股趨勢**、可切換艾略特波浪。
4. **族群輪動**：近數日類股漲跌熱力（依累計強弱排序）＋ **交叉選股**（你的選股清單依官方類股分組、附當日漲跌）。
5. **自選股**：加入關注股 → 追蹤是否在選股榜、在榜次數、進榜日、自進榜報酬。
6. **海期監控**：國際期貨／美股五大分類延遲報價卡片（指數期貨、能源金屬、農產品、外匯、美股）。
7. **杯柄選股**：全市場上市＋上櫃每日掃描「亞當杯柄」型態（左緣未破高＋杯身夠寬＋柄回檔淺守穩＋強度濾網），K 線疊趨勢線／壓力線／**停損線**；每檔附 **ATR(14) 停損建議**與（設定可容忍虧損後）**建議部位**；可切換「同時符合籌碼/基本選股」交集；「📊 回測報告」統計歷史訊號的突破率、持有 5/10/20 日勝率與報酬。
8. **交易帳本**：記錄實單／模擬單（進場日/價、股數，出場後平倉），未平倉自動以最新收盤估未實現損益；統計勝率、平均賺賠、賺賠比、**期望值**、已實現/未實現損益、對比同期加權指數（alpha）。
9. **跨週 ＋ AI**：本週 vs 上週（新進榜／加速／退榜）＋ AI 籌碼分析師。
10. **設定**：每日排程時間、「讀取最新檔」資料夾、資料庫狀態、Gemini／LINE 狀態（**只顯示已/未設定，不顯示也不輸入金鑰**）、盤中哨兵是否只警示交集股、單筆可容忍虧損（供杯柄頁算建議部位）、左側分頁順序（可拖拉、雲端同步）。

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
│  ├─ index.html              # 單頁；左側多視圖（見上「功能」）
│  ├─ app.js                  # 全部前端邏輯 + ECharts（K線/族群/卡片/波浪/杯柄/交易帳本）
│  └─ styles.css
├─ stocks_power_rich/         # 後端（FastAPI）
│  ├─ main.py                 # API 入口、靜態頁掛載、選用排程
│  ├─ config.py               # .env / 環境變數設定
│  ├─ db.py                   # SQLite schema 與存取（market_daily / chip_snapshot / tx_history …）
│  ├─ updater.py              # 同日更新協調：以加權日期 D 為錨，各源依 D 直連；含近期校正回補
│  ├─ csv_import.py           # 籌碼 CSV/Excel 匯入（big5/cp950 編碼自動偵測、欄位對應）
│  ├─ analysis.py             # 選股篩選、細產業統計、跨週比較、融資維持率、交易帳本統計(trade_stats)
│  ├─ patterns.py             # 亞當杯柄型態偵測（逐日+向量化版）、ATR(14)
│  ├─ backtest.py             # 杯柄訊號歷史回測（突破率、持有5/10/20日勝率與報酬）
│  ├─ elliott.py              # 艾略特波浪偵測（三大鐵律 + A-B-C 修正浪）
│  ├─ exporter.py             # 選股清單匯出 Excel（openpyxl）
│  ├─ gemini.py               # AI 盤勢/籌碼摘要（未設金鑰自動降級為純數據）
│  ├─ line_push.py            # LINE 推播訊息組裝（速報/完整版/盤中突破警示）+ broadcast
│  ├─ scheduler.py            # APScheduler 每日排程 + 盤中突破哨兵排程
│  ├─ cli.py                  # 命令列更新（給 Windows 工作排程器）
│  └─ sources/
│     ├─ twse.py              # 證交所：加權/融資券/三大法人現貨/類股指數/個股T86/個股收盤/指數OHLC/全市場個股OHLC/融資明細
│     ├─ taifex.py            # 期交所：台指期/期貨三大法人未平倉/多空比/歷史日K/P-C ratio/大額交易人
│     ├─ tdcc.py             # 集保(TDCC)：個股集保大戶持股分散（當週，逐週累積）
│     ├─ tpex.py             # 櫃買(TPEx)：上櫃個股三大法人買賣超/個股OHLC/個股報價
│     ├─ intl.py              # yfinance 國際指數（含 VIX ^VIX），機房限流時備援直連 Yahoo chart API
│     ├─ mis.py               # 證交所盤中即時快照（供盤中突破警示輪詢用，非官方端點）
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
| 國際指數、個股 K 線 | yfinance | 雲端偶爾限流 → 已加重試／fallback；國際指數另備援直連 Yahoo v8 chart API |
| 全市場個股日 OHLC（上市） | TWSE 直連 `MI_INDEX`（`type=ALLBUT0999`） | 杯柄型態選股用，逐日回補累積於 `stock_ohlc` |
| 全市場個股日 OHLC（上櫃） | TPEx 直連 `dailyQuotes` | 官方只回溯約 1.4 年（實測底線），達底線視為完成非卡死 |
| 個股融資明細 | TWSE 直連 `MI_MARGN`（`selectType=ALL`） | 算大盤整體融資維持率用 |
| 盤中即時報價 | TWSE 非官方 `mis.twse.com.tw` 快照 | 盤中突破警示輪詢用，僅供参考、非官方保證 |

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
| `GET /api/ohlc/backfill?days=&max_fetch=&reset=` | 回補全市場個股日 OHLC（杯柄型態用；分段可重跑續補，`reset=1` 清熔斷狀態重來） |
| `GET /api/patterns/cup-handle` | 全市場杯柄型態掃描（附 ATR／建議停損／籌碼交集標記，逐日快取） |
| `GET /api/patterns/cup-handle/backtest` | 杯柄訊號歷史回測（突破率、持有5/10/20日勝率與報酬） |
| `GET /api/stock/{code}/ohlc?bars=` | 個股已存 OHLC（杯柄 K 線畫線用） |
| `POST /api/intraday/test?push=` | 手動跑一輪盤中突破掃描（`push=1` 才真推播；驗證雲端連通性用） |
| `GET/POST/DELETE /api/trades`、`POST /api/trades/{id}/close` | 交易帳本 CRUD＋統計（勝率/賺賠比/期望值/對比大盤） |
| `GET /api/sectors`、`/api/sectors/rotation`、`/api/sectors/picks` | 當日漲跌族群、輪動熱力、交叉選股 |
| `GET /api/inst-ranking?who=&unit=` | 法人買賣超排行（張／金額） |
| `GET /api/options-sentiment` | P/C ratio ＋ 大額交易人 |
| `GET /api/os-futures`、`/api/breadth` | 海期監控（五大分類）、台股漲跌家數 |
| `POST /api/csv/upload`、`GET /api/csv/import-latest`、`import-all` | 上傳／讀最新／匯入資料夾全部 CSV |
| `GET /api/analysis/daily`、`/weekly`、`/export` | 選股清單、跨週、匯出 Excel |
| `GET /api/market/summary`、`/api/analysis/summary` | AI 盤勢／籌碼摘要 |
| `GET /api/stock/{code}/kline`、`/profile`、`/chips`、`/custody` | 個股 K 線、籌碼/基本面、三大法人近10日、集保大戶趨勢 |
| `GET /api/index/kline` | 加權／台指期 K 線 |
| `GET/POST /api/watchlist`、`DELETE /api/watchlist/{code}` | 自選股 ＋ 進出選股榜追蹤 |
| `GET/POST /api/settings` | 讀取/更新排程時間、資料夾、盤中哨兵交集開關、可容忍虧損等（金鑰只回狀態） |
| `POST /api/line/test` | 手動觸發一則完整版 LINE 推播（驗證設定用） |
| `GET /public/overview`、`/public/logic`、`/public/disclaimer` | **免帳密公開頁**（供 LINE 好友從圖文選單開啟），詳見下方「公開頁」 |

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
4. 開啟排程後：
   - **平日 09:00–13:35 每 5 分鐘**：盤中突破哨兵掃描杯柄訊號股，現價通過「壓力線 + 0.3×ATR」
     門檻且連續兩輪站穩才推播（避免開盤瞬間插針、微幅探頭洗版）；可在設定頁切換只警示
     「同時符合籌碼/基本選股」的交集股。
   - **平日 16:00**：推「盤後速報」（大盤/國際/法人/期貨/類股/自選股/杯柄新符合＋AI 解讀）。
   - **21:00** 更新完推「完整版」（加融資券，含**融資維持率**）。
   - 假日或資料未更新自動不推；推播失敗會自動重試一次，仍失敗則於下次成功推播時在
     訊息頂部標註提醒（不會靜默漏推而不自知）。
5. 設定頁有「📱 測試推播」按鈕可立即驗證。免費方案每月 200 則，每日 2～3 則綽綽有餘。

### 公開頁（免帳密，供 LINE 好友查看）
即使全站開了 Basic Auth，`/public/*` 這幾頁仍對外開放（無需帳密），內容只含「本來就會
LINE 廣播出去」等級的公開市場資訊，不含交易帳本／自選股／設定等個人資料：
- `/public/overview`：大盤、國際行情、三大法人、法人買賣超個股排行（可切外資/投信/三大法人、張/金額）、
  期貨籌碼、融資券、類股強弱、AI 解讀。
- `/public/logic`：杯柄選股邏輯與盤中突破濾網說明。
- `/public/disclaimer`：免責聲明。

適合放進 LINE 官方帳號的**圖文選單**（Rich Menu），讓沒有網站帳密的好友也能點開查看。

### 安全性（雲端務必設定）
服務預設無認證，任何人知道網址即可存取。**部署到公網前，請在環境變數設定
`SPR_BASIC_USER` 與 `SPR_BASIC_PASS`**（兩者皆設定才啟用），啟用全站 HTTP Basic 登入；
瀏覽器會跳一次帳密視窗，記住後日常無感。`/public/*` 為刻意放行的例外（見上）。
詳見 [docs/SECURITY.md](docs/SECURITY.md)。

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
3. **持久化 Volume（務必）**：掛載到 `/data`（對應 `SPR_DB_PATH`）。**未掛 Volume 每次重新部署資料就會清空**（大盤歷史、集保逐週累積、自選股）。每日 21:00 排程會自動備份到 `/data/backup/`（輪替保留 7 份），也可按需 `POST /api/db/backup`；要異地保存可從 Volume 下載該資料夾。
4. **區域**：選離台灣近者（連 TWSE／TAIFEX／TDCC 較穩）。
5. **排程備援**：免費方案可能休眠導致 21:00 排程不觸發；可改用平台 Cron／外部排程每日 `POST /api/update/run`。
6. **冷啟動補資料**（首次部署或 Volume 剛掛好，DB 是空的）：瀏覽器打開一次
   - `…/api/backfill?days=35` → 回補近一個月大盤/現貨法人/融資券（可重跑續補）
   - `…/api/csv/import-all` → 匯入 repo `Date/` 內全部選股 CSV
   - `…/api/ohlc/backfill?days=600` → 回補全市場個股日 OHLC（**杯柄選股必要**；分段執行、需重複呼叫
     直到回應 `done:true`，上櫃約 600 次請求／30 分鐘，因 TPEx 官方只回溯約 1.4 年，達歷史底線會標
     `otc_exhausted:true` 並視為完成、非卡死；若懷疑誤判熔斷可加 `&reset=1` 強制重來一次）

注意：雲端上「讀取資料夾最新檔」只會讀到 repo 內的 `Date/`，每日請改用「上傳今日檔」。`集保（TDCC）` 憑證有瑕疵，程式對該主機停用 SSL 驗證（僅此主機）。
