from stocks_power_rich.sources import mis


def test_parse_mis_quotes_z_and_bid_fallback():
    payload = {"rtcode": "0000", "msgArray": [
        {"c": "2330", "z": "1100.0000", "b": "1099.00_1098.00_"},   # 有成交價 → 用 z
        {"c": "8069", "z": "-", "b": "212.50_212.00_211.50_"},      # 無成交 → 退回最佳買價
        {"c": "9999", "z": "-", "b": "-"},                          # 都沒有 → 略過
    ]}
    out = mis.parse_mis_quotes(payload)
    assert out["2330"] == 1100.0
    assert out["8069"] == 212.5
    assert "9999" not in out
