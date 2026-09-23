# 總覽融資融券改用證交所官方定義 設計

日期：2026-09-23　狀態：使用者已核准設計（方案 A、§3 選 3a、四段照寫）

## 問題

證交所 2026-08-03 上線「臺股儀表板 › 信用交易」（`twse.com.tw/dashboard/zh/credit/margin.html`），
使用者要求總覽的融資融券區塊參考它優化，且**定義要一致**。逐項對過（2026-09-22 資料日）：

| 項目 | 本站 | 證交所 | 結論 |
|---|---|---|---|
| 融資餘額（張）／融資金額／融券餘額 | 9,245,371／6,048.6 億／218,839 | 完全相同 | 同源（MI_MARGN），已一致 |
| 融資維持率 | 上市 184.6%、上櫃 190.8%（自算） | **193.92%**（整戶擔保維持率） | **定義不同** |
| 融資餘額占市值比重／信用交易成交值與占比／低於 130% 戶數／追繳戶／處分戶 | 無 | 有 | 官方 08-03 起逐日提供 |

維持率的差異是定義不是誤差：官方數字是券商申報的**真實帳戶合計**，而且**上市與上櫃公布的是同一個
全市場數字**（TWSE `BFIJ3U_TREND` 與 TPEx `dashboardOtc/marginTrend` 30 個交易日逐日相同）。本站是用
「Σ融資明細×收盤 ÷ 融資金額」的市值公式估的、還拆成兩個市場。08-03 起 19 個交易日實測，自算值比官方低
0.3%～5.9%，且不是固定比例（1.003～1.059 之間跳），是會漂的估計值。

## 使用者的決定（2026-09-23）

1. **維持率改用官方全市場「整戶擔保維持率」**，自算的上市／上櫃兩張卡與兩條兩平線退場。
2. 新增指標：**低於 130% 戶數／追繳戶／處分戶**（合成一張「追繳壓力」卡）、**融資餘額占市值比重**、
   **信用交易占成交值比重**（信用交易成交值併進副標）。不加「信用交易戶數／占開戶數」。
3. 台股大盤組變成 10 張卡，**改 5 欄**（5+5）。
4. 兩平線改成**加權兩平線**（3a）：以當日上市／上櫃融資金額為權重加權融資成數。
5. 自算維持率整條路徑**刪除**（不是留成死程式碼）。

## 資料源（全部實測，2026-09-23）

| 端點 | 用途 | 特性 |
|---|---|---|
| `GET https://www.twse.com.tw/rwd/zh/marginTrading/BFIJ3U?response=json&date=YYYYMMDD` | 單日信用交易概況 | 欄位 `crdAmt`（元）、`keepRate`（%）、`belowAccNum`、`callAccNum`、`callAmt`、`exeAccNum`、`exeAmt`、`marginAccNum`、`marginAccRate`。**08-03 前回 `{"stat":"很抱歉，沒有符合條件的資料!"}`**，當日尚未產製時同樣。 |
| `GET .../marginTrading/MI_MARGN_TREND?response=json&date=YYYYMMDD&days=N` | 逐日融資融券＋**上市總市值** | `data[]` 每列 `date`／`marginShr`（張）／`marginAmt`（仟元）／`shortShr`（張）／`marketValue`（億）。`days` 實測上限 **60**（傳 120／250 都只回 60 筆）。 |
| `GET .../marginTrading/MI_MARGN_HISTORY?response=json` | 年度歷史（2000～） | `data[]` 每年 `marginRatio`／`creditRatio`（%），當年度為前月底值。只給 tooltip 用。 |
| `GET https://www.tpex.org.tw/www/zh-tw/dashboardOtc/marginTrend?lang=zh-tw&days=N`（需 `verify=False`） | 上櫃對照 | `keepRate` 與 TWSE 逐日相同（30/30 天、差 <0.02）。**不存**，只在文件記錄「同一個數字」。 |

儀表板 JS（`dashboard/js/credit-margin.js`）裡的兩條算式，本站沿用：
- **融資餘額占市值比重** ＝ `marginAmt(仟元) ÷ 1e5 ÷ marketValue(億) × 100`。本站 `margin_value`（億）就是
  `marginAmt/1e5`，實測 6048.61 ÷ 1,563,443.68 ＝ **0.387%**，與頁面 0.39% 一致。
