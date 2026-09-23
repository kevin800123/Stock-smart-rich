from stocks_power_rich.analysis import daily_signals

ROWS = [
    {"code": "A", "name": "a", "big_holder_ratio": 0.9, "holder_drop_ratio": -0.5, "w55": 1, "rev_yoy": 10, "trust_3d": 2, "foreign_3d": 3, "industry": "半導體"},
    {"code": "B", "name": "b", "big_holder_ratio": 0.1, "holder_drop_ratio": 0.2, "w55": 0, "rev_yoy": -3, "trust_3d": 0, "foreign_3d": 0, "industry": "水泥"},
    {"code": "C", "name": "c", "big_holder_ratio": 0.6, "holder_drop_ratio": -0.3, "w55": 1, "rev_yoy": 5, "trust_3d": 1, "foreign_3d": -1, "industry": "半導體"},
]


def test_ranks_big_holder_up_retail_down_first():
    out = daily_signals(ROWS, top_n=2)
    assert [r["code"] for r in out] == ["A", "C"]
    assert out[0]["score"] >= out[1]["score"]
    assert out[0]["flags"]["w55_bull"] is True
    assert out[0]["flags"]["rev_growth"] is True


def test_credit_ratios_follow_twse_dashboard_formulas():
    from stocks_power_rich.analysis import credit_ratios

    # 2026-09-22 證交所頁面：融資金額 6048.6 億 ÷ 上市總市值 1,563,443.68 億 = 0.39%；
    # 信用交易成交值 1433.06 億 ÷ (2 × 市場總成交值 10787.8 億) = 6.64%——分母乘 2 是
    # 證交所 JS 的原式（買賣兩邊各算一次成交值）。不乘 2 會得到 13.3%。
    out = credit_ratios(6048.6, 1563443.68, 1433.06, 10787.8)
    assert out == {"margin_mcap_pct": 0.39, "credit_ratio": 6.64}


def test_credit_ratios_return_none_per_field_when_inputs_missing_or_zero():
    from stocks_power_rich.analysis import credit_ratios

    assert credit_ratios(None, 1563443.68, 1433.06, 10787.8) == {"margin_mcap_pct": None, "credit_ratio": 6.64}
    assert credit_ratios(6048.6, 0, 1433.06, 10787.8)["margin_mcap_pct"] is None
    assert credit_ratios(6048.6, 1563443.68, None, 10787.8)["credit_ratio"] is None
    assert credit_ratios(6048.6, 1563443.68, 1433.06, 0)["credit_ratio"] is None


def test_short_margin_ratio_and_rolling_change():
    from stocks_power_rich.analysis import short_margin_ratio, rolling_change

    # 2026-09-22：融券 218,839 張 ÷ 融資 9,245,371 張 = 2.37%
    assert short_margin_ratio(218839, 9245371) == 2.37
    assert short_margin_ratio(None, 9245371) is None
    assert short_margin_ratio(218839, 0) is None
    # 5 日增減：往前第 5 個「有效值」，缺值略過不中斷；不足 6 筆有效值給 None
    vals = [100, 110, None, 120, 130, 140, 150, 165]
    assert rolling_change(vals, 5) == [None, None, None, None, None, None, 50, 55]
    assert rolling_change([1, 2, 3], 5) == [None, None, None]
