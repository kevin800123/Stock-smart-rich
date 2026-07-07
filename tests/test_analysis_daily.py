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


def test_margin_maintenance_ratio():
    from stocks_power_rich.analysis import margin_maintenance

    # 融資部位市值 = 9,050張×1000×100元 + 1,020張×1000×50元 = 9.56 億；融資金額 6 億 → 159.3%
    lots = {"2330": 9050, "0050": 1020, "9999": 500}   # 9999 無報價 → 不計（保守）
    closes = {"2330": 100.0, "0050": 50.0}
    assert margin_maintenance(lots, closes, 6.0) == 159.3
    assert margin_maintenance(lots, closes, 0) is None      # 無融資金額
    assert margin_maintenance({}, closes, 6.0) is None      # 無部位
