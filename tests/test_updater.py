from stocks_power_rich import updater
from stocks_power_rich.db import get_connection, init_db


def test_run_update_collects_and_tolerates_failure(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    monkeypatch.setattr(updater.twse, "fetch_taiex", lambda: {"taiex": 23000.0, "taiex_chg": 50.0})
    monkeypatch.setattr(updater.twse, "fetch_institutional", lambda: {"inst_foreign": 1.0, "inst_trust": 2.0, "inst_dealer": 3.0})
    monkeypatch.setattr(updater.twse, "fetch_margin", lambda: {"margin_balance": 1000.0, "margin_chg": 10.0, "short_balance": 200.0, "short_chg": 5.0})
    monkeypatch.setattr(updater.taifex, "fetch_tx_quote", lambda: {"tx_price": 23010.0, "tx_chg": 40.0})
    monkeypatch.setattr(updater.taifex, "fetch_retail_ratios", lambda: {"fut_inst_net": 600, "retail_ls_mtx": -0.2, "retail_ls_tmf": -0.1})

    monkeypatch.setattr(updater.taifex, "fetch_tx_history", lambda *a, **k: [])

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(updater.intl, "fetch_intl_indices", lambda tickers: boom())

    result = updater.run_update(conn, intl_tickers={"sox": "^SOX"})
    assert "twse_taiex" in result["success"]
    assert any(f["source"] == "intl" for f in result["failed"])
    row = conn.execute("select taiex, retail_ls_mtx from market_daily").fetchone()
    assert row[0] == 23000.0 and row[1] == -0.2
