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
