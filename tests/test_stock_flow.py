from datetime import date, timedelta

from stocks_power_rich import stock_flow
from stocks_power_rich.db import get_connection, init_db


def _alpha(mean):
    return {"mean": mean, "median": mean, "q1": mean, "q3": mean, "n": 40,
            "positive_dates": 40 if mean > 0 else 0,
            "positive_date_pct": 100.0 if mean > 0 else 0.0}


def _result(hits=30, median_hits=3, hit_pct=1, ret10=1, ret20=1,
            valid=60, mature=40):
    return {"valid_dates": valid, "mature_ret20_dates": mature,
            "hit_observations": hits, "daily_hit_median": median_hits,
            "daily_hit_pct_median": hit_pct,
            "alpha": {"ret5": _alpha(0), "ret10": _alpha(ret10), "ret20": _alpha(ret20)}}


def _results(**combined_overrides):
    market = _result()
    combined = _result(**combined_overrides)
    return {"combined": combined, "twse": dict(market), "tpex": dict(market)}


def test_research_guard_observation_boundary_29_and_30():
    results = _results()
    results["tpex"] = _result(hits=29)
    assert stock_flow.classify_research(results)["code"] == "insufficient_data"
    results["tpex"] = _result(hits=30)
    assert stock_flow.classify_research(results)["code"] == "candidate_for_prospective"


def test_research_guard_breadth_boundaries_2_3_100_101():
    assert stock_flow.classify_research(_results(median_hits=2))["code"] == "too_sparse"
    assert stock_flow.classify_research(_results(median_hits=3))["code"] == "candidate_for_prospective"
    assert stock_flow.classify_research(_results(median_hits=100))["code"] == "candidate_for_prospective"
    assert stock_flow.classify_research(_results(median_hits=101))["code"] == "too_broad"
    assert stock_flow.classify_research(_results(median_hits=3, hit_pct=10))["code"] == "candidate_for_prospective"
    assert stock_flow.classify_research(_results(median_hits=3, hit_pct=10.01))["code"] == "too_broad"


def test_research_guard_rejects_market_direction_disagreement():
    results = _results()
    results["tpex"] = _result(ret10=-1, ret20=-1)
    assert stock_flow.classify_research(results)["code"] == "no_historical_edge"


def test_alpha_quartiles_and_positive_date_share():
    stats = stock_flow._quartile([-2, 0, 2, 4])
    assert stats == {"mean": 1.0, "median": 1.0, "q1": -0.5, "q3": 2.5, "n": 4,
                     "positive_dates": 2, "positive_date_pct": 50.0}


def test_forward_return_starts_at_next_trading_day_open():
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
             "2026-01-08", "2026-01-09"]
    ohlc = {
        (dates[0], "2330"): (10, 10),
        (dates[1], "2330"): (20, 21),
        (dates[5], "2330"): (29, 30),
    }
    assert stock_flow.forward_return(ohlc, dates, 0, 5, "2330") == 50.0


def test_coverage_uses_220_calendar_days_and_estimates_batches(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    report = stock_flow.coverage_report(conn, days=220, batch_size=3,
                                        today=date(2026, 8, 10))
    assert report["calendar_start"] == "2026-01-02"
    assert report["estimated_trading_days"] == 157
    assert report["estimated_calls_remaining"] == 53
    assert "每批最多 3 日" in report["estimate_basis"]
    assert len(report["markets"]["TWSE"]["sources"]["quotes"]["gaps"]) == 157


def test_backfill_counts_trading_dates_and_keeps_market_failures_separate(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    calls = {"twse_quotes": 0, "tpex_quotes": 0}

    def twse_quotes(day=None):
        calls["twse_quotes"] += 1
        return {"2330": {"open": 1, "high": 2, "low": 1, "close": 2,
                         "volume_lots": 10, "amount_twd": 100}}

    def tpex_quotes(day=None):
        calls["tpex_quotes"] += 1
        return {}

    monkeypatch.setattr(stock_flow.twse, "fetch_stock_daily", twse_quotes)
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_daily", tpex_quotes)
    monkeypatch.setattr(stock_flow.twse, "fetch_t86", lambda day=None: {
        "2330": {"name": "台積電", "foreign": 1, "trust": 1, "dealer": 0, "total": 2}})
    monkeypatch.setattr(stock_flow.twse, "fetch_margin_detail", lambda day=None: {
        "margin": {"2330": 0}, "short": {"2330": 0}})
    monkeypatch.setattr(stock_flow.tpex, "fetch_tpex_insti", lambda day=None: {})
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_margin", lambda day=None: {
        "margin": {}, "short": {}})

    result = stock_flow.backfill(conn, days=60, max_fetch=1, today=date(2026, 8, 10))
    assert len(result["filled_dates"]) == 1
    assert calls == {"twse_quotes": 1, "tpex_quotes": 1}
    rows = conn.execute(
        "SELECT market, source, status FROM stock_source_coverage "
        "WHERE date=? ORDER BY market, source", (result["filled_dates"][0],)).fetchall()
    assert ("TPEx", "quotes", "failed") in [tuple(row) for row in rows]
    assert ("TWSE", "quotes", "complete") in [tuple(row) for row in rows]


