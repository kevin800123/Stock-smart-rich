from stocks_power_rich import cli


def test_cli_main_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(
        cli.updater, "run_update",
        lambda conn, tickers: {"date": "2026-06-17", "success": ["twse_taiex"], "failed": []},
    )
    out = cli.main()
    assert out["success"] == ["twse_taiex"]
