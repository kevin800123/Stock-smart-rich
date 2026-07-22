from stocks_power_rich.sources import mis


def test_parse_mis_rank_full_fields():
    """高價股排行需要的完整欄位：現價 z（- 退回買一）、昨收 y 算漲跌、時間 t、名稱 n。"""
    payload = {"rtcode": "0000", "msgArray": [
        {"c": "2330", "n": "台積電", "z": "1100.0000", "y": "1090.0000", "t": "10:23:45", "b": "-",
         "v": "28754"},
        {"c": "8069", "n": "元太", "z": "-", "y": "210.0000", "t": "10:23:40", "b": "212.50_212.00_"},
        {"c": "9999", "n": "壞檔", "z": "-", "y": "-", "t": "-", "b": "-"},   # 無價 → 略過
    ]}
    out = mis.parse_mis_rank(payload)
    r = out["2330"]
    assert r["price"] == 1100.0 and r["name"] == "台積電"
    assert r["chg"] == 10.0 and r["chg_pct"] == 0.92        # (1100-1090)/1090
    assert r["time"] == "10:23"
    assert r["vol"] == 28754                                # v＝當日累積成交量（張）
    assert out["8069"]["price"] == 212.5                    # z='-' 退回買一
    assert out["8069"]["vol"] is None                       # 無 v → None（不假造 0）
    assert "9999" not in out


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
