import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient

from stocks_power_rich.main import create_app
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, insert_chip_snapshot, bulk_upsert_custody, get_setting, set_setting


@pytest.fixture(autouse=True)
def _alert_clock_on_a_weekday(monkeypatch):
    """告警週六日不推，所以判斷依據的「現在」必須固定，不能吃系統時間。

    否則這個檔案的告警測試會「星期天跑紅、星期一跑綠」——而失敗原因跟被測的東西無關
    （同 conftest 那支行事曆樁的理由）。固定在最近一個平日 21:00；要測週末的測試自己覆寫。
    """
    from datetime import datetime
    from stocks_power_rich.api import helpers
    d = datetime.now().replace(hour=21, minute=0, second=0, microsecond=0)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    monkeypatch.setattr(helpers, "_now", lambda: d)


def test_health_endpoint_calculation(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    # Insert mock records
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    upsert_market_daily(conn, {"date": yesterday_str, "taiex": 23000.0})
    insert_chip_snapshot(conn, yesterday_str, [{"code": "2330", "name": "台積電", "close": 1000.0}])
    conn.execute("INSERT INTO stock_ohlc (date, code, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
                 (yesterday_str, "2330", 1000.0, 1010.0, 990.0, 1000.0))
    bulk_upsert_custody(conn, yesterday_str, {"2330": {"big1000_pct": 80.0, "big400_pct": 85.0, "big_holders": 10}})
    conn.commit()

    app = create_app()
    client = TestClient(app)

    # Test the API
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["market_daily"]["latest"] == yesterday_str
    assert body["market_daily"]["lag_days"] == 1
    assert body["chip_snapshot"]["latest"] == yesterday_str
    assert body["stock_ohlc"]["latest"] == yesterday_str
    assert body["custody_dist"]["latest_week"] == yesterday_str


def test_health_endpoint_not_ok_when_stale(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t2.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    # Insert old record
    old_date = (date.today() - timedelta(days=15)).isoformat()
    upsert_market_daily(conn, {"date": old_date, "taiex": 23000.0})
    conn.commit()

    app = create_app()
    client = TestClient(app)

    r = client.get("/api/health")
    body = r.json()
    assert body["ok"] is False  # Too old and other tables are empty


def test_alert_deduplication_logic(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t3.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    conn = get_connection(db_file)
    init_db(conn)

    from stocks_power_rich.config import load_config
    cfg = load_config()

    sent_messages = []
    def mock_broadcast_text(token, text):
        sent_messages.append(text)
        return {"ok": True}

    from stocks_power_rich import line_push
    monkeypatch.setattr(line_push, "broadcast_text", mock_broadcast_text)

    # Import the app to get the inner function _check_update_result_and_alert
    from stocks_power_rich import updater

    # 這條測的只有「告警去重」。daily_update 其他步驟（CSV 匯入、AI 摘要、LINE 卡片、備份、
    # 前瞻記帳、自算選股）全部樁掉——原本沒樁，整支排程真的連外，單條要 ~143 秒。
    from stocks_power_rich import main as _main, csv_import, ledger
    from stocks_power_rich.api import market as _market, public as _public
    monkeypatch.setattr(csv_import, "find_latest_file", lambda d: None)
    monkeypatch.setattr(_market, "market_summary_logic", lambda *a, **k: {})
    monkeypatch.setattr(_public, "summary_logic", lambda *a, **k: {})
    monkeypatch.setattr(_main, "_push_line", lambda *a, **k: {"ok": False, "error": "stubbed"})
    monkeypatch.setattr(_main, "backup_db", lambda p: None)
    monkeypatch.setattr(ledger, "record_daily_signals", lambda c_: None)
    monkeypatch.setattr(ledger, "update_ledger_returns", lambda c_: None)
    monkeypatch.setattr(_main, "_refresh_self_screen_cache", lambda c_: {"skipped": "stubbed"})
    # 網路絆線：任何 DNS 查詢或 socket 連線都記下來並直接失敗，最後斷言一次都沒有
    import socket
    tripped = []

    def _trip(*a, **k):
        tripped.append(a[:2])
        raise RuntimeError("network tripwire: 這條測試不可以連外")
    monkeypatch.setattr(socket, "getaddrinfo", _trip)
    monkeypatch.setattr(socket.socket, "connect", _trip)

    # Run 1: failure alert
    failed_result = {
        "date": (date.today() - timedelta(days=2)).isoformat(),
        "success": ["twse_taiex"],
        "failed": [{"source": "twse", "name": "twse_inst", "error": "timeout error"}]
    }
    
    monkeypatch.setattr(updater, "run_update", lambda conn, tickers: failed_result)
    
    captured_jobs = {}
    def mock_start_scheduler(job_func, schedule_time):
        captured_jobs["scheduled_job"] = job_func
        class DummyScheduler:
            def add_job(self, func, *args, **kwargs):
                captured_jobs[kwargs.get("id", "dummy")] = func
            def shutdown(self, wait=False):
                pass
        return DummyScheduler()

    import stocks_power_rich.scheduler
    monkeypatch.setattr(stocks_power_rich.scheduler, "start_scheduler", mock_start_scheduler)
    
    app = create_app(enable_scheduler=True)
    try:
        assert "scheduled_job" in captured_jobs
        # 排程器拿到的是 run_job 包過的版本（同一天第二次呼叫會被 job_runs 去重略過），
        # 這裡測的是告警本身的去重，所以呼叫原始 job（app.state.jobs）
        job = app.state.jobs["daily_update"]

        job()

        assert len(sent_messages) == 1
        assert "資料更新警告" in sent_messages[0]
        assert "twse_inst" in sent_messages[0]

        job()
        assert len(sent_messages) == 1  # Still 1!

        different_failed_result = {
            "date": (date.today() - timedelta(days=2)).isoformat(),
            "success": ["twse_taiex"],
            "failed": [{"source": "taifex", "name": "taifex_chips", "error": "connection failed"}]
        }
        monkeypatch.setattr(updater, "run_update", lambda conn, tickers: different_failed_result)

        job()
        assert len(sent_messages) == 2  # Sent again!
        assert tripped == [], f"排程在測試裡連外了：{tripped[:3]}"
    finally:
        if getattr(app.state, "scheduler", None):
            app.state.scheduler.shutdown(wait=False)


def test_expected_after_close_data_is_not_a_line_alert(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t4.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    c = get_connection(db_file)
    init_db(c)
    from stocks_power_rich import line_push
    from stocks_power_rich.api.helpers import _check_update_result_and_alert

    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda token, text: sent.append(text))
    _check_update_result_and_alert(c, {
        "date": date.today().isoformat(),
        "failed": [
            {"name": "twse_credit", "error": "信用交易概況尚未公布，稍後回補"},
            {"name": "intl", "error": "當日場次收盤尚未取得，下次更新自動回補"},
        ],
    })
    assert sent == []


def test_otc_margin_unpublished_is_not_a_line_alert(tmp_path, monkeypatch):
    """上櫃融資餘額約 21:00 後才公布——error 寫明「尚未發布，稍後回補」時屬預期晚到，不告警。"""
    db_file = str(tmp_path / "t4c.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    c = get_connection(db_file)
    init_db(c)
    from stocks_power_rich import line_push
    from stocks_power_rich.api.helpers import _check_update_result_and_alert

    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda token, text: sent.append(text))
    _check_update_result_and_alert(c, {
        "date": date.today().isoformat(),
        "failed": [
            {"source": "tpex", "name": "otc_margin", "error": "上櫃融資餘額尚未發布，稍後回補"},
        ],
    })
    assert sent == []


def test_twse_credit_failure_with_real_error_still_alerts(tmp_path, monkeypatch):
    """`expected_later` 只在 twse_credit 的 error 含「尚未」／「稍後回補」時才排除告警——
    同一個欄位若因別的原因失敗（連線逾時等），仍要照常告警，不能用欄位名無條件排除。
    """
    db_file = str(tmp_path / "t4b.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db_file)
    c = get_connection(db_file)
    init_db(c)
    from stocks_power_rich import line_push
    from stocks_power_rich.api.helpers import _check_update_result_and_alert

    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda token, text: sent.append(text))
    _check_update_result_and_alert(c, {
        "date": date.today().isoformat(),
        "failed": [
            {"source": "twse", "name": "twse_credit", "error": "ConnectTimeout: timed out"},
        ],
    })
    assert sent, "應該要送出告警"
    assert "twse_credit" in sent[0], sent[0]


def test_alert_names_the_source_so_two_markets_are_distinguishable(monkeypatch, tmp_path):
    """月營收是**逐市場**判定的（上市 twse／上櫃 tpex 各自失敗），但告警只印 `name`，
    所以使用者看到的永遠是「revenue」——分不出是哪一邊。實測 2026-09-12 收到那則時
    完全無從判斷（`source` 其實一直都記著，只是沒印出來）。"""
    from stocks_power_rich.api.helpers import _check_update_result_and_alert
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db

    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda token, msg: sent.append(msg) or {"ok": True})
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)

    _check_update_result_and_alert(conn, {
        "date": (date.today() - timedelta(days=1)).isoformat(),
        "success": [],
        "failed": [{"source": "tpex", "name": "revenue", "error": "ConnectTimeout: timed out"}],
    })
    assert sent, "應該要送出告警"
    assert "revenue" in sent[0] and "tpex" in sent[0], sent[0]
    assert "ConnectTimeout" in sent[0], "原因也要帶上，否則還是查不出來"


def test_no_data_alert_on_weekends(monkeypatch, tmp_path):
    """**週六日不推資料更新告警**（使用者決定：六日台股沒開盤，LINE 只要週六的選股週報）。

    實際發生過：2026-09-12(六)、09-13(日) 21:00 各收到一則「資料更新警告」。每日排程週末
    照跑是對的（月營收每天重抓、備份、自算選股快取），錯的是把週末的失敗推上 LINE。
    週末的失敗不會被遺漏：同一個來源週一還失敗的話，週一那次就會告警。
    """
    from datetime import datetime
    from stocks_power_rich.api import helpers
    from stocks_power_rich import line_push

    sent = []
    monkeypatch.setattr(line_push, "broadcast_text", lambda token, msg: sent.append(msg) or {"ok": True})
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    failure = {"date": "2026-09-11", "success": [],
               "failed": [{"source": "tpex", "name": "revenue", "error": "ReadError: [Errno 104]"}]}

    for weekend in (datetime(2026, 9, 12, 21, 0), datetime(2026, 9, 13, 21, 0)):   # 六、日
        monkeypatch.setattr(helpers, "_now", lambda d=weekend: d)
        helpers._check_update_result_and_alert(conn, failure)
    assert sent == []
    # 週末沒推，就不能把去重鍵記下來，否則週一同樣的失敗會被當成「已經講過」而不推
    assert get_setting(conn, "last_alert_key") in (None, "")

    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 14, 21, 0))       # 一
    helpers._check_update_result_and_alert(conn, {**failure, "date": "2026-09-14"})
    assert len(sent) == 1 and "revenue" in sent[0]
