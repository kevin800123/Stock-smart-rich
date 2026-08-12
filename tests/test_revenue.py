"""月營收（MOPS t187ap05_L / mopsfin_t187ap05_O）純解析函式測試。

樣本列取自實測回應（2026-08-11 出表，2026-07 資料年月），保留真實出現過的邊界案例：
去年同月營收為 0 時「去年同月增減(%)」是空字串（非 "-"、也非負數），備註有實際內容 vs "-"。
"""
from stocks_power_rich.sources import revenue

_TWSE_2330 = {
    "出表日期": "1150811", "資料年月": "11507", "公司代號": "2330", "公司名稱": "台積電",
    "產業別": "半導體業", "營業收入-當月營收": "467580548", "營業收入-上月營收": "442679969",
    "營業收入-去年當月營收": "323165707", "營業收入-上月比較增減(%)": "5.624961765550318",
    "營業收入-去年同月增減(%)": "44.68755126916978", "累計營業收入-當月累計營收": "2872064238",
    "累計營業收入-去年累計營收": "2096211240", "累計營業收入-前期比較增減(%)": "37.01215713355301",
    "備註": "-",
}
_TWSE_1102_NEG = {
    "出表日期": "1150811", "資料年月": "11507", "公司代號": "1102", "公司名稱": "亞泥",
    "產業別": "水泥工業", "營業收入-當月營收": "5398231", "營業收入-上月營收": "5885972",
    "營業收入-去年當月營收": "5836590", "營業收入-上月比較增減(%)": "-8.286498814469386",
    "營業收入-去年同月增減(%)": "-7.510532691177554", "累計營業收入-當月累計營收": "38104679",
    "累計營業收入-去年累計營收": "41097034", "累計營業收入-前期比較增減(%)": "-7.281194550438847",
    "備註": "-",
}
_TWSE_1438_NO_PRIOR_YEAR = {  # 去年同月營收=0（無可比基期）→ 增減(%) 是空字串，不是負數也不是 "-"
    "出表日期": "1150811", "資料年月": "11507", "公司代號": "1438", "公司名稱": "三地開發",
    "產業別": "建材營造", "營業收入-當月營收": "39787", "營業收入-上月營收": "1115",
    "營業收入-去年當月營收": "0", "營業收入-上月比較增減(%)": "3468.3408071748877",
    "營業收入-去年同月增減(%)": "", "累計營業收入-當月累計營收": "431482",
    "累計營業收入-去年累計營收": "241", "累計營業收入-前期比較增減(%)": "178938.17427385892",
    "備註": "因本年已有建案完工交屋，故營收變化大。",
}
_TPEX_1240 = {
    "出表日期": "1150811", "資料年月": "11507", "公司代號": "1240", "公司名稱": "茂生農經",
    "產業別": "農業科技", "營業收入-當月營收": "242511", "營業收入-上月營收": "270176",
    "營業收入-去年當月營收": "214130", "營業收入-上月比較增減(%)": "-10.239621580007107",
    "營業收入-去年同月增減(%)": "13.254097977863914", "累計營業收入-當月累計營收": "1683183",
    "累計營業收入-去年累計營收": "1564493", "累計營業收入-前期比較增減(%)": "7.586483288835424",
    "備註": "-",
}


def test_parse_monthly_revenue_basic_fields():
    out = revenue.parse_monthly_revenue([_TWSE_2330])
    row = out["2330"]
    assert row["name"] == "台積電"
    assert row["industry"] == "半導體業"
    assert row["year_month"] == "2026-07"
    assert row["report_date"] == "2026-08-11"
    assert row["revenue"] == 467580548.0
    assert row["revenue_last_year"] == 323165707.0
    assert row["yoy_pct"] == 44.68755126916978
    assert row["revenue_accum"] == 2872064238.0
    assert row["accum_yoy_pct"] == 37.01215713355301
    assert row["note"] is None  # "-" → None


def test_parse_monthly_revenue_negative_yoy_preserved():
    out = revenue.parse_monthly_revenue([_TWSE_1102_NEG])
    assert out["1102"]["yoy_pct"] == -7.510532691177554


def test_parse_monthly_revenue_missing_prior_year_yields_none_not_zero():
    """空字串（無可比基期）要落成 None，不能被 _f 誤判成 0 或拋例外。"""
    out = revenue.parse_monthly_revenue([_TWSE_1438_NO_PRIOR_YEAR])
    row = out["1438"]
    assert row["yoy_pct"] is None
    assert row["revenue_last_year"] == 0.0  # 這是真實回報的 0，不是缺值
    assert row["note"] == "因本年已有建案完工交屋，故營收變化大。"


def test_parse_monthly_revenue_shared_shape_across_markets():
    """TWSE 與 TPEx 的 t187ap05 回應欄位命名完全相同，用同一支解析函式。"""
    out = revenue.parse_monthly_revenue([_TPEX_1240])
    assert out["1240"]["yoy_pct"] == 13.254097977863914
    assert out["1240"]["industry"] == "農業科技"


def test_parse_monthly_revenue_skips_rows_without_code():
    out = revenue.parse_monthly_revenue([{"公司名稱": "無代號"}, _TWSE_2330])
    assert list(out.keys()) == ["2330"]


def test_parse_monthly_revenue_empty_payload():
    assert revenue.parse_monthly_revenue([]) == {}
    assert revenue.parse_monthly_revenue(None) == {}


def test_fetch_twse_revenue_uses_openapi(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        class _Resp:
            def json(self):
                return [_TWSE_2330]
        return _Resp()

    monkeypatch.setattr(revenue.httpx, "get", fake_get)
    out = revenue.fetch_twse_revenue()
    assert out["2330"]["yoy_pct"] == 44.68755126916978
    assert calls[0] == "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"


def test_fetch_twse_revenue_returns_empty_on_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(revenue.httpx, "get", fake_get)
    assert revenue.fetch_twse_revenue() == {}


def test_fetch_otc_revenue_uses_verify_false(monkeypatch):
    """www.tpex.org.tw 憑證缺 SKI，同 sources/tpex.py 其他 fetcher 的既有規矩——
    忘記帶 verify=False 在 Windows 本機測不出來，只有雲端(Linux)才會靜默失敗。"""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        class _Resp:
            def json(self):
                return [_TPEX_1240]
        return _Resp()

    monkeypatch.setattr(revenue.httpx, "get", fake_get)
    out = revenue.fetch_otc_revenue()
    assert out["1240"]["yoy_pct"] == 13.254097977863914
    assert calls[0].get("verify") is False


def test_fetch_otc_revenue_returns_empty_on_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(revenue.httpx, "get", fake_get)
    assert revenue.fetch_otc_revenue() == {}
