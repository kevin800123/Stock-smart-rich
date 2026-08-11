"""木質 / 木率 評分邏輯層（Stage 1）。

lan_score＝忠實還原蘭弦「蘭質」15 項（先鎖邏輯，Stage 2 才接季報源）；
mu_score＝木質（財報分 × 本站籌碼）；mu_value＝木率（木質÷本業PE×100 + 品質閘）。
純函數、無 I/O，算不出回 None——同 estimate_price_range / margin_maintenance 風格。
"""

from stocks_power_rich import analysis


# ---------- lan_score（15 項蘭質） ----------

def _base():
    """一份合法、剛好 0 分的季報輸入（全常數 → 每個嚴格 > / < 比較都不成立）。

    各數列長度 8（capex 需要近 8 季）。最新在前，[n]＝n 季前，[4]＝去年同季。
    """
    return {
        "revenue":       [100.0] * 8,
        "pretax_income": [100.0] * 8,
        "gross_margin":  [30.0] * 8,
        "ar_turnover":   [5.0] * 8,
        "inv_turnover":  [5.0] * 8,
        "debt_ratio":    [40.0] * 8,
        "ocf":           [50.0] * 8,
        "net_income":    [50.0] * 8,
        "roe":           [10.0] * 8,
        "capex":         [-50.0] * 8,
    }


def _with(**over):
    b = _base()
    b.update(over)
    return b


def test_lan_score_perfect_hits_all_15():
    fin = {
        "revenue":       [500, 400, 300, 200, 100, 100, 100, 100],   # yoy 500>100, qoq 500>400
        "pretax_income": [200, 100, 100, 100, 100, 100, 100, 100],   # qoq 200>100
        "gross_margin":  [50, 45, 40, 35, 30, 30, 30, 30],           # yoy 50>30
        "ar_turnover":   [10, 9, 8, 7, 6, 6, 6, 6],                  # yoy 10>6
        "inv_turnover":  [5, 4, 3, 2, 1, 1, 1, 1],                   # yoy 5>1；turn_qoq (10+5)>(9+4)
        "debt_ratio":    [30, 40, 35, 50, 45, 45, 45, 45],           # 30<40
        "ocf":           [100, 80, 60, 40, 20, 20, 20, 20],          # 100>80 且 100>20
        "net_income":    [50, 40, 30, 20, 10, 10, 10, 10],           # Σ3ocf 240>Σ3ni 120；Σ4 280/140=2
        "roe":           [20, 15, 10, 8, 5, 5, 5, 5],                # 20>15 且 20>5
        "capex":         [-100, -100, -100, -50, -50, -50, -50, -50],  # |avg3|100>|avg8|68.75
    }
    out = analysis.lan_score(fin)
    assert out["score"] == 15
    assert out["max"] == 15
    assert all(v > 0 for v in out["checks"].values())


def test_lan_score_all_flat_is_zero_strict_comparisons():
    out = analysis.lan_score(_base())
    assert out["score"] == 0
    assert set(out["checks"].values()) == {0}


def test_lan_score_two_point_items():
    # turn_qoq：只抬 ar_turnover[0] → (6+5)>(5+5) 得 2
    assert analysis.lan_score(_with(ar_turnover=[6, 5, 5, 5, 5, 5, 5, 5]))["checks"]["turn_qoq"] == 2
    # cash_content：ocf 全抬到 60 → Σ4 240/200=1.2>1 得 2
    assert analysis.lan_score(_with(ocf=[60.0] * 8))["checks"]["cash_content"] == 2


def test_lan_score_debt_down_second_leg_only():
    # [0]=38 不小於 [1]=35，但小於 [4]=40 → OR 成立
    out = analysis.lan_score(_with(debt_ratio=[38, 35, 40, 40, 40, 40, 40, 40]))
    assert out["checks"]["debt_down"] == 1


def test_lan_score_ocf_up_requires_both_legs():
    # [0]=60>[1]=50 但 60 不大於 [4]=70 → AND 不成立
    out = analysis.lan_score(_with(ocf=[60, 50, 50, 50, 70, 50, 50, 50]))
    assert out["checks"]["ocf_up"] == 0


def test_lan_score_capex_uses_absolute_value():
    # 資本支出是現金流出（負值）；近3季均 |100| > 近8季均 |68.75| → 擴張
    out = analysis.lan_score(_with(capex=[-100, -100, -100, -50, -50, -50, -50, -50]))
    assert out["checks"]["capex_expand"] == 1