- **信用交易占成交值比重** ＝ `crdAmt ÷ 1e8 ÷ (2 × 市場總成交值(億)) × 100`。**分母乘 2**（買賣兩邊各算一次
  成交值）。本站 `turnover`（FMTQIK，億）與頁面的市場總成交值相同（10,787.76），1433.06 ÷ (2×10,787.76) ＝
  **6.64%**，與頁面一致。不乘 2 會得到 13.3%，是錯的。

## 設計

### 1. 資料層

- `sources/twse.py` 新增：
  - `parse_credit_summary(payload) -> dict`：BFIJ3U → `{"keep_rate", "below_call_acc", "call_acc", "exe_acc",
    "credit_amt"}`，`credit_amt` 存**億**（`crdAmt/1e8`，四捨五入 2 位）。`stat != "OK"` 回空 dict。
  - `fetch_credit_summary(date) -> dict`：thin wrapper，`raise_for_status()`。
  - `parse_margin_trend(payload) -> {date_iso: market_value_億}`；`fetch_margin_trend(date, days=60)`。
- `market_daily` 新增六欄（`MARKET_COLS`，lazy migration）：`keep_rate`、`below_call_acc`、`call_acc`、
  `exe_acc`、`credit_amt`、`market_value`。
- **衍生比率不落地**（同 `turnover_ma10` 的規矩，落地就要配自己的 heal pass）：`analysis.credit_ratios(margin_value,
  market_value, credit_amt, turnover) -> {"margin_mcap_pct", "credit_ratio"}`，任一輸入缺或分母為 0 回 `None`。
  `/api/dashboard` 對 `history` **逐列**注入這兩個鍵（位階條才有整個視窗可取樣）。
- `run_update` 的 task list 加 `("twse_credit", lambda: twse.fetch_credit_summary(D))`。BFIJ3U 當日尚未產製時
  `fetch` 回空 dict，**要記進 `failed`**：`{"source": "twse", "name": "twse_credit", "error": "尚未公布，稍後回補"}`
  （看得見但不告警——見 §4 的 `expected_later`）。不往回找別的日期（資料日 D 紀律）。
- 新增 `_backfill_credit(conn, days=10)`（同 `_backfill_intl` 的洞掃描、**只填 NULL 不覆蓋**）：
  `market_value` 用一次 `MI_MARGN_TREND`（days=60）填滿窗口；其餘五欄對每個有洞的日期各打一次 BFIJ3U，
  `date < 2026-08-03`（常數 `CREDIT_SINCE`）的列直接略過、不打。掛進 `run_update`，成功記 `twse_credit_backfill`。
- 一次性回補走 `GET /api/credit/backfill?days=60`（`api/admin.py`，比照 `/api/intl/backfill`）。
- 年度歷史：`fetch_margin_history()` 存 `ai_cache` 鍵 `credit_hist:{YYYY-MM}`（月更），`/api/dashboard` 回
  `credit_history: {"margin_ratio": {"min", "max", "since"}, "credit_ratio": {...}}` 給 tooltip。

### 2. 卡片（台股大盤組，10 張、5 欄）

順序：外資買賣超／投信買賣超／自營買賣超／融資餘額(張)／融券餘額(張)／**整戶擔保維持率**／**追繳壓力**／
**融資占市值**／**信用交易占比**／10 日均量。

| 卡 | 主數字 | 副標／註記 | 琥珀外框 |
|---|---|---|---|
| 整戶擔保維持率 | `keep_rate` 193.9%，較昨 ▲0.8 | `相對兩平 +11.4%（獲利）`（見 §3）| 同現行 `isMaintAlert`：`< 130×1.08` 或相對兩平 ≤ −20% |
| 追繳壓力 | `below_call_acc` 147（戶） | `追繳 27 戶　處分 11 戶` | 位階頭 10%（走泛用 `cardAlert`）|
| 融資占市值（上市） | `margin_mcap_pct` 0.39% | `近 26 年 0.36%～2.42%` | 位階頭尾 10%（泛用）|
| 信用交易占比（上市） | `credit_ratio` 6.64% | `成交 1,433 億` | 位階頭尾 10%（泛用）|

