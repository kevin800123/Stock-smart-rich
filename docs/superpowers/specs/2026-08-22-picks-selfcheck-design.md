# 選股自算對照（picks self-check）設計

日期：2026-08-22
狀態：設計已確認，待寫實作計畫

## 目的

逐欄位比對「XQ CSV 匯入值」與「App 自算值」，讓使用者建立對「零 CSV 選股」的信心。
這是 Stage 2e 的**前置驗證步驟**，不是切換本身。

> 背景：`filtered_picks` 目前的四個硬閘（`w55`、`big_holder_ratio`、`rev_yoy`、`est_profit`）
> 與排序用的 `lan_value`/`lan_score`/`lpe` 全部來自每日上傳的 XQ CSV。Stage 2a/2c/2d/2b
> 已各自把這些數字用官方公開資料獨立重建成純函式（見 CLAUDE.md 對應段落），但**都尚未接進
> `filtered_picks`**（並存對照策略）。本頁是「並存對照」的可視化：把兩邊放一起看差多少，
> 等自算值連續對得起來、且資料成熟後，才在未來另一階段（2f）真正切換。

## 硬性不變式（不可違反）

1. **只讀**。本頁不寫任何 DB、不改 `filtered_picks`、不改任何選股結果。CSV 仍是唯一正式來源。
2. **沒有切換**。本頁不提供「改用自算」的開關；那是未來階段的事。
3. **自算值為 None 不是「不一致」**。資料未成熟（缺月營收/季報/集保）時該欄標「尚無自算」，
   絕不能被算成 mismatch——否則使用者會誤以為自算邏輯錯了。
4. 沿用既有色彩語彙：一致/差異的狀態記號**不用紅綠**（紅綠鎖給行情漲跌）；用中性 + 既有的
   「注意這格」語彙。差異細節走 hover，cell 保持乾淨（使用者明確要求）。

## 範圍

### 比對的 5 個欄位

| 欄位（顯示名） | chip_snapshot 欄 | 自算來源 | 現況 |
|---|---|---|---|
| 營收年增 | `rev_yoy` | `db.revenue_yoy_map(conn, as_of=date)` | ✅ 現可對（月營收端點全市場當月即到） |
| W55 | `w55` | `analysis.w55_signal(highs, lows, closes)`（讀 `stock_ohlc` 到基準日） | ✅ 現可對（OHLC 已為杯柄回補） |
| 大戶增比 | `big_holder_ratio` | `db.custody_change_map(conn, as_of=date)` | ⚠️ 需 ≥2 週集保才有 week-over-week 差 |
| 推估EPS | `est_profit` | `analysis.estimate_quarterly_eps(...)` | ❌ 需 6 個月月營收；現多為 None → 標「還在等」 |
| 蘭值 | `lan_value` | `analysis.lan_score(financials)` ÷ 自算本業PE × 100 | ❌ **雙重依賴**：需季報財務（算蘭質）**且**需自算本業PE（＝現價 ÷ 自算年化EPS，後者又吃 6 月月營收）；任一未成熟即 None → 標「還在等」 |

- 5 欄全上（使用者選）。未成熟的欄一律顯示「—」＋ hover 說明還需要什麼資料。
- **基準日期可選**（使用者選）：任意已匯入 CSV 的 `snap_date`，預設最新。

### 明確不做

- 不做「選股清單重疊」比對（est_profit/lan 未成熟前，自算清單幾乎空，現階段無意義）。
- 不接進 `filtered_picks`、不加任何 flag。
- 不新增資料源、不新增背景排程（純讀既有表 + 既有純函式）。
- 無前端自動化測試（同專案慣例）。

## 架構

### 純函式（`analysis.py`，TDD 先行）

**`selfcheck_compare(field, csv_v, self_v) -> str`**
- 回傳 `"match"` / `"diff"` / `"self_na"`（自算為 None）/ `"csv_na"`（CSV 端也沒值，兩邊都無從比）。
- 依欄位套容差（見下常數）。`w55` 為二元、完全相等才 `match`。
- 純算術、無 I/O，遵循「算不出不擲例外」慣例。

