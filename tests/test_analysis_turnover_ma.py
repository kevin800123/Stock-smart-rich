from stocks_power_rich import analysis


def test_turnover_ma_rolls_over_last_n_values():
    """逐點回傳「到該點為止最近 n 個有效值」的均值，長度與輸入相同。"""
    vals = [10.0, 20.0, 30.0, 40.0]
    out = analysis.turnover_ma(vals, n=2)
    assert len(out) == len(vals)
    assert out[0] is None                 # 只有 1 筆，不足 n=2
    assert out[1] == 15.0                 # (10+20)/2
    assert out[2] == 25.0                 # (20+30)/2
    assert out[3] == 35.0                 # (30+40)/2


def test_turnover_ma_returns_none_until_n_valid_values():
    """不足 n 筆有效值一律 None——用 3 筆算「10 日均量」是誤導，寧可留白。"""
    out = analysis.turnover_ma([1.0, 2.0, 3.0], n=10)
    assert out == [None, None, None]


def test_turnover_ma_skips_nulls_instead_of_breaking_the_window():
    """中間的 None 略過而非中斷視窗。

    turnover 偶因來源當日尚未發布而留 NULL；若要求連續 n 筆非空，一個洞會讓
    後面 n 列全部算不出來。所以取的是「最近 n 個有效值」，可以跨過洞往前撈。
    """
    vals = [10.0, None, 20.0, 30.0]
    out = analysis.turnover_ma(vals, n=2)
    assert out[0] is None                 # 只有 1 筆有效
    assert out[1] is None                 # None 這一列本身仍只有 1 筆有效值可用
    assert out[2] == 15.0                 # 跨過洞取 10、20
    assert out[3] == 25.0                 # (20+30)/2


def test_turnover_ma_empty_input():
    assert analysis.turnover_ma([], n=10) == []


def test_turnover_ma_all_nulls():
    assert analysis.turnover_ma([None, None, None], n=2) == [None, None, None]
