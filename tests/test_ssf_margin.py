from stocks_power_rich.sources import taifex_ssf as ssf

STOCK_MARGIN_CSV = """一、股票期貨契約保證金一覽表
(一) 標的證券為股票之股票期貨契約
更新日期:2026/09/15
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,保證金所屬級距,結算保證金適用比例,維持保證金適用比例,原始保證金適用比例,
61,CDF    ,2330,台積電期貨                    ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
62,QFF    ,2330,小型台積電期貨                ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
33,KUF    ,1802,台玻期貨                      ,"台灣玻璃工業股份有限公司",,16.00%,16.56%,21.60%,
(二) 標的證券為ETF之股票期貨契約
更新日期:2026/08/12
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,結算保證金,維持保證金,原始保證金,
1,NYF    ,0050,元大台灣50ETF期貨             ,元大台灣卓越50證券投資信託基金,64000,67000,87000,
2,SRF    ,0050,小型元大台灣50ETF期貨         ,元大台灣卓越50證券投資信託基金,6400,6700,8700,
二、股票選擇權契約保證金一覽表
更新日期:2026/09/09
序號,代碼,名稱,
1,ZZZ,不該被讀進來,
"""

INDEX_MARGIN_CSV = """更新日期:2026/08/12
商品別,結算保證金,維持保證金,原始保證金,,
臺股期貨,519000,538000,701000,
小型臺指,129750,134500,175250,
微型臺指期貨,25950,26900,35050,
臺指選擇權風險保證金(A)值,138000,143000,187000,
"""


def test_stock_margin_reads_ratios_and_its_own_update_date():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["stock_updated"] == "2026/09/15"
    cdf = m["stock"]["CDF"]
    assert cdf["code"] == "2330" and cdf["tier"] == "級距1"
    assert cdf["initial_pct"] == 13.50 and cdf["maintenance_pct"] == 10.35
    assert m["stock"]["QFF"]["initial_pct"] == 13.50


def test_stock_margin_accepts_a_blank_tier():
    """風險係數 >15% 的 14 檔沒有級距，級距欄是空字串而不是缺列。"""
    assert ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)["stock"]["KUF"] == {
        "code": "1802", "name": "台玻期貨", "tier": "",
        "clearing_pct": 16.00, "maintenance_pct": 16.56, "initial_pct": 21.60}


def test_etf_section_has_fixed_amounts_and_a_different_update_date():
    """ETF 期貨公布固定金額、不套價格×比例，而且生效日與股票區段不同。"""
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["etf_updated"] == "2026/08/12"
    assert m["etf"]["NYF"]["initial"] == 87000
    assert m["etf"]["SRF"]["initial"] == 8700      # 小型恰為標準的 1/10


def test_option_sections_are_not_read_as_futures():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert "ZZZ" not in m["stock"] and "ZZZ" not in m["etf"]


def test_index_margin_reads_tmf():
    m = ssf.parse_index_margining_csv(INDEX_MARGIN_CSV)
    assert m["updated"] == "2026/08/12"
    assert m["items"]["微型臺指期貨"] == {"clearing": 25950, "maintenance": 26900,
                                          "initial": 35050}
    assert m["items"]["臺股期貨"]["initial"] == 701000