**容差常數（單一權威，寫在 `analysis.py` 頂部具名常數；同 ss_trader/scoring-rules 規矩，前端不得複製）**
```
SELFCHECK_TOL = {
  "rev_yoy": 0.5,             # 百分點
  "big_holder_ratio": 0.05,  # 百分點
  "est_profit": None,        # 相對 5% → 用 SELFCHECK_REL 表達
  "lan_value": 1.0,          # 絕對
}
SELFCHECK_REL = {"est_profit": 0.05}   # 相對容差（|self-csv|/|csv| ≤ 5%）
# w55 不入表 → 完全相等
```
- 之後要調容差改這裡即可，並經 API 揭露（見下）讓前端唯讀顯示，不寫死。

### 組裝（新模組 `stocks_power_rich/selfcheck.py`）

`selfcheck_compare` 與容差常數放 `analysis.py`（純算術，與其他評分常數同層）。組裝函式
`build_selfcheck` 需要同時碰資料層（`db.revenue_yoy_map`/`custody_change_map`/`chip_snapshot`
查詢/`stock_ohlc`）與分析層（`analysis.w55_signal`/`estimate_quarterly_eps`/`lan_score`/
`selfcheck_compare`），為避免 `analysis`↔`db` 反向依賴，放**新模組 `selfcheck.py`**（同 api 層
的作法，import 兩邊），不塞進已偏大的 `analysis.py`。

**`build_selfcheck(conn, date) -> dict`**
- 讀 `chip_snapshot` 該 `snap_date` 的所有列（CSV 基準值 + code/name/capital）。
- 對每個 code 算自算值：
  - `rev_yoy` ← `revenue_yoy_map(conn, as_of=date)`（一次全市場，dict 查表）
  - `big_holder_ratio` ← `custody_change_map(conn, as_of=date)`（一次全市場，dict 查表）
  - `w55` ← 取該 code 在 `stock_ohlc` 中 `date` 及之前的 highs/lows/closes（≥55 根）餵 `w55_signal`
  - `est_profit` ← `estimate_quarterly_eps(...)`（多為 None）
  - `lan_value` ← `lan_score(financials)` + 本業PE（多為 None）
- 逐欄位 `selfcheck_compare`，組出：
  ```
  {
    "date": "2026-08-22",
    "dates": [...可選日期清單...],
    "tolerances": {...揭露容差...},
    "rows": [ {code, name, fields: {rev_yoy: {csv, self, status}, ...}} ],
    "coverage": { "rev_yoy": {computable: N, total: M, median_abs_diff: x}, ... }
  }
  ```
- `coverage` 的 `median_abs_diff` 只在有值的配對上計算；欄位全 None 時 `computable: 0`、`median_abs_diff: null`。

### 端點（`api/admin.py`，屬驗證工具）

**`GET /api/picks/selfcheck?date=`**
- 不帶 `date` → 用最新 `snap_date`。
- **計算較重**（W55 逐檔掃 OHLC，CSV 可達 ~1500 檔）→ 用 `ai_cache` 快取，鍵
  `selfcheck:{date}:{data_version}`，`data_version` 取自相關表的內容雜湊（同 stock_flow research
  的既有作法），資料沒變直接回快取。
- **不新增背景執行緒**（研究報告那支是因為同步計算會撞 Zeabur 代理逾時；本頁若實測單次計算
  仍偏久，實作階段再評估是否比照背景執行緒化——先做同步 + 快取，量到逾時風險再加）。
- 揭露容差常數（`analysis.SELFCHECK_TOL` 等），前端唯讀顯示、不寫死。

### 前端（`web/`，command-bar 標準）

