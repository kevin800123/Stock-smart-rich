from stocks_power_rich import analysis


def test_estimate_price_range_normal_buildup():
    # 最近三個月營收 1000(萬)、毛利率 30% → 毛利 300；營業費用 100、所得稅 20 → 稅後淨利 180
    # 股數 10(萬股) → 單季EPS 180/10=18；年化 ×4=72；本益比 10/15/20 → 720/1080/1440
    out = analysis.estimate_price_range(
        revenue=1000, gross_margin_pct=30, opex=100, tax=20, shares=10,
        pe_low=10, pe_mid=15, pe_high=20,
    )
    assert out["eps_quarter"] == 18.0
    assert out["eps_annual"] == 72.0
    assert out["low"] == 720.0
    assert out["mid"] == 1080.0
    assert out["high"] == 1440.0


def test_estimate_price_range_zero_gross_margin_flows_through():
    out = analysis.estimate_price_range(
        revenue=1000, gross_margin_pct=0, opex=0, tax=0, shares=100,
        pe_low=10, pe_mid=10, pe_high=10,
    )
    assert out["eps_quarter"] == 0.0
    assert out["eps_annual"] == 0.0
    assert out["low"] == out["mid"] == out["high"] == 0.0


def test_estimate_price_range_nonpositive_shares_returns_none():
    assert analysis.estimate_price_range(
        revenue=1000, gross_margin_pct=30, opex=100, tax=20, shares=0,
        pe_low=10, pe_mid=15, pe_high=20,
    ) is None
    assert analysis.estimate_price_range(
        revenue=1000, gross_margin_pct=30, opex=100, tax=20, shares=-5,
        pe_low=10, pe_mid=15, pe_high=20,
    ) is None


def test_estimate_price_range_missing_inputs_returns_none():
    assert analysis.estimate_price_range(
        revenue=None, gross_margin_pct=30, opex=100, tax=20, shares=10,
        pe_low=10, pe_mid=15, pe_high=20,
    ) is None
    assert analysis.estimate_price_range(
        revenue=1000, gross_margin_pct=30, opex=100, tax=20, shares=10,
        pe_low=10, pe_mid=None, pe_high=20,
    ) is None


def test_estimate_price_range_negative_net_income_allowed():
    # 虧損季度：稅後淨利可以是負的，EPS/預估價位跟著是負值，不擋——由前端顯示判斷
    out = analysis.estimate_price_range(
        revenue=100, gross_margin_pct=10, opex=50, tax=0, shares=10,
        pe_low=10, pe_mid=10, pe_high=10,
    )
    assert out["eps_quarter"] == -4.0
    assert out["low"] == -160.0