- 四張都**不著紅綠**（紅綠鎖給行情漲跌，這些是槓桿與壓力讀數）。
- 缺當日值時比照 `balanceCard`：退到最近一筆有值的列並標 `截至 MM-DD`。
- tooltip 逐字採用證交所的定義文字：維持率「全市場融資融券擔保品市值加計融券保證金與融資金加計融券標的市值
  之比例…不代表個別投資人或個別證券商狀況」；低於 130% 戶數「截至當日止，整戶擔保維持率低於130%之戶數」；
  處分戶「未依期限補足差額或屆期未清償，次一營業日應由證券商處分信用交易部位之戶數」；占市值「融資餘額占上市
  股票總市值的比率」；信用占比「信用交易成交值占市場總成交值的比率」。

### 3. 加權兩平線（3a）

- `ss_trader.blended_margin_ratio(tse_value, otc_value) -> float`：`(tse×0.6 + otc×0.5) ÷ (tse+otc)`，兩者皆
  缺回 `None`，只缺一邊就用另一邊的成數。今天 6,048.6／2,085.2 億 → 成數 0.5744 → 兩平 `100/0.5744` ＝ **174.1%**。
- `margin_breakeven(ratio)`／`margin_verdict(maintenance, ratio)` 規則不動，改吃加權成數。
- `/api/dashboard` 的 `bands["keep_rate"]`＝`{"breakeven": <當日加權兩平>, "call": 130}`（**依最新列每日算**，
  不再是常數；`_BANDS` 裡兩個舊鍵刪除）。前端一律讀 `lastBands.keep_rate`，不在 `app.js` 複寫公式。
- 已知代價寫進 tooltip：「兩平線以當日上市／上櫃融資金額加權融資成數推得（60%／50%），為近似值；官方
  維持率含融券項且為全市場合計」。

### 4. 消費端一致化

| 位置 | 改法 |
|---|---|
| `ss_trader.market_checklist` | `margin_maint`／`margin_maint_otc` 兩項併成一項 `margin_maint`「整戶擔保維持率」，值取 `keep_rate`，成數取 `blended_margin_ratio(margin_value, otc_margin_value)`。`market_pulse` 採計項數少 1（仍 ≥ `PULSE_MIN_ITEMS`）。 |
| `line_push.compose_daily_flex` 融資段 | 「維持率(上市)／(上櫃)」兩列併成一列「整戶維持率 193.9%（昨 193.1%）」；`full` 版多一列「低於130% 147 戶　追繳 27　處分 11」。`compose_daily_brief` 純文字同步。bubble 仍須 ≤ `_BUBBLE_MAX`（既有測試守）。 |
| 大盤×籌碼對照圖 `CHIP_PANES` `maint` 窗格 | 兩條線改**一條** `keep_rate`（`C.info`，不用紅），加 130% 追繳線 `markLine`（虛線、`C.muted`）。08-03 之前留白（同其他窗格前導 null 的既有處理）。 |
| `/public/api/overview` | `margin.maintenance`／`maintenance_prev` 改名 `keep_rate`／`keep_rate_prev`，值取官方；公開頁渲染端同步改鍵名。 |
| `api/market.py:722`（Gemini 盤勢摘要輸入）| 「融資維持率(%)」改讀 `keep_rate`，鍵名改「整戶擔保維持率(%)」。 |
| `helpers._check_update_result_and_alert.expected_later` | 舊的兩個名字換成 `twse_credit`（error 含「尚未」）。 |
| 設定頁 Stage2／文件 | 無。 |

### 5. 移除

- `updater._compute_margin_maintenance`／`_compute_otc_margin_maintenance`／`_heal_margin_maintenance`、
  `run_update` 裡呼叫它們的兩段與 heal 那段、`analysis.margin_maintenance`、`GET /api/margin-maintenance/heal`、
  `_BANDS` 的兩個舊鍵、前端 `marginMaintCard` 改成讀 `keep_rate` 的單一版本（`maintTip` 文案改 §2 的官方定義）。
