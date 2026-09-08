"""下週行事曆的資料源解析。

**日期一律取自官方排程，不用「可計算規則」推算。** 實測 2026 年 BLS 官方排程，
「非農＝每月第一個週五」12 個月會錯 4 個月（2025/12 參考月發布在 1/09＝第二個週五、
2026/01 發布在 2/11＝**週三**、2026/04 發布在 5/08＝第二個週五、2026/06 發布在
7/02＝週四，撞美國國慶連假）。一條 33% 會錯的規則比沒有規則更糟——它會安靜地
產生一個看起來很正常的錯日期。
"""
import json

from stocks_power_rich.sources import econ_calendar as ec


BLS_HTML = """
<table class="release-list">
<tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>
<tr><td><p>July 2026</p></td><td><p>Aug. 12, 2026</p></td><td><p>08:30 AM</p></td></tr>
<tr><td><p>August 2026</p></td><td><p>Sep. 11, 2026</p></td><td><p>08:30 AM</p></td></tr>
<tr><td><p>April 2026</p></td><td><p>May 12, 2026</p></td><td><p>08:30 AM</p></td></tr>
</table>
"""


def test_parse_bls_schedule_reads_reference_month_and_release_date():
    rows = ec.parse_bls_schedule(BLS_HTML)
    assert rows == [
        {"date": "2026-08-12", "ref": "July 2026"},
        {"date": "2026-09-11", "ref": "August 2026"},
        {"date": "2026-05-12", "ref": "April 2026"},
    ]


def test_parse_bls_schedule_handles_may_which_has_no_trailing_period():
    """月份縮寫多半帶句點（Aug.／Sep.），但 May 本身就是三個字母、官方不加句點。"""
    rows = ec.parse_bls_schedule(BLS_HTML)
    assert {"date": "2026-05-12", "ref": "April 2026"} in rows


FOMC_HTML = """
<div class="panel-heading">2026 FOMC Meetings</div>
<div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">27-28</div>
<div class="fomc-meeting__month"><strong>March</strong></div>
<div class="fomc-meeting__date">17-18*</div>
<div class="fomc-meeting__month"><strong>December</strong></div>
<div class="fomc-meeting__date">8-9*</div>
<div class="panel-heading">2025 FOMC Meetings</div>
<div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">28-29</div>
"""


def test_parse_fomc_calendar_pairs_month_with_its_own_date():
    """**必須同一個 match 內成對擷取**。先前寫成「兩份 findall 再 zip」，實跑對
    2026 年真實頁面得到 January 27-28／April 17-18／July 28-29／October 16-17
    ——只有第一筆是對的，其餘全是交叉配對出來的假日期，而且看起來完全正常。"""
    rows = ec.parse_fomc_calendar(FOMC_HTML, 2026)
    assert rows == [
        {"date": "2026-01-28", "projections": False},
        {"date": "2026-03-18", "projections": True},
        {"date": "2026-12-09", "projections": True},
    ]


def test_parse_fomc_calendar_only_reads_the_year_it_was_asked_for():
    """同一頁含多年度區塊，抓錯年會給出去年的會議日期。"""
    rows = ec.parse_fomc_calendar(FOMC_HTML, 2025)
    assert rows == [{"date": "2025-01-29", "projections": False}]


def test_fomc_date_is_the_decision_day_not_the_first_day():
    """會議兩天，利率決議在**第二天**收盤後公布——行事曆要標的是那一天。"""
    rows = ec.parse_fomc_calendar(FOMC_HTML, 2026)
    assert rows[0]["date"] == "2026-01-28"      # 官方寫 27-28


MOPS_HTML = """
<table><tr><th>公司代號</th><th>公司名稱</th><th>召開法人說明會日期</th><th>時間</th></tr>
<tr><td>1217</td><td>愛之味</td><td>115/09/11</td><td>14:30</td><td>台北市</td></tr>
<tr><td>2330</td><td>台積電</td><td>115/09/17</td><td>14:00</td><td>新竹</td></tr>
<tr><td colspan="5">合計</td></tr>
</table>
"""


def test_parse_mops_conferences_converts_roc_dates():
    rows = ec.parse_mops_conferences(MOPS_HTML)
    assert rows == [
        {"date": "2026-09-11", "code": "1217", "name": "愛之味", "time": "14:30"},
        {"date": "2026-09-17", "code": "2330", "name": "台積電", "time": "14:00"},
    ]


def test_parse_mops_conferences_ignores_rows_without_a_four_digit_code():
    """表頭與合計列混在同一張表裡（同 t21sc03 月營收那支的既有處理）。"""
    assert all(r["code"].isdigit() for r in ec.parse_mops_conferences(MOPS_HTML))


NASDAQ_PAYLOAD = {"data": {"rows": [
    {"symbol": "ORCL", "name": "Oracle Corporation", "time": "time-after-hours",
     "marketCap": "$457,361,195,000"},
    {"symbol": "DSGX", "name": "The Descartes Systems Group Inc.",
     "time": "time-pre-market", "marketCap": "$6,759,641,600"},
    {"symbol": "ZZZ", "name": "No Cap Co", "time": "time-not-supplied", "marketCap": ""},
]}}


