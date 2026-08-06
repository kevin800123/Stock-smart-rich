from stocks_power_rich.sources import nasdaq


def test_parse_sox_history_normal_rows():
    rows = [
        {"date": "08/05/2026", "close": "12,008.88", "open": "12,141.69"},
        {"date": "08/04/2026", "close": "12,179.26", "open": "11,834.77"},
    ]
    assert nasdaq.parse_sox_history(rows) == {
        "2026-08-05": 12008.88, "2026-08-04": 12179.26,
    }


def test_parse_sox_history_skips_missing_close_marker():
    # 假日/停牌時 Nasdaq 回 "N/A" 或 "--"，不能當成 0
    rows = [{"date": "07/04/2026", "close": "N/A"}, {"date": "07/03/2026", "close": "--"},
            {"date": "07/02/2026", "close": "11,000.00"}]
    assert nasdaq.parse_sox_history(rows) == {"2026-07-02": 11000.0}


def test_parse_sox_history_empty_rows():
    assert nasdaq.parse_sox_history([]) == {}
    assert nasdaq.parse_sox_history(None) == {}


def test_parse_sox_history_tolerates_malformed_row():
    rows = [{"date": "08/05/2026", "close": "12,008.88"},
            {"date": "bad-date", "close": "1.0"},
            {"date": "08/04/2026", "close": "not-a-number"}]
    assert nasdaq.parse_sox_history(rows) == {"2026-08-05": 12008.88}


def test_fetch_sox_history_wraps_http(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"data": {"tradesTable": {"rows": [
                {"date": "08/05/2026", "close": "12,008.88"},
            ]}}}

    calls = {}

    def fake_get(url, headers=None, timeout=None, params=None):
        calls["url"], calls["params"] = url, params
        return _Resp()

    monkeypatch.setattr(nasdaq.httpx, "get", fake_get)
    out = nasdaq.fetch_sox_history(days=30)
    assert out == {"2026-08-05": 12008.88}
    assert calls["params"]["assetclass"] == "index"


def test_fetch_sox_history_non_200_returns_empty(monkeypatch):
    class _Resp:
        status_code = 401

        def json(self):
            return {}

    monkeypatch.setattr(nasdaq.httpx, "get", lambda *a, **k: _Resp())
    assert nasdaq.fetch_sox_history() == {}


def test_fetch_sox_history_failure_returns_empty(monkeypatch):
    def raise_err(*a, **k):
        raise Exception("boom")

    monkeypatch.setattr(nasdaq.httpx, "get", raise_err)
    assert nasdaq.fetch_sox_history() == {}


def test_fetch_sox_history_malformed_json_returns_empty(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(nasdaq.httpx, "get", lambda *a, **k: _Resp())
    assert nasdaq.fetch_sox_history() == {}
