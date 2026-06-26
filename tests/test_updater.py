from datetime import date, timedelta

from stocks_power_rich import updater
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily


def test_run_update_collects_and_tolerates_failure(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.twse, "fetch_taiex", lambda: {"taiex": 23000.0, "taiex_chg": 50.0, "date": "2026-06-23"})
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0, "inst_trust": 2.0, "inst_dealer": 3.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda date=None: {"margin_balance": 1000.0, "margin_chg": 10.0, "short_balance": 200.0, "short_chg": 5.0})
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date", lambda date=None: {
        "tx_price": 23010.0, "tx_chg": 40.0, "fut_inst_net": 600,
        "retail_ls_mtx": -0.2, "retail_ls_tmf": -0.1, "tx_foreign_oi": -76502, "retail_oi_mtx": -600,
    })

    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(updater.intl, "fetch_intl_indices", lambda tickers: boom())

    result = updater.run_update(conn, intl_tickers={"sox": "^SOX"})
    assert "twse_taiex" in result["success"]
    assert any(f["source"] == "intl" for f in result["failed"])
    row = conn.execute("select taiex, retail_ls_mtx from market_daily").fetchone()
    assert row[0] == 23000.0 and row[1] == -0.2


def test_refresh_recent_corrects_inst_and_fills_margin(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    # 近期一筆：三大法人是「錯置/初值」、融資為空（白天更新所致）
    ds = (date.today() - timedelta(days=1)).isoformat()
    upsert_market_daily(conn, {"date": ds, "taiex": 100.0, "inst_foreign": -405.1})
    monkeypatch.setattr(updater.twse, "fetch_institutional",
                        lambda date=None: {"inst_foreign": -1431.89, "inst_trust": 83.95, "inst_dealer": -707.34})
    monkeypatch.setattr(updater.twse, "fetch_margin",
                        lambda date=None: {"margin_balance": 9999.0, "margin_chg": 5.0,
                                           "short_balance": 200.0, "short_chg": -1.0})
    healed = updater._refresh_recent(conn)
    assert ds in healed
    r = conn.execute("SELECT inst_foreign, margin_balance FROM market_daily WHERE date=?", (ds,)).fetchone()
    assert r[0] == -1431.89  # 三大法人被定稿值覆蓋校正
    assert r[1] == 9999.0    # 融資回補
