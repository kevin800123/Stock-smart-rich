# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

STOCKS POWER RICH (股力智富) — a single-user Taiwan-stock chip-analysis app: daily market/chip dashboard, daily-CSV stock screening, per-stock K-line + chips, sector rotation, optional Gemini summaries. FastAPI backend that also serves a vanilla-JS frontend; SQLite storage.

## Commands
Windows. Use the project `.venv` (Python 3.11+).

- **Run locally**: `.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000`
  - Or double-click `啟動.bat` (first run creates venv + installs deps; sets `SPR_ENABLE_SCHEDULER=1`; opens the browser).
- **All tests**: `.venv\Scripts\python -m pytest -q`
- **Single test**: `.venv\Scripts\python -m pytest tests/test_twse.py::test_parse_taiex_rwd_latest_row -q`
- No linter/formatter is configured; match surrounding style.

Gotchas:
- The Windows terminal mangles CJK (Big5/UTF-8) output. To inspect API/data with non-ASCII, write results to a UTF-8 file and Read it, rather than printing to the shell.
- **Never gate a commit on `pytest | tail`** — the pipe masks pytest's exit code (a failing suite looks like it passed). Run pytest as its own command and check the summary.

## Architecture

**One service, two roles** (`main.py::create_app`): JSON API under `/api/*` **and** serves the frontend (`web/`, no build step) via `StaticFiles` at `/`. Because relative `/api` paths work, it deploys as a single service (no CORS). `app = create_app(enable_scheduler=os.getenv("SPR_ENABLE_SCHEDULER")=="1")` at import time.

`main.py` is now a thin coordinator (~190 lines): it builds the app, registers middleware (Basic Auth + security headers), mounts static, starts the scheduler, and `include_router`s the six `APIRouter`s under **`api/`** — `market` (大盤/板塊/情緒/法人排行), `stock` (個股 OHLC/K線/股東分佈/自選股/型態), `trades` (交易帳本), `csv` (籌碼 CSV 上傳匯入), `public` (免密碼 `/public/*` 頁面與 overview API), `admin` (系統/更新/回補/備份). Shared pieces: `api/deps.py` (`conn()` DB lifecycle) and `api/helpers.py` (LINE compose, watchlist, `data_is_stale`/update-result alerts, cup-handle screen — logic that background Jobs also call). **New endpoints or logic go in the matching `api/` submodule; anything a scheduler Job needs lives in `api/helpers.py` first, then the Job in `main.py` calls it.** `main.py` re-exports `data_is_stale`/`_check_basic` from `api.helpers` for existing tests.

**Storage** (`db.py`, stdlib `sqlite3`): schema + upserts + lazy migrations (`init_db` runs `ALTER TABLE` for newly-added columns, so adding a column to `MARKET_COLS`/`CHIP_COLS` is enough). Tables: `market_daily`, `chip_snapshot`, `tx_history`, `custody_dist`, `watchlist`, `settings`, `ai_cache`, `csv_files`. `ai_cache` doubles as a general per-key cache (valuation, T86/TPEx per date, sectors, TDCC week, etc.).

**Data sources** (`sources/*.py`) — each module = *pure parse functions* (unit-tested with sample payloads) + *thin network wrappers* (mocked in tests). `twse` (證交所), `taifex` (期交所), `tdcc` (集保), `tpex` (櫃買), `intl` (yfinance 國際指數), `kline` (yfinance K線 + generic OHLC resampler).

### The central design: single "資料日期 D"
`updater.run_update` fetches 加權指數 **first** to define the data date `D`, then fetches every other source **for that exact D**, so all values on a dashboard row are the same trading day. Two rules make this reliable:
- **Prefer direct endpoints over openapi.** TWSE `rwd/zh/...` and TAIFEX official CSV downloads publish same-day (~15:00); the openapi mirrors lag to evening/next day. Only fall back to openapi where no direct source exists.
- **Never walk back to another date.** A source returns null if D isn't published yet (rather than silently returning yesterday's data mislabeled as D). `_refresh_recent` / `_backfill_chips` / `_backfill_margin` re-fetch recent days on later runs to fill nulls and overwrite preliminary→final revisions.

### Two hard-won invariants (do not regress)
- **`run_update`'s "delete future rows" keys off the real calendar `datetime.now()`**, deleting only rows outside `[today-400d, today]`. It must NOT key off the *fetched* date — a source occasionally returning a wrong old date (e.g. a month-boundary bug) would then wipe all good history.
- Month-boundary: to get "latest / a month" of index data, anchor on the **last day of the previous month**, not "today − N days" (which overshoots two months back at the start of a month).

### Frontend (`web/app.js`, one file)
View-switching SPA + ECharts (local `web/vendor/echarts.min.js`, no CDN — CSP is `script-src 'self'`). Candlestick data is `[open, close, low, high]`. All fetches use relative `/api`. Charts degrade to "尚無資料" on empty; tooltips round floats. Elliott-wave detection lives **only** in Python (`elliott.py`); `kline.py` precomputes `waves` (a `{pct: segments}` dict for thresholds 2–15%) into the K-line API response, and `app.js` just renders `data.waves[pctKey]`. **Do not reintroduce a JS Elliott implementation** — the dual-implementation drift it caused is gone; add new wave logic on the backend.

**卡片異常與今日重點**：`.card.alert` 只由 `cardWrap` 產生並同步收集明細；泛用門檻理由以 `alertReason` 為唯一權威版本，`isAlert` 只是布林投影。10 日均量刻意走 `isVolMaAlert`，不吃泛用 `isAlert` 的位階頭尾 10%，避免稀釋「量縮破線」語意。所有固定門檻仍由 `/api/dashboard` 的 `bands` 供給。

**「注意這格」只有琥珀外框**（`.card.alert`／`.ms-verdict.alert`），紅綠只給行情漲跌。`#today-focus` 刻意不用琥珀：一個在清單每一列都亮的訊號，在那份清單裡的資訊量是零。

**全域搜尋／新鮮度徽章／總覽新聞條（2026-08）**：`GET /api/stock/search` → `analysis.search_symbols`（純數字只比對**代號前綴**、其餘只比對**名稱包含**，名稱排序是「完全相等→開頭相符→只是包含」；名稱表沿用既有的 `_ohlc_names`，不另設快取鍵）。**該路由必須註冊在 `/stock/{code}/...` 之前**，否則被當成 `code="search"` 吃掉——回 200 的安靜失效，已用測試鎖住。`GET /api/news/headlines` → `headlines_logic` **只讀既有快取，絕不抓取也絕不呼叫 Gemini**（直接叫 `news_logic` 等於「開一次總覽就吃掉 1/20 的當日免費額度」），沒有快取就回空、前端整塊隱藏。新鮮度徽章的顏色吃後端 `data_stale`、天數用日曆天，**不用紅綠**（鎖給行情漲跌），過期沿用琥珀＝「注意這格」。側欄的「進階」群組收合 **CSS 只在 >860px 生效**：以下是圖示軌／手機 `display:contents`，沒有標題可點，收起來就打不開。

**手機版（≤600px）**：底部列是「4 個常駐分頁（`data-primary`）＋『更多』面板」，不是 13 個平均分佈（一格只有 28.8px）；面板展開的就是側欄那 13 個 `.nav` 節點本身，不複製 DOM。**面板 2026-08 起是右側抽屜**（`position:fixed; right:0; width:min(84vw,300px)`），不再是貼底 3 欄網格——要顯示分組標題就得把手機段拿掉的 `display:contents`／`display:none` 三層覆寫在 `.sidebar.open` 底下逐一還原；「進階」群組標題要加 `pointer-events:none`，否則 app.js 的收合 handler 是無條件執行的，手機點下去畫面沒反應但會把 localStorage 寫壞，回到桌機時展開狀態被無聲改掉。三個踩過的坑：(1) **`.sidebar` 手機段一定要 `display: flex`**——它本身沒宣告 display，桌機的 flex 容器是裡面的 `.sidebar-nav`，而手機段把它設成 `display: contents`；少了那行只設 `flex-direction` 完全沒作用也不報錯，實測底部列高 **648px** 蓋掉 80% 畫面。(2) **`flex-basis` 在 `flex-direction` 翻成 column 時會從「寬度」變成「高度」**（`.market-strip` 的 `flex: 2 1 420px` → 420px 高，內容只有 244px），直排時一律 `flex: 0 0 auto`。(3) 寬表格凍結第一欄要用 **`::after` 疊不透明底**而非改 `background`——`#daily`/`#industry` 的斑馬紋規則帶 ID 前綴、權重贏過任何不含 ID 的選擇器；並用 `:not([colspan])` 排除自選股的估價面板列。`window` 的 resize handler 要列出**每一張** echarts（手機轉方向就會走到）。**11 個總覽區塊手機首訪只展開台股大盤與法人排行**（`MOBILE_OPEN`），其餘 9 組預設收合——**首訪只加 class，絕不寫入 localStorage**，寫進去會把桌機的展開狀態一併改掉。**置頂 KPI 條手機版改用 IntersectionObserver**（桌機路徑不變）：`.overview-top` 還在視野內就 `.hidden` 掉 KPI 條，避免與大盤條重複 6 個項目（佔首屏 11%）。

**四個實測缺陷（2026-08）**：(1) **總覽開頁 2,639→169 KB**——`analysis/weekly`(2,262 KB)＋`analysis/daily`(209 KB) 在開機的 IIFE 就抓，總覽一個位元組都用不到；改成進頁才載入（沿用既有 `cupLoaded` 的 once-flag 慣例），但 `applyImportResult`（上傳 CSV 後）必須維持立即重抓。(2) **`--muted` 對比 4.12→4.85**：它用在 11–12px 小字、光總覽 56 處，正是對比要求最嚴的尺寸，而本專案對 `--up`/`--down` 有明文量測紀律卻漏了用量最大的這個。(3) **觸控目標補到 24px**（WCAG 2.2 SC 2.5.8）：**先量才發現原本的判斷是錯的**——勾選框雖然只有 13×13px，但命中範圍是外層 `<label>`（77–129px 寬），要撐的是 label 的**高度**不是把方框畫大。(4) **個股查詢空狀態**：原本 722px 空白畫布，改成搜尋提示＋自選股快捷＋最近查詢；**解除 `hidden` 必須排在 `initChart` 之前**，否則容器還是 `display:none`，echarts 把尺寸記成 0。另：**測試不可複製色碼**（`tests/test_line_push.py` 改引用 `line_push._C_*` 常數）——複製一份權威值，換色時整批紅燈而那不是真回歸，是測試拿舊底量新色。**同批曾試過一次「暖色記帳桌」整體改版，使用者看過後 `git revert`**；量測數字說服不了「不喜歡」，視覺方向要先讓使用者看到再談細節。

**兩個 ≤900px 的版面缺陷（2026-08）**：(1) **「展開組成」把 `.pulse-card` 從 352 撐成 745px**——`.overview-top` 是 `flex-wrap: wrap` 的直排容器，多行 flex 的 `align-items: stretch` 拉到的是「**該 flex line** 的 cross size」，而那條線的寬度由項目自己的 max-content 決定，不是容器寬度；裡面檢核表的說明欄 `nowrap` 長句 min-content 就 710px。**`min-width: 0` 治不了**（自動最小尺寸只作用在**主軸**，這裡撐開的是交叉軸），**也與 `flex` 值無關**（用改版前的值實測同樣 745px ＝既有缺陷非回歸）。修法是把 ≤900px 誤設成 `none` 的 `max-width` 改回 `100%`。(2) **市場儀表指針壓過讀數**：`center:["50%","68%"]`＋`offsetCenter:[0,"6%"]` 把讀數放在圓心正下方 4px，而起訖角 200°/−20° 兩端都指斜下方——幾何上的必然，**與螢幕寬度無關**。改成讀數移到弧線下方（`radius:66%`／`center:["50%","44%"]`／`offsetCenter:[0,"105%"]`／容器 176px，**這四個數字綁在一起，改一個要重算其他三個**）。驗證法：對 canvas `getImageData` 數「讀數方框內有幾個指針色的像素」，新幾何 7 個值全是 0，**且用舊幾何跑同一支檢查會得到 639~758**——證明這個檢查不是恆真。

**兩張 K 線圖走同一套系統（2026-09）**：個股圖與杯柄圖都是「價格 59%／量能 13%」兩窗格，**日期標籤只出現在最底部那個窗格**（價格窗格的日期會與量能 y 軸刻度撞在一起，實測「2,800」與日期同一行）。量能單位一律**張**。杯柄圖的預設視窗由 `left_date` 的 index 推算（往前留 12% 前導、夾在 0~55%），不可寫死百分比——左緣落在畫面外就等於這張圖的唯一任務失效。左右緣是小圓錨點不是圖釘；**右緣點與壓力線起點同座標，兩個標籤必須往相反方向推開**（窄畫面下壓力線變短、中點標籤會貼上來，桌機看不出來）。`drawCupChart` 的 `setOption` 後**必須 `resize()`**（進頁才畫，正好踩 echarts 凍尺寸）。

**個股 note 要說出「資料到哪一天、有沒有洞」**：category 軸按索引排列不按日期，缺口兩端會被畫成連續的，同時產生「日期沒更新」與「價格斷崖」兩個假象；門檻依週期分開（`KLINE_GAP_DAYS`：日 14／週 21／月 45 天）。

**Layout quirk**: `.view` is `display: flex; flex-direction: column;` so content-heavy pages (e.g., trading journal with 未平倉+已平倉 tables) can be compressed by flex-shrink. **Solution**: `.table-wrap` has `flex-shrink: 0` by default; `.table-wrap.fill` overrides to `flex-shrink: 1; flex: 1 1 0; min-height: 0` for tables that should occupy remaining space. Add `flex-shrink: 0` to any new table that must maintain readable height regardless of page overflow.

