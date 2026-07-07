# 開發路線圖（#4–#10 交接規格）

> 背景：2026-07-06 使用者要求 10 項「提高賺錢決策品質」的優化並排序。#1 回測引擎、
> #2 杯柄訊號 LINE 推播、#3 上櫃納入選股 已由 Fable 5 完成。本文件是 #4–#10 的交接規格，
> 供指定模型接手。**開工前先讀 CLAUDE.md**（架構、資料源怪癖、慣例都在裡面），
> 遵守專案 TDD 慣例：先寫 parse/純函數測試、網路包裝薄層、endpoint 用 TestClient+monkeypatch。

## 共同原則
- 誠實原則：任何統計/回測結果必須附註限制（費用未計、存活者偏差、歷史≠未來）。
- 資料優先用 TWSE/TPEx 官方直連（openapi 會延遲）；yfinance 僅限「單次批次下載」用法
  （`threads=False`，見 intl.py 註解），逐檔多次請求在雲端會被擋。
- 新增 UI 一律過手機斷點（≤600px）驗證；外部字串進 innerHTML 必須 `esc()`。
- 昂貴計算進 `ai_cache` 逐日快取；每日更新後快取鍵自然失效的設計優先。

---

## #4 籌碼異動掃描器 — 指派：Opus 4.8
**目標**：掃全市場「籌碼轉強」訊號榜：投信連N買、外資連N買、大戶增比跳增、集保千張大戶週增。
**資料**：已全部就緒——`t86:{date}`/`tpex:{date}`（個股三大法人，ai_cache 逐日快取）、
`custody_dist` 表（集保逐週）、CSV 快照的 `big_holder_ratio`。
**做法**：endpoint `/api/chip-scan`：對近 N 日 t86 快取做連買計數（參考 main.py `market_summary`
的 `_streak` 寫法）；集保週增從 custody_dist 兩週差分。回 top 排行榜（買賣超金額加權）。
**UI**：新分頁或併入「族群輪動」；表格 + 連買天數徽章。
**坑**：t86 僅上市、上櫃走 tpex 快取鍵；歷史 t86 若無快取需逐日補抓（重），限制 N≤10。

## #5 風險/部位管理計算器 — 指派：Sonnet 5
**目標**：每檔杯柄/自選股附「建議停損價、部位大小」：ATR(14)×2 停損、
部位=可容忍虧損金額÷(進場價−停損價)。
**資料**：`stock_ohlc` 已有全市場 OHLC，ATR 可直接算。
**做法**：`patterns.py` 加 `atr(highs,lows,closes,n=14)` 純函數+測試；杯柄 API 每檔附
`stop_loss`/`atr`；設定頁加「單筆可容忍虧損(元)」欄位（settings 表）。前端在杯柄 K 線
畫停損水平線（markLine，參考壓力線做法）、卡片顯示建議股數。
**坑**：ATR 用 True Range（含跳空），不是單純 H-L；除權息跳空會失真，註明即可。

## #6 交易帳本＋績效追蹤 — 指派：Opus 4.8
**目標**：記錄實單/模擬單（買入日/價/股數/賣出日/價），統計勝率、期望值、對比大盤同期。
**做法**：新表 `trades`（lazy migration 見 db.py init_db 慣例）；CRUD endpoints（已在
Basic Auth 後面）；績效頁：總報酬、勝率、平均賺賠比、vs 加權指數同期（market_daily 有）。
**UI**：自選股頁加「+記一筆」或獨立分頁。
**坑**：賣出前的未實現損益用 `stock_ohlc` 最新收盤估；手續費/稅選配欄位（預設 0.585% 來回）。

## #7 市場溫度計 — 指派：Sonnet 5
**目標**：紅綠燈 regime 指標：加權乖離(收盤 vs 60MA)、漲跌家數 10 日趨勢、VIX 分級、
外資期貨淨部位方向 → 綜合「積極/中性/防守」。
**資料**：全在 `market_daily` + `breadth:{date}` 快取。
**做法**：純函數 `analysis.market_regime(rows)->{level,reasons[]}` + 測試；總覽頂部色條顯示。
**價值**：#1 回測可依 regime 分組統計（好盤勢 vs 壞盤勢的杯柄勝率），把兩者串起來最有價值。

## #8 型態庫擴充 — 指派：Opus 4.8
**目標**：加 W底、箱型突破、創52週新高 三種 setup。
**做法**：每種都照杯柄的三件套：`patterns.py` 純函數（逐日版+向量化版+等價性測試）→
screen endpoint → **必須接 #1 的 backtest.py 統計**（`backtest_cup` 泛化成
`backtest_signals(signal_fn)`）。UI 併入杯柄分頁改成「型態選股」多 tab。
**坑**：向量化與逐日版必須寫等價性測試（見 test_patterns.py `test_signals_equivalent_*`）。

## #9 事件行事曆 — 指派：Gemini 3.5 Flash（簡單資料工程）
**目標**：自選股/持股的除權息日、月營收公布(每月10日前)、法說會提醒。
**資料**：TWSE openapi `/exchangeReport/TWT48U`（除權息預告）、公開資訊觀測站法說會列表。
**做法**：每日排程抓一次入 ai_cache；自選股表加「近期事件」欄；LINE 推播在事件前一日提醒。
**坑**：民國日期轉換用 twse.py `_roc_to_iso`；欄位名可能變動，parse 測試務必用真實樣本。

## #10 圖表操作升級 — 指派：Gemini Flash / Sonnet 5
**目標**：個股 K 線加週K/月K切換（stock_ohlc 聚合，參考 kline.ohlc_candles）、
十字游標量測（兩點漲跌幅）、手動水平線（localStorage 記憶）。
**坑**：ECharts CDN 版本鎖 5.x；CSP 只允許 jsdelivr，別引入其他 CDN。

---

## 即時監控的定位（2026-07-07 與使用者討論的結論）
- 使用者目標＝**盤中突破警示**（分鐘級足夠）→ 已實作 A 方案：`sources/mis.py` 輪詢證交所
  盤中快照（非官方、低頻、含哨兵離線告警），平日 09:00–13:35 每 5 分掃杯柄訊號股，
  現價 > 壓力線即 LINE 警示（每檔每日一次）。`POST /api/intraday/test` 可手動驗證。
- **統一期貨 API**（使用者持有）＝第二階段選項：僅覆蓋期貨/海期（無台股個股行情）、
  Windows .NET 元件 → 需「使用者本機收集器 + 回報雲端」架構（使用者已接受盤中開機）。
  適用場景：台指期/海期 tick 級看盤、或 mis 來源失效時的備援。指派 Opus 4.8，
  開工前先驗證其 Python 文件與 Linux 不相容性假設。

## 已完成（供接手者理解現狀）
- #1 `backtest.py`＋`patterns.cup_handle_signals`（向量化，等價性測試）＋
  `/api/patterns/cup-handle/backtest`（逐日快取）＋杯柄頁「📊 回測報告」面板。
- #2 杯柄「新符合/突破壓力」進 LINE 推播（`_cup_push_info`；每日訊號快照存 `cupsig:{date}`）。
- #3 上櫃納入：`tpex.fetch_otc_ohlc`；`backfill_ohlc` 雙市場（指標股法追蹤各自進度）；
  每日更新自動累積雙市場。**雲端需重跑回補**：`/api/ohlc/backfill?days=600` 直到 done
  （上櫃歷史從零開始，約 600 次請求、30 分鐘）。
