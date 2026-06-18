import io

import openpyxl

from stocks_power_rich.exporter import picks_to_xlsx


def test_picks_to_xlsx_has_header_and_rows():
    data = picks_to_xlsx(
        [{"code": "2330.TW", "name": "台積電", "lan_value": 80, "lan_score": 12,
          "lpe": 20, "est_profit": 30, "rev_yoy": 12, "accum_inc": 5,
          "holder_drop_ratio": -0.5, "big_holder_ratio": 0.8, "sub_industry": "晶圓"}],
        "2026-06-17",
    )
    assert isinstance(data, bytes) and len(data) > 0
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    rows = list(ws.iter_rows(values_only=True))
    assert "2026-06-17" in str(rows[0][0])     # 第一列含資料日期
    assert rows[1][0] == "代碼"                 # 第二列為欄名
    assert rows[1][2] == "蘭值"
    assert rows[2][0] == "2330.TW"             # 資料列
    assert rows[2][2] == 80
