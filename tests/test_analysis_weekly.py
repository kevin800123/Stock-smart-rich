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
