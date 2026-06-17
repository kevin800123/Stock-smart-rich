from stocks_power_rich.csv_import import parse_csv
from tests.conftest import ROW_2330

# 末欄「產業地位」未加引號且含 ASCII 逗號（重現真實 CSV 5464.TW 的格式）
ROW_COMMA_TAIL = (
    '2\t,"5464.TW","霖宏","50","0.5","100","49","0.5","1","30","10","5","1","6",'
    '"20","20","0","1","0","0","60","0.1","0.3","-0.1","1","8","2","0.5","0.3",'
    '"0.8","0.1","0","上櫃電子零組件","軟性電路板","軟性電路板,軟性電路板材料",'
    'LCD TV,軟性印刷電路板及PCB系'
)


def test_parse_handles_unquoted_comma_in_last_column(make_big5_csv):
    path = make_big5_csv([ROW_2330, ROW_COMMA_TAIL])
    snap_date, rows = parse_csv(path)
    assert snap_date == "2026-06-15"
    assert len(rows) == 2
    by = {r["code"]: r for r in rows}
    assert by["5464.TW"]["industry_position"] == "LCD TV,軟性印刷電路板及PCB系"
    assert by["5464.TW"]["close"] == 50.0
    assert by["2330.TW"]["code"] == "2330.TW"


def test_parse_extracts_date_and_row(big5_csv):
    snap_date, rows = parse_csv(big5_csv)
    assert snap_date == "2026-06-15"
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "2330.TW"
    assert r["name"] == "台積電"
    assert r["close"] == 1000.0
    assert r["big_holder_ratio"] == 0.8
    assert r["holder_drop_ratio"] == -0.5
    assert r["rev_yoy"] == 12.3
    assert r["w55"] == 1.0
    assert r["industry"] == "上市半導體"
    assert "raw_json" in r
