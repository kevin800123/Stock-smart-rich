import pytest

# 一筆完整的 Big5 CSV 樣本（前三列標頭資訊 + 欄名列 + 一筆資料），
# 欄位順序與真實「.常用.csv」一致。
HEADER = (
    '"序號","代碼","商品","成交","漲幅%","總量","收盤價(06/15)","區間漲幅%","振幅",'
    '"市值(億)","股本(億)","成交值(億)","推估獲利","蘭質","LPE","蘭值","有CB","W55",'
    '"55高","55低","集保","評比值","大戶增比","人數降比","月增","年增","累增","投三",'
    '"外三","TOTAL","55漲%","21跌%","產業","細產業","所有細產業",產業地位'
)
ROW_2330 = (
    '1\t,"2330.TW","台積電","1000","1.5","30000","985","1.5","2","250000","2593",'
    '"500","30","6","20","20","1","1","0","0","75","0.1","0.8","-0.5","3","12.3","5",'
    '"2.5","3.1","5.6","0.2","0","上市半導體","晶圓","晶圓代工",全球晶圓代工龍頭'
)


def _make_csv(path, data_rows, date_line="資料日期：2026年  6月 15日", encoding="cp950", preamble_lines=None):
    pre = preamble_lines if preamble_lines is not None else ["符合條件商品", date_line, "策略,\t.常用"]
    content = "\n".join(pre) + "\n" + HEADER + "\n" + "\n".join(data_rows) + "\n"
    path.write_bytes(content.encode(encoding))
    return str(path)


HEADER_COLS = [
    "序號", "代碼", "商品", "成交", "漲幅%", "總量", "收盤價(06/15)", "區間漲幅%", "振幅",
    "市值(億)", "股本(億)", "成交值(億)", "推估獲利", "蘭質", "LPE", "蘭值", "有CB", "W55",
    "55高", "55低", "集保", "評比值", "大戶增比", "人數降比", "月增", "年增", "累增", "投三",
    "外三", "TOTAL", "55漲%", "21跌%", "產業", "細產業", "所有細產業", "產業地位",
]
ROW_2330_CELLS = [
    "1\t", "2330.TW", "台積電", 1000, 1.5, 30000, 985, 1.5, 2, 250000, 2593, 500, 30, 6,
    20, 20, 1, 1, 0, 0, 75, 0.1, 0.8, -0.5, 3, 12.3, 5, 2.5, 3.1, 5.6, 0.2, 0,
    "上市半導體", "晶圓", "晶圓代工", "全球晶圓代工龍頭",
]


def _make_xlsx(path, data_rows_cells, date_line="資料日期：2026年  6月 15日", sheet=".常用"):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["符合條件商品"])
    ws.append([date_line])
    ws.append(["策略", "\t" + sheet])
    ws.append(HEADER_COLS)
    for r in data_rows_cells:
        ws.append(r)
    wb.save(path)
    return str(path)


@pytest.fixture
def xlsx_file(tmp_path):
    return _make_xlsx(tmp_path / "sample.xlsx", [ROW_2330_CELLS])


@pytest.fixture
def big5_csv(tmp_path):
    return _make_csv(tmp_path / "sample.csv", [ROW_2330])


@pytest.fixture
def make_big5_csv(tmp_path):
    """工廠 fixture：自訂日期、資料列、編碼與前置列，產生 CSV。"""
    counter = {"n": 0}

    def factory(data_rows, date_line="資料日期：2026年  6月 15日", encoding="cp950", preamble_lines=None):
        counter["n"] += 1
        return _make_csv(tmp_path / f"s{counter['n']}.csv", data_rows, date_line, encoding, preamble_lines)

    return factory


@pytest.fixture(autouse=True)
def _no_calendar_network(request, monkeypatch):
    """下週行事曆會去打 BLS／Fed／MOPS／Nasdaq 四個外部網站，而它只在**週日**才觸發。

    不擋的話整份測試會「星期天跑跟平日跑不一樣」——`slot=evening` 的既有測試在週日
    會真的連外，慢、看網路臉色，而且失敗原因與被測的東西無關。這是「本機全綠、
    production 壞掉」那類問題的鏡像版本：同一份程式碼、不同環境（這裡是不同星期幾）
    走到不同路徑。

    預設回「本週無事件」。
    要**測 `build_week_calendar` 本身**的測試標 `@pytest.mark.real_calendar` 退出這一層
    （它們自己會樁掉底下四個 fetcher，一樣不連外）。沒有這個出口的話，那些測試會在
    不知不覺中測到樁、斷言永遠成立。
    """
    if request.node.get_closest_marker("real_calendar"):
        return
    from stocks_power_rich.api import news as _news

    monkeypatch.setattr(_news, "build_week_calendar",
                        lambda *a, **kw: [], raising=True)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_calendar: 不套用 _no_calendar_network 的樁（測試自行樁掉 fetcher）")