def test_daily_update_fetches_each_shared_source_once(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    calls = {name: 0 for name in ("twq", "otcq", "twi", "otci", "twm", "otcm")}

    def once(name, payload):
        def fetch(day=None):
            calls[name] += 1
            return payload
        return fetch

    quote = {"2330": {"open": 1, "high": 2, "low": 1, "close": 2,
                       "volume_lots": 10, "amount_twd": 100}}
    inst = {"2330": {"name": "台積電", "foreign": 1, "trust": 0, "dealer": 0, "total": 1}}
    margin = {"margin": {"2330": 0}, "short": {"2330": 0}}
    monkeypatch.setattr(stock_flow.twse, "fetch_stock_daily", once("twq", quote))
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_daily", once("otcq", quote))
    monkeypatch.setattr(stock_flow.twse, "fetch_t86", once("twi", inst))
    monkeypatch.setattr(stock_flow.tpex, "fetch_tpex_insti", once("otci", inst))
    monkeypatch.setattr(stock_flow.twse, "fetch_margin_detail", once("twm", margin))
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_margin", once("otcm", margin))

    payloads = stock_flow.update_day(conn, date(2026, 8, 7))
    assert calls == {name: 1 for name in calls}
    assert payloads["TWSE"]["quotes"]["2330"]["volume_lots"] == 10
    assert conn.execute("SELECT COUNT(*) FROM stock_source_coverage WHERE status='complete'").fetchone()[0] == 6


def test_daily_update_keeps_other_market_when_one_source_raises(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(stock_flow.twse, "fetch_stock_daily", lambda day=None: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_daily", lambda day=None: {
        "6488": {"open": 1, "high": 2, "low": 1, "close": 2, "volume_lots": 10, "amount_twd": 100}})
    monkeypatch.setattr(stock_flow.twse, "fetch_t86", lambda day=None: {})
    monkeypatch.setattr(stock_flow.tpex, "fetch_tpex_insti", lambda day=None: {
        "6488": {"name": "環球晶", "foreign": 1, "trust": 0, "dealer": 0, "total": 1}})
    monkeypatch.setattr(stock_flow.twse, "fetch_margin_detail", lambda day=None: {})
    monkeypatch.setattr(stock_flow.tpex, "fetch_otc_margin", lambda day=None: {
        "margin": {"6488": 0}, "short": {"6488": 0}})
    stock_flow.update_day(conn, date(2026, 8, 7))
    states = {(row[0], row[1]): row[2] for row in conn.execute(
        "SELECT market, source, status FROM stock_source_coverage")}
    assert states[("TWSE", "quotes")] == "failed"
    assert states[("TPEx", "quotes")] == "complete"


def test_mature_dates_do_not_depend_on_having_a_hit(tmp_path):
    from stocks_power_rich.db import bulk_upsert_ohlc, bulk_upsert_stock_flow

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    cursor = date(2026, 1, 1)
    days = []
    while len(days) < 80:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    for ds in days:
        bulk_upsert_stock_flow(conn, ds, "TWSE", {"2330": {"institutional_total_lots": -1}})
        bulk_upsert_ohlc(conn, ds, {"2330": {"open": 100, "close": 100}})
    result = stock_flow._market_research(conn, "TWSE")
    assert result["valid_dates"] == 21
    assert result["mature_ret20_dates"] == 1
    assert result["hit_observations"] == 0


def test_turnover_cache_cannot_complete_the_other_market(tmp_path):
    from stocks_power_rich.db import bulk_upsert_ohlc, set_ai_cache

    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    ds = date.today().isoformat()
    bulk_upsert_ohlc(conn, ds, {"2330": {"open": 1, "high": 2, "low": 1, "close": 2,
                                                "volume_lots": 10, "amount_twd": 100}})
    set_ai_cache(conn, f"turnover:otc:{ds}", {"6488": {"vol": 8, "amount": 80}})
    stock_flow.materialize_existing_cache(conn, days=220)
    assert conn.execute(
        "SELECT 1 FROM stock_source_coverage WHERE date=? AND market='TPEx' AND source='quotes'",
        (ds,)).fetchone() is None


def test_research_api_is_fixed_and_always_returns_disclaimer(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from fastapi.testclient import TestClient
    from stocks_power_rich.main import create_app

    client = TestClient(create_app())
    coverage = client.get("/api/stock-flow/coverage?days=220").json()
    assert coverage["days"] == 220
    assert set(coverage["markets"]) == {"TWSE", "TPEx"}
    report = client.post("/api/stock-flow/research").json()
    assert report["verdict"]["code"] == "insufficient_data"
    assert report["disclaimer"] == stock_flow.DISCLAIMER
    assert report["candidate_warning"] == stock_flow.CANDIDATE_WARNING


def test_institutional_research_frontend_covers_verdicts_mobile_and_stale_states():
    from pathlib import Path

    web = Path(__file__).parents[1] / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")
    assert 'data-view="inst-research" data-short="法人研究"' in html
    assert 'data-view="inst-research" data-primary' not in html
    assert 'id="view-inst-research"' in html
    for code, label in (("insufficient_data", "資料尚不足"), ("too_broad", "訊號過寬"),
                        ("too_sparse", "訊號過稀"), ("no_historical_edge", "未見歷史優勢"),
                        ("candidate_for_prospective", "僅可前瞻觀察")):
        assert code in js and label in js
    for copy in ("系統忙碌", "資料已更新，請重新計算", "正在計算全市場", "部分來源失敗"):
        assert copy in html + js
    assert 'id="ir-candidate-warning"' in html and 'role="note"' in html
    assert "@media (max-width: 600px)" in css
    assert ".ir-readiness { grid-template-columns: 1fr" in css
    assert "本頁為歷史資料研究工具，不構成投資建議" in html
