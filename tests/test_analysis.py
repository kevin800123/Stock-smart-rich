

def test_top_movers_filters_warrants_out_of_the_ranking():
    """原始報價快取約 14k 筆、6 碼權證佔絕大多數且天天漲跌停——不濾的話整個榜都是權證。
    過濾是這份排行「定義」的一部分，所以寫在純函式裡才測得到。"""
    from stocks_power_rich.analysis import top_movers

    rows = [
        {"code": "2330", "name": "台積電", "chg_pct": 1.5},
        {"code": "031234", "name": "某某購01", "chg_pct": 10.0},   # 6 碼權證
        {"code": "00878", "name": "國泰永續高股息", "chg_pct": 0.8},  # 5 碼 ETF
        {"code": "2615", "name": "萬海", "chg_pct": -9.9},
    ]
    out = top_movers(rows, n=5)
    codes = [r["code"] for r in out["up"]] + [r["code"] for r in out["down"]]
    assert "031234" not in codes and "00878" not in codes
    assert out["n"] == 2


def test_top_movers_orders_both_ends_and_respects_n():
    from stocks_power_rich.analysis import top_movers

    rows = [{"code": f"1{i:03d}", "name": f"股{i}", "chg_pct": float(i)} for i in range(1, 10)]
    rows += [{"code": f"2{i:03d}", "name": f"跌{i}", "chg_pct": -float(i)} for i in range(1, 10)]
    out = top_movers(rows, n=3)
    assert [r["chg_pct"] for r in out["up"]] == [9.0, 8.0, 7.0]
    assert [r["chg_pct"] for r in out["down"]] == [-9.0, -8.0, -7.0]
    assert out["n"] == 18


def test_top_movers_is_stable_when_percentages_tie():
    """同漲跌幅時以代號排序，否則結果會隨 dict 順序抖動、每次重整都換一批。"""
    from stocks_power_rich.analysis import top_movers

    rows = [{"code": c, "name": c, "chg_pct": 10.0} for c in ("2609", "2603", "2615")]
    assert [r["code"] for r in top_movers(rows, n=2)["up"]] == ["2603", "2609"]


def test_top_movers_skips_missing_pct_and_handles_empty():
    from stocks_power_rich.analysis import top_movers

    out = top_movers([{"code": "2330", "name": "台積電", "chg_pct": None}], n=5)
    assert out == {"up": [], "down": [], "n": 0}
    assert top_movers([], n=5)["n"] == 0


def test_top_movers_never_pads_a_ranking_with_the_wrong_direction():
    """全面下殺那天，「漲幅排行」寧可只有 1 列，也不能把 −4% 的股票排進去湊滿——
    那一列會直接說謊。"""
    from stocks_power_rich.analysis import top_movers

    rows = [{"code": "2330", "name": "台積電", "chg_pct": 0.5},
            {"code": "2615", "name": "萬海", "chg_pct": -7.5},
            {"code": "3105", "name": "穩懋", "chg_pct": -4.0},
            {"code": "2603", "name": "長榮", "chg_pct": 0.0}]      # 平盤兩邊都不進
    out = top_movers(rows, n=5)
    assert [r["code"] for r in out["up"]] == ["2330"]
    assert [r["code"] for r in out["down"]] == ["2615", "3105"]
    assert out["n"] == 4          # 採計檔數仍算全部有效報價
