"""族群輪動「法人 × 大戶」資金流向：純函式測試（spec 2026-09-22-sector-flow-quadrant-design.md §2）。"""
import inspect

from stocks_power_rich import analysis


def test_big_holder_amount_is_delta_pct_times_mcap():
    # Δ400張↑ +1%、10 億股、收盤 100 → 大戶淨買進 10 億元
    assert analysis.big_holder_amount(1.0, 1_000_000_000, 100.0) == 1_000_000_000.0
    assert analysis.big_holder_amount(-0.5, 2_000_000, 50.0) == -500_000.0


def test_big_holder_amount_returns_none_when_any_input_missing():
    assert analysis.big_holder_amount(None, 1000, 10.0) is None
    assert analysis.big_holder_amount(1.0, None, 10.0) is None
    assert analysis.big_holder_amount(1.0, 0, 10.0) is None
    assert analysis.big_holder_amount(1.0, 1000, None) is None
    assert analysis.big_holder_amount(1.0, 1000, 0) is None


def test_build_self_screen_uses_the_shared_formula_not_an_inline_copy():
    """大戶淨買進金額只能有一份算式。selfcheck 要呼叫 analysis.big_holder_amount，
    不能自己再寫一次 `bhr / 100 * shares * price`（兩份會漂移）。"""
    from stocks_power_rich import selfcheck
    src = inspect.getsource(selfcheck)
    assert "big_holder_amount(" in src
    assert "/ 100 * shares * price" not in src
