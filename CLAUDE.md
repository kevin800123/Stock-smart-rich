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
View-switching SPA + ECharts (CDN). Candlestick data is `[open, close, low, high]`. All fetches use relative `/api`. Charts degrade to "尚無資料" on empty; tooltips round floats. Elliott-wave detection is ported to **both** Python (`elliott.py`) and JS (in `app.js`) — keep them in sync.

### Other backend pieces
- `analysis.py`: `filtered_picks` (W55 翻多 ∧ 大戶增比>0 ∧ 營收年增>0 ∧ 推估EPS>0, sorted by 蘭值); `industry_to_sector` maps the CSV's `上市/上櫃+類股` field to official 類股 names for sector cross-referencing.
- `csv_import.py`: imports the user's daily 選股 CSV/Excel — multi-encoding auto-detect (cp950 / big5hkscs / utf-8-sig …), `.xlsx/.xlsm` via openpyxl, ROC/西元 date extraction, field-count normalization for unquoted commas in text columns.
- `gemini.py`: AI summaries degrade to plain data when no key; cached per day in `ai_cache`. Never expose the key (API returns only `gemini_configured: bool`).
- `scheduler.py` (APScheduler, `timezone="Asia/Taipei"`) runs the daily update in-process; needs the process alive. `cli.py` is the equivalent for Windows Task Scheduler.

## Data-source quirks (would trip you up)
- **TWSE**: ROC (民國) dates = year+1911. `T86` (per-stock 三大法人) is **上市 only**; OTC uses TPEx. Direct RWD endpoints take a `date` param.
- **TAIFEX**: official CSV downloads (`dlFutDataDown`, `futContractsDateDown`) need **GET-cookie-then-POST**, ≤~30-day chunks, and `.decode("ms950")`.
- **TDCC (集保)**: opendata `getOD.ashx?id=1-5` returns **the current week only** (trend accumulates weekly via `updater._accumulate_custody`, new-week-only). Requires `verify=False` (their cert lacks a Subject Key Identifier). Stock codes are **space-padded to 6 chars** — `.strip()`.
- **TPEx (櫃買)**: `dailyTrade` by date; fields are parsed **by fixed column position** (the field labels 買進/賣出/買賣超股數 repeat and can't disambiguate groups).
- **yfinance**: flaky / rate-limited from datacenter IPs → `kline._history` retries; the index K-line falls back to TWSE `MI_5MINS_HIST` OHLC; `.TW`→`.TWO` fallback covers OTC.

## Config (`config.py`, via .env / env vars)
`GEMINI_API_KEY`, `SPR_SCHEDULE_TIME` (default 21:00), `SPR_DB_PATH`, `SPR_DATA_DIR` (Date/), `SPR_ENABLE_SCHEDULER`, `TZ`. On any non-Taipei host, `TZ=Asia/Taipei` is mandatory — the data-date/schedule logic uses naive local time.

LINE push (`line_push.py`): `LINE_CHANNEL_ACCESS_TOKEN` (Messaging API **broadcast** — the user's OA has only themselves as friend; LINE Notify is discontinued) + `SPR_LINE_PUSH_TIME` (default 16:00 weekday brief, no 融資券; the `SPR_SCHEDULE_TIME` job pushes the full version). Non-today data auto-skips pushes; `POST /api/line/test` forces one. Never expose the token (settings returns `line_configured: bool` only).

Security (`docs/SECURITY.md`, P0+P1+P2 done): `SPR_BASIC_USER`+`SPR_BASIC_PASS` enable a global HTTP Basic Auth middleware (both must be set; unset = off for local dev) — gates all routes incl. static. A second middleware always sets security headers (CSP allowing self + jsdelivr for ECharts, no unsafe-eval; X-Frame-Options DENY; nosniff). Frontend must `esc()` any external/CSV string before innerHTML (XSS). `data_dir` from settings is whitelisted to `REPO_DIR`/`SPR_DATA_DIR` via `_dir_within`. CSV upload capped at 10MB + extension allowlist. `db.backup_db` (SQLite online-backup API, rotate 7) runs in the 21:00 job + `POST /api/db/backup`. Rate-limiting (M2), TDCC's `verify=False` (M4), and unsanitized error detail (L4) are deliberately deferred/kept — each re-evaluated post-auth and judged low residual risk (see SECURITY.md for the reasoning per item, not just "not done"). `requirements-lock.txt` is a `pip freeze` snapshot for audit reference — regenerate via pip-audit when touching requirements.txt, then uninstall pip-audit itself so its transitive deps don't pollute the lock file.

## Cloud deploy (Zeabur, one service)
`Procfile` + `zbpack.json` start `uvicorn ... --port ${PORT:-8080}` (single worker only — multiple workers duplicate the scheduler and contend on SQLite). Must mount a **persistent Volume at `/data`** with `SPR_DB_PATH=/data/spr.sqlite`, else every redeploy wipes the DB. Cold-start / one-off helpers: `GET /api/backfill?days=35` (backfills ~1 month of 加權/現貨法人/融資券), `GET /api/csv/import-all` (imports every CSV in `Date/`). Daily use: the web "上傳今日檔" button → `POST /api/csv/upload` (no redeploy). "讀取資料夾最新檔" only sees `Date/` committed to the repo.

## Conventions
- TDD: write the parse test first; keep parse functions pure and wrappers thin. Endpoint tests use `TestClient` with `SPR_DB_PATH` = tmp file and `monkeypatch.setattr(sources.X, "fetch_...", ...)`.
- Daily 選股 CSVs are tracked in `Date/`. Commits end with a `Co-Authored-By: Claude ...` trailer. Pushing to `main` (private GitHub repo) triggers a Zeabur redeploy.