### Other backend pieces
- `analysis.py`: `filtered_picks` (W55 翻多 ∧ 大戶增比>0 ∧ 營收年增>0 ∧ 推估EPS>0, sorted by 蘭值); `industry_to_sector` maps the CSV's `上市/上櫃+類股` field to official 類股 names for sector cross-referencing; `trade_stats(trades, closes, taiex_by_date)` returns `{trades: [...{status, net_pct, pnl, mkt_pct, alpha}], stats: {closed_n, win_rate, avg_win, avg_loss, payoff, expectancy, realized_pnl, open_pnl, avg_alpha}}` for trading journal performance (fees deducted, open positions marked-to-market).
- `csv_import.py`: imports the user's daily 選股 CSV/Excel — multi-encoding auto-detect (cp950 / big5hkscs / utf-8-sig …), `.xlsx/.xlsm` via openpyxl, ROC/西元 date extraction, field-count normalization for unquoted commas in text columns.
- `gemini.py`: AI summaries degrade to plain data when no key; cached per day in `ai_cache`. Never expose the key (API returns only `gemini_configured: bool`).
- `line_push.py`: `compose_daily_brief(row, sectors, watch, ai_text, full, tsmc, prev, cup)` + `compose_breakout_alert(hits, hhmm)` format LINE messages（16:00 的 brief job 已於 2026-08 移除，`compose_daily_brief` 現在只服務每日完整版與 webhook 查詢；intraday breakouts with ⭐ for picks). Breakout alerts use **ATR threshold** (price > resistance + 0.3×ATR) + **two-round confirmation** (candidate on first cross, report only if still above threshold 5min later) to reduce false alerts. **月額度用盡會自動停播（2026-08）**：`api/helpers.py::_is_quota_exceeded` 認 429 + body 含「monthly limit」，三個 broadcast 路徑（每日完整版／週報／盤中警示）都先查 `line_quota_paused(c)`，本月已知用盡就跳過不打；`line_quota_month` 存的是「用盡當月」字串，判斷式只比對是否等於**現在**的月份，月份一換自動恢復，不用另外排程去清。webhook 的 reply 不耗額度、完全不受影響。
- `patterns.py`: cup-handle detection (左緣未破高 ∧ 杯身寬 ∧ 柄淺守穩 ∧ strength filter); `atr(closes, period=14)` for position sizing.
- `ledger.py`: signal forward-test. `record_daily_signals` snapshots each day's `filtered_picks` + cup-handle hits into `signal_ledger`; a RetN updater later backfills 5/10/20-day realized returns from `stock_ohlc`. Bias-free (no survivorship) counterpart to `backtest.py`'s one-shot historical cup backtest; the performance-aggregation API compares "signals-all" vs the trade journal's actual alpha.
- **`traders/` (操盤手)**: a registry of trading-persona analyzers behind the「操盤手」view. Each persona = one module exposing `META = {id,name,emoji,tagline,desc}` + `analyze(conn) -> {date, sections[], disclaimer}`, registered in `traders/__init__._MODULES`. `sections` are generic typed blocks (`checklist` / `table` / `routine` / `note`) the frontend renders without bespoke code, so **adding a persona = one new module, no endpoint/frontend change**. Endpoints: `GET /api/traders` (list for the picker) + `GET /api/traders/{id}` (that persona's analysis, `{**META, **analyze()}`). `traders/ss.py` is the first persona; its pure rule engine lives in `ss_trader.py` (quantifiable subset of the "Ss" methodology — full qualitative distillation in `.Codex/skills/ss-trader/SKILL.md`): market checklist (融資維持率 13X% 抄底區, 融資 vs 大盤 wash, VIX contrarian, USD/TWD via the `twd` intl ticker, volume×position, 小那 vs 小道 fund flow, night-session ratio, settlement week) + 一紅吃三黑 candle signal + 季季高-approx picks. Every persona's output carries a mandatory not-advice disclaimer.
- `offsite_backup.py`: after the 21:00 `backup_db`, pushes the rotated backup to a remote Git repo (env-gated; silently skips if unset). `mask_secrets` scrubs OAuth tokens from logs via `re.sub(r'https?://[^@\s]+@', 'https://***@', text)` — never log a raw remote URL.
- `scheduler.py` (APScheduler, `timezone="Asia/Taipei"`) runs the daily update in-process; needs the process alive. Intraday breakout scanning runs every 5min during market hours. `cli.py` is the equivalent for Windows Task Scheduler.

**`stock_flow.py`（2026-08）**：正規化的法人／融資券逐檔資料層（`stock_flow_daily`）＋研究引擎，只交付資料與研究報告，不交付選股頁或分數——五個研究狀態最好的結果 `candidate_for_prospective` 也只代表「值得前瞻觀察」，不是選股訊號，這句話進 API 回應本身。`update_day` 讓 `MI_INDEX ALLBUT0999`／`dailyQuotes` 各只打一次，同一份 payload 同時供 OHLC、量額、維持率使用。`bulk_upsert_ohlc` 改成 `COALESCE`（null 不再洗值），是兩來源合併寫入同一列的必要條件；改這個函式前看 CLAUDE.md 對應段落。`days=220` 是算出來的（交易日/日曆天≈0.67，研究閘門需要≥119 個交易日），不是隨手選的常數。**回補「卡在同一批補不完」（2026-08 修）**：`_weekdays()` 把每個平日都當候選、不排除國定假日，假日原本沒有終止狀態（`continue` 不寫任何 row）→ 每次呼叫都重新掃描同一批假日、完整度永遠到不了 100%。修法：`stock_source_coverage` 新增 `"holiday"` 終止狀態，`backfill()` **兩輪確認**才判定（第一輪兩市場皆空只標 `failed`，避免單次網路小抖動誤殺；第二輪仍空才全 source 標 `holiday`），`coverage_report()` 把已確認假日排除出 `expected_days`/`gaps`。細節見 CLAUDE.md 同名段落。**緊接著「產生全市場研究報告」502（2026-08 修）**：回補真的補齊 143 天全市場資料後，`_market_research()` 兩個原本被稀疏資料掩蓋的成本才現形——OHLC 查詢沒有日期範圍（每次讀整張 `stock_ohlc`）、`research()` 完全沒快取（資料沒變也每次重掃兩市場）。單 worker 部署下同步計算太久觸發 Zeabur 502。修法：OHLC 查詢限定 `[dates[0], dates[-1]]`（`forward_return()` 結構上只會查這個範圍，行為不變只是不再多讀）；`research()` 用既有的 `data_version` 當 `ai_cache` 鍵，資料沒變直接回快取。**但這兩項還是 502**（Zeabur 共享 CPU 被節流，第一次算不完就被砍→永遠寫不進快取→每次點都 502），2026-08 再改成**背景執行緒**：`POST /api/stock-flow/research` 永遠立刻回 `status`（`ready`/`computing`/`error`），計算在 `_run_research_job` daemon 執行緒跑（自己開 sqlite 連線、算完寫快取），前端 `pollInstResearch` 每 3 秒輪詢到 `ready`。實測首個 POST 16ms 回 computing、不再阻塞。**教訓：請求裡不要放無界時間的同步計算——移到背景讓請求恆為毫秒級。**細節見 CLAUDE.md 同名段落。

**木質／木率（`analysis.py`，2026-08）**：從 XScript 還原蘭弦「蘭質／蘭值」後演化成的自家版「籌碼×基本面」評分（不是選股訊號）。三純函數 + 三常數，常數是規則的單一權威版本 → `GET /api/scoring-rules` → 設定頁唯讀顯示，前端不得寫死（同 bands）。`蘭值=蘭質÷本業PE×100`（已兩處證實）。`lan_score(financials)`＝忠實還原 15 項（`LAN_SCORE_ITEMS` 合計必 15；充足性守衛只查用到的季別 index；`cash_content` 除零判 0）——**Stage 1 先鎖邏輯、未 wire**，Stage 2 接季報源後取代木質的財報分並可回算比對 CSV。`mu_score`＝木質＝財報分（Stage 1 用匯入 `lan_score`）＋籌碼四訊號各 +1（`MU_CHIP_ITEMS`），0–19。`mu_value`＝木率＝木質÷本業PE×100 ＋**品質閘**（木質<`MU_QUALITY_FLOOR`(10) 時 value 歸 0、raw 保留）。並存對照：`attach_mu(row)` 是唯一入口、`filtered_picks` 逐列呼叫，選股表在蘭值/蘭質旁加木率/木質；分數非漲跌 → 中性藍 `--info`、不碰紅綠/琥珀。細節與 Stage 2 四項改良見 CLAUDE.md 同名段落。

**自算籌碼/基本選股（`view-self-screen`，2026-08）**：獨立選股頁，候選池＝**全市場**（`universe = {**_otc_industry(c), **_industry_map(c)}`，公司基本資料給類股/股名/**已發行股數**）、篩選與分數**全自算、零 CSV**、**不動 `filtered_picks`**（平行入口）。`GET /api/picks/self-screen?mu_value_min=&mu_score_min=`。自算計算只有一份權威版本 `selfcheck._row_self(code, shares, ...)`——`build_selfcheck`（傳 CSV 股本×1e7 維持對照口徑）與 `build_self_screen`（傳真實股數）共用，既有 selfcheck 測試＝重構護欄。`build_self_screen` 回 `heatmap`（大戶增比>0 依類股：版塊＝**本週成交額**、顏色＝**大戶增比強度**紅色漸層、標題＝週成交額 **WoW**，`renderSelfScreenHeatmap` 仿 `renderHeatmapDetail`）＋ `rows`（`analysis.screen_pass`＝營收年增>0∧W55∧大戶增比>0∧人數降比<0∧推估EPS>0∧木率>門檻∧木質>門檻，依木率降冪，欄位同 SC_FIELDS）＋ `coverage`。門檻在設定頁可調（預設 `analysis.SCREEN_MU_VALUE_MIN`=50/`SCREEN_MU_SCORE_MIN`=9，經 `GET/POST /api/settings` 的 `screen_mu_value_min`/`screen_mu_score_min`）。`db.weekly_amounts()` 用**同期**比較（上週只取前 N 交易日對齊本週至今，否則週中 WoW 恆負）。**dev DB 沒季報/OHLC≥55 → 本機 `picked` 恆 0 是資料稀疏不是 bug**；list 由單元測試鎖、前端靠注入假 rows 驗證。細節見 CLAUDE.md 同名段落。

## Data-source quirks (would trip you up)
- **TWSE**: ROC (民國) dates = year+1911. `T86` (per-stock 三大法人) is **上市 only**; OTC uses TPEx. Direct RWD endpoints take a `date` param.
- **TAIFEX**: official CSV downloads (`dlFutDataDown`, `futContractsDateDown`) need **GET-cookie-then-POST**, ≤~30-day chunks, and `.decode("ms950")`.
- **TDCC (集保)**: opendata `getOD.ashx?id=1-5` returns **the current week only** (trend accumulates weekly via `updater._accumulate_custody`, new-week-only). Requires `verify=False` (their cert lacks a Subject Key Identifier). Stock codes are **space-padded to 6 chars** — `.strip()`.
- **TPEx (櫃買)**: `dailyTrade` by date; fields are parsed **by fixed column position** (the field labels 買進/賣出/買賣超股數 repeat and can't disambiguate groups).
- **yfinance**: flaky / rate-limited from datacenter IPs → `kline._history` retries; the index K-line falls back to TWSE `MI_5MINS_HIST` OHLC; `.TW`→`.TWO` fallback covers OTC; `intl.fetch_intl_indices` falls back to the direct Yahoo v8 chart API (no cookie/crumb handshake — the part that fails on datacenter IPs; Stooq CSV endpoints are dead, 404).
- **`kline._sanitize_series` 的跳動門檻會連鎖丟掉整條尾巴（2026-09 修）**：被拒時 `last` 不更新，所以一根壞值會讓後面**每一根**都相對它跳太多而被拒。實測 3022：41.7 那根相對前一根只跌 30%（低於 35% 門檻因而**被接受**），之後 5 個月的真實資料（~60→95.9）全數丟棄，圖表停在 41.7、停在四月，看起來像「資料沒更新」——**但 DB 是完整的**（`/api/ohlc/coverage-for` 顯示 643 列、末日 09-04、零空收盤）。兩道護欄缺一不可：`just_rejected`（拒絕過一根後下一根不套門檻）＋ `_adjacent`（>5 天視為有洞，跨洞不套「單日」門檻）。**刻意不收緊門檻**：除權息的合理大跌可能 >10%，收緊會誤刪真實走勢（測試已鎖住這個取捨）。教訓：**「畫面沒資料」先分清是資料層還是顯示層**，別急著回補。
- **個股 K 線的量單位一律「張」**：yfinance 的 `Volume` 是**股數**、`stock_ohlc.volume_lots` 是**張**，差 1000 倍；在 `fetch_kline` 除 1000（只動個股路徑，指數另有口徑、有測試鎖住）。不統一＝「本機正常、雲端差 1000 倍」。另：`get_ohlc_history` 一定要 SELECT `volume_lots`——量能窗格其實早就做好了，只是沒帶資料出來，柱全 0 看起來像「功能沒做」。
- **備援不能是「全有全無」——主來源可用 ≠ 主來源是最新的。** `/api/index/kline` 原本只在 yfinance 回不到 5 根時才改用官方 TWSE，於是 yfinance 只是**落後一天**時完全沒有補救：實測 2026-08-06，`^TWII` 只到 08-04 而 TWSE `MI_5MINS_HIST` 已有 08-05。症狀是「大盤×籌碼對照」的籌碼窗格有最新一天、K 線那格卻是 `'-'` 佔位——而那天通常正是使用者最想看的一天。`kline.merge_tail(base, rows, interval)` 只補**嚴格比主來源最後一天更新**的列（既有日期不覆蓋也不重複，缺收盤價的列跳過，沒得補就原樣回傳同一個物件、不重跑波浪），官方那份走既有的 `idxohlc:{YYYYMMDD}` 逐日快取所以一天最多一次網路呼叫。**加任何「主來源 → 備援」的降級路徑時都要問：主來源只是落後而不是掛掉時，會發生什麼事？**
- **國際指數的「歷史」與「今天」是兩條路**：歷史走 yfinance（Zeabur 被 429 擋，只剩 gold/jpy/twd/btc 靠它）＋FRED（`vix`/`n225`，**慢一天**）＋**Nasdaq 公開 API（`sox`，2026-08 換掉長期斷線的 yfinance 路徑，見 `sources/nasdaq.py`／`updater._backfill_intl_nasdaq`，免金鑰、不受 Yahoo 那個 IP 封鎖影響）**；kospi 沒有可靠免費歷史源，維持現狀。**當日那一格**走 `updater._backfill_intl_tv` → TradingView scanner 帶日期快照（sox/vix/n225/kospi）。scanner 的 `time` 是該根日 K 的**開盤**時間戳（實測 SOX 09:30 NY、NI225/KOSPI 09:00 當地），**「日期解得出來」不等於「那一場收完了」**——09:05 台北打回來的 NI225/KOSPI 就是進行中的盤中值。所以 `intl.TV_DATED` 每個代碼自帶場次收盤時刻、由 `session_closed()`（ZoneInfo 比較，自動處理夏令時間）＋30 分緩衝把關；`INTL_SAME_DAY` 只填 `D == S`，其餘填所有 `D > S` 的洞，全部只填 NULL 不覆蓋。亞股時段回 `filled: []` 是**正確**結果。**`_backfill_intl(conn, intl_tickers)` 在 `intl_tickers` 濾完後若剛好變空會炸 SQL 語法錯誤**（`cols` 空字串 → `SELECT date,  FROM ...`）——把 `sox` 也排除到「有頂替來源」清單後才踩到，現已在 `keys` 為空時直接回傳 `[]`。

### 個股日線「本機抓→匯入雲端」與 Windows 雙擊工具（2026-09）

**Zeabur 打不動官方 OHLC 來源**（第三次同型問題，前兩次 Yahoo／mopsfin report）：本機打 TWSE `MI_INDEX`／櫃買 `dailyQuotes` 每個日期都拿得到（09-04 上市 1,083 檔），雲端 `backfill_ohlc` 卻連續失敗到兩市場熔斷。解法沿用季報那套：`GET /api/ohlc/pending`（交易日曆取自 `market_daily`，它仍每天更新，所以不會把國定假日誤判成失敗）＋ `POST /api/ohlc/import`（COALESCE，**非空值覆蓋**，錯值也修得掉）＋ `scripts/sync_ohlc.py` / `sync_ohlc.bat`。

**`pending` 的判定是「日期層級」（指標股有列就算有），回答不了「這一檔有沒有」** ——實測回報「缺 0 天、最新 09-04」而某檔仍停在四月，使用者完全卡住。兩個補救：`GET /api/ohlc/coverage-for?codes=…` 分辨「整列不存在／列在但收盤 NULL／有值但過期」（`last_close_date` 才是圖表看到的那天）；`force_days`＋`before` 游標強制重抓最近 N 個交易日（**沒有游標會每輪拿到同一批最新日期原地打轉**）。`backfill_ohlc` 仍有兩個已知缺陷：終止條件只看已存交易日**數量**不看**連續性**（有洞照樣 `done: true`），熔斷只丟 `exhausted: true` 不說原因。**`stock_ohlc` 稀疏是常態**（643 列橫跨 9 年＝不到三成日子有列），判斷「夠不夠」要看 `rows` 相對日期範圍。

**Windows 雙擊工具四個坑**（邏輯都對，只是在使用者環境跑不起來）：(1) **cp950 主控台印 `✓` 會 `UnicodeEncodeError` 炸掉整支腳本**——資料都匯入成功了卻以 traceback 收場，所有 sync 腳本加 `sys.stdout.reconfigure(errors="replace")`。(2) **`Get-Credential` 彈窗會被主控台蓋住**，使用者只看到一片空白，三支 `*_click.ps1` 全改 `Read-Host`。(3) **`New-Object Type($u,$p)` 會被誤解析**、userName 傳不進去，**必須 `-ArgumentList`**。(4) **不要用 Win+R 一行指令**（踩兩次：型別名稱被截、`--user` 拿不到值）——要帶參數就做成 `.bat`。驗證法：把目標 py 暫時換成印 `sys.argv` 的版本、跑**真正的** `.ps1`（跑完還原，`git diff` 應為空）；`Read-Host` 不吃管線輸入，要測互動路徑得用 `-Command` 先定義 `function global:Read-Host` 樁。

### 自算選股第二輪與杯柄流動性（2026-09）

- **自算 picks 進 `signal_ledger`**（`ledger.record_self_screen_signals`）：前瞻報酬不能事後回補，晚接一天歷史就永遠少一天。`signal_date` 用最新 CSV 日（與 `filtered_picks` 同一天才好對照）；**進場價取訊號日當天收盤，不可用最新收盤**（否則等於未來價、報酬灌水）。只掛每日排程，不進請求路徑。
- **日期基準**：籌碼選股用 `chip_snapshot`（手動上傳）、自算選股原本用 `market_daily`（每日自動），實測差 09-04 vs 08-28。`/picks/self-screen` 加 `date` 並**預設最新 CSV 日**。**兩個並排比較的頁面，日期基準必須來自同一張表。**
- **融資3日是參考欄、不進計分**（使用者決定，避免動搖木質 0–19 刻度與門檻）：`db.margin_3d_map`＝餘額(最新)−(3 交易日前)，**餘額是存量只能相減**（抄 `institutional_3d_map` 的 SUM 會大一個量級）。**刻意不著色**——融資減少是籌碼清洗不是下跌。
- **7 條件勾選交叉檢視**：`analysis.SCREEN_CONDITIONS` 是唯一權威清單、由端點送前端渲染；`screen_pass(conds=None)` ＝全部套用＝原本行為；**狀態不持久化**（每次回到預設，避免隔天忘了自己關過某一關）。
- **杯柄流動性濾網**：`patterns.avg_recent`／`filter_liquid`，門檻預設日均額 3,000 萬、設定頁可調，**必須進 `cuphandle:` 快取鍵**（否則調門檻拿到舊結果）。**`filter_liquid` 要 fail-open**：整批都量不到流動性時不過濾——預設開啟時 4 個既有測試被刷光，正暴露「production 缺量能會安靜清空畫面」。建議部位吃 `POSITION_ADV_CAP_PCT`(5%) 日均量上限（實測大華 44→6 張）。「量太少」與「線很奇怪」是**同一個根因**：冷門股的連續同價＋極端報價讓均線出現方塊平台。
- **版圖只畫前 50 大子產業**（`SS_MAP_LIMIT`）＋標「顯示前 50 / 共 N」；版塊的「大戶買 N 檔」要帶限定詞——同畫面兩個不同定義的同名數字必須各自說清楚。

## Config (`config.py`, via .env / env vars)
`GEMINI_API_KEY`, `SPR_SCHEDULE_TIME` (default 21:00), `SPR_DB_PATH`, `SPR_DATA_DIR` (Date/), `SPR_ENABLE_SCHEDULER`, `TZ`. On any non-Taipei host, `TZ=Asia/Taipei` is mandatory — the data-date/schedule logic uses naive local time.

LINE push (`line_push.py`): `LINE_CHANNEL_ACCESS_TOKEN` (Messaging API **broadcast** — the user's OA has only themselves as friend; LINE Notify is discontinued). `SPR_SCHEDULE_TIME`（預設 21:00）推每日完整版；`SPR_WEEKLY_PUSH_TIME`（預設 17:00，固定週六）推週報。**`SPR_LINE_PUSH_TIME`／16:00 速報 job 已於 2026-08 移除，設了不會生效。** Non-today data auto-skips pushes; `POST /api/line/test` forces one. Never expose the token (settings returns `line_configured: bool` only, plus `line_quota_paused: bool` for the auto-pause guard above).

Security (`docs/SECURITY.md`, P0+P1+P2 done): `SPR_BASIC_USER`+`SPR_BASIC_PASS` enable a global HTTP Basic Auth middleware (both must be set; unset = off for local dev) — gates all routes incl. static. A second middleware always sets security headers (CSP allowing self + jsdelivr for ECharts, no unsafe-eval; X-Frame-Options DENY; nosniff). Frontend must `esc()` any external/CSV string before innerHTML (XSS). `data_dir` from settings is whitelisted to `REPO_DIR`/`SPR_DATA_DIR` via `_dir_within`. CSV upload capped at 10MB + extension allowlist. `db.backup_db` (SQLite online-backup API, rotate 7) runs in the 21:00 job + `POST /api/db/backup`. Rate-limiting (M2), TDCC's `verify=False` (M4), and unsanitized error detail (L4) are deliberately deferred/kept — each re-evaluated post-auth and judged low residual risk (see SECURITY.md for the reasoning per item, not just "not done"). `requirements-lock.txt` is a `pip freeze` snapshot for audit reference — regenerate via pip-audit when touching requirements.txt, then uninstall pip-audit itself so its transitive deps don't pollute the lock file.

### 新聞第二輪：10 則、平日/週末分流、夜盤、擴充延伸閱讀（ui45，2026-09）

- **時段分流**：原本四個 news job **都沒設 `day_of_week`，等於每天都跑**——週末的「盤前早報」與「收盤快訊」其實在報上一個交易日的舊事。改為平日 07:00/12:00/17:00/21:10、週末只留 12:00/21:10。
- **每市場 6 → 10 則**：`gemini.py` 的 prompt、`_PUSH_PLAN`、`web/app.js` 的標籤與每市場列表上限四處同步。**CLAUDE.md 原本那條「前端靠 `｜6 則精選` 字串比對切分市場卡片、改則數會失效」已經過期**——程式後來改成比對旗標/名稱（`line.includes("🇹🇼") || line.includes("台股")`）、後端只看 `startswith("####")`，則數不再是切分依據。已加測試把這個穩健性鎖住。
- **台指期夜盤**（`taifex.parse_tx_night_price`）：期交所 Q_FUT **每個月份回兩列**，用 `TradingSession` 區分「一般」與「盤後」，盤後列的 `OpenInterest` 是 `'-'`（既有 `parse_tx_price` 正是靠這點排除它）。兩道防呆缺一不可：
  - **只在平日 17:00／21:10 顯示**（`_should_show_night`）。週末不顯示是使用者的決定，也正好避開資料誠信問題——週五夜盤到週六 05:00 就結束，週末能拿到的必然是上一個交易日的收盤；07:00 夜盤已收、12:00 還沒開，本來就沒有「當下夜盤」。
  - **日期對不上就整行不出現**。實測 2026-09-06(六) 21:58 打 Q_FUT，回的是 **20260904(五)** 的數字——端點給的是「最後一個交易時段」而非即時。不比對日期就會在週一早上把上週五的夜盤當成當下（同 `pick_close_for` 的規矩）。
- **延伸閱讀從「每市場 1 條、只顯示媒體名」擴充為「每市場 3 條、顯示原始標題」**。使用者原本要的是**每一條 bullet 都掛連結**，討論後改成這樣，理由是**歸屬正確性**：摘要是 AI 綜合改寫，一句可能併了兩三則，「這句出自哪一篇」沒有唯一答案；就算讓模型輸出來源編號、由 Python 附網址（網址不會亂編），仍擋不住「編號選錯」——結果是連結看起來正常、點下去卻是另一篇，而**讀者不點開根本發現不了**，那比沒有連結更糟。改成獨立列出原始標題後歸屬 100% 正確，「延伸閱讀」這個名稱也終於名實相符。實測 9 條約 3,145 UTF-16 units，整則訊息約 6,400 → 會被 `split_message` 切成 2 則（**它在換行處切、不會切壞行內連結**，這點先前擔心過但查證後確認安全）。


### 關鍵數據：prompt 與過濾器互相抵銷（ui46，2026-09）

- **這一格結構上幾乎不可能有內容**：prompt 說「僅填快照或標題中的數字」，而 `useful_data` 的工作正是把**這兩類全部剃掉**。兩邊各自合理，湊起來是死路。實測 2026-08-18 真實輸出 18 則：5 則模型寫「來源未提供」、8 則被剃、**只有 5 則（28%）送得出去**；但同場 58 則**原始標題有 35 則含數字**——瓶頸在 prompt 的任務定義，不在過濾器。
- **prompt 改成「補回你濃縮標題時捨棄的數字」**（從原始標題挑「你自己標題沒寫進去」的量化事實），並把過濾規則前移進 prompt（不得複述自己標題的數字、不得複述加權指數／成交金額／台指期／日經／費半／VIX），沒有就寫『無』。
- **`useful_data` 改逐子句判斷**，四關：不含盤面欄位名 → 有數字 → 不是標題已講過 → 不是盤面已講過（新增 `snapshot_numbers`，`telegram_digest` 多一個選用 `snapshot` 參數）。**分隔符要含「，」**——實跑輸出用逗號，舊版只切「、」會讓一個盤面子句拖垮整串。
- **誠實標註**：逗號 bug 是真的但在該樣本救回 0 則，改善要靠 prompt。**不要因為「修了一個真 bug」就宣稱改善幅度**。
- **網頁把「關鍵數據：無」整列隱藏**（`NEWS_NO_DATA`）。`無` 這個選項**必須錨死**否則「無償配股 12 億元」會被吃掉；`來源未提供…` 後面還有字，不能用同一種錨點。實跑真實輸出驗證 18→13 列、其餘 78 列未受影響。
- **快取版號進 `news:v8:`**（含 `headlines_logic` 掃的鍵）。

### 下週行事曆（週日 21:10，2026-09；純推播，無前端改動故不跳 ui 版號）

`calendar_events.py`（純組裝）＋`sources/econ_calendar.py`（四來源 parse/fetch）＋`api/news.py` 的 `_should_show_calendar`／`build_week_calendar`／`render_calendar_block`。只掛週日 21:10。

- **唯一風險是「日期寫錯」，而錯的日期讀者無從發現**。紀律：**日期一律取自官方排程，不用可計算規則推算**。實測「非農＝每月第一個週五」2026 年 12 個月會錯 4 個月（2026/01 發布在 **2/11 週三**、2026/06 在 7/02 週四撞國慶）。改抓 BLS `bls.gov/schedule/news_release/{cpi,empsit}.htm`，**只取日期不取時間**（08:30 ET 隨日光節約在台北 20:30／21:30 跳動）。
- **台指期結算（第三個週三）刻意不納入**——遇假日位移，性質同上面被否決的規則。
- **FOMC 的月份與日期必須同一個 match 成對擷取**。寫成「兩份 findall 再 zip」實跑得到 4 筆，**只有第一筆是對的**，其餘是交叉配對出來的假日期。正解 8 場，取「27-28」的**第二天**（決議日），`*`＝含經濟預測；一頁多年度，要先切年度區塊。
- 法說會走 **`mopsov`**（`mops.twse.com.tw` 回 FOR SECURITY REASONS），用市值濾到 3,000 億；美股財報走 Nasdaq `api.nasdaq.com/api/calendar/earnings`（免金鑰、**自帶 marketCap**，門檻取代手動名單）。**同一天同一檔要去重、且去重排在限額之前**（實測廣達同日兩場會吃掉名額）。
- **`macro`（FOMC／CPI／非農）標粗體＋底線、法說會與財報素面**（使用者要求）；沿用推播內文既有的哨兵機制，相鄰兩段會併出 `____` 讓 Telegram 整則無聲退純文字，每筆自成一行所以不相鄰（有測試鎖住）。
- **日股財報走 JPX 官方 xlsx**（檔名帶日期會變，連結要從頁面解析）；那份檔**沒有市值**，用既有的 TradingView scanner 補（`market_cap_basic` 實測是**美元**，故與美股共用門檻）。JPX 只公布已結束的會計季 → 可見視窗約 6～8 週，日本淡季整週空白是正確結果（實測 8/25～10/23 全市場僅 1 檔過 $50B，那週確實有亮）。
- **三市場統一「名稱（代號）」**（使用者要求補股號）；美股原本只有 ticker，`clean_company_name` 剝掉 Nasdaq 的法律後綴（長後綴要排前面，否則 `Corp.` 吃掉 `Corporation` 前半）。
- **MSCI／富時查證後無可靠免費端點**（MSCI JS 渲染、FTSE 404）→ 只能手填每年會過期的表，**使用者決定不放**，這兩類完全不收。要加回來也只能手填＋標明涵蓋範圍，不可憑規則推算。
- 空的一週回空字串。美股財報空窗期整週掛零是**正確**結果（實測 09/14 那週最大僅 TCOM 260 億美元；10/26 那週 AAPL／AMZN／TSLA 全在）。
- **跨年那一週要抓兩個年度的排程。** 只用起日那一年（`int(start[:4])`）的話，
  12/28～01/03 那一週落在 1 月的事件會**安靜消失**——一年只出現一次，而且沒有任何
  跡象顯示少了東西。`years = {int(start[:4]), int(end[:4])}` 逐年抓、逐年快取後合併。
- **測試不可以「星期天跑跟平日跑不一樣」**：`slot=evening` 的既有測試在週日會真的連外，`tests/conftest.py` 用 autouse fixture 把 `build_week_calendar` 樁掉，另補兩條鎖住接線。守衛做過反證（覆寫 fixture 後實測 12.2 秒＝確實在連外）。
- 兩層快取（`econcal:{year}`／`weekcal:{start}`）都讓 `refresh=1` 穿透——週日排程本來就帶 refresh=1，年度排程每週重抓一次是刻意的（BLS 會改公布日）。

### 細分類凍結成獨立表（`sub_industry_ref`，2026-09）

使用者決定**停止每日上傳 XQ CSV**，先處理唯一無替代的欄位：細分類（1,777 檔／530 種）。

- **查過的公開來源不能用**：產業價值鏈平台（`ic.tpex.org.tw`，官方免金鑰）涵蓋 97.9%，但只有 **69 個分段**（XQ 是 530）、**35% 的公司同時屬於多段**，而且語意是「出現在產業鏈的哪些環節」而非「屬於哪一類」——嘉泥被列進「貨櫃航運」「金控業」。**不混用兩套分類法**：泡泡圖按細分類分群，90% 叫「記憶體」而 10% 叫「石灰石」會讓整張圖失去意義。
- `sub_industry_ref(code PK, sub_industry, source, updated_at)`；`seed_sub_industry_ref()` 取每檔**最新日期**的值，**空字串／NULL 絕不洗掉既有值**。`csv_import` 仍會呼叫它（凍結不等於封死）。
- **一次性遷移放在 `init_db`**（表為空才做，lazy migration 慣例），**只做一次**——非空之後不再回頭覆寫，免得舊 CSV 蓋掉新值。
- 順帶解掉一個會持續惡化的成本：舊版每次全表掃 `chip_snapshot`（每上傳一次就長一截），實測 **35.2ms → 1.7ms**。
- **刷新掛在資料寫入邊界（`insert_chip_snapshot`）而非 `csv_import`**——掛呼叫端會讓其他寫入者悄悄落後（既有 `build_self_screen` 測試當場掛掉）。全表重掃是刻意的：補匯入舊 CSV 時仍要保持取最新。
- **核心契約有測試鎖住**：`chip_snapshot` 清空後 `sub_industry_map` 仍回得到值。凍結表對現在全市場 1,974 檔涵蓋 89.8%，缺的退回官方類股、由 `coverage.with_subindustry` 揭露。
### 籌碼選股頁保留但標出 CSV 已凍住（ui48，2026-09）

- 使用者決定**停止上傳但保留這一頁**。它原本只有日期下拉選單，看得到日期卻沒有參照——停止上傳後會安靜顯示同一份選股。
- `GET /api/snapshots` 加 `market_date`／`behind_days`（**任一邊缺回 None，不回 0 假裝很新**）；落差用**日曆天**（同 renderFreshness）；`CSV_STALE_DAYS=2` 才提示（落後 1 天是常態）。
- **沿用既有 `.freshness.stale`**，不另開樣式——第一版自寫 `.csv-stale` 還用了不存在的 token `--accent-warn`（靠 fallback 僥倖正確）。全站琥珀是 `var(--accent)`。
- 實跑四種狀態驗過，只有「落後 ≥2 天」會顯示。

- **⚠️ 停止上傳 CSV 會凍住哪些地方（2026-09-17 查證更正）**：舊記載的「公開總覽」「Telegram 今日精選」是錯的（公開總覽不讀名單；Telegram 新進榜讀自算快取）。族群交叉選股與杯柄 ⭐ 已改優先讀自算（見「選股名單來源」）。**刻意維持 CSV**：籌碼選股頁與上傳、自選股入選紀錄 `_picks_index`、LINE 週報與 `週報` webhook、21:00 `public_summary`、`ledger.record_daily_signals`、`traders/ss.py`（要 CSV 的月增／累增欄）。

### 自算選股改「每日排程預算＋快取」（2026-09）

- 拆成 `compute_self_screen`（貴：全市場 ~2,000 檔逐檔自算＋版圖）與 `build_self_screen(precomputed=)`（便宜：套門檻排序）。門檻/勾選不影響貴的那半 → 一份快取服務所有組合（勾選有 2^7 種）。**等價測試**＋**JSON round-trip 測試**（要進 ai_cache TEXT）鎖住。
- **只存最新一天、一列**（`selfscreen:v1`）：實測一份 729 KB，每天存一列＝一年 180MB。日期對不上一律當沒有，端點回 `precomputed: bool`。
- **自算選股平日 17:30／18:30／19:30 提早算（要求 20:00 前）**：`early_self_screen` 週末略過、已算好略過，否則 `stock_flow.update_day(今天)` 後 `refresh_self_screen_cache(day=今天)`。**當天上市／上櫃的行情與三大法人都 `complete` 才算**（`self_screen_missing_inputs`，21:00 那條也吃）——`institutional_3d_map` 取「有資料的最近 3 天」，沒到齊會拿昨天冒充今天。**訊號日要明傳 `signal_date`**：傍晚 `market_daily` 還沒有今天那列，否則今天的名單會記在昨天。`margin_3d_map(exact=True)` 讓今天融資未公布時空著。快取帶 `ready_at`（當天最早算好）與 `computed_at`。**頁面預設日期＝快取裡算好的那天**，不跟 CSV、也不直接用 market_daily 最新日。
- **覆蓋表 `complete` 是黏的（2026-09-17）**：21:00 `run_update` 會對同一天再跑 `update_day`，櫃買行情重抓失敗時原本把 17:30 的 `complete` 覆寫成 `failed`（資料其實都在），21:00 自算選股重算因此連兩晚 `data_not_ready`、「融資3日」天天空白。`set_stock_source_coverage` 現在不讓 `failed`／`holiday` 蓋掉 `complete`，`attempts`／`last_error` 照記。21:00 只有櫃買**行情**（~1.7MB）失敗、法人與融資正常，同 09-12／13 大檔傳到一半被切的型；此端點不支援 Range，續傳用不上，上櫃維持率當晚算不出、靠 heal 補（未處理）。
- **自算選股排程只抓到一個市場就整天跳過**（`refresh_self_screen_cache` → `skipped: "partial_universe"`，帶 `listed`／`otc` 檔數）。原本只擋兩邊都空，半邊失敗會照常存快取並**記前瞻訊號**，而 `record_self_screen_signals` 一天只寫一次、補不回來。上櫃名單是月快取，換月第一次才去櫃買抓，正是風險點。
- **LINE 週六日不推，只留週六選股週報**（使用者規則）。週末唯一漏網的是 `_check_update_result_and_alert` 的資料告警（`lagging` 有看星期幾、失敗來源那支沒有），已在函式開頭擋掉；每日排程週末照跑、只是不推，**也不記 `last_alert_key`**（否則週一同樣的失敗不會講）。「現在」走 `helpers._now()`，`tests/test_health.py` 以 autouse fixture 固定在平日，否則測試會星期天紅、星期一綠。Telegram 新聞不受此限。
- **櫃買 openapi 靜態檔要走 `tpex.get_resumable`（斷線續傳）**：2026-09-12／13 兩晚 21:00 伺服器回 200、宣告 496 KB 卻在 65～212 KB 處切斷（Zeabur 上是 `ReadError Errno 104`），換 UA／encoding 都一樣，約 10 分鐘後自癒；單純重試那段時間每次都斷。伺服器有 `Accept-Ranges`＋`ETag`，所以從斷點續傳。**必須帶 `If-Range`**（否則檔案重產時會拼出「舊前半＋新後半」且可能解析得過）、**回的不是從 offset 起算的 206 就從頭來過**、`Accept-Encoding: identity`。放棄條件是「連續 3 次零進度」不是總次數；HTTP 錯誤碼不重試。月營收上櫃與公司基本資料（t187ap03）都已改走它。
- **杯柄突破量能確認**：盤中哨兵加「當日累積量 ≥ 近 20 日均量 ×`patterns.BREAKOUT_VOL_MULT`(1.5)」。分母是 `screen_cup_handle` 已附的 `avg_volume_lots`、分子是 MIS 的 `v`（同為**張**），掃描改用 `fetch_mis_rank`（同端點同請求數，只是多解出 `v`）。**`volume_confirmed` 是三態**：True/False/**None（算不出）**，掃描端**只擋 False**，None 照發並標「量能未確認」——壓成布林會讓缺基準的股票安靜地不再發警示。`intraday_lots==0` 判 False（MIS 無成交時退回買價，零成交站上壓力正是假突破）。被擋下的仍留在 `cuppending`、不寫 `cupalerted`，量堆上來下一輪就發。閘放在 `hits` 不放 `crossing`（否則多賠一輪）。回傳 `held_by_volume`。**代價**：警示普遍變晚（累積量早盤本來就少），**刻意不猜盤中量能分布形狀**。
- **`signals_performance` 的來源清單（`api/trades.py::LEDGER_SOURCES`）漏過 `self_screen`**：記錄端與報酬回填端都不分來源，唯一會漏新來源的就是這個讀出口，而症狀完全無聲（資料照常累積、只是讀不出來）。**新增訊號來源時這裡一定要跟著加。** 每個來源另回 `signals`/`since`/`latest`，因為「有訊號但未到期」與「根本沒在記」要分得出來；`PERF_MIN_SAMPLE`(20) 放後端送前端，不在 app.js 複製。設定頁 `#set-signal-perf` 顯示三來源 × 5/10/20 日，**不用紅綠也不用琥珀**（統計讀數，暖色會被讀成「可以買」，同法人研究實驗室的取捨）。
- **`record_self_screen_signals` 改用市場最新交易日**（原本 `MAX(snap_date)`，CSV 一停前瞻追蹤就停——漏列的第五個凍結點）。排程算一次、ledger 吃同一份。
- **排程邏輯放 `api/helpers.refresh_self_screen_cache`**，不是 main.py 閉包——寫在閉包裡就測不到、也沒辦法單獨跑一次驗證，而它被 `except: pass` 包著、壞掉沒聲音。回傳可觀察的 dict（含 `skipped` 原因）。
- **畫面標出「來源：排程預算／現算」**，排程失敗時端點會安靜退回現算，不標就發現不了。
- 本機 0.42s 不具代表性（dev DB 無季報/OHLC≥55，貴的那半沒跑滿）。

### 週集保一公布就反映（custody_watch，2026-09）

週五 17:00–23:30、週六 08:00–21:30 每 30 分鐘只讀 TDCC 檔頭（`tdcc.peek_custody_week`），比最新**完整**
週新才抓（`_accumulate_custody`）並重算快取裡那一天的自算選股（不寫前瞻紀錄）；完整週判定
（`db._recent_custody_week_counts`，`custody_compare_weeks`／`custody_week_complete` 共用同一支查詢）
避開個股集保回補（背景自動補或手動 `/custody/backfill`）寫進的殘缺週（舊碼會被它擋一整週）。TDCC 沒有新週時，若**集保已入庫但名單快取沒用上**
（`_cache_custody_stale`：快取 `coverage.custody_weeks[0]` ≠ `custody_compare_weeks(c, 快取日期)[0]`，
即寫入後重算失敗／被重啟打斷／回 skipped）就補算一次、回 `retried: True`；比「那一天應該用的週」不比
資料庫最新週，快取是週四名單時不會每 30 分鐘重算全市場。檔頭已是新週、完整下載卻沒寫入是故障，丟
RuntimeError（run_job 記 failed），不再回 `not_stored`；丟錯前重讀資料表，另一條路同時已寫入（21:00
run_update、補跑與排程重疊的正常競爭）就照常重算、回 `stored_elsewhere: True`。週六週報 18:00–21:30 每 30 分鐘，本週集保到了
就送、同週只送一次（「讀標記→送出→寫標記」包在 `_WEEKLY_PUSH_LOCK`：週報一天 8 個 run_key，run_job
擋不住補跑與排程兩條執行緒同時送），21:30 照送並註明。「到了沒」看**名單快取實際用的集保週**
（`custody_is_current`），不看資料表——資料表一寫入就判「已是本週」，名單卻可能還是舊集保。週報一律附
一行集保說明（「集保 09-11→09-18」；沒用上時看資料表：已有本週寫「集保仍為 09-11 週（本週集保已取得，
名單尚未用上）」、也沒有才寫「集保仍為 09-11 週（本週集保尚未取得）」，快取不知道週別時只寫括號裡那句；
不寫「尚未重算」：快取可能是更早一天的名單（週五名單沒算出來、停在週四），週四本來就用不到週五的集保，
看到「已取得、尚未用上」要查最新名單的日期是否已到本週集保那天、以及 job_runs 的 custody_watch 重算是否失敗；
「已有本週」＝資料表最新完整週 ≥ 最新交易日所在週的週一，不是「比名單的週新」；寫「尚未取得」不寫
「尚未公布」，程式不知道 TDCC 有沒有公布）。前瞻紀錄只在訊號日當天寫。
新進榜比對基準＝帳本 ∪ 使用者看過的代號＝**Telegram 新進榜推播實際送出成功、且內文實際列出的代號**
（`_send_new_picks` 在送出成功後記 `ai_cache selfscreen_shown:{名單日期}`，平日與週報都記、同日聯集；
payload 的 `listed_codes`；finding #4＋fix wave 2／3）：否則週報已列成本週新進的股，週一會被帳本裡的舊週五
名單再報成「✦ 今天才進」。**只記內文列出的、不記整份名單**：週報只列本週新進、平日只列 ✦／NEW、都有 20 檔
上限，沒列出的重新進榜股與超過上限的股沒人看過，記了會讓它們週一不再是新進、永遠不被播出（fix wave 2 記
整份名單就是這個迴歸）。**但週一是否算新進仍以帳本為準**：訊號日當天已記入帳本的股（例如平日超過上限
沒列出的）之後不會再播出，只有帳本沒有的（訊號日之後重算才進榜的）週一才會播出。列出規則只有一份
（`pick_push.daily_listed`／`weekly_listed`，compose 與
`listed_codes_*` 共用、payload 在呼叫時讀 `DEFAULT_LIMIT` 傳給兩邊）。**不在重算時記**：週報送出之後的
週末重算帶進來的新進股同樣沒人收到過。送出失敗、預覽端點都不記；沒設定 Telegram 時基準退回只看帳本。
**送出後記錄失敗不讓 job 失敗**：否則 run_job 記 failed、平日推播被啟動補跑重送一次；改收進 `failed_steps`
→ partial（算跑過），錯誤仍進 job_runs／`/api/health` 並 log.exception。前瞻紀錄不受影響。查鍵用字典序
範圍不用 LIKE（`_` 是萬用字元），且嚴格小於當天（含當天的話當晚一檔新進都沒有）；空的不寫（沒列出就沒
看過）。**比對基準的日期只看帳本**：推播記錄只是列出的幾檔、不是完整名單——`previous_self_screen_codes`
取帳本 < 當天的最後一天、代號併上那天起講過的；`previous_custody_week_codes` 期間內帳本沒有名單就回 None。
否則週五名單週六才算出來（帳本沒有週五）、週報以它送出時，週一整份名單會被當成新進。
`slot_times` 支援 `minute="0,30"`（`_cron_minutes`）。頁面覆蓋率列標「集保 前週→本週・取得時間」，只有
一週完整時標「尚無前一週可比」。已知、刻意不修：TDCC 若在週五名單第一次記入前瞻紀錄之前就公布並被抓到
（custody_watch_fri 17:00 就開始抓；第一次記入是 17:30／18:30／19:30 裡第一個資料到齊的那次，三次都沒到齊
延到 21:00；平常 17:30 就已記入，之後公布的不會
用到），週五前瞻紀錄會用到收盤後的新集保（實測週六才有，且原本 21:00 路徑同樣依賴時序）；全新空庫只有
個股集保回補（背景自動補或手動 `/custody/backfill`）寫進的單檔週時完整週判定是相對的、單檔週會被當完整
（正式站已有全市場週）。細節見 CLAUDE.md 同名段落。
**⚠ 部署時間：這個分支第一次推上 main 不要落在週六 18:00～24:00**——舊版週報 run_key 是 `<日期>`、不寫
`picks_weekly_sent` 標記，新版 run_key 是 `<日期>:HH:MM`、靠標記去重；那段時間部署，重啟後的啟動補跑看到
新 run_key 沒有紀錄、也沒有本週已送標記，會把週報再送一次給訂閱者。

### `conn()` 不關連線：查證後刻意不修（2026-09）

- **先量**：引用計數不回收（相依套件有循環參照），50 個請求尖峰 **44 條**開著，`gc.collect()` 後歸零，**握著寫入鎖的 0 條**（反證：刻意不 commit 的連線抓得到）。**不是那次 `database is locked` 的成因**，是資源浪費不是鎖競爭。
- **試過的修法失敗**：contextvars 登記＋中介層關閉 → `sqlite3.ProgrammingError: created in thread X, this is thread Y`——同步端點在 threadpool 建連線、中介層在 event loop，`check_same_thread=True` 不准跨執行緒關。
- **更該記的**：我寫的 `except sqlite3.Error: pass` 把那個例外整個吃掉，中介層看似在跑、實際一條沒關且無聲。**替清理動作加寬鬆 except 之前，先問「它失敗時我看得見嗎」。**
- 剩下選項（拆 `check_same_thread`／改 89 個呼叫點／每執行緒延後關閉）都不成比例，**維持現狀**。

### `backfill_ohlc` 的 done／exhausted 判定（2026-09）

- **`done` 只數天數不看窗口** → `_dates_with(since=)`，**計數與掃描共用同一個 `floor`**。根因是兩者分家：production 643 列橫跨 9 年 → `643>=377` 判完成、迴圈沒跑、`added: 0`。**不是把 done 變成永遠 False**，窗口補滿仍回 True（有反證測試）。
- **一個天數說不出有沒有洞** → 回 `coverage.{twse,otc}` 的 days/oldest/newest/`max_gap_days` ＋ `window_start`。缺口不由程式下結論（週末 3 天、農曆年 ~10 天屬正常）。
- **熔斷不說原因** → 記 `exhausted_at.{twse,otc}`。**刻意不發明分類器**，只講事實：停在 1990 年是歷史底線、停在上週是來源出問題。`reset_ohlc_progress` 一併清掉新鍵。
- 實測真實本機 DB：`done: False`、`max_gap_days: 12`（舊版看不出那個洞）。

### 月營收告警查不出原因：例外被吞了兩層（2026-09）

- 使用者收到「revenue（查無資料或抓取失敗）」來問原因，**程式已經把答案丟掉了**。第一層在 `sources/revenue.py` 的 fetcher 自己 `except: return {}`，所以 updater 那層的 except 幾乎不會觸發——**只修第二層等於白做**。
- `fetch_*_revenue` 改成往上拋＋`raise_for_status()`（沒有它時 503 會拖到 `.json()` 才炸成 JSONDecodeError，看起來像我們解析壞掉）。
- `_refresh_monthly_revenue` 回 `{market: {count, error}}`，**「端點回 200 沒資料」與「抓取失敗」分開**（月營收 10 日前本來就可能還沒公告）。
- **告警要印 `source`**：月營收逐市場判定，只印 name 永遠看到「revenue」分不出上市/上櫃——`source` 一直都記著只是沒印。
- 兩條鎖舊契約的測試（回空 dict）**刻意刪改**；成功路徑的假 response 補 `raise_for_status`（替身跟著真實介面走）。

### 排程補跑 ＋ 執行紀錄表 ＋ logging（2026-09）

- **問題**：APScheduler 記憶體 jobstore；push 到 main → Zeabur 重啟，橫跨排程時間那一場整場消失、無告警（告警就在那支 job 裡）。**`misfire_grace_time` 救不了**：它只管「排程器活著但來不及跑」，重啟後 jobstore 不知道錯過了什麼。
- **`job_runs` 表**（`db.py`）：job_id／run_key／trigger／started_at／finished_at／status（running→ok／partial／failed；`interrupted`＝啟動時標掉上一個程序留下的 running）／error／note。`JOB_RUN_DONE=("ok","partial")`＝跑過了。每日排程清 60 天前。
- **`run_job`**（`api/helpers.py`）包每支 job：寫紀錄、進出各一行 log，**同 (job_id, run_key) 已 ok／partial／running 就略過**（Telegram 沒去重，靠這裡）。**真正的去重是 DB 唯一鍵 `uq_job_runs_running`**（partial UNIQUE `(job_id, run_key) WHERE status='running'`），先查只是省一次失敗的 INSERT；撞鍵 `IntegrityError` → skipped，**撞鍵後必 `rollback()`**（否則輸的連線握著寫入鎖、贏的那條 finish 時等滿 busy_timeout）。紀錄失敗不吞、往上丟。intraday_watch 的 run_key 是當下分鐘（每 5 分一個 key）；self_screen_early 三場各自 `日期:HH:MM`，17:30 data_not_ready 記 ok＋note，18:30 照跑。`main.py` 的 job **不再 `except: pass`**：`scheduled_job` 逐步失敗收進 `failed_steps` → partial；其他 job 例外直達 run_job。
- **排程規格唯一版本 `job_schedule(cfg, schedule_time)`**：註冊與補跑共用。`family`＝補跑分組、`catchup=False`＝不補（intraday_watch）。run_key：一天一場＝日期、多場＝`日期:HH:MM`。
- **標 interrupted 的啟動競態**：上一個程序留下的 running 要在 `start_scheduler` **之前同步**標掉（否則排程器先觸發時 run_job 會當「已在執行」略過、這場就沒了），而且 `mark_interrupted_job_runs(…, started_before=booted_at)` **只標本程序啟動前開始的列**（否則補跑執行緒會把本程序剛開始跑的那列標掉、再跑一次）。補跑也吃同一個 `booted_at`。兩條測試各自反證過。
- **啟動補跑 `catchup_missed_jobs`**：daemon 執行緒 `spr-catchup`（不阻塞啟動），依 `catchup_plan` 走 `run_job(trigger="catchup")`。**走同一支 job 函式**→ 週末不推 LINE、資料日≠今天不推卡片等守衛自動生效。**範圍（使用者拍板）：只補今天、同 family 只補最近錯過的一場**（整天停機晚上恢復只補 21:10 一場新聞；最近一場 ok 就整族不補）。
- `/api/health` 多 `jobs`（各 job 最近一列）。logging 取代 print（`spr`／`spr.jobs`／`spr.gemini`），cli.py 的 print 保留。
- **測試**：`tests/test_job_runs.py`；`test_health.py` 只改一處（`test_alert_deduplication_logic` 改呼叫 `app.state.jobs["daily_update"]`，排程器拿到的已是 run_job 包過、同日第二次呼叫會被去重）；`test_gemini.py` 三條 `capsys` 改 `caplog`。時間走 `helpers._now`；`main.py` 改成 `_helpers._now()` 呼叫時取（`from … import _now` 綁死的名字 patch 不到）。conftest autouse 樁掉 `catchup_missed_jobs`（否則每條起排程器的測試都會真的連外），要測補跑標 `@pytest.mark.real_catchup`。七個守衛都做過反證（含拿掉唯一鍵 → 3 條並發測試紅；並發測試把先查弄瞎、鎖換成不互斥仍只跑一次）。`test_alert_deduplication_logic` 樁掉六步＋網路絆線（socket 層記錄並拋，斷言零次；反證：樁換成真 `httpx.get` → 紅）。`pytest -k not_again` 會把 `not` 當運算子，反證要用完整名稱。

### 前瞻報酬回填修正（2026-09-16）

`update_ledger_returns` 兩個錯：(1) CSV 的 filtered_picks 代號帶 `.TW`／`.TWO`、`stock_ohlc` 不帶，
`code=?` 永遠對不到——production 7,576 筆 5/10/20 日全空（既有測試把日線代號寫成 `2330.TW` 蓋住了
bug，已改）；(2) 「第 N 日」原本數該檔日線筆數，缺一天就錯位，改用 `market_daily`（`taiex` 非空）
當交易日曆找第 N 個交易日、查那天收盤，缺價留空再補。補算已記下的訊號不算事後回補偏誤。自算選股
（代號正確）也還沒有報酬，原因未查明，部署後觀察下一次 21:00。

### 自算選股新進榜 Telegram 推播（2026-09）

平日 21:40「今日新進榜」（✦／NEW 分兩段）、週六 18:00–21:30（等本週集保，最晚 21:30）「本週新進榜」＋本週大戶買進前三子產業（大戶資料週更，
只放週報）；沒有就送「無新進榜」。欄位 收盤／漲跌%／木率／木質，最多 20 檔。`pick_push.py` 純函式組訊息、
`api/helpers.new_picks_push_payload` 備資料、`telegram_new_picks_job` 送出；只讀名單快取、新進判定與網頁共用
`ledger.annotate_new_entries`。守衛：daily 名單須是今天、weekly 須在本週；漲跌% 查基準日當天收盤、缺價顯示
`--` 不頂替。每列一行 inline code（``` 區塊在手機多一顆複製鈕、太長）＋名稱在後，**數字部分壓在 27 字元**（`ROW_WIDTHS` 4/7/8/5/3；原本 34 字元在手機上把名稱折到下一行，手機一行約 39 等寬字元）；表頭移到列表外當說明行（等寬區裡的中文表頭實測偏左）；加「集中：」同子產業 ≥2 檔；不提網頁；缺值用 ASCII `--`（em dash 手機會畫成全形）。預覽
`GET /api/picks/new-push-preview?kind=daily|weekly&force=1`（不送）。

### 自算選股新進榜標籤（ui54，2026-09）

`is_new`＝前一份使用者看過的自算名單（帳本最後一份 `signal_ledger` ∪ 那天起推播內文實際列出過的代號 `selfscreen_shown:{date}`，日期只看帳本，見 custody_watch 段）沒有這檔；`is_week_new`＝**上一個集保週期**
（以 `custody_compare_weeks` 的週五為界、從週五隔天起算，因為週五名單用的是舊集保）所有名單都
沒有。沒有比對基準就一檔都不標。顯示：兩者皆是＝`WEEK NEW ✦`、只有週＝`WEEK NEW`、只有日＝`NEW`
（第一版「同時符合只掛 Week NEW」讓今天的新進榜全被蓋掉，使用者回報後改）。視覺是半透明 HUD
晶片（第一版燙金被退回）：NEW 冰藍細框，WEEK NEW 青→紫漸層細框＋四角刻線，✦ 用 clip-path 畫。
掃描線只在資料載入時播一次。手機標籤換到股名下一行（凍結欄 179→110px）。
表頭「今日新進／本週新進」計數即篩選鈕（ui55，再按取消、與子產業篩選 AND、不持久化；計數在篩選前算，檔數顯示「3 / 5 檔」）。
版圖排名壓到名稱已修（ui56）：排名改回文字流、內容往下溢出，畫完 `fitMarketCells` 逐格量放不下就依序收 副標→排名→縮字→金額→名稱一行（寬高門檻猜不準，1600px 下舊版 5 格重疊、新版 0）。
版圖名稱被壓扁已修（ui57）：flex 子項預設會縮、名稱 overflow:hidden 最小高度 0，放不下時被壓扁而不是溢出，fit 量不到 → `.ss-market-cell > * { flex-shrink: 0 }`（1600px 舊 6 格、新 0）。

### 選股名單來源：優先自算、CSV 較新才用 CSV（ui58，2026-09）

- 唯一判定 `api/helpers.active_picks(c)` → `{source, label, date, rows, codes}`，族群交叉選股／杯柄頁／LINE 杯柄段／盤中哨兵共用。**快取日期 ≥ CSV 最新日用自算，CSV 較新才用 CSV**（快取可能連續幾天沒更新，不可蓋掉新 CSV）。**0 檔仍是自算那份**，不偷換 CSV。壞快取 log warning 退回 CSV。
- **呼叫端判斷「有沒有名單」看 `source`，不看 `codes` 空不空**：看 codes 的話自算 0 檔入選時「只警示入選股」會被安靜跳過、全部杯柄股都發 LINE（審查實跑抓到）。`has_picks`＝有名單，名單檔數另給 `picks_total`。`?date=CSV 日` 一律回那天 CSV（含剛好等於快取日）。手機交叉選股標題列在 `#view-rotation` 換行。杯柄測試要樁 `tpex.fetch_otc_names`，否則真的連外。
- 族群交叉選股：自算列的 `sector` 是細分類，對不上類股指數、也沒有 `industry` 欄（直接給 `picks_by_sector` 會安靜回空）→ 改查 `_industry_map`／`_otc_industry`，`_SECTOR_ALIAS` 補官方寫法 `農業科技`→其他；查不到類股回 `unclassified`。
- LINE 杯柄段只載一次名單（`cup_handle_screen_logic(picks=)`）；盤中哨兵有待監控股才載名單、被「只警示入選股」濾光時 note 寫出名單與日期。
- 所有文案寫出用哪份、哪天（`picks_label`：`自算籌碼/基本`／`籌碼/基本`）。
- **舊測試只種 CSV、正式站走快取**——`tests/test_picks_source.py` 種「只在快取」的股；10 個反證各自轉紅。

### 個股 K 線改 Lightweight Charts（2026-09）

**只換個股頁這一張圖**，其餘全部維持 ECharts。自架
`web/vendor/lightweight-charts.standalone.production.js`（npm `lightweight-charts@5.2.1`
standalone production 版，理由同 echarts.min.js 的 CSP 自架規矩）。資料格式轉換只在三支
純函式做（`lwCandleData`/`lwVolumeData`/`toLwLineData`），`time` 直接用 `"YYYY-MM-DD"`
字串。紅漲綠跌讀 `C.up`/`C.down`（LWC 預設綠漲紅跌，六個色欄位都要設，缺一個那部位就是
預設色），字型讀既有 `HM_FONT`。**授權要求的 attribution 標誌不可關**（純 DOM `<a>`
連結，實測不發網路請求）。滾輪縮放／拖曳平移／十字線都是 LWC 內建，不必自己接
`dataZoom`。艾略特波浪改 `createSeriesMarkers`＋`position:"aboveBar"`（視覺等價、非
像素級同款）。初始可視範圍用 `setVisibleRange`（日期字串）而非
`setVisibleLogicalRange`（邏輯索引）——後者實測會被 LWC 自己的最小柱寬限制悄悄改動起訖
值，且每次 resize 還會再漂移。查無資料時 `chart.remove()` 整個丟掉，不畫空圖。

**兩個實測才抓到的 bug**：(1) `chart.remove()` 不會清掉我們手動塞進容器的圖例／tooltip
覆蓋層，反覆「查無資料 → 有資料」會疊出重複 DOM——`disposeStockChart()` 補清這兩個
class。(2) `autoSize:true` 對「切走再切回個股頁」這種祖先 display:none→可見的轉場**是
真的競態**（同一份程式碼、同樣操作，實測有時 200ms 內修好、有時卡 1 秒以上），視窗縮放
與側欄收合這兩種連續變化倒是穩定——修法是 `showView` 切回時補一次確定性手動 resize
（短暫關 autoSize、用當下 `clientWidth/Height` 呼叫 `resize()`、再開回 autoSize），
6 輪反覆測試後每輪切回當下就是正確尺寸。**這正是「換函式庫仍要親自驗一次，不要假設
沒事」這次真的踩到的例子**，兩者都不是虛驚一場。

**複查再抓到三個**：(3) **圖表高度無限長大**——`.chart-big` 是 `.view`（flex column、
min-height:100%）裡的 `flex:1`，頁面內容超過一個螢幕後，autoSize 的畫布撐高 `.view`、
flex 再分給容器更多高度，形成迴圈（實測每 400ms 長 ~35px）。第一輪驗證時內容沒超過螢幕、
迴圈沒啟動所以沒抓到。修法 `#stock-chart { contain: size; }`，實測穩定 378px，拿掉即復發。
(4) `createSeriesMarkers` 每呼叫一次就多掛一個 primitive，丟掉參照不會卸下——改成只建一次、
之後 `setMarkers`。(5) tooltip 垂直方向要夾在容器內，否則游標在下半部時蓋到下方圖表。

**月線假缺口警示已修（ui52）**：`stock-note` 缺口門檻原本寫死 14 天，月K 相鄰就差 28~31 天、
每查必誤報。改成依週期查表 `KLINE_GAP_DAYS`（日 14／週 21／月 45），實測正常資料不警示、
挖掉一段仍會亮（反證三種週期都做過）。提示改指向 `scripts/sync_ohlc.bat`。

**個股頁三張圖合成一張（2026-09）**：K 線／量能／三大法人／集保四個窗格在同一張 Lightweight
Charts，共用時間軸與十字線。容器整體固定高度（桌機 560／手機 460px），四格相對比例由
`setStretchFactor(0.23/0.32/0.32)` 決定、隨容器等比縮放（實測桌機約 368/42.67/59.33/60px、
手機約 297.5/34/47.5/48.5px，不是兩組獨立常數）。籌碼一律貼到 K 棒（`snapToBars`／
`sumToBars`；週K/月K 的聚合就是貼齊時相加，整根沒資料是 null 不是 0）。法人堆疊柱是自訂
series（LWC 沒有內建）。每格左上角一行讀數取代 tooltip，y 位置逐格累加
`chart.panes()[i].getHeight()`（不是拿 stretch factor 比例反推，理由見 CLAUDE.md 同名段落），
`disposeStockChart` 要清 `.lw-readout`。**Task 10 另外抓到一個 ResizeObserver 失效案例**：
切頁再切回個股頁之後若視窗縮放跨過 600px 斷點，LWC 的 autoSize 仍會正確縮放容器，但讀數列
專用的 `lwReadoutObserver` 從此不再觸發，讀數卡在切頁當下的座標（用 git stash 對照修前修後
版本排除測試環境假象）；修法是在既有 window resize 的圖表清單裡補一個 60ms debounce timer
呼叫 `lwPaintReadouts(null)`（不能同步呼叫，resize 當下 LWC 自己的 autoSize 多半還沒處理完，
量到的是舊高度）。集保加 `total_shares` 算人均數（後端算，只進讀數列）。**集保窗格是
「400張↑% 柱狀圖」，不是兩條階梯線、也沒有人均數箭頭**（2026-09，使用者看過 production 後
指定比照 XQ；原本 35 支箭頭蓋住線，而兩個量差 10 個百分點、同軸互相壓平成平線）：只畫一個
序列且選 400張↑（自算選股的「大戶增比」吃的就是 `big400_pct`）；**往前填滿**每個交易日取
≤ 當天最近一週（52 根孤柱散在 240 天是破圖，實測 2330 日K 244 根柱、0 空洞，不是 52）；
**顏色比上一週不是比上一根**（反證：逐根比，週內非中性柱 172→0）；**紅增綠減是使用者知道
代價後指定的例外**，代價是同一張圖裡紅色有兩個意思（K 線＝股價漲、集保＝大戶增），持平與
第一週用中性色、亮色那組不是 upFill/downFill；**base 不設 0**（＝最小值往下留全距 15%，實測
最高/最低柱 40.5/5.3px；反證要 base=0 **並重灌資料**逼軸重新 autoscale，得 40.5/39.4px＝全部
等高），代價由價格軸 2 位小數刻度誠實標示（實測該格畫得下 2 個：87.50/90.00）。讀數列
400張↑ 排第一；**柱與讀數吃同一份往前填滿的對照表**（`custodyHistogram` 回 `{data, byBar}`）
——只填柱子、讀數仍查未填滿那份的話，週中會「有柱子卻寫『集保 —』」（實測 239 根柱有 188 根
中招）。該週 `big400_pct` 為 null 時不畫柱、讀數仍顯示千張大戶與人均，這是對的。
**`applyOptions` 會立刻重畫：順序必須先 `setData` 再 `applyOptions`**，反過來會在切週期時讓
舊資料對著新時間軸畫、丟 `Error: Value is null`（實測切時K 三個）。集保歷史改背景自動補（每檔每天
一次、同時只一個），端點回
`backfilling`，前端每 5 秒輪詢、最多 2 分鐘；手動端點 `/custody/backfill` 保留除錯用，
個股頁本身已無手動連結。

### 股期概況（`view-ssf`）＋ 原始保證金試算（2026-09）

盤後版新頁（v1 無即時，MIS 已探測可達留 v2）。`sources/taifex_ssf.py`（與 `taifex.py` 分開，
CSV 形狀/主力月規則/tick 級距都不同）：340 個合約代碼收斂成 320 個 root、~150KB/1,900 餘列。
`GET /api/ssf/overview`（五區塊：成交量前30排行表/量漲跌前20 K線/期現價差/未平倉增減/近10日熱力圖）＋
`GET /api/ssf/margin`（口數留前端乘整數，`fetch=True` 可連外）。個股頁（`#stock-ssf-margin`，
同 `#today-focus` `:empty` 做法）直接打這支端點讀 `by_stock`；**自算選股不一樣**（`SS_FIELDS`
的「股期保證金」欄，同「融資3日」取捨純參考不進計分）——改呼叫共用函式
`helpers.ssf_margin_index(c, fetch=False)`（cache-only，絕不連外，review I3），只認**標準
合約**（`is_mini` 為假），查無結算價回 `None`、**絕不退而求其次改用小型合約**（曾重現：2330
標準缺價時顯示小型的 32,832，只有標準真正金額 1/20）。`ssf_margin_index` 是 `rows`/`by_stock`
的唯一權威計算，端點與自算選股共用、差別只在 `fetch` 旗標。`ssf_daily` 表 PK `(date,root)`
（含 `oi_total`，見下）COALESCE upsert，留 60 個交易日；合約表/保證金表沿用 `ai_cache` 快取
（`_ssf_contracts`/`_ssf_margin_table` 都吃 `fetch` 旗標；讀取端也守衛筆數：合約 <300、保證金
缺 `stock_updated` 視為未命中，退回的舊快取一樣要過這關，不能原樣放行（final review #3）。
**只有保證金表有冷卻，合約表沒有**：抓一次失敗就進冷卻 15 分鐘，同 `_osfut_cooling_down`；
冷卻中或失敗都退回最近一次通過檢查的舊表，不再直接回 `{}`（final review #2））。
排程 `ssf_daily` 平日 17:15/18:15/20:15，行情/保證金/合約表三者獨立更新（合約表也要暖，否則
cache-only 路徑永遠拿不到資料）。**「已完成」＝`ai_cache` 鍵 `ssf_ready:{D}` 存在（D 真的被
寫入），不是「`ssf_daily` 有 D 這列」**（review I2）：D 沒發佈時仍寫回已抓到的前幾天，但回報
`data_not_ready`+`refreshed_prior`，不謊稱處理了 D——否則三個時段都「看起來成功」，
`/api/health` 的 `jobs.ssf_daily.note` 判斷哪個時段先到齊的方法就失效。`note` 是 `run_job`
存進去的回傳值字串化結果（`str(dict)`），不是巢狀物件——`ready_at` 要從這段字串裡讀，不是
`note.ready_at` 這種取值路徑（final review #5）。

**假成功防線**（既有 `taifex._post_csv` 全部會誤判為成功）：區間超過一月→UTF-8 616B 警告頁；
今天只有夜盤→**逐交易日**（非整個回應加總）檢查一般列數 ≥`MIN_GENERAL_ROWS`(1200，正常1629)，
否則排程 `[D-6,D]` 約 7 天重疊視窗（正常含 5 個交易日）會讓 5 天的量蓋過今天的不足。**表頭
真的改版時（`ValueError`）不再吞成 `[]`，原樣往上拋**（review I2）——與「非交易日只有表頭」
（表頭沒變，解析出 0 列）不同一種情況，不衝突。連線例外刻意往上拋不吞（同月營收教訓），由
`run_job` 記失敗留原因。

**漲跌% 參考價是前一日結算價非收盤價**：實測 CCF 202610 09-04 自算 4.38% vs 官方欄位
**4.80%**（參考價 125.0=09-03結算，非09-03收盤125.5）——直接用官方欄位。

**股期 tick 級距與現貨不同（僅1000–2500有差：股期跳1／現貨跳5，500–1000兩者皆跳1），價差
必須沿網格走**：實測 KBF 495/501=**11檔**（除單一tick得6或12皆錯）。期貨結算價(13:44-45)
與現貨收盤(13:30)本身不同步，
實測最多差5 tick，標題須註明；14檔ETF期貨到16:15（落差2.5小時）另標`late_session`。

**期現價差一列一標的、不是一列一合約**（review #2）：標準/小型去重原本保留成交量較高者，
小型量常較大會讓 2330 那列顯示「小型台積電」——改成依「該標的全部合約成交量合計」排名，
結算價/主力月固定取**標準合約**（`is_mini` 為假），標籤固定用 `stock_name`（非會加「小型」
前綴的 `name`）。

**調整後合約（`root+1`如CM1）只併成交量絕不併價格**（乘數非標準，實測見過2020/5965.5892等）：
量＝全部非價差列(含盤後/調整後)依root加總(官方STFTop10口徑)，價/結算價/OI只取`root+F`列。

**未平倉增減/K 棒 tooltip 的 OI 用 `oi_total`（前 30 表不顯示 OI；該 root 的 F 合約、一般時段、全部月份加總），不是
主力月自己的 `oi`**（review I1）：換月（結算日附近常態）當天主力月整個換掉，用主力月 `oi`
相減會把移倉算成假的大增/大減——實測真實總量僅 +500，主力月口徑卻算出 −8,500 並列進「減少
最多」。`ssf_daily` 新增 `oi_total` 欄，`oi` 欄語意不變。

**保證金四條紀律**（`margin_amount`）：(1) 必須`Decimal`+`ROUND_HALF_UP`不可`round()`——1,181
個(合約,月份)中**101個**不同，CAF 96,592.5→96,593、PWF 42,808.5→42,809。(2) 用官方兩位小數
比例欄不可`×1.35/×1.035`反推——467個不同。(3) ETF不套公式，用公布固定金額。(4) TMF固定
35,050（維持26,900）＝TX/20。寫入守衛股票≥290列/ETF≥20列(正常296/24)，**三段任一段不合格
整份回`{}`**（含指數段：`index_updated`存在且TMF原始保證金>0，原本指數段沒被檢查會讓半套
結果冒充成功）。

**保證金生效日只在CSV/HTML的「更新日期」，不可用OpenAPI的`Date`**（後者天天跳、非生效日）；
處置股臨時加成1.5/2/3倍常見，故**每天重抓**。價格輸入用「當日結算價」（官方僅盤後時段規定
前日結算價，日盤實際券商用最新成交價，20/20對得上）——畫面須標「以YYYY-MM-DD結算價估算，
實際以期貨商收取為準」。

**顏色：價差正負/OI增減/保證金金額都不著紅綠**（不是行情方向）；熱力圖白字用
`--up-fill`/`--down-fill`，`sectorColor`加`scale`參數(新呼叫端7%/舊呼叫端仍3%，10樣本驗證
不影響既有呼叫端)。

**2026-09-18 production 探測（`ssf_probe.py`，設計§8，先探測再寫程式，同 Yahoo/mopsfin/ohlc
backfill 前例，驗完即刪、本次任務已移除）**：7項全過含MIS即時（1,533檔）、14天區間1.65MB/
2.7秒未被切斷→v1全雲端跑不需本機抓匯入、回補同步分批夠用不必背景執行緒。**D的日盤資料確切
發佈時間仍未量到**：10:02當天請求只回夜盤列（一般0列）、約20:50本機回補已取得完整日盤資料，
落在此區間內尚未確認；17:15/18:15/20:15三時段維持不變（`/api/health`的`jobs.ssf_daily`
可查哪個時段最先成功）。

**資料源坑**：保證金CSV須`csv.reader`不可`split(",")`（"TPK Holding Co., Ltd."引號內逗號會
使第6欄起整排位移，筆數守衛296/24看不出來；`parse_index_margining_csv` 同一份 CSV 家族也補上
同樣的 `csv.reader`，review M5）；ETF區段真實標題是「標的證券為受益憑證之股票期貨契約」非
「…ETF之股票期貨」（fixture 要用真實標題）；每支擷取器都要`raise_for_status()`；**合理性
守衛除股票/ETF筆數、指數段外，也要查 `etf_updated`**（review M4，原本只查 `stock_updated`，
ETF 更新日那行解析失敗跟筆數門檻無關，會讓殘缺結果冒充合格逃出去）。

**計算/資料坑**：`count_ssf_dates`要數真實總數不可`len(dates)`（被熱力圖10日軸寬夾住永遠
≤10）；覆蓋率計數器(`no_stock_code`/`no_spot`)要走訪全部root不能在湊滿輸出上限時break（否則
資料越殘缺計數越接近0）；6488(環球晶)其實有股期，1234(黑松)才是無股期範例。
**`coverage.lag_trading_days`**（review I6）＝`market_daily` 裡「`taiex` 非 NULL 且日期晚於
SSF 資料日」的列數，文字與顏色都吃它——**與總覽 `renderFreshness` 不是同一招**：後者文案
仍是日曆天、只有顏色吃後端已考慮週末的 `data_stale`；股期頁沒有 `data_stale` 可借，所以文字
與顏色一起用交易日落差（落後 ≥2 個交易日才亮琥珀）。**不要照「同 renderFreshness」改回日曆
天**——那會讓每個週日與週一傍晚前都誤亮琥珀。`market_daily` 只在真的開盤才建列，天生排除
週末/假日；空資料庫這個鍵仍回 0，同其他 coverage 鍵一起給。

**前端坑**：設計文件的`gt-sub`/`.empty`/`card-title`/`--fs-xxs`不存在，改用既有
`muted small`/`card-label`/`--fs-xs`；圖表一律`initChart(el)`不可直接`echarts.init`；空資料
不可`innerHTML`蓋活著的圖表實例(改`clear()`+切兄弟元素)；頁面標題不可掛`.group-title`且h2要
`flex-shrink:0`(同ui58 `#view-rotation`)；candlestick缺值給`'-'`絕不給`null`(否則`setOption`
中止且不進console)。

**前端 review 修復**(2026-09)：保證金表補表頭排序——標的/契約乘數/1口保證金/維持保證金
(＋原始比例，final review #7)可排序，同 selfcheck 既有的 `th.sc-sort`/`aria-sort`/
`scope="col"` 慣例；**表頭在靜態 HTML 裡是完整的，`renderSsfMargin` 只更新各 th 的
aria-sort／箭頭與 N 口合計文字，不整列重繪**(final review #6；整列重繪的話資料沒回來時表頭
一片空白)，N口合計是1口金額的單調倍數不開獨立排序鍵(I4)；切走再切回
股期頁要對 `ssfCharts` 逐一補 `resize()`(同 cup-handle 既有寫法——`ssfLoaded` 在
`loadSsf()` 之前就同步設 true，切走再切回不會重新載入也不會補救，I5)；`renderSsfFreshness`
改讀 `coverage.lag_trading_days` 不再自己拿日曆天硬算(週五資料撐過整個週末會被算成落後
2~3天而誤亮琥珀，I6 前端部分)；期現價差表補顯示 `main_month`(換月會讓價差跳動，沒有月份
看不出正/逆價差算的是哪個月合約，#2 前端部分)；`.ssf-oi-name` 補 `title`(同 `.hm-bar-name`
既有慣例，#4)；漲跌%判斷改 `>0` 不用 `>=0`(剛好平盤不再顯示「+0.00%」；熱力圖底色對
`chg_pct===0` 直接給中性色 `#2b3038`——同 `sectorColor` 對 `null` 用的字面值，不動
`sectorColor` 本身，它還給總覽熱力圖/權值卡用，#5)；note 補 `coverage.no_spot`(算了但沒印，
比照既有 `no_stock_code`，M3)；保證金表搜尋加 `normTW()`(台→臺)+指數三檔別名表(微台/微台指
/tmf→微型臺指、小台/小台指/mtx→小型臺指、大台/台指期/tx→臺股)，**別名比對用完全相等不
用 `includes`**(MTX 字面含 TX 子字串，方向反了會讓「TX」連小型臺指也撈出來，M8)；**查詢字
剛好是別名時只回該列**(`ssfAliasTarget`：否則「大台」會因「元大台灣50ETF」字面含「大台」多帶
出 3 檔 ETF；非別名查詢行為不變)；個股頁
股期保證金依 `kind` 分文案：ETF 是官方公布固定金額，股票才是結算價估算(M9)。

**測試坑**：新端點依賴(`_attach_ssf_margin`)讓~5條既有自算選股測試真連外卻照樣通過→conftest
加autouse樁`_no_ssf_network`+`@pytest.mark.real_ssf_fetch`退出標記（2026-09 後續：review I3
把這個修到根，`_attach_ssf_margin` 改用 `fetch=False` 後結構上就不會呼叫這兩支 fetcher，這道
樁降級成防禦性第二層；新測試改樁成「raise」而非「回空 dict」才證明得了路徑真的不連外）；
測試替身要委派真實`httpx.Response.raise_for_status()`不可自編Exception；「測試通過但理由
不對」三例(sr-only測在不讀的欄位/OI測兩天OI相同被錯誤路徑排除/熱力圖測兩天root相同)——
**每道守衛都要反證**。

**回補只補缺的交易日**：`GET /api/ssf/backfill?days=30&max_fetch=3`，重複呼叫直到`remaining`
不再下降(同 chips/backfill)。交易日曆＝`market_daily` 有 `taiex` 的日子(同 `lag_trading_days`)，
缺口依新到舊切成 ≤14 日曆天一段(`_ssf_missing_spans`)，每次最多 `max_fetch`(上限 7)段；
**今天不算**(交給每日排程，否則白天 remaining 卡在 1)；窗口內沒有交易日時回應附 `note`
(新部署先跑 `/api/backfill`)，不讓 `remaining=0` 被讀成已補齊。

**版面（ui61→ui63）**：12 欄格線（掛在 `#view-ssf.active`，少了 `.active` 這頁會出現在每一頁）。
**第一列是使用者指定的：左邊成交量前 30 股期、右邊保證金試算**；之後是三張 K 棒 12／未平倉 12／
價差 5＋熱力圖 7。DOM 順序＝閱讀順序。1904×980 實測 19.2→2.08 屏，主因是保證金 342 列全攤開
佔 83%。第一列：≥1641 前 30 佔 7（兩欄 1–15／16–30）＋保證金 5；1261–1640 前 30 佔 5（單欄）
＋保證金 7；≤1260 第一列上下排、≤1180 全部單欄。高度由前 30 決定，保證金 `flex:1 1 0; min-height:300px` 填滿（6＋6 在
1641 切不成兩欄，前 30 會變 878px 單欄）。保證金表內距所有寬度 6px、名稱 `overflow-wrap:anywhere`
（「群益ESG投等債20+ETF」＋代號沒有斷行點，最窄卡在 529px）。說明列 `#ssf-margin-note` 要能換行
（全域 `.pane-count` nowrap 會撐出卡片，聲明被 `.content` 的 overflow-x:hidden 切掉，頁面級溢出
檢查量不到）；內捲框 `tabindex="0" role="region"`（Safari 鍵盤）。**成交量前 30**：後端 `hot`
＝30 檔（`SSF_HOT_N`；未平倉榜拆出 `SSF_OI_N`＝10），只列股期、排除 ETF 期貨（`is_etf`），
`vol_chg3`＝今日口數 ÷ 前 3 個存了資料的交易日平均 − 1（湊不齊／缺列／平均 0 → None，不拿更早的
日子頂替）；欄序 名次／股期／股價／漲跌／成交量／較前3日（股價＝股期主力月收盤、不著色，使用者
指定放名稱右邊）；前端兩張 `table-layout:fixed` 表，具名容器 `ssf-hot` ≥744px 並排（2×363＋16，
固定欄 280＋名稱 83）；1181–1260 第一列上下排（5 欄不到 363px）；手機拿掉名次欄、13px；成交量長度條只寫
`background-image` 分項（簡寫會蓋掉斑馬紋底色）；量的增減 ▲▼ 中性色、不著紅綠；手機凍結第一欄
要排除。**保證金類別切換** `#ssf-kind`（全部／個股／小型／ETF／指數；index→指數、etf→ETF、
is_mini→小型、其餘個股，實測 249＋47＋24＋22＝342），與搜尋 AND、檔數算在搜尋之前、不持久化，
`.tf.ssf-kind-btn`＋`aria-pressed`，**不可掛 `.hm-tab`**（總覽熱力圖用它掛點擊，掛了會把熱力圖
市場切成 undefined、整片變空；熱力圖監聽已收窄到 `.hm-tab[data-market]`）。空結果只在「全部」
有結果時才提示切回。量增減先四捨五入再決定箭頭，三階亮度（一般 --text-secondary，因為 --text
就是 --text-primary）。手機凍結第一欄的排除寫在 ≤600 段（寫在外面會拿掉桌機滑過高亮）。
`?date=` 的前 3 日與 OI 前一日從多抓的 `ext_dates` 取，不被 10 天熱力圖窗口截斷。
未平倉清單 ≥560px（具名容器 `ssf-oi`）
折成兩欄，名次用 CSS 計數器畫（li 是 flex、`<ol>` 編號從沒顯示過，折欄後會讀錯順序）。
三張 K 棒並排改用具名容器查詢 `ssf-charts` ≥790px（每張至少 254px；舊的視窗 760px 斷點讓
761～1120px 名稱疊在一起）；三張圖共用同一個 grid.bottom（各自算的話繪圖區高度不同，同幅度
不成立），tooltip 用 `financeTooltip`（confine）。價差＋熱力圖並排
門檻 1641px，並排時熱力圖 `height:100%` 格子填滿同列。K 棒軸 `interval:0`（不設會悄悄藏一半名稱）
＋直書＋`ssfShortName`（上限 6 讓「元大台灣50」完整）；漲幅／跌幅共用刻度 `ssfSharedRange`；
三張圖掛 ResizeObserver（寬 0 不重畫）——**窗格隱藏時 RO/resize 都不發、量到舊畫布寬是測試環境
假象**。`th.sc-sort{nowrap}` 權重同、要寫在後面才蓋得過。**`sed -i` 會把 CRLF 檔改成 LF**，
改完用 bytes 確認。

### Public pages (`/public/*`)
Never require auth. Serve market-level (non-personal) data via `/api/overview` (enhanced with intl indices, institutional rankings, futures positioning, margin/short data):
- `GET /public/overview` — dashboard page (for LINE rich-menu): market summary, sectors, AI text.
- `GET /public/api/overview` — data endpoint: taiex, intl, sectors, inst (buy/sell spread + prev), fut (foreign OI, retail LS ratio + prev), margin/short balance + prev, ai_text.
- `GET /public/api/inst-rank?who=foreign&unit=shares` — lightweight filter-and-rerender endpoint (張/金額 toggle without full page reload).
- `GET /public/logic` — cup-handle explanation page.
- `GET /public/disclaimer` — risk warning page.

**Never return** personal data (watchlist, trades, settings) from `/public/*`.

## Cloud deploy (Zeabur, one service)
`Procfile` + `zbpack.json` start `uvicorn ... --port ${PORT:-8080}` (single worker only — multiple workers duplicate the scheduler and contend on SQLite). Must mount a **persistent Volume at `/data`** with `SPR_DB_PATH=/data/spr.sqlite`, else every redeploy wipes the DB. Cold-start / one-off helpers: `GET /api/backfill?days=200&max_fetch=20`（加權/現貨法人/融資券——**`market_daily` 建列的唯一入口**，上限 400 天，其他回補端點都只填既有列、建不出列，故窗口上限要跟得上這支）, `GET /api/chips/backfill?days=200&max_fetch=15`（台指期籌碼歷史）, `GET /api/intl/backfill?days=120`（國際指數歷史，新增 ticker 後必跑）, `GET /api/inst/backfill?days=60&max_fetch=15`（個股三大法人預熱）, `GET /api/csv/import-all` (imports every CSV in `Date/`), `GET /api/ohlc/backfill?days=377&max_fetch=60` (全市場個股 OHLC 進 `stock_ohlc` for the cup-handle screen — chunked/resumable, call repeatedly until `done`; then `/api/patterns/cup-handle` screens 亞當杯柄 型態, `patterns.py`). Daily use: the web "上傳今日檔" button → `POST /api/csv/upload` (no redeploy). "讀取資料夾最新檔" only sees `Date/` committed to the repo. **每日上傳（見 `scripts/README-upload.md`）**：使用者手動在 XQ 匯出並存到 `C:\Users\<user>\XQExport` → 雙擊 `scripts/upload_today.bat`（呼叫 `upload_xq_click.ps1` → 底層 `upload_xq.ps1` → 同一支 `/api/csv/upload`，server 端零改動）。帳密只問一次，之後靠 DPAPI 快取在 `%LOCALAPPDATA%\StocksPowerRich\spr_cred.xml`（repo 外）。這是使用者 2026-08 明確選擇的方案（查過 XQ 沒有免點擊的匯出排程做法，且不想設 Windows 工作排程器）——不改變、也無法取代「XQ 每天要跑選股並匯出」這個前提（蘭質/蘭值是蘭弦付費專有指標，本站無法自行重算或上網代抓）。排程版（`upload_xq.example.ps1`）保留作為進階備案。**Stage 2 五片進行中**（2a 月營收年增／2c W55／2d 大戶增比人數降比／2b 季報財務 sub-task 1／投信外資近3日），**皆尚未接進 `filtered_picks`**（並存對照）：`sources/revenue.py`（月營收年增，TWSE/TPEx OpenAPI）；`analysis.w55_signal()`→`patterns.percent_r()`（PercentR(55)>50，用既有 `stock_ohlc`）；`analysis.custody_change()`＋`db.py::custody_change_map()`（大戶增比/人數降比，`custody_dist` 新增 `total_holders`）；`db.py::institutional_3d_map()`（投信/外資近3日淨買超＝`stock_flow_daily` 近3交易日 `trust_lots`/`foreign_lots` 加總，張數帶正負、缺日=0；已接進 selfcheck 兩欄對照 CSV 投三/外三，容差用隨量級寬容 `SELFCHECK_ABS_REL` max(5張,2%)——實測自算＝官方 T86、與 XQ 僅 sub-% 差異，外資量級達數萬張，固定絕對容差會假性全 diff；「含/不含外資自營商」口徑差經實測推翻（外資自營商 3 日全市場皆 0，改含自營是 no-op）；木質缺的最後兩個籌碼訊號）；`sources/financials.py`（季報財務，官方 `mopsfin.twse.com.tw/compare/data`，一次帶多代號取 13 年季度歷史，8 個乾淨 JSON 指標存進 `stock_financials` tall 表，`GET /api/financials/backfill` 逐檔回補）。四片公式/欄位都用真實資料交叉驗證過（非猜測），細節與踩過的坑（OpenAPI 沒有現金流量表、W55 門檻反推、「總持股人數」不是大戶人數、季報「缺任一指標才算 pending」等）見 CLAUDE.md 對應四段。**2b sub-task 2 已完成**（`稅前淨利`/`營業費用`/`所得稅費用`/`資本支出`，走 mopsfin 完整報表 `POST /compare/report`，`ys` 必填不然安靜回錯季資料；回應是年度累計，`financials.decumulate_quarterly()` 反推單季；抓取深度要 `depth+1` 且要「持續往回抓到蒐集滿」而非固定季別清單，兩者都是實測踩到才修對；`GET /api/financials/backfill-report` 是背景執行緒＋輪詢，但**報表端點從 Zeabur 根本打不動**（每請求逾時、0 提交；ratios 的 /compare/data 同主機卻通），改走「**本機抓→匯入 production**」：`updater.compute_report_indicators()`（純函式、不碰 DB）＋雲端 `GET /api/financials/report-pending`＋`POST /api/financials/import`＋本機 `scripts/sync_report_financials.py`（迴圈 pending→本機抓→POST）。端到端實測 2330 一輪 36 秒匯入 20 列。ratios 仍直接在 Zeabur 跑；見 CLAUDE.md 同名段落）——端到端對台積電實跑後 `lan_score()` 已能回傳真實非 None 分數，是木質財報分第一次不靠 CSV 算出來。**同批也做了 Call_LE（`analysis.estimate_quarterly_eps`，推估季EPS，CSV「推估獲利」欄位）**，用台積電真實數字驗證 ≈20.91 對官方 22.08、差約 5%（模型簡化屬預期）；VALUE111 需要 6 個月月營收，openapi 只給最新月，但 **MOPS `t21sc03` 有歷史來源**——`sources/revenue.py::fetch_monthly_revenue_history()`＋`updater.backfill_monthly_revenue_history()`＋`GET /api/revenue/backfill-history?months=6` 一次補齊（Big5 HTML、每列 10 td、單月 YoY 要自算、report_date 取次月 10 日、與 openapi 最新月重疊為冪等；實測 2330 兩邊 revenue 同值、六個月皆可達）。時間鎖已解，接進 selfcheck est_profit 屬 2e。**2b＋Call_LE 皆仍未接進 `filtered_picks`／設定頁 Stage 2 揭露**（並存對照，2e 整合階段一起處理）。

## 字級整體放大一級（ui68，2026-09）
`:root` 的 9 個 `--font-size-*` 全部 +1 級（base 15→16、md 16→18、lg 20→22、xl 26→28、hero 40→44…），另換掉 34 處硬寫的 `font-size: Npx`；`body` 補 `line-height: 1.5`（原本沒有），`.card` min-height 104→116、內距 10→12px。**只測 1904/1280/375 會漏掉問題**——實測三個壞點有兩個在中間寬度：1024px 標題單行只差 4px 就折行（頂欄 59→80）、601–860px 本來就折 3 列而一起長高（82→125）。三道修正：`.topbar{line-height:1.25}`（頂欄是常駐 chrome，不吃 body 的 1.5）、`.topbar h1{white-space:nowrap;flex:0 0 auto}`＋`.topbar .gsearch{min-width:0}`（讓搜尋框吸收那 4px，品牌名不截斷）、`@media(max-width:860px)` 把頂欄自己的控制項釘回放大前尺寸（頁面內容照樣放大）。最終實測頂欄 1904/1280/860/768/375 皆 59px、1024/862 為 65px、601 為 85px（基準 82），全寬度零頁面溢出。**刻意沒動**：app.js 的 ECharts `fontSize`（軸標籤有 `interval:0` 密度約束，另案處理）；個股頁 `.toolbar` 在 375px 溢出 222px 是**既有缺陷**（改動前 205px，A/B 確認）。**順帶更正**：CLAUDE.md 那條「8 張卡靠 `minmax(138px)`」已過期——卡片改用 `.stat-board` 固定欄數（4/3/4/6 × `minmax(0,1fr)`），auto-fit 那條吃不到。

## `<title>` 與「兩個股力智富」不是程式問題（ui68 同批）
分頁標題「股票，財富增值 --- STOCKS POWER RICH」、頂欄兩個「股力智富」、自算選股股名後的「週刊／新版／新聞／週報」——**三者同因，都不在程式碼裡**：全 repo grep 不到「財富增值」、沒有任何 `document.title`、`index.html` 的「股力智富」只出現 1 次、CSS 無中文 `content:`、名稱格只有一個 `<a>`＋一個 `<span>`。「譯文 --- 原文」的標題格式＋原文旁插譯文＝**瀏覽器翻譯擴充功能的雙語對照模式**。程式端能做的只有把 `<title>` 改成「股力智富」（已做）；其餘要在瀏覽器關掉該站翻譯。判斷法：Console 跑 `fetch('/api/picks/self-screen').then(r=>r.text()).then(t=>console.log(t.includes('週刊')))`，`false` 即代表是用戶端注入。

## 個股三大法人不再只有 60 天（2026-09）
根因是兩件事疊起來：`stock_chips` 把 `days` 夾在 60，而真正成因是它逐日走 `_insti_for`、**快取沒中就即時連外**，加上 `ai_cache` 每天清 120 天前的鍵（結構上最多留約 80 個交易日）——一年窗口會變成一次請求 150+ 次官方請求。**改讀 `stock_flow_daily` 一支 SQL、端點從此零連外**（那張表每天由 `update_day` 寫入且無清理機制）。值等價實測 136,759 筆全同 0 筆不同。**但表不是快取的超集**：上櫃有 8 天只在 `ai_cache` 裡，純改讀表會在窗格中間挖洞，所以保留 `get_ai_cache` **唯讀**後備（**絕不可用 `_insti_for`**，它會 fetch），上限 `CHIPS_CACHE_FALLBACK_MAX=60` 保證成本不高於改版前。市場判定也改讀表的 `market` 欄。**`idx_stock_flow_code_date (code,date)` 必須同 commit**：既有索引都以 date 開頭，`WHERE code=?` 只能全表掃描，實測 78.5ms→0.175ms、端點 1,071ms→4.6–13.2ms，DB +27.4MB，一次性建索引 ~929ms 在 `init_db`（啟動時，不在請求路徑）。回應多 `first_date`／`covered`，前端只在 `covered < dates.length` 時標「（法人 MM-DD 起，共 N 日）」——`dates` 是 market_daily 的視窗，拿 `dates[0]` 會指到一個當天沒資料的日期。**已知取捨**：16:00–17:30（T86 已公布、update_day 未跑）今天那格可能空著，除非期間開過總覽暖了 `t86:{今天}`。舊的兩條 chips 測試**刻意刪除改寫**（它們依賴的正是即時連外那條路）；新 5 條帶網路絆線，**絆線用計數不用拋例外**（`_insti_for` 會吞例外，拋的看起來還是綠的），反證：後備改成 `for ds in []` 正好只有那條轉紅。順帶移除 `stock.py`／`public.py` 兩處不用卻載重的 `_insti_for` import。

## 族群輪動改「法人 × 大戶」資金流向四象限（ui69，2026-09）
主圖「近 N 日類股漲跌表」從 `bfd0a53`（拆 APIRouter）起就壞了——後端把 `sectors` 從陣列改成 dict、前端沒跟，永遠「尚無類股資料」。直接移除 `/api/sectors/rotation`，換成 `GET /api/sectors/flow`：X＝法人近 5 日淨買賣 ÷ 類股市值、Y＝大戶 400張↑ 週增 ÷ 類股市值（除以市值才跨類股可比），泡泡＝類股、尾巴＝上一期→本期；純函式 `analysis.sector_flow`，大戶金額只有一份算式 `analysis.big_holder_amount`（selfcheck 也改呼叫它，`inspect.getsource` 鎖住）。大戶缺某檔 Δ 不進分子仍進分母、整類股沒 Δ 時 `y=None` 不是 0；法人交易日曆用 `stock_flow_daily` 自己的日期（不用 `market_daily`，今天的空列會被算進窗口）；快取鍵 `sectorflow:v3:{法人最新日}:{窗口指紋}:{全部集保週}:{days}`——**鍵要描述每一個輸入**：只放最新週會在 2→3 週時吃到舊快取（`has_custody` 讀取守衛是死碼、已拿掉），只放最新日則看不出「回補補進窗口中段一天」與「上櫃單邊重抓」，故加 `db.stock_flow_fingerprint`（逐日 COUNT＋Σ淨額的 md5 前 10 碼）——它結構上跑在快取查詢之前、連命中那次都要付（實測 days=5 掃 18 萬列 ~100ms、days=20 掃 64 萬列 ~341ms），**明知並接受**：一次進頁面只打一次，而改用 `stock_source_coverage` 當指紋雖只要 ~0.1ms，卻會把快取正確性綁在一張直接寫入者（含測試）不會更新的側表上；集保週用 `as_of=法人最新日` 挑，且第 2、3 個完整週**不保證相鄰**（殘缺週被略過，真實 DB 是 09-18／09-11／08-21），`_weeks_adjacent`（≤10 天）兩段都成立才給 `y_prev`，否則 `custody_prev_skipped="weeks_not_adjacent"`＋`custody_prev_gap` 讓說明列講出「跨 N 週、尾巴省略」；顏色一律 `C.info` 不碰紅綠；四區排行 `<button aria-pressed>` 是 canvas 的鍵盤替代，點泡泡或列篩下方交叉選股；集保不足兩週降級成單軸長條。`custody_compare_weeks` 加 `limit`（預設 2）。

## Conventions
- TDD: write the parse test first; keep parse functions pure and wrappers thin. Endpoint tests use `TestClient` with `SPR_DB_PATH` = tmp file and `monkeypatch.setattr(sources.X, "fetch_...", ...)`.
- Daily 選股 CSVs are tracked in `Date/`. Commits end with a `Co-Authored-By: Codex ...` trailer. Pushing to `main` (private GitHub repo) triggers a Zeabur redeploy.
