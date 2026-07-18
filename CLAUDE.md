# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
View-switching SPA + ECharts (local `web/vendor/echarts.min.js`, no CDN — CSP is `script-src 'self'`). **Fonts are self-hosted in `web/vendor/fonts/` (CSP `font-src 'self'`)**: 日式圓體 — CJK is jf open 粉圓/Huninn (SIL OFL, `huninn.woff2` ~2MB, single weight + synthesized bold), digits/Latin are M PLUS Rounded 1c via the `"Num"` @font-face `unicode-range` trick (decouples number sizing from CJK via `size-adjust: 92%`; proportional, so not strictly tabular). `app.js`'s `HM_FONT` (heatmap canvas-measure + ECharts render) **must stay in sync with the body font stack**, else labels measure-fit-then-truncate; a `document.fonts.ready` refit covers the async swap. Candlestick data is `[open, close, low, high]`. All fetches use relative `/api`. Charts degrade to "尚無資料" on empty; tooltips round floats. Elliott-wave detection lives **only** in Python (`elliott.py`); `kline.py` precomputes `waves` (a `{pct: segments}` dict for thresholds 2–15%) into the K-line API response, and `app.js` just renders `data.waves[pctKey]`. **Do not reintroduce a JS Elliott implementation** — the dual-implementation drift it caused is gone; add new wave logic on the backend.

**Layout quirk**: `.view` is `display: flex; flex-direction: column;` so content-heavy pages (e.g., trading journal with 未平倉+已平倉 tables) can be compressed by flex-shrink. **Solution**: `.table-wrap` has `flex-shrink: 0` by default; `.table-wrap.fill` overrides to `flex-shrink: 1; flex: 1 1 0; min-height: 0` for tables that should occupy remaining space. Add `flex-shrink: 0` to any new table that must maintain readable height regardless of page overflow.

