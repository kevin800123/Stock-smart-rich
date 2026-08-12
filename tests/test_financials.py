"""季報財務（mopsfin.twse.com.tw 財務比較E點通 /compare/data）純解析函式測試。

樣本取自實測回應結構：頂層 xaxisList（季別標籤，如 "2013Q1"）＋ showNameList（每序列的
「代號 名稱 (產業)」）＋ graphData（每序列 data 為 [xIndex, value, 型別] tuples，型別 "C"＝合併）。
一次請求可帶多個 companyId → 多序列（實測 50 檔一次到位），由 showNameList 順序對應回代號。
"""
from stocks_power_rich.sources import financials

# 兩檔（2330 台積電、2317 鴻海）ROE，各 4 季（真實結構、值取自實測片段）
_PAYLOAD = {
    "xaxisList": ["2025Q2", "2025Q3", "2025Q4", "2026Q1"],
    "ylabel": "%",
    "showNameList": ["2330 台積電 (上市半導體業)", "2317 鴻海 (上市其他電子業)"],
    "graphData": [
        {"label": "台積電", "data": [[0, 9.10, "C"], [1, 9.36, "C"], [2, 9.63, "C"], [3, 10.06, "C"]]},
        {"label": "鴻海", "data": [[0, 2.50, "C"], [1, 2.70, "C"], [2, 2.80, "C"], [3, 2.88, "C"]]},
    ],
}


def test_parse_ratio_series_maps_code_to_quarter_values():
    out = financials.parse_ratio_series(_PAYLOAD)
    assert set(out.keys()) == {"2330", "2317"}
    assert out["2330"] == {"2025Q2": 9.10, "2025Q3": 9.36, "2025Q4": 9.63, "2026Q1": 10.06}
    assert out["2317"]["2026Q1"] == 2.88


def test_parse_ratio_series_skips_null_values():
    """新上市公司早期季別沒有資料，值為 null → 略過該季（缺席＝無資料，同 lan_score 的 None 守衛）。"""
    payload = {
        "xaxisList": ["2025Q3", "2025Q4", "2026Q1"],
        "showNameList": ["6488 環球晶 (上市半導體業)"],
        "graphData": [{"label": "環球晶", "data": [[0, None, "C"], [1, 5.5, "C"], [2, 6.1, "C"]]}],
    }
    out = financials.parse_ratio_series(payload)
    assert out["6488"] == {"2025Q4": 5.5, "2026Q1": 6.1}  # 2025Q3 的 null 不入表


def test_parse_ratio_series_empty_payload():
    assert financials.parse_ratio_series({}) == {}
    assert financials.parse_ratio_series({"graphData": [], "showNameList": []}) == {}


def test_parse_ratio_series_string_values_coerced():
    """值若以字串回傳（防禦性），要能轉 float；無法轉的略過。"""
    payload = {
        "xaxisList": ["2026Q1", "2026Q2"],
        "showNameList": ["1101 台泥 (上市水泥工業)"],
        "graphData": [{"label": "台泥", "data": [[0, "7.5", "C"], [1, "--", "C"]]}],
    }
    out = financials.parse_ratio_series(payload)
    assert out["1101"] == {"2026Q1": 7.5}


def test_fetch_financial_ratio_posts_batch_and_parses(monkeypatch):
    """一次 POST 帶多個 companyId；item 對應 RATIO_ITEMS 的 mopsfin 代碼。"""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        class _Resp:
            def json(self):
                return _PAYLOAD
        return _Resp()

    monkeypatch.setattr(financials.httpx, "post", fake_post)
    out = financials.fetch_financial_ratio(["2330", "2317"], "roe")
    assert out["2330"]["2026Q1"] == 10.06
    assert captured["url"] == financials.MOPSFIN_URL
    # roe → mopsfin 代碼 ROE，且兩個 companyId 都在 payload 裡
    assert captured["data"]["compareItem"] == "ROE"
    assert captured["data"]["companyId"] == ["2330", "2317"]


def test_fetch_financial_ratio_unknown_indicator_raises():
    import pytest
    with pytest.raises(KeyError):
        financials.fetch_financial_ratio(["2330"], "not_a_real_indicator")


def test_fetch_financial_ratio_returns_empty_on_error(monkeypatch):
    def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(financials.httpx, "post", boom)
    assert financials.fetch_financial_ratio(["2330"], "roe") == {}


def test_ratio_items_cover_the_json_available_lan_keys():
    """sub-task 1 只交付「乾淨 JSON」那 8 個指標；pretax_income 與 capex 需 HTML 報表，屬 sub-task 2。"""
    assert set(financials.RATIO_ITEMS) == {
        "revenue", "gross_margin", "net_income", "ocf",
        "debt_ratio", "roe", "ar_turnover", "inv_turnover",
    }


# ---- sub-task 2：完整報表 HTML（/compare/report），多公司、逐季 ----

