from stocks_power_rich.analysis import (
    filtered_picks,
    industry_to_sector,
    picks_by_sector,
    subindustry_counts,
)

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


def test_industry_to_sector_strips_prefix_and_aliases():
    assert industry_to_sector("上市半導體") == "半導體"
    assert industry_to_sector("上櫃光電") == "光電"
    assert industry_to_sector("上市化工") == "化學"      # 別名
    assert industry_to_sector("上市航運業") == "航運"    # 別名
    assert industry_to_sector("上市金融") == "金融保險"  # 別名
    assert industry_to_sector("") is None


def test_picks_by_sector_groups_and_sorts_by_change():
    picks = [
        {"code": "2330", "industry": "上市半導體"},
        {"code": "3008", "industry": "上市光電"},
        {"code": "2317", "industry": "上市其他電子"},
        {"code": "6488", "industry": "上櫃光電"},
    ]
    chg = {"半導體": -3.41, "光電": -7.37, "其他電子": -3.83}
    out = picks_by_sector(picks, chg)
    assert [g["sector"] for g in out] == ["半導體", "其他電子", "光電"]  # 強→弱
    light = next(g for g in out if g["sector"] == "光電")
    assert light["count"] == 2 and light["chg_pct"] == -7.37
