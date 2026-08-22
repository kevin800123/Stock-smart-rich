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
