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

**個股 note 要說出「資料到哪一天、有沒有洞」**：category 軸按索引排列不按日期，缺口兩端會被畫成連續的，同時產生「日期沒更新」與「價格斷崖」兩個假象；>14 天才算缺口（週末與農曆年連假屬正常）。

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

- **⚠️ 停止上傳 CSV 仍會凍住四個地方**（尚未處理）：`api/csv.py`（選股頁）、`api/helpers.py`（**LINE／Telegram 推播的今日精選與週報**）、`api/market.py`（族群 picks）、`api/public.py`（公開總覽）——都是 `filtered_picks(get_snapshot(...))`，會永遠送出最後一份名單且無跡象。第五個（`record_self_screen_signals` 的前瞻追蹤）已於「每日排程預算＋快取」那批修掉。

### 自算選股改「每日排程預算＋快取」（2026-09）

- 拆成 `compute_self_screen`（貴：全市場 ~2,000 檔逐檔自算＋版圖）與 `build_self_screen(precomputed=)`（便宜：套門檻排序）。門檻/勾選不影響貴的那半 → 一份快取服務所有組合（勾選有 2^7 種）。**等價測試**＋**JSON round-trip 測試**（要進 ai_cache TEXT）鎖住。
- **只存最新一天、一列**（`selfscreen:v1`）：實測一份 729 KB，每天存一列＝一年 180MB。日期對不上一律當沒有，端點回 `precomputed: bool`。
- **`record_self_screen_signals` 改用市場最新交易日**（原本 `MAX(snap_date)`，CSV 一停前瞻追蹤就停——漏列的第五個凍結點）。排程算一次、ledger 吃同一份。
- **排程邏輯放 `api/helpers.refresh_self_screen_cache`**，不是 main.py 閉包——寫在閉包裡就測不到、也沒辦法單獨跑一次驗證，而它被 `except: pass` 包著、壞掉沒聲音。回傳可觀察的 dict（含 `skipped` 原因）。
- **畫面標出「來源：排程預算／現算」**，排程失敗時端點會安靜退回現算，不標就發現不了。
- 本機 0.42s 不具代表性（dev DB 無季報/OHLC≥55，貴的那半沒跑滿）。

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

## Conventions
- TDD: write the parse test first; keep parse functions pure and wrappers thin. Endpoint tests use `TestClient` with `SPR_DB_PATH` = tmp file and `monkeypatch.setattr(sources.X, "fetch_...", ...)`.
- Daily 選股 CSVs are tracked in `Date/`. Commits end with a `Co-Authored-By: Codex ...` trailer. Pushing to `main` (private GitHub repo) triggers a Zeabur redeploy.
