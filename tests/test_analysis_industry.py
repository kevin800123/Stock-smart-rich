from stocks_power_rich.analysis import industry_aggregate

ROWS = [
    {"code": "A", "industry": "半導體", "big_holder_ratio": 0.9, "holder_drop_ratio": -0.5},
    {"code": "C", "industry": "半導體", "big_holder_ratio": 0.6, "holder_drop_ratio": -0.3},
    {"code": "B", "industry": "水泥", "big_holder_ratio": 0.1, "holder_drop_ratio": 0.2},
]


def test_aggregates_and_ranks_industry():
    out = industry_aggregate(ROWS)
    assert out[0]["industry"] == "半導體"
    assert out[0]["count"] == 2
    assert round(out[0]["avg_score"], 2) == round(((0.9 + 0.5) + (0.6 + 0.3)) / 2, 2)
