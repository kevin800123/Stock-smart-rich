from stocks_power_rich.sources import twse


def test_parse_taiex_uses_last_and_signed_change():
    records = [
        {"Date": "1150601", "TAIEX": "45000.0", "Change": "100"},
        {"Date": "1150602", "TAIEX": "45337.91", "Change": "337.91"},
    ]
    out = twse.parse_taiex(records)
    assert out["taiex"] == 45337.91
    assert out["taiex_chg"] == 337.91  # 用連續收盤算 = 45337.91-45000.0


def test_parse_taiex_negative_change():
    records = [{"Date": "1", "TAIEX": "100"}, {"Date": "2", "TAIEX": "90"}]
    assert twse.parse_taiex(records)["taiex_chg"] == -10.0


def test_parse_taiex_returns_iso_date_from_roc():
    records = [
        {"Date": "1150615", "TAIEX": "45000", "Change": "0"},
        {"Date": "1150616", "TAIEX": "45337.91", "Change": "337.91"},
    ]
    assert twse.parse_taiex(records)["date"] == "2026-06-16"


def test_parse_institutional_net_in_yi():
    payload = {
        "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
        "data": [
            ["自營商(自行買賣)", "10,073,156,957", "6,662,827,387", "3,410,329,570"],
            ["自營商(避險)", "38,051,599,411", "35,373,159,078", "2,678,440,333"],
            ["投信", "38,695,533,641", "26,871,872,698", "11,823,660,943"],
            ["外資及陸資(不含外資自營商)", "481,498,901,895", "440,700,704,146", "40,798,197,749"],
            ["外資自營商", "0", "0", "0"],
            ["合計", "568,319,191,904", "509,608,563,309", "58,710,628,595"],
        ],
    }
    out = twse.parse_institutional(payload)
    assert out["inst_foreign"] == 407.98
    assert out["inst_trust"] == 118.24
    assert out["inst_dealer"] == 60.89


def test_parse_taiex_rwd_latest_row():
    payload = {
        "stat": "OK",
        "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
        "data": [
            ["115/06/24", "16,786,237,802", "1,539,046,268,153", "8,227,804", "46,043.60", "-1,057.05"],
            ["115/06/25", "13,439,264,364", "1,355,687,298,430", "6,071,253", "46,255.26", "211.66"],
        ],
    }
    out = twse.parse_taiex_rwd(payload)
    assert out["taiex"] == 46255.26
    assert out["taiex_chg"] == 211.66
    assert out["date"] == "2026-06-25"


def test_parse_margin_rwd_summary_in_lots():
    payload = {
        "stat": "OK", "date": "20260624",
        "tables": [{
            "title": "115年06月24日 信用交易統計",
            "fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
            "data": [
                ["融資(交易單位)", "684,163", "496,735", "19,052", "9,300,654", "9,469,030"],
                ["融券(交易單位)", "31,457", "30,912", "1,516", "204,255", "202,194"],
                ["融資金額(仟元)", "51,217,597", "36,966,775", "724,575", "593,930,024", "607,456,271"],
            ],
        }],
    }
    out = twse.parse_margin_rwd(payload)
    assert out["margin_balance"] == 9469030
    assert out["margin_chg"] == 168376   # 9,469,030 - 9,300,654
    assert out["short_balance"] == 202194
    assert out["short_chg"] == -2061     # 202,194 - 204,255


def test_parse_sector_indices_filters_strips_and_signs():
    payload = {"tables": [{
        "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
        "data": [
            ["發行量加權股價指數", "44,571.76", "<p style='color:green'>-</p>", "1,683.50", "-3.64", ""],  # 非類股→排除
            ["半導體類指數", "1,516.72", "<p style='color:green'>-</p>", "53.55", "-3.41", ""],
            ["航運類指數", "179.73", "<p style='color:red'>+</p>", "4.23", "2.30", ""],
            ["其他類指數", "271.92", "<p style='color:green'>-</p>", "5.03", "1.82", ""],  # %未帶號→以顏色定負
        ],
    }]}
    out = twse.parse_sector_indices(payload)
    names = [s["name"] for s in out]
    assert "發行量加權股價指數" not in names           # 只取產業「類指數」
    assert names == ["半導體", "航運", "其他"]           # 去掉「類指數」後綴
    by = {s["name"]: s for s in out}
    assert by["半導體"]["chg_pct"] == -3.41 and by["半導體"]["close"] == 1516.72
    assert by["航運"]["chg_pct"] == 2.30
    assert by["其他"]["chg_pct"] == -1.82               # 綠色=跌，magnitude 轉負


def test_parse_t86_per_stock_in_lots():
    payload = {
        "fields": ["證券代號", "證券名稱", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數",
                   "自營商買賣超股數", "三大法人買賣超股數"],
        "data": [["2330", "台積電", "5,000,000", "2,000,000", "-1,000,000", "6,000,000"]],
    }
    out = twse.parse_t86(payload)
    assert out["2330"] == {"name": "台積電", "foreign": 5000, "trust": 2000, "dealer": -1000, "total": 6000}


def test_parse_margin_sums_balances():
    records = [
        {"融資今日餘額": "10757", "融資前日餘額": "10291", "融券今日餘額": "91", "融券前日餘額": "87"},
        {"融資今日餘額": "1000", "融資前日餘額": "900", "融券今日餘額": "10", "融券前日餘額": "20"},
    ]
    out = twse.parse_margin(records)
    assert out["margin_balance"] == 11757
    assert out["margin_chg"] == 566
    assert out["short_balance"] == 101
    assert out["short_chg"] == -6