def test_lan_score_cash_content_zero_denominator_guarded():
    # Σ4(淨利)=0 不可除；判 0 分且不擲例外
    out = analysis.lan_score(_with(net_income=[10, -10, 5, -5, 10, -10, 5, -5]))
    assert out is not None
    assert out["checks"]["cash_content"] == 0


def test_lan_score_insufficient_data_returns_none():
    b = _base(); del b["roe"]
    assert analysis.lan_score(b) is None                      # 缺指標
    assert analysis.lan_score(_with(capex=[-50.0] * 5)) is None  # capex 需 8 季
    bad = _base(); bad["revenue"] = [None, 100, 100, 100, 100, 100, 100, 100]
    assert analysis.lan_score(bad) is None                    # 用到的 index 缺值


def test_lan_score_unused_index_may_be_none():
    # revenue 只用到 {0,1,4}；index 2 為 None 不影響
    b = _base(); b["revenue"] = [100, 100, None, 100, 100, 100, 100, 100]
    assert analysis.lan_score(b) is not None


def test_lan_score_items_constant_sums_to_15():
    assert sum(pts for _id, _label, pts in analysis.LAN_SCORE_ITEMS) == 15
    assert len(analysis.LAN_SCORE_ITEMS) == 13


# ---------- mu_score（木質＝財報 × 籌碼） ----------

def test_mu_score_no_chips_equals_base():
    out = analysis.mu_score(6, {})
    assert out["score"] == 6 and out["base"] == 6 and out["chip_bonus"] == 0
    assert out["max"] == 19


def test_mu_score_each_favourable_signal_adds_one():
    assert analysis.mu_score(6, {"big_holder_ratio": 0.5})["chip_bonus"] == 1
    assert analysis.mu_score(6, {"holder_drop_ratio": -2})["chip_bonus"] == 1
    assert analysis.mu_score(6, {"trust_3d": 1.5})["chip_bonus"] == 1
    assert analysis.mu_score(6, {"foreign_3d": 10})["chip_bonus"] == 1


def test_mu_score_all_four_signals():
    out = analysis.mu_score(6, {"big_holder_ratio": 1, "holder_drop_ratio": -1,
                                "trust_3d": 1, "foreign_3d": 1})
    assert out["chip_bonus"] == 4 and out["score"] == 10


def test_mu_score_adverse_or_zero_signals_score_nothing():
    out = analysis.mu_score(6, {"big_holder_ratio": 0, "holder_drop_ratio": 5,
                                "trust_3d": -1, "foreign_3d": 0})
    assert out["chip_bonus"] == 0 and out["score"] == 6


def test_mu_score_missing_chip_field_is_neutral():
    assert analysis.mu_score(9, {})["score"] == 9


def test_mu_score_none_base_returns_none():
    assert analysis.mu_score(None, {"trust_3d": 1}) is None


def test_mu_chip_items_constant_has_four():
    assert len(analysis.MU_CHIP_ITEMS) == 4


# ---------- mu_value（木率＝木質 ÷ 本業PE × 100 + 品質閘） ----------

def test_mu_value_normal_ratio_rounds():
    out = analysis.mu_value(10, 50, quality_floor=0)
    assert out["raw"] == 20 and out["value"] == 20 and out["quality_ok"] is True


def test_mu_value_quality_gate_zeros_value_but_keeps_raw():
    out = analysis.mu_value(5, 50, quality_floor=10)
    assert out["raw"] == 10          # 便宜事實仍保留
    assert out["value"] == 0         # 但品質未達門檻 → 不給便宜分
    assert out["quality_ok"] is False


def test_mu_value_nonpositive_pe_returns_zero():
    assert analysis.mu_value(10, 0)["value"] == 0
    assert analysis.mu_value(10, -5)["value"] == 0


def test_mu_value_missing_inputs_returns_none():
    assert analysis.mu_value(None, 50) is None
    assert analysis.mu_value(10, None) is None


def test_mu_value_matches_lan_value_formula_magnitude():
    # 台泥實證：蘭質6 / 本業PE56 × 100 ≒ 11（沿用同一條公式，只是換品質分子）
    assert analysis.mu_value(6, 56, quality_floor=0)["value"] == 11


def test_mu_quality_floor_within_scale():
    assert 0 <= analysis.MU_QUALITY_FLOOR <= 19