- 對應測試刪除或改寫：`tests/test_analysis_daily.py` 的 `margin_maintenance` 測試、`tests/test_updater.py`
  兩條 heal 測試、`tests/test_api.py::test_dashboard_bands_come_from_ss_trader` 改斷言 `keep_rate` 的動態兩平、
  `tests/test_ss_trader.py` 兩市場那三條改成加權版、`tests/test_line_push.py` 三處欄位名。
- **保留**：`twse.parse_margin_detail`／`fetch_margin_detail`（`stock_flow.update_day` 仍用它抓逐檔融資餘額）、
  `market_daily` 既有欄位 `margin_maintenance`／`otc_margin_maintenance`／`margin_mv`／`short_mv`／`otc_*`
  （SQLite 不刪欄，留在 schema、不再寫值；`MARKET_COLS` 加註「已停用」）。

### 6. 版面

- `.stat-board--tw` 改 `repeat(5, minmax(0, 1fr))`；斷點沿用既有階梯但重量：≤1240px 3 欄→改成
  **≤1400px 4 欄、≤1240px 3 欄、≤1100px 2 欄**，手機 ≤600px 既有 2 欄不變。**改完要在 1904／1600／1400／
  1280／1181／768／375 逐一量 `.card-val` 是否溢出**（ui68 那節的教訓：中間寬度才會壞）。
- 追繳壓力卡副標「追繳 27 戶　處分 11 戶」與占市值卡副標「近 26 年 0.36%～2.42%」都要量長度；放不下時
  副標改兩行，不縮字級。

### 7. 版號與文件

- `20260817-ui69` → `20260817-ui70`（`web/index.html`×2、`api/public.py`×2 行、`tests/test_api.py`×4）。
- `CLAUDE.md`「融資維持率：兩個市場，兩條基準線」整節改寫為本次決定；新增一節記錄：官方數字是全市場單一值、
  自算值漂 0.3～5.9%、`creditRatio` 分母乘 2、BFIJ3U 08-03 起、TREND 60 天上限、TPEx 同值。`AGENTS.md` 同步一段。

### 8. 測試

- `tests/test_twse.py`：`parse_credit_summary`（正常／`stat` 非 OK 回空／`crdAmt` 換算億）、`parse_margin_trend`。
- `tests/test_analysis_*.py`：`credit_ratios`（含**乘 2**、缺值回 None、分母 0 回 None）。
- `tests/test_ss_trader.py`：`blended_margin_ratio`（兩邊有／只有一邊／都缺）、checklist 只剩一項且用加權成數。
- `tests/test_updater.py`：`_backfill_credit` 只填 NULL、`< CREDIT_SINCE` 不打、`market_value` 只打一次 TREND；
  `run_update` 當日 BFIJ3U 回空 → `failed` 帶「尚未公布」且不告警（`tests/test_health.py` 既有 `expected_later` 測試延伸）。
- `tests/test_api.py`：`/api/dashboard` 的 `history` 每列帶 `margin_mcap_pct`／`credit_ratio`、`bands.keep_rate.breakeven`
  隨最新列變動、`/public/api/overview` 的 `margin.keep_rate`；版號四處一致。
- `tests/test_line_push.py`：合併後的一列與 `full` 版追繳列，bubble 大小仍過上限測試。
- 瀏覽器實測：七個寬度零溢出；四張新卡的數字與證交所頁面同日一致；`has_keep_rate=false`（清掉當日值）時退回
  「截至」路徑；對照圖窗格一條線＋130 線。

### 9. 刻意不做

- 不存上櫃的信用交易概況（維持率同值；占市值／信用占比照證交所頁面用上市口徑）。
- 不做「信用交易戶數／占開戶數」（與追繳壓力重疊，且分母定義證交所未說明）。
- 不回補 2026-08-03 之前的官方維持率（來源沒有）；對照圖那段留白，不拿自算值頂替（兩個定義不能接在同一條線上）。
- 不改融資餘額／融券餘額兩張卡（已與證交所同源同值）。