### Other backend pieces
- `analysis.py`: `filtered_picks` (W55 翻多 ∧ 大戶增比>0 ∧ 營收年增>0 ∧ 推估EPS>0, sorted by 蘭值); `industry_to_sector` maps the CSV's `上市/上櫃+類股` field to official 類股 names for sector cross-referencing; `trade_stats(trades, closes, taiex_by_date)` returns `{trades: [...{status, net_pct, pnl, mkt_pct, alpha}], stats: {closed_n, win_rate, avg_win, avg_loss, payoff, expectancy, realized_pnl, open_pnl, avg_alpha}}` for trading journal performance (fees deducted, open positions marked-to-market). `margin_maintenance(lots_by_code, closes, margin_value_yi, short_lots_by_code, short_margin_pct=0.9)` computes the full official 整戶擔保維持率 = (融資市值 + 融券擔保品 + 融券保證金) ÷ (融資金額 + 融券市值) × 100 — TWSE never publishes a per-stock original 融券賣出價金, so both the 融券擔保品 and 融券保證金 terms are approximated from *current* price (`短張數×1000×收盤`) rather than the true original short-sale price; `short_lots_by_code` defaults to `None`, which fully degrades to the pre-existing 融資-only ratio (backward compatible). Fed by `sources/twse.py::parse_margin_detail`, which returns `{"margin": {code: 張}, "short": {code: 張}}` from the same `MI_MARGN` `selectType=ALL` payload (融券今日餘額 is column index 12, right after 融資's index 6 — no extra network call).
- `csv_import.py`: imports the user's daily 選股 CSV/Excel — multi-encoding auto-detect (cp950 / big5hkscs / utf-8-sig …), `.xlsx/.xlsm` via openpyxl, ROC/西元 date extraction, field-count normalization for unquoted commas in text columns.
- `gemini.py`: AI summaries degrade to plain data when no key; cached per day in `ai_cache`. Never expose the key (API returns only `gemini_configured: bool`).
- `line_push.py`: `compose_daily_brief(row, sectors, watch, ai_text, full, tsmc, prev, cup)` + `compose_breakout_alert(hits, hhmm)` format LINE messages (full version with 融資券 at 21:00, brief at 16:00; intraday breakouts with ⭐ for picks). Breakout alerts use **ATR threshold** (price > resistance + 0.3×ATR) + **two-round confirmation** (candidate on first cross, report only if still above threshold 5min later) to reduce false alerts.
- `patterns.py`: cup-handle detection — XS base conditions (左緣未破高 ∧ 杯身寬 ∧ 柄淺守穩 ∧ %R≥`min_r`, default 70, UI-adjustable 50–90) + fixed Adam quality filters (杯深 12–50% ∧ 柄低點不破杯身中點 ∧ 收盤距壓力 ≤10%). `cup_handle_signals` is hybrid: vectorized base scan, then per-candidate scalar confirm (equivalence test locks them together). The `cupsig` intraday-sentinel/ledger snapshot is only written at default `min_r` so UI experimentation can't pollute alerts/forward-test. `atr(closes, period=14)` for position sizing.
- `ledger.py`: signal forward-test. `record_daily_signals` snapshots each day's `filtered_picks` + cup-handle hits into `signal_ledger`; a RetN updater later backfills 5/10/20-day realized returns from `stock_ohlc`. Bias-free (no survivorship) counterpart to `backtest.py`'s one-shot historical cup backtest; the performance-aggregation API compares "signals-all" vs the trade journal's actual alpha. **The 訊號追蹤 view was removed (user decision, 2026-07) but the recording deliberately keeps running** — forward-test data can't be backfilled without reintroducing bias, so the ledger accumulates silently; re-adding a view later shows full history. Read via `GET /api/signals/performance`. Do not "clean up" the ledger calls as dead code.
- **`traders/` (操盤手)**: a registry of trading-persona analyzers behind the「操盤手」view. Each persona = one module exposing `META = {id,name,emoji,tagline,desc}` + `analyze(conn) -> {date, sections[], disclaimer}`, registered in `traders/__init__._MODULES`. `sections` are generic typed blocks (`checklist` / `table` / `routine` / `note`) the frontend renders without bespoke code, so **adding a persona = one new module, no endpoint/frontend change**. Endpoints: `GET /api/traders` (list for the picker) + `GET /api/traders/{id}` (that persona's analysis, `{**META, **analyze()}`). `traders/ss.py` is the first persona; its pure rule engine lives in `ss_trader.py` (quantifiable subset of the "Ss" methodology — full qualitative distillation in `.claude/skills/ss-trader/SKILL.md`): market checklist (融資維持率 13X% 抄底區, 融資 vs 大盤 wash, VIX contrarian, USD/TWD via the `twd` intl ticker, volume×position, 小那 vs 小道 fund flow, night-session ratio, settlement week) + 一紅吃三黑 candle signal + 季季高-approx picks. Every persona's output carries a mandatory not-advice disclaimer.
- `offsite_backup.py`: after the 21:00 `backup_db`, pushes the rotated backup to a remote Git repo (env-gated; silently skips if unset). `mask_secrets` scrubs OAuth tokens from logs via `re.sub(r'https?://[^@\s]+@', 'https://***@', text)` — never log a raw remote URL.
- `scheduler.py` (APScheduler, `timezone="Asia/Taipei"`) runs the daily update in-process; needs the process alive. Intraday breakout scanning runs every 5min during market hours. `cli.py` is the equivalent for Windows Task Scheduler.

## Data-source quirks (would trip you up)
- **TWSE**: ROC (民國) dates = year+1911. `T86` (per-stock 三大法人) is **上市 only**; OTC uses TPEx. Direct RWD endpoints take a `date` param.
- **TAIFEX**: official CSV downloads (`dlFutDataDown`, `futContractsDateDown`) need **GET-cookie-then-POST**, ≤~30-day chunks, and `.decode("ms950")`.
- **TDCC (集保)**: opendata `getOD.ashx?id=1-5` returns **the current week only** (trend accumulates weekly via `updater._accumulate_custody`, new-week-only). Requires `verify=False` (their cert lacks a Subject Key Identifier). Stock codes are **space-padded to 6 chars** — `.strip()`.
- **TPEx (櫃買)**: `dailyTrade` by date; fields are parsed **by fixed column position** (the field labels 買進/賣出/買賣超股數 repeat and can't disambiguate groups).
- **yfinance**: flaky / rate-limited from datacenter IPs → `kline._history` retries; the index K-line falls back to TWSE `MI_5MINS_HIST` OHLC; `.TW`→`.TWO` fallback covers OTC; `intl.fetch_intl_indices` falls back to the direct Yahoo v8 chart API (no cookie/crumb handshake — the part that fails on datacenter IPs; Stooq CSV endpoints are dead, 404).

## Config (`config.py`, via .env / env vars)
`GEMINI_API_KEY`, `SPR_SCHEDULE_TIME` (default 21:00), `SPR_DB_PATH`, `SPR_DATA_DIR` (Date/), `SPR_ENABLE_SCHEDULER`, `TZ`. On any non-Taipei host, `TZ=Asia/Taipei` is mandatory — the data-date/schedule logic uses naive local time.

LINE push (`line_push.py`): `LINE_CHANNEL_ACCESS_TOKEN` (Messaging API **broadcast** — the user's OA has only themselves as friend; LINE Notify is discontinued) + `SPR_LINE_PUSH_TIME` (default 16:00 weekday brief, no 融資券; the `SPR_SCHEDULE_TIME` job pushes the full version) + `SPR_WEEKLY_PUSH_TIME` (default 17:00; the `weekly_line` job broadcasts the 籌碼週報 — 跨週變化＋AI — every **Saturday** at this time; day is fixed, only time is configurable). Non-today data auto-skips pushes; `POST /api/line/test` forces one. Never expose the token (settings returns `line_configured: bool` only; the three push times are surfaced read-only as `line_push_time`/`weekly_push_time`/`schedule_time` and shown in the 設定 page's LINE badge).

Security (`docs/SECURITY.md`, P0+P1+P2 done): `SPR_BASIC_USER`+`SPR_BASIC_PASS` enable a global HTTP Basic Auth middleware (both must be set; unset = off for local dev) — gates all routes incl. static. A second middleware always sets security headers (CSP allowing self + jsdelivr for ECharts, no unsafe-eval; X-Frame-Options DENY; nosniff). Frontend must `esc()` any external/CSV string before innerHTML (XSS). `data_dir` from settings is whitelisted to `REPO_DIR`/`SPR_DATA_DIR` via `_dir_within`. CSV upload capped at 10MB + extension allowlist. `db.backup_db` (SQLite online-backup API, rotate 7) runs in the 21:00 job + `POST /api/db/backup`. Rate-limiting (M2), TDCC's `verify=False` (M4), and unsanitized error detail (L4) are deliberately deferred/kept — each re-evaluated post-auth and judged low residual risk (see SECURITY.md for the reasoning per item, not just "not done"). `requirements-lock.txt` is a `pip freeze` snapshot for audit reference — regenerate via pip-audit when touching requirements.txt, then uninstall pip-audit itself so its transitive deps don't pollute the lock file.

### Public pages (`/public/*`)
Never require auth. Serve market-level (non-personal) data via `/api/overview` (enhanced with intl indices, institutional rankings, futures positioning, margin/short data):
- `GET /public/overview` — dashboard page (for LINE rich-menu): market summary, sectors, AI text.
- `GET /public/api/overview` — data endpoint: taiex, intl, sectors, inst (buy/sell spread + prev), fut (foreign OI, retail LS ratio + prev), margin/short balance + prev, ai_text.
- `GET /public/api/inst-rank?who=foreign&unit=shares` — lightweight filter-and-rerender endpoint (張/金額 toggle without full page reload).
- `GET /public/logic` — cup-handle explanation page.
- `GET /public/disclaimer` — risk warning page.

**Never return** personal data (watchlist, trades, settings) from `/public/*`.

## Cloud deploy (Zeabur, one service)
`Procfile` + `zbpack.json` start `uvicorn ... --port ${PORT:-8080}` (single worker only — multiple workers duplicate the scheduler and contend on SQLite). Must mount a **persistent Volume at `/data`** with `SPR_DB_PATH=/data/spr.sqlite`, else every redeploy wipes the DB. Cold-start / one-off helpers: `GET /api/backfill?days=35` (backfills ~1 month of 加權/現貨法人/融資券 — deliberately NOT 期貨籌碼), `GET /api/chips/backfill?days=90&max_fetch=15` (台指期籌碼歷史 — 外資未平倉/散戶多空比/`tx_price`; the daily `_backfill_chips` only looks back 10 days × 3/run, so pre-deployment history needs this; ~4 TAIFEX CSV requests per date, call repeatedly until `remaining` stops dropping — 連假 dates stay NULL by design), `GET /api/csv/import-all` (imports every CSV in `Date/`), `GET /api/ohlc/backfill?days=377&max_fetch=60` (全市場個股 OHLC 進 `stock_ohlc` for the cup-handle screen — chunked/resumable, call repeatedly until `done`; then `/api/patterns/cup-handle` screens 亞當杯柄 型態, `patterns.py`). Daily use: the web "上傳今日檔" button → `POST /api/csv/upload` (no redeploy). "讀取資料夾最新檔" only sees `Date/` committed to the repo.

## Conventions
- TDD: write the parse test first; keep parse functions pure and wrappers thin. Endpoint tests use `TestClient` with `SPR_DB_PATH` = tmp file and `monkeypatch.setattr(sources.X, "fetch_...", ...)`.
- Daily 選股 CSVs are tracked in `Date/`. Commits end with a `Co-Authored-By: Claude ...` trailer. Pushing to `main` (private GitHub repo) triggers a Zeabur redeploy.
