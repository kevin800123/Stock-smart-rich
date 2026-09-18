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


@pytest.fixture(autouse=True)
def _no_startup_catchup(request, monkeypatch):
    """create_app(enable_scheduler=True) 會在背景執行緒做啟動補跑——「今天該跑卻沒紀錄」的
    job 在測試裡永遠成立（tmp DB 是空的），不擋的話每條起排程器的測試都會真的跑
    daily_update／新聞推播去連外。預設樁成 no-op；要測補跑本身的測試直接呼叫底層
    `catchup_missed_jobs`／`catchup_plan`（它們自己樁掉 job 函式），標
    `@pytest.mark.real_catchup` 可退出這一層。"""
    if request.node.get_closest_marker("real_catchup"):
        return
    from stocks_power_rich.api import helpers as _h

    monkeypatch.setattr(_h, "catchup_missed_jobs",
                        lambda *a, **kw: {"stubbed": True}, raising=True)


@pytest.fixture(autouse=True)
def _no_ssf_network(request, monkeypatch):
    """`fetch=True` 的呼叫端——`GET /api/ssf/margin`、排程 `refresh_ssf_daily`
    （經 `_ssf_contracts`／`_ssf_margin_table`，`api/helpers.py`）——會打
    `taifex_ssf.fetch_ssf_contract_map`／`fetch_ssf_margin_table` 兩個外部端點
    （www.taifex.com.tw）。每個測試 DB 都是空的，沒有另外樁掉這兩支函式的測試
    都會真的連外——而且兩個 fetcher 失敗時本來就回空 dict（見它們自己的
    docstring：「呼叫端的月/日快取兩端都擋空值，不會把失敗永久化」），所以樁成
    空 dict 完全落在既有的容錯路徑內，不是在模擬一個特殊情境。

    **`GET /api/picks/self-screen`（`_attach_ssf_margin`）已經不會踩到這裡**
    （review I3／Fix F，2026-09 修正）：它改成 `ssf_margin_index(c, fetch=False)`，
    結構上只走 cache-only 路徑，永遠不會呼叫這兩支 fetcher——這支 fixture 原本是
    為它加的，現在單純是其餘 `fetch=True` 呼叫端的通用防護。

    這正是 CLAUDE.md 記過的那種問題：測試沒有因為連網路而變紅，只是變慢又
    看網路臉色，所以一直沒被發現（同一份教訓先前發生在杯柄型態測試安靜地打
    TPEx）。預設樁成回空字典；要測**這兩支 fetcher 本身**的測試標
    `@pytest.mark.real_ssf_fetch` 退出這一層（它們自己樁掉 `ssf.httpx.Client`，
    不會真的連外）。"""
    if request.node.get_closest_marker("real_ssf_fetch"):
        return
    from stocks_power_rich.sources import taifex_ssf as _ssf

    monkeypatch.setattr(_ssf, "fetch_ssf_contract_map", lambda *a, **kw: {}, raising=True)
    monkeypatch.setattr(_ssf, "fetch_ssf_margin_table", lambda *a, **kw: {}, raising=True)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_calendar: 不套用 _no_calendar_network 的樁（測試自行樁掉 fetcher）")
    config.addinivalue_line(
        "markers", "real_catchup: 不套用 _no_startup_catchup 的樁（測試自行樁掉 job 函式）")
    config.addinivalue_line(
        "markers", "real_ssf_fetch: 不套用 _no_ssf_network 的樁（測試自行樁掉 ssf.httpx.Client）")