- 側欄「進階」群組新增一個 `.nav`：「選股自算對照」（`data-view="selfcheck"`，手機 `data-short`）。
- 新 `<section id="view-selfcheck" class="view picks-view">`：
  - `.picks-command`：h2「選股自算對照」＋一句定位＋日期下拉（`#selfcheck-date`）＋
    覆蓋率摘要（`.picks-command-foot`，每欄「可自算 N/M・中位數差 x」）＋ `aria-live` 狀態。
  - `#selfcheck-table`（`.table-wrap fill`）：一列一檔，欄＝股票＋5 欄。
  - 每格：自算值 + 小狀態記號（✓/~/—）；`title`（hover tooltip）顯示「CSV: x ／ 自算: y ／ 差 z」。
  - 未成熟欄顯示「—」，hover 說明「需 N 個月月營收」「需回補季報財務」。
  - 空/載入/錯誤狀態走既有 `.table-empty` 元件。
- `showView("selfcheck")` 時載入（沿用既有 once-flag / 進頁才抓的慣例）。
- 遵循既有前端不變式：事件委派（無 inline handler）、CSP `script-src 'self'`、狀態同時有文字與
  結構語意、觸控目標 ≥24px、無頁面級水平溢出（寬表在 `.table-wrap` 內橫捲、第一欄凍結）。
- 快取版本字串（目前 `20260817-ui13`）bump，並同步三處（index.html / api/public.py / test_api.py）。

## 資料流

```
使用者開「選股自算對照」→ 選日期
  → GET /api/picks/selfcheck?date=D
    → ai_cache 命中？回快取
    → 否則 build_selfcheck(conn, D):
        chip_snapshot[D]（CSV 基準）
        × revenue_yoy_map / custody_change_map / w55_signal / estimate_quarterly_eps / lan_score
        → 逐欄 selfcheck_compare → rows + coverage
      寫 ai_cache → 回傳
  → 前端渲染表格（值 + 狀態記號 + hover 細節）+ 覆蓋率摘要
```

## 測試

- `tests/test_analysis_selfcheck.py`：`selfcheck_compare` 各欄容差邊界（含 self=None → self_na、
  csv=None → csv_na、W55 二元、相對容差 est_profit、絕對容差 lan_value）；`build_selfcheck`
  以 monkeypatch 掉 map/純函式，驗證組裝與 coverage 計算（含全 None 欄的 median 為 null）。
- `tests/test_api.py`：端點 TestClient + 暫存 DB，塞一天 chip_snapshot、monkeypatch 自算來源，
  驗證回傳結構、快取命中、容差揭露來自 `analysis` 常數（防前端另寫一份，同 scoring-rules 慣例）。
- 全套 `pytest -q` 綠燈、無回歸；`node --check web/app.js`；detector 無新增 finding；
  桌機 1512/900/390 無水平溢出。

## 風險與取捨

- **W55 逐檔掃 OHLC 的成本**：CSV ~1500 檔，每檔取 ≥55 根算 %R(55)。先做「同步 + `ai_cache`
  快取」；若實測單次計算逼近 Zeabur 代理逾時，再比照 stock_flow research 改背景執行緒（不預先
  過度工程）。
- **本機資料 vs production**：本機 `stock_revenue_monthly`/`stock_financials` 累積不足 → est_profit/
  lan 多為 None，屬預期；驗證「尚無自算」路徑正好。三個現可對的欄（rev_yoy/w55/big_holder_ratio）
  要在有資料的情況下驗證至少數檔數值兜得起來。
- **蘭質是蘭弦專有指標**：自算的 `lan_score` 是「忠實還原」但口徑不必逐位對齊 CSV（同 CLAUDE.md
  「不要拿外部數字校準」的提醒）；容差 ±1 是給「大方向對不對」用的，不是要求逐位相同。

## 不在此案

- 真正把自算接進 `filtered_picks`／零 CSV 切換（未來 2f）。
- 調整任何選股公式、資料源、既有 API schema。
- 設定頁的 UI 標準化（另案）。
