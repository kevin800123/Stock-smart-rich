# STOCKS POWER RICH（股力智富）

本機股市每日更新與籌碼分析 App。一鍵更新大盤＋國際儀表板、每日上傳籌碼 CSV 做跨週分析、個股 K 線。

## 功能
1. **大盤儀表板**：加權指數、三大法人現貨買賣超、台指期、微台/小台散戶多空比、融資融券，以及費半 / 日經 / KOSPI / 黃金 / 比特幣。可一鍵更新、可選每日排程。
2. **CSV 籌碼分析**：每日上傳 CSV，找出「大戶增加＋散戶減少」的個股與類股，本週 vs 上週比較（新進榜 / 加速 / 退榜），統整籌碼面（大戶增比、人數降比、集保、投三/外三）、技術面（W55 翻多）、基本/財務面（營收年增、本益比）。
3. **個股 K 線**：榜單個股可點開日 K 蠟燭圖（含 MA5/MA20、成交量）。
4. **Gemini 統整**：產生白話盤勢摘要與籌碼洞察（未設金鑰時自動降級為純數據）。

## 資料來源
- TWSE openapi：加權指數（FMTQIK）、融資融券（MI_MARGN）、本益比（BWIBBU_ALL）。
- TWSE RWD：三大法人買賣超（BFI82U）。
- TAIFEX openapi：期貨行情（DailyMarketReportFut）、三大法人各契約未平倉（散戶多空比計算）。
- yfinance：國際指數與個股 K 線。

## 安裝與啟動
需求：Windows + Python 3.11 以上。

1. 設定金鑰：複製 `.env.example` 為 `.env`，填入 `GEMINI_API_KEY`（沒有也能跑，AI 摘要會降級）。
2. 雙擊 **`啟動.bat`**（首次會自動建虛擬環境並安裝套件），瀏覽器會開 `http://127.0.0.1:8000`。

手動方式：
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --port 8000
```

## 每日使用
1. 按右上「⟳ 一鍵更新」抓最新大盤與國際資料。
2. 點「📤 上傳今日檔（CSV 或 Excel .xlsx/.xlsm）」上傳當日籌碼檔。
3. 看「當日訊號榜 / 跨週變化 / 產業榜」，點任一股號開 K 線。
4. 連續上傳不同日期的 CSV 後，跨週比較才會出現（需至少兩個交易週的快照）。

## 每日自動更新（程式沒開也能跑）
App 內排程（APScheduler）需程式保持開啟。若要關機也能跑，用 **Windows 工作排程器**：
1. 開「工作排程器」→ 建立基本工作 → 每日 15:30（或自訂）。
2. 動作：啟動程式
   - 程式：`<專案路徑>\.venv\Scripts\python.exe`
   - 引數：`-m stocks_power_rich.cli`
   - 起始於：`<專案路徑>`

## 測試
```
.venv\Scripts\python -m pytest -q
```

## 設定（.env）
| 變數 | 說明 | 預設 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini 金鑰（空＝停用 AI 摘要） | （空） |
| `SPR_SCHEDULE_TIME` | 每日排程時間 HH:MM | 15:30 |
| `SPR_DB_PATH` | SQLite 路徑 | data/spr.sqlite |
| `SPR_DATA_DIR` | 「讀取最新檔」的資料夾 | Date |
| `SPR_ENABLE_SCHEDULER` | 程式內每日自動更新（1 開啟，需程式開著） | 0（啟動.bat 會設 1） |

AI 摘要會快取於當日，只在按「一鍵更新」或上傳新檔後重新生成（省 token）。
