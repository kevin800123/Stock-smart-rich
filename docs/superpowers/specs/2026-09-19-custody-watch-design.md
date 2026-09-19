# 週集保一公布就反映到自算選股（custody_watch）設計

日期：2026-09-19　狀態：使用者已核准設計

## 問題

使用者回報：週六新一週的集保已經公布，自算選股卻還沒更新。截圖是資料日 09-18、名單於 09-18 17:30 算好。

實測原因（2026-09-19 13:23 台北）：

- TDCC opendata `getOD.ashx?id=1-5` 已是 **20260918** 這一週（69,139 列）。
- 本站只在每日 21:00 的 `run_update` 裡呼叫 `_accumulate_custody`。週五 21:00 那次 TDCC 還沒放出新週，抓回 09-11 就放棄；下一次要到週六 21:00。
- 所以週六白天的大戶增比、人數降比、木質的籌碼分與「大戶買進版圖」都是上一週的集保。
- 週六 18:00 的 Telegram「本週新進榜＋本週大戶買進前三子產業」排在 21:00 之前，**一定**用舊集保。大戶買進金額就是用大戶增比算的。
- 自算選股頁只寫資料日，看不出用的是哪一週集保，使用者只能憑「怎麼沒變」察覺。

## 使用者的決定

1. 新一週集保**一公布就重算**自算選股（網頁與週報）。
2. 週六週報遇到新集保還沒公布時**等新集保再送**，最晚 21:30 照送並註明。
3. 採用獨立的集保輪詢排程（方案 A）。另外兩個方案被否決：週末多跑幾次整支 `run_update`（十幾個來源白打、撞其他節流與告警），以及開頁面時才檢查（對外抓取進請求路徑，也救不了週報）。
4. **前瞻紀錄不回頭改**：09-18 的訊號維持週五收盤時算出的名單。

## 設計

### 1. 偵測：`custody_watch` 排程

- **時段**：週五 17:00–23:30、週六 08:00–21:30，每 30 分鐘一次（cron：週五 `hour=17-23, minute=0,30`；週六 `hour=8-21, minute=0,30`）。兩筆排程同一個 family `custody_watch`，`catchup=True`。啟動補跑只補同家族最近錯過的一場，重跑無害。
- **每次只讀檔頭**：新函式 `tdcc.peek_custody_week() -> str | None` 用 `httpx.stream` 讀到第二列就關閉連線，回傳 ISO 日期（`20260918` → `2026-09-18`），不下載整份（整份約數 MB）。要帶 `verify=False`（TDCC 憑證缺 SKI，既有規矩）。連線錯誤往上拋；格式不符回 `None`，由 `custody_watch` 丟 `RuntimeError`。兩者都由 `run_job` 記進執行紀錄，不吞。
- **比資料庫新才動作**：`peek` 的週 ≤ 資料庫最新**完整**週（`db.latest_complete_custody_week`）→ 回 `{"skipped": "no_new_week", "tdcc": week, "local": ...}`。比較新 → 呼叫 `_accumulate_custody`（完整下載、寫入），再呼叫 `refresh_self_screen_cache(c, day=快取裡那一天, record_signals=False)`。
- **重算快取裡那一天，不是 market_daily 最新一列**：週五 17:30 名單已算好，但 market_daily 要到 21:00 才有週五那一列；週五傍晚新集保進來時若用最新交易日重算，會算成週四、蓋掉週五的名單。沒有快取時才退回最新交易日。
- **完整週判定（撰寫計畫時發現的既有缺陷）**：個股頁「補歷史」會把**單一檔**寫進全市場還沒公布的那一週。`_accumulate_custody` 原本用 MAX 做 6 天節流、用「該週有任何一列」判斷已存在，只要有人先點過補歷史，全市場那一批就被擋一整週。節流與「已存在」都改看完整週（門檻同 `custody_compare_weeks` 的 0.5）。
- **記下第一次取得的時間**：`_accumulate_custody` 寫入新週時存 `ai_cache` 鍵 `custody_fetched:{week}`＝ISO 時間。`custody_watch` 的回傳值同時帶 `week` 與 `fetched_at`，進 `job_runs.note`。累積幾週就知道 TDCC 實際幾點公布，屆時可收窄時段。
- **不影響 21:00 的 `run_update`**：它照舊呼叫 `_accumulate_custody`。那裡已有「同一週已存在就不寫」的判斷，兩邊不會重複寫入。

排程函式放在 `api/helpers.py`（既定分工：排程邏輯先放 helpers，`main.py` 只呼叫），走既有的 `run_job` 去重與執行紀錄。

### 2. 週六週報：`picks_new_weekly` 等新集保

