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
