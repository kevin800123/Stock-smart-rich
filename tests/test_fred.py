from stocks_power_rich.sources import fred


def test_parse_fred_csv_normal_rows():
    text = "DATE,VIXCLS\n2026-07-21,17.05\n2026-07-22,16.64\n2026-07-23,18.70\n"
    out = fred.parse_fred_csv(text)
    assert out == {"2026-07-21": 17.05, "2026-07-22": 16.64, "2026-07-23": 18.70}


def test_parse_fred_csv_skips_no_data_marker():
    # FRED 用 "." 標示當天無觀測值（假日等），不能當成 0 或報錯
    text = "DATE,NIKKEI225\n2026-07-20,.\n2026-07-21,66232.19\n"
    out = fred.parse_fred_csv(text)
    assert out == {"2026-07-21": 66232.19}


def test_parse_fred_csv_empty_response():
    assert fred.parse_fred_csv("") == {}


def test_parse_fred_csv_header_only():
    assert fred.parse_fred_csv("DATE,VIXCLS\n") == {}


def test_parse_fred_csv_tolerates_malformed_line():
    # 單行壞資料不該讓整批解析失敗
    text = "DATE,VIXCLS\n2026-07-21,17.05\nnot,a,valid,row\n2026-07-23,18.70\n"
    out = fred.parse_fred_csv(text)
    assert out == {"2026-07-21": 17.05, "2026-07-23": 18.70}


def test_fetch_fred_series_wraps_http(monkeypatch):
    class _Resp:
        status_code = 200
        text = "DATE,VIXCLS\n2026-07-23,18.70\n"

    calls = {}

    def fake_get(url, params=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        return _Resp()

    monkeypatch.setattr(fred.httpx, "get", fake_get)
    out = fred.fetch_fred_series("VIXCLS", "2026-07-01")
    assert out == {"2026-07-23": 18.70}
    assert calls["params"]["id"] == "VIXCLS"
    assert calls["params"]["cosd"] == "2026-07-01"


def test_fetch_fred_series_failure_returns_empty(monkeypatch):
    def raise_err(url, params=None, timeout=None):
        raise Exception("boom")

    monkeypatch.setattr(fred.httpx, "get", raise_err)
    assert fred.fetch_fred_series("VIXCLS", "2026-07-01") == {}


def test_fetch_fred_series_non_200_returns_empty(monkeypatch):
    class _Resp:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(fred.httpx, "get", lambda *a, **k: _Resp())
    assert fred.fetch_fred_series("VIXCLS", "2026-07-01") == {}
