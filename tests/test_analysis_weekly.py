from stocks_power_rich.analysis import weekly_comparison

LAST = [
    {"code": "A", "name": "a", "custody": 70, "big_holder_ratio": 0.3, "industry": "半導體"},
    {"code": "B", "name": "b", "custody": 50, "big_holder_ratio": 0.2, "industry": "水泥"},
]
THIS = [
    {"code": "A", "name": "a", "custody": 75, "big_holder_ratio": 0.6, "industry": "半導體"},  # 大戶加碼
    {"code": "C", "name": "c", "custody": 40, "big_holder_ratio": 0.5, "industry": "航運"},      # 新進榜
]


def test_weekly_marks_status_and_delta():
    out = weekly_comparison(THIS, LAST)
    by = {r["code"]: r for r in out["stocks"]}
    assert by["A"]["custody_delta"] == 5
    assert by["A"]["status"] == "加速"
    assert by["C"]["status"] == "新進榜"
    assert by["B"]["status"] == "退榜"


def test_pick_weekly_pair_crosses_week_boundary():
    """本期=最新；上期=最新一筆 ISO 週早於本期的快照（上週的最後一份 CSV）。"""
    from stocks_power_rich.analysis import pick_weekly_pair
    # 2026-07-16(四)/07-17(五) 同屬 W29；07-08(三)/07-10(五) 屬 W28
    dates = ["2026-07-08", "2026-07-10", "2026-07-16", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-10")


def test_pick_weekly_pair_same_week_falls_back_to_prev_snapshot():
    """全部同週（還沒有上週資料）→ 退回前一筆，至少能比。"""
    from stocks_power_rich.analysis import pick_weekly_pair
    dates = ["2026-07-16", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-16")


def test_pick_weekly_pair_single_and_multiweek():
    from stocks_power_rich.analysis import pick_weekly_pair
    assert pick_weekly_pair(["2026-07-17"]) == ("2026-07-17", None)
    # 跨多週：取「最近的較早週」（W28 的 07-06），不是更早的 W27
    dates = ["2026-06-24", "2026-07-06", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-06")
