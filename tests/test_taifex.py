from stocks_power_rich.sources import taifex


def test_retail_ls_inverse_of_institutional():
    # 三大法人淨額 +600（偏多），全市場未平倉 3000 → 散戶 ≈ -600 → -0.2
    assert taifex.retail_long_short_ratio(600, 3000) == -0.2


def test_retail_ls_zero_total_is_none():
    assert taifex.retail_long_short_ratio(100, 0) is None


FUT = [
    {"Contract": "TX", "ContractMonth(Week)": "202606", "Last": "45772", "Change": "100", "OpenInterest": "21508", "Open": "45600", "High": "45900", "Low": "45550"},
    {"Contract": "TX", "ContractMonth(Week)": "202606", "Last": "46246", "Change": "", "OpenInterest": "-"},  # 盤後
    {"Contract": "TX", "ContractMonth(Week)": "202607", "Last": "45849", "Change": "90", "OpenInterest": "80581"},
    {"Contract": "MTX", "ContractMonth(Week)": "202606", "Last": "45762", "Change": "5", "OpenInterest": "2000"},
    {"Contract": "MTX", "ContractMonth(Week)": "202607", "Last": "45800", "Change": "6", "OpenInterest": "1000"},
    {"Contract": "TMF", "ContractMonth(Week)": "202606", "Last": "45763", "Change": "4", "OpenInterest": "5000"},
]
INST = [
    {"ContractCode": "小型臺指期貨", "Item": "自營商", "OpenInterest(Net)": "-100"},
    {"ContractCode": "小型臺指期貨", "Item": "投信", "OpenInterest(Net)": "50"},
    {"ContractCode": "小型臺指期貨", "Item": "外資", "OpenInterest(Net)": "650"},
    {"ContractCode": "微型臺指期貨", "Item": "自營商", "OpenInterest(Net)": "-50"},
    {"ContractCode": "微型臺指期貨", "Item": "投信", "OpenInterest(Net)": "-100"},
    {"ContractCode": "微型臺指期貨", "Item": "外資", "OpenInterest(Net)": "-100"},
    {"ContractCode": "臺股期貨", "Item": "外資", "OpenInterest(Net)": "9999"},
]


def test_parse_tx_price_picks_near_month_day_session():
    out = taifex.parse_tx_price(FUT)
    assert out["tx_price"] == 45772.0
    assert out["tx_chg"] == 100.0
    assert out["tx_open"] == 45600.0
    assert out["tx_high"] == 45900.0
    assert out["tx_low"] == 45550.0


def test_compute_retail_ratios():
    out = taifex.compute_retail_ratios(FUT, INST)
    assert out["fut_inst_net"] == 600          # 小台三大法人淨額
    assert out["retail_ls_mtx"] == -0.2        # -600/3000
    assert out["retail_ls_tmf"] == 0.05        # -(-250)/5000
