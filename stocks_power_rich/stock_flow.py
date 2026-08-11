"""Normalized stock-flow ingestion and bias-aware institutional-flow research."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median, quantiles

from .db import (
    bulk_upsert_ohlc,
    bulk_upsert_stock_flow,
    get_ai_cache,
    set_stock_source_coverage,
)
from .sources import tpex, twse

MARKETS = ("TWSE", "TPEx")
SOURCES = ("quotes", "institutional", "margin")
WINDOWS = (1, 3, 5, 10, 20, 60)
HORIZONS = (5, 10, 20)
MIN_VALID_DATES = 60
MIN_MATURE_RET20_DATES = 40
MIN_HIT_OBSERVATIONS = 30
DISCLAIMER = "本頁為歷史資料研究工具，不構成投資建議、推薦或買賣訊號。"
CANDIDATE_WARNING = "這只代表值得進入 6–12 個月前瞻觀察，不代表可用於選股。"


def _day(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _weekdays(days: int, today: date | None = None) -> list[str]:
    end = today or date.today()
    cutoff = end - timedelta(days=days)
    out = []
    cursor = end
    while cursor >= cutoff:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return out


def _inst_rows(rows: dict) -> dict:
    return {
        code: {
            "name": value.get("name"),
            "foreign_lots": value.get("foreign"),
            "trust_lots": value.get("trust"),
            "dealer_lots": value.get("dealer"),
            "institutional_total_lots": value.get("total"),
        }
        for code, value in rows.items()
    }


def _margin_rows(payload: dict) -> dict:
    codes = set(payload.get("margin") or {}) | set(payload.get("short") or {})
    return {
        code: {
            "margin_balance_lots": (payload.get("margin") or {}).get(code),
            "short_balance_lots": (payload.get("short") or {}).get(code),
        }
        for code in codes
    }


def _mark(conn: sqlite3.Connection, ds: str, market: str, source: str,
          rows: dict, error: str | None = None) -> bool:
    ok = bool(rows)
    set_stock_source_coverage(conn, ds, market, source,
                              "complete" if ok else "failed", len(rows), error)
    return ok


def materialize_existing_cache(conn: sqlite3.Connection, days: int = 220) -> dict:
    """Copy useful historic general-cache payloads into normalized tables."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    counts = {"institutional": 0, "turnover": 0}
    keys = [row[0] for row in conn.execute(
        "SELECT cache_key FROM ai_cache WHERE cache_key LIKE 't86:%' "
        "OR cache_key LIKE 'tpex:%' OR cache_key LIKE 'turnover:tse:%' "
        "OR cache_key LIKE 'turnover:otc:%'").fetchall()]
    for key in keys:
        ds = key.rsplit(":", 1)[-1]
        if ds < cutoff:
            continue
        payload = get_ai_cache(conn, key)
        if not payload:
            continue
        if key.startswith("t86:") or key.startswith("tpex:"):
            market = "TWSE" if key.startswith("t86:") else "TPEx"
            bulk_upsert_stock_flow(conn, ds, market, _inst_rows(payload))
            set_stock_source_coverage(conn, ds, market, "institutional", "complete", len(payload))
            counts["institutional"] += 1
        else:
            market = "TWSE" if ":tse:" in key else "TPEx"
            normalized = {
                code: {"volume_lots": value.get("vol"), "amount_twd": value.get("amount")}
                for code, value in payload.items()
            }
            bulk_upsert_ohlc(conn, ds, normalized)
            counts["turnover"] += 1
            # A turnover cache completes quotes only when OHLC for that date already exists.
            full_codes = {row[0] for row in conn.execute(
                "SELECT code FROM stock_ohlc WHERE date=? AND open IS NOT NULL "
                "AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL "
                "AND volume_lots IS NOT NULL AND amount_twd IS NOT NULL", (ds,)).fetchall()}
            full = sum(code in full_codes for code in payload)
            if full:
                set_stock_source_coverage(conn, ds, market, "quotes", "complete", full)
    return counts


def _coverage_status(conn: sqlite3.Connection, ds: str, market: str, source: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM stock_source_coverage WHERE date=? AND market=? AND source=?",
        (ds, market, source),
    ).fetchone()
    return row[0] if row else None


def _fetch_quotes(conn: sqlite3.Connection, ds: str, market: str) -> dict:
    D = _day(ds)
    rows = twse.fetch_stock_daily(D) if market == "TWSE" else tpex.fetch_otc_daily(D)
    if rows:
        bulk_upsert_ohlc(conn, ds, rows)
    return rows


