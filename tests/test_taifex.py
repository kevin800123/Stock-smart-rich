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
    {"ContractCode": "臺股期貨", "Item": "自營商", "OpenInterest(Net)": "100"},
    {"ContractCode": "臺股期貨", "Item": "投信", "OpenInterest(Net)": "200"},
    {"ContractCode": "臺股期貨", "Item": "外資及陸資", "OpenInterest(Net)": "9999"},
]


def test_parse_tx_price_picks_near_month_day_session():
    out = taifex.parse_tx_price(FUT)
    assert out["tx_price"] == 45772.0
    assert out["tx_chg"] == 100.0
    assert out["tx_open"] == 45600.0
    assert out["tx_high"] == 45900.0
    assert out["tx_low"] == 45550.0


TX_HIST_CSV = """交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量
2026/05/19,TX,202605  ,40890,40931,40188,40248,-549,-1.35%,86112,40240,17052,40239,40247,42554,31357,,一般,
2026/05/19,TX,202606  ,40992,41100,40380,40383,-580,-1.42%,45710,40399,72130,40381,40390,42669,20819,,一般,
2026/05/19,TX,202605  ,40962,41450,40524,40878,81,0.20%,65991,-,-,40878,40896,42554,31357,,盤後,
2026/05/20,TX,202606  ,40383,40500,40000,40100,-283,-0.70%,90000,40110,72000,40098,40105,42669,20819,,一般,
"""


def test_parse_tx_history_picks_near_month_day_session():
    from stocks_power_rich.sources.taifex import parse_tx_history_csv

    out = parse_tx_history_csv(TX_HIST_CSV)
    assert [r["date"] for r in out] == ["2026-05-19", "2026-05-20"]
    # 2026-05-19 取成交量最大者(202605, 86112)；盤後列排除
    assert out[0] == {"date": "2026-05-19", "open": 40890.0, "high": 40931.0, "low": 40188.0, "close": 40248.0, "volume": 86112.0}
    assert out[1]["close"] == 40100.0


def test_compute_retail_ratios():
    out = taifex.compute_retail_ratios(FUT, INST)
    assert out["fut_inst_net"] == 600          # 小台三大法人淨額
    assert out["retail_ls_mtx"] == -0.2        # -600/3000
    assert out["retail_ls_tmf"] == 0.05        # -(-250)/5000


def test_inst_net_oi_for_with_item_filter():
    # 全體三大法人（不分投資人）小台淨額 = -100+50+650 = 600
    assert taifex.inst_net_oi_for(INST, "小型臺指期貨") == 600
    # 僅外資（含「外資及陸資」）臺股期貨淨未平倉
    assert taifex.inst_net_oi_for(INST, "臺股期貨", item="外資") == 9999


def test_compute_oi_positions():
    out = taifex.compute_retail_ratios(FUT, INST)
    # 外資台指淨未平倉（口）：臺股期貨外資列
    assert out["tx_foreign_oi"] == 9999
    # 散戶小台淨未平倉（口）≈ -(三大法人小台淨額) = -600
    assert out["retail_oi_mtx"] == -600
