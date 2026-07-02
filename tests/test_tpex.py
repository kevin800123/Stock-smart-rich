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


def test_parse_tpex_insti_positional_in_lots():
    payload = {"tables": [{"fields": ["x"] * 24, "data": [_ROW] * 21}]}  # >=20 列才視為明細表
    out = tpex.parse_tpex_insti(payload)
    assert out["8383"]["name"] == "千附"
    assert out["8383"]["foreign"] == 1119   # 1,118,809 股 / 1000
    assert out["8383"]["trust"] == 0
    assert out["8383"]["total"] == 1119