def _fetch_source(conn: sqlite3.Connection, ds: str, market: str, source: str) -> bool:
    D = _day(ds)
    if source == "institutional":
        key = f"t86:{ds}" if market == "TWSE" else f"tpex:{ds}"
        rows = get_ai_cache(conn, key)
        if rows is None:
            try:
                rows = twse.fetch_t86(D) if market == "TWSE" else tpex.fetch_tpex_insti(D)
            except Exception:  # noqa: BLE001 — coverage records the isolated source failure
                rows = {}
        if rows:
            bulk_upsert_stock_flow(conn, ds, market, _inst_rows(rows))
        return _mark(conn, ds, market, source, rows or {},
                     None if rows else "官方法人資料未回傳")
    try:
        payload = twse.fetch_margin_detail(D) if market == "TWSE" else tpex.fetch_otc_margin(D)
    except Exception:  # noqa: BLE001
        payload = {}
    rows = _margin_rows(payload or {})
    if rows:
        bulk_upsert_stock_flow(conn, ds, market, rows)
    return _mark(conn, ds, market, source, rows,
                 None if rows else "官方融資券資料未回傳")


def update_day(conn: sqlite3.Connection, D: date) -> dict:
    """Fetch each daily source once, persist it, and return payloads for shared consumers."""
    ds = D.isoformat()
    result = {}
    for market in MARKETS:
        try:
            quotes = _fetch_quotes(conn, ds, market)
        except Exception:  # noqa: BLE001
            quotes = {}
        _mark(conn, ds, market, "quotes", quotes,
              None if quotes else "官方行情資料未回傳")
        try:
            inst = twse.fetch_t86(D) if market == "TWSE" else tpex.fetch_tpex_insti(D)
        except Exception:  # noqa: BLE001
            inst = {}
        if inst:
            bulk_upsert_stock_flow(conn, ds, market, _inst_rows(inst))
        _mark(conn, ds, market, "institutional", inst or {},
              None if inst else "官方法人資料未回傳")
        try:
            margin = twse.fetch_margin_detail(D) if market == "TWSE" else tpex.fetch_otc_margin(D)
        except Exception:  # noqa: BLE001
            margin = {}
        margin_rows = _margin_rows(margin or {})
        if margin_rows:
            bulk_upsert_stock_flow(conn, ds, market, margin_rows)
        _mark(conn, ds, market, "margin", margin_rows,
              None if margin_rows else "官方融資券資料未回傳")
        result[market] = {"quotes": quotes, "institutional": inst or {},
                          "margin": margin or {}}
    return result