def test_parse_nasdaq_earnings_keeps_market_cap_as_a_number():
    rows = ec.parse_nasdaq_earnings(NASDAQ_PAYLOAD, "2026-09-10")
    assert rows[0] == {"date": "2026-09-10", "symbol": "ORCL",
                       "name": "Oracle Corporation", "session": "盤後",
                       "market_cap": 457361195000.0}


def test_parse_nasdaq_earnings_drops_rows_with_no_market_cap():
    """市值是「大型權值股」這個篩選的唯一依據，缺值就無從判斷——寧可不列。"""
    got = [r["symbol"] for r in ec.parse_nasdaq_earnings(NASDAQ_PAYLOAD, "2026-09-10")]
    assert "ZZZ" not in got


def test_parse_nasdaq_earnings_translates_the_session_marker():
    rows = {r["symbol"]: r["session"]
            for r in ec.parse_nasdaq_earnings(NASDAQ_PAYLOAD, "2026-09-10")}
    assert rows["ORCL"] == "盤後" and rows["DSGX"] == "盤前"


def test_parsers_return_empty_on_garbage_rather_than_raising():
    """來源改版是常態；解析不到就當作沒有這一類，不要讓整則推播炸掉。"""
    assert ec.parse_bls_schedule("<html>改版了</html>") == []
    assert ec.parse_fomc_calendar("<html>改版了</html>", 2026) == []
    assert ec.parse_mops_conferences("<html>改版了</html>") == []
    assert ec.parse_nasdaq_earnings({}, "2026-09-10") == []
    assert ec.parse_nasdaq_earnings({"data": None}, "2026-09-10") == []


def _jpx_xlsx(rows):
    """仿 JPX「決算発表予定日」xlsx：前 4 列是標題與基準日，第 5 列才是欄名。"""
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "List"
    ws.append(["８月に四半期末又は期末を迎えた決算発表予定会社の"])
    ws.append(["List of companies scheduled"])
    ws.append(["2026年9月3日 現在"])
    ws.append(["As of 2026/9/3"])
    ws.append(["決算発表予定日\nScheduled Dates ", "コード\nCode", "会社名", "Issue Name",
               "決算期末\nFiscal Year-end", "業種名", "Industry", "種別"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_jpx_schedule_reads_date_code_name_and_kind():
    import datetime as dt

    blob = _jpx_xlsx([
        [dt.datetime(2026, 9, 14), "3189", "ＡＮＡＰホールディングス", "ANAP HOLDINGS INC.",
         dt.datetime(2026, 8, 31), "小売業", "Retail Trade", "本決算"],
        [dt.datetime(2026, 9, 15), "2678", "アスクル", "ASKUL Corporation",
         dt.datetime(2027, 5, 20), "小売業", "Retail Trade", "第１四半期"],
    ])
    rows = ec.parse_jpx_schedule(blob)
    assert rows == [
        {"date": "2026-09-14", "code": "3189", "name": "ＡＮＡＰホールディングス", "kind": "本決算"},
        {"date": "2026-09-15", "code": "2678", "name": "アスクル", "kind": "第１四半期"},
    ]


def test_parse_jpx_schedule_skips_the_four_header_rows_and_junk():
    """欄名列自己也長得像資料列（第一格是字串），只認「日期可解析＋代號是 4 碼」的列。"""
    import datetime as dt

    blob = _jpx_xlsx([
        ["合計", None, None, None, None, None, None, None],
        [dt.datetime(2026, 9, 14), "3189", "ＡＮＡＰ", "ANAP", None, None, None, "本決算"],
        [None, "9999", "無日期", "NoDate", None, None, None, "第１四半期"],
    ])
    rows = ec.parse_jpx_schedule(blob)
    assert [r["code"] for r in rows] == ["3189"]


def test_parse_jpx_schedule_returns_empty_on_a_non_xlsx_blob():
    """來源改版／下載到 HTML 錯誤頁時不要炸掉整份行事曆。"""
    assert ec.parse_jpx_schedule(b"<html>not an excel file</html>") == []


def test_parse_jpx_links_finds_every_schedule_file():
    """JPX 每個會計月份一個檔，檔名帶日期會變（kessan08_0904.xlsx），
    **必須從頁面解析、不可寫死**。"""
    html = '''<a href="/listing/event-schedules/financial-announcement/x-att/kessan07_0904.xlsx">7月</a>
              <a href="/listing/event-schedules/financial-announcement/x-att/kessan08_0904.xlsx">8月</a>
              <a href="/other/thing.pdf">無關</a>'''
    got = ec.parse_jpx_links(html)
    assert got == [
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/x-att/kessan07_0904.xlsx",
        "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/x-att/kessan08_0904.xlsx",
    ]


def test_parse_tv_market_caps_reads_usd_values():
    """TradingView 的 market_cap_basic 是**美元**（實測 Toyota 0.24 兆＝$240B），
    所以日股可以沿用美股那條美元門檻，不必另立一個日圓門檻。"""
    payload = {"data": [
        {"s": "TSE:7203", "d": [2968.5, 240e9]},
        {"s": "TSE:3189", "d": [104, None]},
        {"s": "TSE:6758", "d": [3654, 150e9]},
    ]}
    assert ec.parse_tv_market_caps(payload) == {"7203": 240e9, "6758": 150e9}
