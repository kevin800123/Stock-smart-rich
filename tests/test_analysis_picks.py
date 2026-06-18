from stocks_power_rich.analysis import filtered_picks, subindustry_counts

ROWS = [
    # 通過：W55=1、大戶增比>0、年增>0、推估EPS>0
    {"code": "A", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80, "sub_industry": "晶圓"},
    {"code": "B", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 50, "sub_industry": "晶圓"},
    {"code": "C", "w55": 0, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 99, "sub_industry": "IC設計"},   # W55≠1 濾掉
    {"code": "D", "w55": 1, "big_holder_ratio": -0.1, "rev_yoy": 10, "est_profit": 1, "lan_value": 99, "sub_industry": "IC設計"},  # 大戶≤0 濾掉
    {"code": "E", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": -1, "est_profit": 1, "lan_value": 99, "sub_industry": "IC設計"},   # 年增≤0 濾掉
    {"code": "F", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": -1, "lan_value": 99, "sub_industry": "IC設計"},  # EPS≤0 濾掉
    {"code": "G", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 65, "sub_industry": "IC設計"},
]


def test_filtered_picks_filters_then_sorts_by_lan_value_desc():
    out = filtered_picks(ROWS)
    assert [r["code"] for r in out] == ["A", "G", "B"]  # 蘭值 80 > 65 > 50


def test_subindustry_counts_from_picks():
    out = subindustry_counts(filtered_picks(ROWS))
    # 通過的：A,B(晶圓) + G(IC設計)
    assert out[0] == {"sub_industry": "晶圓", "count": 2}
    assert out[1] == {"sub_industry": "IC設計", "count": 1}