def backfill(conn: sqlite3.Connection, days: int = 220, max_fetch: int = 3,
             today: date | None = None) -> dict:
    """Resume backfill by trading date, retrying partial failures before older gaps."""
    days = max(60, min(int(days), 400))
    batch = max(1, min(int(max_fetch), 5))
    materialized = materialize_existing_cache(conn, days)
    calendar = _weekdays(days, today)
    state = {(row[0], row[1], row[2]): row[3] for row in conn.execute(
        "SELECT date, market, source, status FROM stock_source_coverage WHERE date>=?",
        (((today or date.today()) - timedelta(days=days)).isoformat(),),
    ).fetchall()}

    def priority(ds: str) -> tuple:
        values = [state.get((ds, market, source)) for market in MARKETS for source in SOURCES]
        return (0 if "failed" in values else 1 if any(values) else 2, -_day(ds).toordinal())

    candidates = sorted(calendar, key=priority)
    processed: list[str] = []
    errors: list[dict] = []
    scan_limit = batch + 12  # absorbs holidays without turning one request into an unbounded crawl
    scanned = 0
    for ds in candidates:
        if len(processed) >= batch or scanned >= scan_limit:
            break
        # "holiday" is terminal alongside "complete" — a date confirmed as a non-trading
        # day (see below) must never re-enter the scan; without this it would burn a
        # scanned/API-call slot on every single future call, forever, for no reason.
        if all(state.get((ds, market, source)) in ("complete", "holiday")
               for market in MARKETS for source in SOURCES):
            continue
        scanned += 1
        known_trading = any(state.get((ds, market, "quotes")) == "complete" for market in MARKETS)
        quote_results = {}
        for market in MARKETS:
            if state.get((ds, market, "quotes")) == "complete":
                quote_results[market] = True
                continue
            try:
                rows = _fetch_quotes(conn, ds, market)
            except Exception:  # noqa: BLE001
                rows = {}
            quote_results[market] = bool(rows)
        if not known_trading and not any(quote_results.values()):
            # Both markets came back empty and neither was ever confirmed trading on
            # this date — almost certainly a holiday, but a transient network hiccup
            # would look identical on the first observation (_fetch_quotes swallows
            # exceptions into {} too). So this is a two-round confirmation, not a
            # one-shot verdict: round 1 marks quotes "failed" (tier-0, retried first
            # on the next call); only if round 2 is *also* empty on both markets do we
            # commit to "holiday" across every (market, source) for this date. A single
            # bad network moment self-heals on the retry instead of permanently losing
            # a real trading day's data.
            already_suspect = all(
                state.get((ds, m, "quotes")) == "failed" for m in MARKETS)
            if already_suspect:
                for market in MARKETS:
                    for source in SOURCES:
                        set_stock_source_coverage(conn, ds, market, source, "holiday", 0)
            else:
                for market in MARKETS:
                    set_stock_source_coverage(conn, ds, market, "quotes", "failed", 0,
                                              "官方行情資料未回傳（可能為非交易日，將於下次重試後確認）")
            continue
        for market in MARKETS:
            if quote_results.get(market):
                if state.get((ds, market, "quotes")) != "complete":
                    set_stock_source_coverage(conn, ds, market, "quotes", "complete",
                                              conn.execute("SELECT COUNT(*) FROM stock_ohlc WHERE date=?",
                                                           (ds,)).fetchone()[0])
            elif state.get((ds, market, "quotes")) != "complete":
                set_stock_source_coverage(conn, ds, market, "quotes", "failed", 0,
                                          "官方行情資料未回傳")
                errors.append({"date": ds, "market": market, "source": "quotes",
                               "error": "官方行情資料未回傳"})
                continue
            for source in ("institutional", "margin"):
                if state.get((ds, market, source)) == "complete":
                    continue
                if not _fetch_source(conn, ds, market, source):
                    errors.append({"date": ds, "market": market, "source": source,
                                   "error": f"官方{source}資料未回傳"})
        processed.append(ds)
    report = coverage_report(conn, days=days, batch_size=batch, today=today)
    return {"busy": False, "days": days, "max_fetch": batch, "filled_dates": processed,
            "materialized": materialized, "errors": errors,
            "estimated_calls_remaining": report["estimated_calls_remaining"],
            "estimate_basis": report["estimate_basis"], "coverage": report["markets"],
            "data_version": report["data_version"]}


def coverage_report(conn: sqlite3.Connection, days: int = 220, batch_size: int = 3,
                    today: date | None = None) -> dict:
    days = max(60, min(int(days), 400))
    batch_size = max(1, min(int(batch_size), 5))
    end = today or date.today()
    cutoff = (end - timedelta(days=days)).isoformat()
    expected_dates = _weekdays(days, end)
    expected = len(expected_dates)
    rows = conn.execute(
        "SELECT date, market, source, status, row_count, last_error, updated_at "
        "FROM stock_source_coverage WHERE date>=? ORDER BY date", (cutoff,)).fetchall()
    by_key: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        by_key[(row[1], row[2])].append(row)
    markets = {}
    max_remaining = 0
    for market in MARKETS:
        sources = {}
        for source in SOURCES:
            items = by_key[(market, source)]
            complete = [row[0] for row in items if row[3] == "complete"]
            holidays = {row[0] for row in items if row[3] == "holiday"}
            failed = [{"date": row[0], "error": row[5]} for row in items if row[3] == "failed"]
            # Confirmed non-trading days (see backfill's two-round holiday check) are
            # excluded from the denominator, not just hidden from the gap list — without
            # this, expected_days stays fixed at "every weekday including holidays" and
            # completeness can never reach 100%, no matter how many times the user backfills.
            effective_expected = max(0, expected - len(holidays))
            remaining = max(0, effective_expected - len(complete))
            max_remaining = max(max_remaining, remaining)
            sources[source] = {
                "complete_days": len(complete), "expected_days": effective_expected,
                "percent": round(len(complete) / effective_expected * 100, 1) if effective_expected else 0,
                "oldest": min(complete) if complete else None,
                "latest": max(complete) if complete else None,
                "failed": failed[-20:], "missing_days": remaining,
                "holiday_days": len(holidays),
                "gaps": [ds for ds in expected_dates if ds not in set(complete) and ds not in holidays],
            }
        markets[market] = {"sources": sources}
    updated_at = max((row[6] for row in rows if row[6]), default=None)
    signature = "|".join(str(tuple(row)) for row in rows)
    data_version = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    calls = math.ceil(max_remaining / batch_size) if max_remaining else 0
    return {
        "days": days, "calendar_start": cutoff, "calendar_end": end.isoformat(),
        "estimated_trading_days": expected, "markets": markets,
        "estimated_calls_remaining": calls,
        "estimate_basis": f"以 {days} 日曆天內的平日估算交易日，依每批最多 {batch_size} 日計算；國定假日會使估值略高。",
        "updated_at": updated_at, "data_version": data_version,
    }