- 排程從「週六 18:00 一次」改成**週六 18:00–21:30 每 30 分鐘**（`hour=18-21, minute=0,30`）。`run_key` 用 `日期:HH:MM`，比照 `self_screen_early`。
- 每次執行的判斷（寫在 `telegram_new_picks_job` 的 weekly 分支，透過它測）：
  - 這一週已送過（`ai_cache` 鍵 `picks_weekly_sent:{ISO 年週}`）→ `skipped: already_sent`。
  - 本週集保還沒進來、且現在 < 21:30 → `skipped: waiting_custody`，下一個 30 分鐘再試。
  - 本週集保已進來 → 送出。
  - 21:30 仍沒進來 → 照送，訊息多一行「集保仍為 MM-DD 週（本週尚未公布）」。
  - 送出成功才寫 `picks_weekly_sent`。送失敗不寫，下一個時段會重試。
- 「本週集保已進來」的定義（`custody_is_current(c)`）：最新**完整**集保週 ≥ 最新交易日（`market_daily` 有 `taiex` 的最新一天）所在 ISO 週的週一。以週為單位，不寫死週五，週五放假時 TDCC 用當週最後一個營業日也照樣成立。
- 平日 21:40 的「今日新進」不變。

### 3. 前瞻紀錄只在訊號日當天寫

- `refresh_self_screen_cache` 新增參數 `record_signals: bool = True`。`custody_watch` 傳 `False`。
- 另外加一道通用守衛：**只有在訊號日當天（`_now().date() == signal_date`）才呼叫 `record_self_screen_signals`**。週六 21:00 的 `run_update` 重算 09-18 時，今天是 09-19，不寫前瞻紀錄。
- 理由：訊號的進場價是訊號日收盤。收盤時拿不到的資料（週六才公布的集保）不能改寫那天的名單，否則前瞻勝率被高估，而且補不回來。代價是週五排程若整晚失敗，週六補算不會補記那一天，前瞻少一天樣本。這與「只抓到一個市場就整天跳過」的既有取捨一致：少一天樣本可以接受，偏一天會讓結論失真。
- 現有的 `record_self_screen_signals` 本身仍是「每個訊號日只寫一次」，行為不變。

### 4. 看得見：頁面標出集保週

- `selfcheck.compute_self_screen` 的 `coverage` 多兩個鍵：`custody_weeks`（新到舊兩週，取自既有的 `db.custody_compare_weeks(conn, date)`，和大戶增比實際用的是同一組）、`custody_fetched_at`（`custody_fetched:{本週}` 或 null）。
- 自算選股頁覆蓋率列加一格「集保 09-11→09-18」，有 `fetched_at` 時附「09-19 09:30 取得」。中性色，不用琥珀（琥珀只給「注意這格」與目前選擇）。
- 快取是排程算好存起來的，這一格描述的就是那一份名單用的集保，不另外即時查 TDCC。

### 5. 錯誤處理

- `peek` 失敗（逾時、TLS、格式改版）：例外往上拋，`run_job` 記成 `failed`，下一個 30 分鐘再試。不告警：21:00 的 `run_update` 仍會照舊抓，漏不掉資料。
- `_accumulate_custody` 或重算失敗：同上記成 `failed`。重算失敗時，集保已寫入，21:00 那次會用新集保重算。
- TDCC 回的週比資料庫舊（換回舊檔）：視為 `no_new_week`，不動作。

### 6. 測試（`tests/test_custody_watch.py`）

- `peek_custody_week`：用假回應驗證只讀到第二列就能取出日期、帶 `verify=False`；BOM 與表頭；格式不符回 None，`custody_watch` 因此丟例外。
- 完整週：逐檔回補寫進新週的一檔不擋全市場批次；第一次取得時間只記一次。
- `custody_watch`：
  - TDCC 週 ≤ 資料庫完整週 → 不下載、不重算。
  - 較新 → 呼叫 `_accumulate_custody`，並以快取裡那一天、`record_signals=False` 重算。
  - 回傳帶 `week`／`fetched_at`。
- 週報：等待→新週進來送出→同週不再送；21:30 保底訊息帶「集保仍為」；送失敗不標記、下一場重試。
- `custody_is_current`：週五放假（當週最後營業日是週四）時仍判定成立。
- 前瞻守衛：`_now` 固定在週六，重算 09-18 → `signal_ledger` 不新增 09-18 的列；固定在週五 → 照常寫入。
- 排程規格：`job_schedule` 有兩筆 `custody_watch`，`picks_new_weekly` 的時段是 18:00–21:30。
- 反證：每道守衛拿掉，對應測試要轉紅。

### 7. 文件

CLAUDE.md 與 AGENTS.md 的「自算選股」與「排程補跑」章節補上：`custody_watch` 的時段與理由、週報等待規則、前瞻紀錄只在訊號日當天寫、`custody_fetched` 用來量 TDCC 公布時間。

## 不做的事

- 不改 6 天節流的長度；只把它的基準從 MAX 改成最新完整週（見第 1 節「完整週判定」）。21:00 路徑照舊呼叫 `_accumulate_custody`。
- 不改籌碼選股頁（CSV 那條線）與 LINE 週六週報（它讀的是 CSV 名單，不是自算集保）。
- 不在網頁請求路徑裡查 TDCC。
