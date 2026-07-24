from stocks_power_rich.sources import tpex

# 24 欄：0代號 1名稱 …4外資買賣超 …13投信 …16自營(合計) …末欄三大法人合計
_ROW = ["8383", "千附", "1551000", "432191", "1118809", "0", "0", "0",
        "1551000", "432191", "1118809", "0", "0", "0", "0", "0", "0",
        "39471", "2926", "36545", "0", "0", "0", "1118809"]


def test_parse_otc_names():
    recs = [
        {"SecuritiesCompanyCode": "6894", "CompanyAbbreviation": "衛司特"},
        {"SecuritiesCompanyCode": "8383", "CompanyAbbreviation": "千附"},
        {"SecuritiesCompanyCode": "", "CompanyAbbreviation": "無代號略過"},
    ]
    out = tpex.parse_otc_names(recs)
    assert out == {"6894": "衛司特", "8383": "千附"}


def test_parse_otc_quotes_signs_pct():
    payload = {"tables": [{"data": [
        ["6894", "衛司特", "361.50", "-2.00 ", "364.50", "365.00", "352.50", "360.50", "19,920", "7,181,100"],
        ["8383", "千附", "52.50", "2.50", "50.00", "52.60", "50.00", "51.00", "1,000", "52,500"],
        ["0000", "停牌股", "---", "---", "", "", "", "", "", ""],   # 無法解析→略過
    ]}]}
    out = tpex.parse_otc_quotes(payload)
    assert out["6894"]["close"] == 361.5 and out["6894"]["chg_pct"] == -0.55   # -2/363.5
    assert out["8383"]["name"] == "千附" and out["8383"]["chg_pct"] == 5.0     # +2.5/50
    assert "0000" not in out


def test_parse_otc_ohlc_positions_and_filter():
    payload = {"tables": [{"data": [
        ["6894", "衛司特", "361.50", "-2.00 ", "364.50", "365.00", "352.50", "360.50", "19,920", "7,181,100"],
        ["006201", "元大富櫃50", "20.0", "0.1", "19.9", "20.1", "19.8", "20.0", "1", "1"],  # ETF→排除
        ["8069", "元太", "45.20", "0.40", "44.80", "45.60", "44.50", "45.0", "1", "1"],
    ]}]}
    out = tpex.parse_otc_ohlc(payload)
    assert out["6894"] == {"open": 364.5, "high": 365.0, "low": 352.5, "close": 361.5}
    assert out["8069"]["close"] == 45.2
    assert "006201" not in out


def test_parse_tpex_insti_positional_in_lots():
    payload = {"tables": [{"fields": ["x"] * 24, "data": [_ROW] * 21}]}  # >=20 列才視為明細表
    out = tpex.parse_tpex_insti(payload)
    assert out["8383"]["name"] == "千附"
    assert out["8383"]["foreign"] == 1119   # 1,118,809 股 / 1000
    assert out["8383"]["trust"] == 0
    assert out["8383"]["total"] == 1119


def test_parse_otc_industry_maps_code_name_shares():
    from stocks_power_rich.sources.tpex import parse_otc_industry
    recs = [
        {"SecuritiesCompanyCode": "1240", "CompanyAbbreviation": "茂生農經",
         "SecuritiesIndustryCode": "33", "IssueShares": "44232373"},   # 33=農業科技(上櫃專屬碼)
        {"SecuritiesCompanyCode": "8299", "CompanyAbbreviation": "群聯",
         "SecuritiesIndustryCode": "24", "IssueShares": "199000000"},  # 24=半導體(與上市共用)
        {"SecuritiesCompanyCode": "9999", "CompanyAbbreviation": "未知碼",
         "SecuritiesIndustryCode": "99", "IssueShares": "1000"},       # 未知碼→剔除
    ]
    out = parse_otc_industry(recs)
    assert out["1240"] == {"sector": "農業科技", "name": "茂生農經", "shares": 44232373.0}
    assert out["8299"]["sector"] == "半導體"
    assert "9999" not in out


def test_parse_otc_turnover_positional():
    """櫃買 dailyQuotes 固定位置：0 代號、8 成交股數、9 成交金額(元)。ETF/非四碼排除。"""
    payload = {"tables": [{"data": [
        ["6488", "環球晶", "1240.00", "+35.00", "1230.00", "1305.00", "1205.00", "1258.00",
         "11,378,000", "14,313,524,000", "9,876", "1240", "2", "1245", "3", "43,000,000",
         "1240", "1364", "1116"],
        ["006201", "元大富櫃50", "41.69", "+1.89", "40.39", "41.72", "40.37", "41.29",
         "229,881", "9,492,550", "227", "41.64", "2", "41.79", "2", "21,446,000",
         "41.69", "45.85", "37.53"],                                     # ETF → 排除
        ["8069", "元太", "-", "-", "-", "-", "-", "-", "-", "-", "-"],     # 無量 → 排除
    ]}]}
    out = tpex.parse_otc_turnover(payload)
    assert out == {"6488": {"vol": 11378, "amount": 14313524000.0}}


def _otc_margin_payload():
    """櫃買 margin/balance 的實際形狀：逐檔在 data、市場合計在 summary 兩列。"""
    return {"tables": [{
        "fields": ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金",
                   "資使用率(%)", "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額",
                   "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"],
        "data": [
            ["6488", "環球晶", "1,000", "10", "5", "1", "1,004", "9", "0.24", "1,612,798",
             "10", "0", "0", "0", "20", "0", "0.0", "1,612,798", "0", ""],
            ["5483", "中美晶", "500", "1", "1", "0", "600", "0", "0.1", "100",
             "0", "0", "0", "0", "0", "0", "0.0", "100", "0", ""],
        ],
        "summary": [
            ["", "合計(張)", "2,369,252", "56,579", "58,867", "1,900", "2,365,064", "", "", "",
             "30,570", "5,032", "5,120", "545", "29,937", "", "", "", "", ""],
            ["", "融資金(仟元)", "192,703,743", "7,307,193", "7,152,506", "104,870", "192,753,560",
             "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ],
    }]}


def test_parse_otc_margin_reads_totals_and_per_stock():
    out = tpex.parse_otc_margin(_otc_margin_payload())
    # 市場合計取 summary 的「今日餘額」欄（索引 6），融資金仟元 → 億
    assert out["balance"] == 2365064
    assert out["short_balance"] == 29937
    assert out["value"] == 1927.5          # 192,753,560 仟元 = 1,927.5 億
    # 逐檔取「資餘額」「券餘額」而非前日餘額
    assert out["margin"] == {"6488": 1004.0, "5483": 600.0}
    assert out["short"] == {"6488": 20.0}   # 餘額 0 者不入表，免得拖累後續加總
    assert tpex.parse_otc_margin({}) == {"balance": None, "short_balance": None,
                                         "value": None, "margin": {}, "short": {}}