def _quartile(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "median": None, "q1": None, "q3": None, "n": 0,
                "positive_dates": 0, "positive_date_pct": None}
    if len(values) == 1:
        q1 = q3 = values[0]
    else:
        q1, _, q3 = quantiles(values, n=4, method="inclusive")
    positive = sum(value > 0 for value in values)
    return {"mean": round(sum(values) / len(values), 4),
            "median": round(median(values), 4), "q1": round(q1, 4),
            "q3": round(q3, 4), "n": len(values), "positive_dates": positive,
            "positive_date_pct": round(positive / len(values) * 100, 1)}


def forward_return(ohlc: dict, dates: list[str], signal_index: int,
                   horizon: int, code: str) -> float | None:
    """Return percent from the next session open to horizon-session close."""
    if signal_index + 1 >= len(dates) or signal_index + horizon >= len(dates):
        return None
    entry = ohlc.get((dates[signal_index + 1], code), (None, None))[0]
    close = ohlc.get((dates[signal_index + horizon], code), (None, None))[1]
    if entry in (None, 0) or close is None:
        return None
    return (close / entry - 1) * 100


def _market_research(conn: sqlite3.Connection, market: str) -> dict:
    flow_rows = conn.execute(
        "SELECT date, code, institutional_total_lots FROM stock_flow_daily "
        "WHERE market=? AND institutional_total_lots IS NOT NULL ORDER BY date, code", (market,)
    ).fetchall()
    dates = sorted({row[0] for row in flow_rows})
    values = defaultdict(dict)
    codes_by_date = defaultdict(set)
    for ds, code, total in flow_rows:
        values[code][ds] = float(total)
        codes_by_date[ds].add(code)
    ohlc = {(row[0], row[1]): (row[2], row[3]) for row in conn.execute(
        "SELECT date, code, open, close FROM stock_ohlc WHERE open IS NOT NULL OR close IS NOT NULL"
    ).fetchall()}
    breadth = []
    alpha_by_horizon = {h: [] for h in HORIZONS}
    observations = 0
    mature20 = 0
    date_payload = {}
    for i, ds in enumerate(dates):
        if i < max(WINDOWS) - 1:
            continue
        hits = []
        for code in codes_by_date[ds]:
            history = values[code]
            if all(all(dates[j] in history for j in range(i - window + 1, i + 1))
                   and sum(history[dates[j]] for j in range(i - window + 1, i + 1)) > 0
                   for window in WINDOWS):
                hits.append(code)
        universe = sorted(codes_by_date[ds])
        observations += len(hits)
        breadth.append({"date": ds, "hits": len(hits), "universe": len(universe),
                        "hit_pct": round(len(hits) / len(universe) * 100, 2) if universe else 0})
        date_payload[ds] = {"hits": hits, "universe": universe, "returns": {}, "mature": {}}
        for horizon in HORIZONS:
            if i + horizon >= len(dates) or i + 1 >= len(dates):
                continue
            date_payload[ds]["mature"][horizon] = True
            if horizon == 20:
                mature20 += 1
            def returns(codes):
                out = []
                for code in codes:
                    value = forward_return(ohlc, dates, i, horizon, code)
                    if value is not None:
                        out.append(value)
                return out

            hit_returns, universe_returns = returns(hits), returns(universe)
            if hit_returns and universe_returns:
                alpha = sum(hit_returns) / len(hit_returns) - sum(universe_returns) / len(universe_returns)
                alpha_by_horizon[horizon].append(alpha)
                date_payload[ds]["returns"][horizon] = {
                    "hit": hit_returns, "universe": universe_returns, "alpha": alpha}
    med_hits = median([row["hits"] for row in breadth]) if breadth else 0
    med_pct = median([row["hit_pct"] for row in breadth]) if breadth else 0
    return {
        "market": market, "valid_dates": len(breadth), "mature_ret20_dates": mature20,
        "hit_observations": observations, "daily_hit_median": med_hits,
        "daily_hit_pct_median": round(med_pct, 2), "breadth": breadth,
        "alpha": {f"ret{h}": _quartile(alpha_by_horizon[h]) for h in HORIZONS},
        "_dates": date_payload,
    }


