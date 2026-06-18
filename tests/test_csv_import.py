from stocks_power_rich.csv_import import parse_csv, find_latest_file
from tests.conftest import ROW_2330


def test_find_latest_file_picks_newest(tmp_path):
    import os

    a = tmp_path / "a.csv"
    a.write_bytes(b"x")
    b = tmp_path / "b.xlsm"
    b.write_bytes(b"y")
    os.utime(str(a), (1000, 1000))
    os.utime(str(b), (2000, 2000))  # b 較新
    assert find_latest_file(str(tmp_path)) == str(b)
    assert find_latest_file(str(tmp_path / "nope")) is None

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


ROW_6174 = (
    '3\t,"6174.TW","安碁","100","1","500","99","1","2","50","20","8","2","6","20","20",'
    '"1","1","0","0","60","0.1","3.18","-10.46","2","25","3","1.1","2.2","3.3","0.2","0",'
    '"上櫃電子零組件","石英元件","石英元件",石英元件廠'
)


def test_parse_cp950_special_char_no_mojibake(make_big5_csv):
    # 「碁」字在純 big5 會變亂碼，cp950 應正確解出
    path = make_big5_csv([ROW_6174], encoding="cp950")
    _, rows = parse_csv(path)
    assert rows[0]["name"] == "安碁"
    assert "�" not in rows[0]["name"]


def test_parse_utf8_encoded_file(make_big5_csv):
    # 使用者另存成 UTF-8 的檔也要能讀
    path = make_big5_csv([ROW_2330], encoding="utf-8-sig")
    snap_date, rows = parse_csv(path)
    assert snap_date == "2026-06-15"
    assert rows[0]["code"] == "2330.TW"


def test_parse_finds_header_when_extra_preamble(make_big5_csv):
    # 前置列數不固定時，仍能掃到標頭與日期
    path = make_big5_csv(
        [ROW_2330],
        preamble_lines=["匯出報表", "你好", "資料日期：2026年  6月 15日", "策略,常用", "註解列"],
    )
    snap_date, rows = parse_csv(path)
    assert snap_date == "2026-06-15"
    assert rows[0]["code"] == "2330.TW"


def test_parse_minguo_date(make_big5_csv):
    # 民國年 115 → 西元 2026
    path = make_big5_csv([ROW_2330], date_line="資料日期：115年 6月 15日")
    snap_date, _ = parse_csv(path)
    assert snap_date == "2026-06-15"


def test_parse_xlsx_excel_file(xlsx_file):
    # 使用者實際每日檔是 .xlsm/.xlsx Excel，需與 CSV 走同一套解析
    snap_date, rows = parse_csv(xlsx_file)
    assert snap_date == "2026-06-15"
    assert rows[0]["code"] == "2330.TW"
    assert rows[0]["name"] == "台積電"
    assert rows[0]["big_holder_ratio"] == 0.8
    assert rows[0]["rev_yoy"] == 12.3
    assert rows[0]["w55"] == 1.0


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
    # 新增欄位：推估EPS、蘭質、本益比、蘭值
    assert r["est_profit"] == 30.0   # 推估獲利＝下季推估 EPS
    assert r["lan_score"] == 6.0     # 蘭質＝綜合財評分數（滿分15）
    assert r["lpe"] == 20.0          # LPE＝本益比
    assert r["lan_value"] == 20.0    # 蘭值＝蘭質/LPE 換算
