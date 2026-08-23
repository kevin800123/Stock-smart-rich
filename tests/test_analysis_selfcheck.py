# tests/test_analysis_selfcheck.py
from stocks_power_rich import analysis


def test_selfcheck_compare_absolute_tolerance():
    # rev_yoy 容差 ±0.5：差 0.4 → match、差 0.6 → diff
    assert analysis.selfcheck_compare("rev_yoy", 10.0, 10.4) == "match"
    assert analysis.selfcheck_compare("rev_yoy", 10.0, 10.6) == "diff"


def test_selfcheck_compare_w55_is_exact_binary():
    assert analysis.selfcheck_compare("w55", 1.0, 1.0) == "match"
    assert analysis.selfcheck_compare("w55", 1.0, 0.0) == "diff"


def test_selfcheck_compare_relative_tolerance_est_profit():
    # est_profit 相對 ±5%：|self-csv|/|csv| ≤ 0.05 → match
    assert analysis.selfcheck_compare("est_profit", 100.0, 104.0) == "match"
    assert analysis.selfcheck_compare("est_profit", 100.0, 106.0) == "diff"


def test_selfcheck_compare_self_none_is_self_na_not_diff():
    assert analysis.selfcheck_compare("rev_yoy", 10.0, None) == "self_na"
    assert analysis.selfcheck_compare("mu_score", 12.0, None) == "self_na"


def test_selfcheck_compare_csv_none_is_csv_na():
    assert analysis.selfcheck_compare("rev_yoy", None, 10.0) == "csv_na"
    assert analysis.selfcheck_compare("rev_yoy", None, None) == "csv_na"


def test_selfcheck_compare_mu_score_absolute_one():
    assert analysis.selfcheck_compare("mu_score", 12.0, 13.0) == "match"   # 差 1.0 = 邊界內
    assert analysis.selfcheck_compare("mu_score", 12.0, 13.01) == "diff"


def test_selfcheck_compare_est_profit_zero_csv_baseline_falls_back_to_exact():
    # est_profit relative tolerance with csv==0: can't divide → exact-match fallback
    assert analysis.selfcheck_compare("est_profit", 0.0, 0.0) == "match"
    assert analysis.selfcheck_compare("est_profit", 0.0, 1.0) == "diff"


def test_selfcheck_compare_3d_uses_magnitude_aware_tolerance():
    """外資/投信3日是張數淨額，量級跨度大（外資可達數萬張、投信常近 0）。改用
    max(下限張, 比例×|csv|)：大量級的 sub-% 差判 match，小值仍靠下限、真正偏差很多才 diff。"""
    # 外資大量級：23,345 vs 23,265（差 80 ≒ 0.3%）→ match（2% 門檻 = 467）
    assert analysis.selfcheck_compare("foreign_3d", 23345.0, 23265.0) == "match"
    # 外資真正偏差很多：20,000 vs 25,000（差 5,000 = 25%）→ diff
    assert analysis.selfcheck_compare("foreign_3d", 20000.0, 25000.0) == "diff"
    # 小值靠下限：100 vs 104（差 4 ≤ 5 張下限）→ match；110（差 10）→ diff
    assert analysis.selfcheck_compare("foreign_3d", 100.0, 104.0) == "match"
    assert analysis.selfcheck_compare("foreign_3d", 100.0, 110.0) == "diff"
    # 投信同規則：-750 vs -742（差 8 ≤ 2%×750=15）→ match
    assert analysis.selfcheck_compare("trust_3d", -750.0, -742.0) == "match"
    # csv=0 → 比例項為 0，只剩下限：差 5 內 match、超過 diff
    assert analysis.selfcheck_compare("trust_3d", 0.0, 5.0) == "match"
    assert analysis.selfcheck_compare("trust_3d", 0.0, 6.0) == "diff"


def test_selfcheck_3d_tolerance_exposed_for_drift_guard():
    """容差常數要能被端點揭露（同 bands 的防漂移規矩），前端/設定頁不得另寫一份。"""
    assert "trust_3d" in analysis.SELFCHECK_ABS_REL
    assert "foreign_3d" in analysis.SELFCHECK_ABS_REL
    # 已從絕對容差表移除（避免兩套規則同時作用產生歧義）
    assert "trust_3d" not in analysis.SELFCHECK_TOL
    assert "foreign_3d" not in analysis.SELFCHECK_TOL
