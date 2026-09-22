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


U = {  # 兩檔半導體、一檔水泥、一檔缺收盤、一檔缺類股、三檔非普通股
    "2330": {"sector": "半導體", "name": "台積電", "shares": 1_000_000_000},
    "2454": {"sector": "半導體", "name": "聯發科", "shares": 100_000_000},
    "1101": {"sector": "水泥",   "name": "台泥",   "shares": 500_000_000},
    "9999": {"sector": "水泥",   "name": "沒收盤", "shares": 1_000_000},
    "8888": {"sector": None,     "name": "沒類股", "shares": 1_000_000},
    "0050": {"sector": "半導體", "name": "ETF",    "shares": 1_000_000},
    "00878": {"sector": "半導體", "name": "ETF2",  "shares": 1_000_000},
    "12345": {"sector": "半導體", "name": "五碼",  "shares": 1_000_000},
}
CLOSES = {"2330": 100.0, "2454": 1000.0, "1101": 20.0, "8888": 10.0,
          "0050": 100.0, "00878": 10.0, "12345": 10.0}
FLOW = {"2330": 10_000, "2454": -2_000, "1101": 500, "9999": 99, "8888": 99,
        "0050": 99_999, "00878": 99_999, "12345": 99_999}


def _by_sector(res):
    return {s["sector"]: s for s in res["sectors"]}


def test_sector_flow_x_is_amount_sum_over_mcap_sum_not_mean_of_pcts():
    res = analysis.sector_flow(U, CLOSES, FLOW)
    semi = _by_sector(res)["半導體"]
    # 市值：2330 = 1e9×100 = 1e11；2454 = 1e8×1000 = 1e11 → 合計 2e11
    # 法人金額：2330 = 10000×1000×100 = 1e9；2454 = -2000×1000×1000 = -2e9 → 合計 -1e9
    assert semi["mcap"] == 200_000_000_000
    assert semi["n"] == 2
    assert semi["x"] == round(-1e9 / 2e11 * 100, 3)   # -0.5%
    # 若錯寫成「各檔 % 的平均」會是 (1% + -2%)/2 = -0.5%——這個例子刻意讓兩者相同，
    # 所以再看水泥：只有一檔，x = 500×1000×20 / (5e8×20) ×100 = 0.1%
    assert _by_sector(res)["水泥"]["x"] == 0.1


def test_sector_flow_excludes_non_common_stocks_and_counts_missing_inputs():
    res = analysis.sector_flow(U, CLOSES, FLOW)
    semi = _by_sector(res)["半導體"]
    assert semi["n"] == 2                       # 0050／00878／12345 都沒進來
    assert res["excluded"] == {"no_price": 1,   # 9999 缺收盤
                               "no_sector": 1,  # 8888 缺類股
                               "sectors_no_mcap": 0}


def test_sector_flow_counts_a_sector_with_no_priced_stock_as_no_mcap():
    u = {"7777": {"sector": "造紙", "name": "只有它", "shares": 1000}}
    res = analysis.sector_flow(u, {}, {"7777": 5})   # 沒有收盤
    assert res["sectors"] == []
    assert res["excluded"]["sectors_no_mcap"] == 1
    assert res["excluded"]["no_price"] == 1


def test_sector_flow_prev_and_custody_are_none_when_inputs_missing():
    res = analysis.sector_flow(U, CLOSES, FLOW)   # 沒給 flow_prev / cust_cur
    for s in res["sectors"]:
        assert s["x_prev"] is None and s["y"] is None and s["y_prev"] is None
        assert s["n_cust"] == 0


def test_sector_flow_custody_missing_stock_stays_in_denominator():
    # 半導體兩檔只有 2330 有集保 Δ：分子只算 2330，分母仍是兩檔市值 2e11
    cust = {"2330": 1.0}                          # Δ +1% → 1e9×100×1% = 1e9
    res = analysis.sector_flow(U, CLOSES, FLOW, cust_cur=cust)
    semi = _by_sector(res)["半導體"]
    assert semi["y"] == round(1e9 / 2e11 * 100, 3)   # 0.5%，不是 1%
    assert semi["n_cust"] == 1
    # 水泥一檔都沒有 Δ → y 是 None（不是 0）
    assert _by_sector(res)["水泥"]["y"] is None


def test_sector_flow_prev_period_uses_prev_inputs():
    res = analysis.sector_flow(U, CLOSES, FLOW, flow_prev={"1101": 1000},
                               cust_cur={"1101": 2.0}, cust_prev={"1101": -1.0})
    cem = _by_sector(res)["水泥"]
    assert cem["x_prev"] == 0.2                   # 1000×1000×20 / 1e10 ×100
    assert cem["y"] == 2.0 and cem["y_prev"] == -1.0
    semi = _by_sector(res)["半導體"]
    assert semi["x_prev"] is None                 # flow_prev 裡沒有半導體的股


def test_sector_flow_sorts_by_mcap_and_reports_top3_by_inst_amount():
    u = dict(U)
    u["2303"] = {"sector": "半導體", "name": "聯電", "shares": 1_000_000}
    closes = dict(CLOSES, **{"2303": 50.0})
    flow = dict(FLOW, **{"2303": 3_000})          # 3000×1000×50 = 1.5e8
    res = analysis.sector_flow(u, closes, flow)
    assert [s["sector"] for s in res["sectors"]] == ["半導體", "水泥"]
    semi = _by_sector(res)["半導體"]
    assert [t["code"] for t in semi["top3"]] == ["2330", "2303", "2454"]
    assert semi["top3"][0] == {"code": "2330", "name": "台積電", "amount": 1_000_000_000}


def test_sector_flow_attaches_sector_chg_pct_for_tooltip():
    res = analysis.sector_flow(U, CLOSES, FLOW, sector_chg={"半導體": 1.23})
    assert _by_sector(res)["半導體"]["chg_pct"] == 1.23
    assert _by_sector(res)["水泥"]["chg_pct"] is None