def _combined_research(twse_result: dict, tpex_result: dict) -> dict:
    dates = sorted(set(twse_result["_dates"]) | set(tpex_result["_dates"]))
    breadth = []
    alpha_values = {h: [] for h in HORIZONS}
    observations = 0
    mature20 = 0
    for ds in dates:
        parts = [result["_dates"].get(ds) for result in (twse_result, tpex_result)]
        parts = [part for part in parts if part]
        hits = sum(len(part["hits"]) for part in parts)
        universe = sum(len(part["universe"]) for part in parts)
        observations += hits
        breadth.append({"date": ds, "hits": hits, "universe": universe,
                        "hit_pct": round(hits / universe * 100, 2) if universe else 0})
        if any(part.get("mature", {}).get(20) for part in parts):
            mature20 += 1
        for horizon in HORIZONS:
            ret_parts = [part["returns"].get(horizon) for part in parts
                         if part["returns"].get(horizon)]
            if not ret_parts:
                continue
            hit_returns = [value for part in ret_parts for value in part["hit"]]
            universe_returns = [value for part in ret_parts for value in part["universe"]]
            if hit_returns and universe_returns:
                alpha_values[horizon].append(
                    sum(hit_returns) / len(hit_returns) - sum(universe_returns) / len(universe_returns))
    return {
        "market": "combined", "valid_dates": len(breadth),
        "mature_ret20_dates": mature20, "hit_observations": observations,
        "daily_hit_median": median([row["hits"] for row in breadth]) if breadth else 0,
        "daily_hit_pct_median": round(median([row["hit_pct"] for row in breadth]), 2) if breadth else 0,
        "breadth": breadth,
        "alpha": {f"ret{h}": _quartile(alpha_values[h]) for h in HORIZONS},
    }


def classify_research(results: dict) -> dict:
    combined = results["combined"]
    markets = (results["twse"], results["tpex"])
    if (any(result["valid_dates"] < MIN_VALID_DATES or
            result["mature_ret20_dates"] < MIN_MATURE_RET20_DATES or
            result["hit_observations"] < MIN_HIT_OBSERVATIONS for result in markets)):
        return {"code": "insufficient_data", "label": "資料尚不足",
                "message": "日期、成熟期或任一市場的命中觀測尚未達研究下限。"}
    if combined["daily_hit_median"] > 100 or combined["daily_hit_pct_median"] > 10:
        return {"code": "too_broad", "label": "訊號過寬",
                "message": "每日命中範圍過大，較像市場狀態而非個股辨識條件。"}
    if 0 <= combined["daily_hit_median"] <= 2:
        return {"code": "too_sparse", "label": "訊號過稀",
                "message": "資料量已足夠，但每日命中中位數不足以形成穩定樣本。"}
    combined_positive = all((combined["alpha"][f"ret{h}"]["mean"] or 0) > 0 for h in (10, 20))
    directions_agree = all(
        (markets[0]["alpha"][f"ret{h}"]["mean"] or 0) *
        (markets[1]["alpha"][f"ret{h}"]["mean"] or 0) > 0 for h in (10, 20))
    if not combined_positive or not directions_agree:
        return {"code": "no_historical_edge", "label": "未見歷史優勢",
                "message": "Ret10／Ret20 未同時為正，或上市與上櫃的方向不一致。"}
    return {"code": "candidate_for_prospective", "label": "僅可前瞻觀察",
            "message": CANDIDATE_WARNING}


def research(conn: sqlite3.Connection) -> dict:
    twse_result = _market_research(conn, "TWSE")
    tpex_result = _market_research(conn, "TPEx")
    combined = _combined_research(twse_result, tpex_result)
    results = {"combined": combined, "twse": twse_result, "tpex": tpex_result}
    verdict = classify_research(results)
    for result in (twse_result, tpex_result):
        result.pop("_dates", None)
    version = coverage_report(conn)["data_version"]
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_version": version, "windows": list(WINDOWS), "returns": list(HORIZONS),
            "entry_rule": "訊號日次一交易日開盤", "results": results,
            "verdict": verdict, "disclaimer": DISCLAIMER,
            "candidate_warning": CANDIDATE_WARNING}
