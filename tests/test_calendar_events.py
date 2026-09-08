"""下週行事曆的組裝層（純函式）。"""
from datetime import date

import pytest

from stocks_power_rich import calendar_events as ce


def test_next_week_range_is_monday_to_sunday_of_the_following_week():
    # 2026-09-13 是週日（推播當下），要的是隔天起算那一週
    assert ce.next_week_range(date(2026, 9, 13)) == ("2026-09-14", "2026-09-20")


def test_next_week_range_works_from_any_weekday():
    """排程只在週日跑，但手動觸發／測試可能落在任何一天，結果必須是同一週。"""
    for d in (date(2026, 9, 7), date(2026, 9, 9), date(2026, 9, 13)):
        assert ce.next_week_range(d) == ("2026-09-14", "2026-09-20")


def test_next_week_range_crosses_the_year_boundary():
    assert ce.next_week_range(date(2026, 12, 27)) == ("2026-12-28", "2027-01-03")


def test_zh_weekday_matches_the_calendar():
    assert ce.zh_weekday("2026-09-14") == "一"
    assert ce.zh_weekday("2026-09-20") == "日"


def test_sort_events_orders_by_date_then_by_declared_kind_order():
    """同一天多筆時，總經在前、個股財報在後——讀者先要知道那天有沒有大事。"""
    events = [
        {"date": "2026-09-16", "kind": "earnings", "label": "NVDA 財報"},
        {"date": "2026-09-15", "kind": "earnings", "label": "ORCL 財報"},
        {"date": "2026-09-16", "kind": "macro", "label": "FOMC 決議"},
    ]
    got = [e["label"] for e in ce.sort_events(events)]
    assert got == ["ORCL 財報", "FOMC 決議", "NVDA 財報"]


def test_pick_big_movers_keeps_only_the_largest_and_caps_the_count():
    rows = [{"symbol": f"S{i}", "market_cap": i * 1e9} for i in range(1, 12)]
    got = ce.pick_big_caps(rows, min_cap=5e9, limit=3)
    assert [r["symbol"] for r in got] == ["S11", "S10", "S9"]


def test_pick_big_caps_returns_nothing_when_none_clear_the_bar():
    rows = [{"symbol": "A", "market_cap": 1e9}]
    assert ce.pick_big_caps(rows, min_cap=5e9, limit=3) == []


def test_dedupe_same_stock_on_the_same_day():
    """實跑 2026-09-16 抓到廣達兩場法說會（09:00 與 14:00，中英文各一場）。
    行事曆要回答的是「那天誰要開法說會」，同一檔列兩次只是重複，還會吃掉
    一個顯示名額（限額是在去重之後才套用的）。不同日期的同一檔要保留。"""
    rows = [
        {"date": "2026-09-16", "code": "2382", "label": "廣達 09:00", "market_cap": 9e11},
        {"date": "2026-09-16", "code": "2382", "label": "廣達 14:00", "market_cap": 9e11},
        {"date": "2026-09-17", "code": "2382", "label": "廣達 10:00", "market_cap": 9e11},
        {"date": "2026-09-16", "code": "2330", "label": "台積電 14:00", "market_cap": 3e13},
    ]
    got = [(r["date"], r["code"]) for r in ce.dedupe_by_day(rows, "code")]
    assert got == [("2026-09-16", "2382"), ("2026-09-17", "2382"),
                   ("2026-09-16", "2330")]


def test_dedupe_by_day_keeps_the_first_occurrence():
    rows = [{"date": "d", "code": "x", "label": "早"},
            {"date": "d", "code": "x", "label": "晚"}]
    assert [r["label"] for r in ce.dedupe_by_day(rows, "code")] == ["早"]


def test_clean_us_name_strips_the_legal_suffix():
    """Nasdaq 的 name 帶法律後綴（Apple Inc.／Exxon Mobil Corporation），
    在行事曆裡是純噪音，而且會把一行擠長。"""
    cases = {
        "Apple Inc.": "Apple",
        "Visa Inc.": "Visa",
        "Exxon Mobil Corporation": "Exxon Mobil",
        "Eli Lilly and Company": "Eli Lilly",
        "The Descartes Systems Group Inc.": "Descartes Systems Group",
        "Oracle Corporation": "Oracle",
        "Tesla, Inc.": "Tesla",
        "PepsiCo, Inc.": "PepsiCo",
        "Delta Air Lines, Inc.": "Delta Air Lines",
    }
    for raw, want in cases.items():
        assert ce.clean_company_name(raw) == want, raw


def test_clean_us_name_truncates_a_very_long_name():
    long = "Some Extremely Long Company Name That Would Wrap The Line Twice"
    got = ce.clean_company_name(long, limit=20)
    assert len(got) <= 21 and got.endswith("…")


def test_clean_us_name_falls_back_to_the_original_when_nothing_is_left():
    """只由後綴組成的怪名字不要被清成空字串——寧可原樣顯示。"""
    assert ce.clean_company_name("Inc.") == "Inc."
    assert ce.clean_company_name("") == ""