# 真實結構縮樣：headTable=會計科目（含縮排空白）、bodyTable 每列 N 欄對應 N 家公司、
# 表頭 <th> 帶代號、隱藏 input 回顯查詢到的實際季別。
_REPORT_HTML = """
<div id="headTable" class="table"><table><thead>
<tr><th class="text-left">會計科目</th></tr></thead><tbody>
<tr><td class="text-left" nowrap="">營業收入合計</td></tr>
<tr><td class="text-left" nowrap="">　營業費用合計</td></tr>
<tr><td class="text-left" nowrap="">稅前淨利（淨損）</td></tr>
<tr><td class="text-left" nowrap="">所得稅費用（利益）合計</td></tr>
</tbody></table></div>
<div id="bodyTable" class="table"><table><thead>
<tr><th nowrap>2330&nbsp;台積電<br/>(上市半導體業) </th><th nowrap>1101&nbsp;台泥<br/>(上市水泥工業) </th></tr>
</thead><tbody>
<tr><td class="text-right" style="text-align: right;font-weight:normal">1,134,103,440</td>
    <td class="text-right" style="text-align: right;font-weight:normal">33,168,148</td></tr>
<tr><td class="text-right" style="text-align: right;font-weight:normal">94,005,657</td>
    <td class="text-right" style="text-align: right;font-weight:normal">3,416,199</td></tr>
<tr><td class="text-right" style="text-align: right;font-weight:normal">687,799,687</td>
    <td class="text-right" style="text-align: right;font-weight:normal">2,132,735</td></tr>
<tr><td class="text-right" style="text-align: right;font-weight:normal">114,998,383</td>
    <td class="text-right" style="text-align: right;font-weight:normal">927,996</td></tr>
</tbody></table></div>
<input type="hidden" name="yearseason" value="2026Q1"/>
"""


def test_parse_report_maps_code_and_label_to_value():
    quarter, data = financials.parse_report(_REPORT_HTML)
    assert quarter == "2026Q1"                       # 從隱藏 input 取實際季別（防「悄悄回舊季」）
    assert set(data.keys()) == {"2330", "1101"}
    # 標籤去除縮排空白；千分位逗號去掉轉 float
    assert data["2330"]["營業費用合計"] == 94005657.0
    assert data["2330"]["稅前淨利（淨損）"] == 687799687.0
    assert data["1101"]["所得稅費用（利益）合計"] == 927996.0


def test_parse_report_empty_when_quarter_not_published():
    """尚未公布的季別：表格空、但隱藏 input 仍回顯查詢季別（呼叫端據此判斷『沒資料』而非當機）。"""
    html = '<div id="bodyTable"><table><tbody></tbody></table></div><input name="yearseason" value="2026Q2"/>'
    quarter, data = financials.parse_report(html)
    assert quarter == "2026Q2" and data == {}


def test_fetch_report_always_sends_ys(monkeypatch):
    """/compare/report 不帶 ys 會悄悄回很舊的固定季（實測 2020Q2），故 fetch 一律組出 ys 並送。"""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        class _Resp:
            text = _REPORT_HTML
        return _Resp()

    monkeypatch.setattr(financials.httpx, "post", fake_post)
    quarter, data = financials.fetch_report(["2330", "1101"], "IncomeStatement", 2026, 1)
    assert captured["data"]["ys"] == "20261"
    assert captured["data"]["compareItem"] == "IncomeStatement"
    assert captured["data"]["companyId"] == ["2330", "1101"]
    assert data["2330"]["稅前淨利（淨損）"] == 687799687.0


def test_fetch_report_returns_empty_on_error(monkeypatch):
    def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(financials.httpx, "post", boom)
    assert financials.fetch_report(["2330"], "IncomeStatement", 2026, 1) == (None, {})


def test_report_line_items_map_lan_and_estimate_keys():
    """REPORT_ITEMS：把要用的科目 → (報表, 會計科目標籤)。含 lan_score 的 pretax/capex，
    以及 Call_LE 需要的營業費用/所得稅費用。"""
    assert financials.REPORT_ITEMS["pretax_income"] == ("IncomeStatement", "稅前淨利（淨損）")
    assert financials.REPORT_ITEMS["opex"] == ("IncomeStatement", "營業費用合計")
    assert financials.REPORT_ITEMS["income_tax"] == ("IncomeStatement", "所得稅費用（利益）合計")
    assert financials.REPORT_ITEMS["capex"] == ("CashflowStatement", "取得不動產、廠房及設備")


def test_decumulate_quarterly_real_tsmc_2025():
    """完整報表（/compare/report）的金額科目是年度累計（Q2=H1、Q3=前三季…），要反推單季。
    真實數字（2330 台積電 2025 全年四季稅前淨利，累計值）：
    Q1=839,253,664　Q2=1,773,045,533　Q3=2,762,963,851　Q4=3,809,054,272（單調遞增，確認是累計）。
    反推單季：Q1 own＝自己（Q1 本來就是當季）；Q2/Q3/Q4 own＝本季累計－上季累計。
    """
    cumulative = {"2025Q1": 839253664.0, "2025Q2": 1773045533.0,
                  "2025Q3": 2762963851.0, "2025Q4": 3809054272.0}
    out = financials.decumulate_quarterly(cumulative)
    assert out["2025Q1"] == 839253664.0
    assert out["2025Q2"] == 933791869.0
    assert out["2025Q3"] == 989918318.0
    assert out["2025Q4"] == 1046090421.0


def test_decumulate_quarterly_resets_at_fiscal_year_boundary():
    """跨年度：新年度 Q1 的累計＝自己，不能跟上一年度 Q4 相減（即使 Q4 剛好在輸入裡）。"""
    cumulative = {"2025Q4": 3809054272.0, "2026Q1": 1134103440.0}
    out = financials.decumulate_quarterly(cumulative)
    assert out["2026Q1"] == 1134103440.0   # 新年度重置，不是 1134103440-3809054272


def test_decumulate_quarterly_missing_prior_quarter_returns_none():
    """算單季需要「上一季」的累計值；缺前一季（如只給 Q3、沒給 Q2）→ 該季回 None，不瞎猜。"""
    cumulative = {"2025Q1": 839253664.0, "2025Q3": 2762963851.0}  # 缺 Q2
    out = financials.decumulate_quarterly(cumulative)
    assert out["2025Q1"] == 839253664.0   # Q1 不需要前一季，仍算得出
    assert out["2025Q3"] is None          # 缺 Q2 → 算不出 Q3 own


def test_decumulate_quarterly_empty():
    assert financials.decumulate_quarterly({}) == {}
