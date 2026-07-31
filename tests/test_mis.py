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


def test_price_skips_zero_bid_levels_and_takes_first_real_one():
    """委買字串的第一檔可能是佔位的 0.0000，不能盲取 index 0。

    實測 2026-07-30 09:43 證交所回的川湖：z='-'（該筆快照無成交）、
    b='0.0000_7850.0000_7845.0000...'。舊碼取 index 0 得 0.0 當現價，於是
    漲跌算成 0−昨收＝−7,140、漲跌幅 −100%，而 rank_price 又因 `price or close`
    把 0 視為假值退回昨收——畫面就變成「正常價格配 −100%」。
    """
    m = {"c": "2059", "n": "川湖", "z": "-", "y": "7140.0000",
         "b": "0.0000_7850.0000_7845.0000_7840.0000_7835.0000", "v": "149", "t": "09:40:52"}
    assert mis._price(m) == 7850.0


def test_price_returns_none_when_no_positive_quote_at_all():
    """完全沒有可用價（z 與各檔委買都是 0/'-'）→ 回 None，讓呼叫端整檔略過，
    而不是給 0 讓下游算出 −100%。"""
    assert mis._price({"c": "1234", "z": "-", "b": "0.0000_0.0000"}) is None
    assert mis._price({"c": "1234", "z": "-", "b": "-"}) is None
    assert mis._price({"c": "1234", "z": "0.0000", "b": "-"}) is None


def test_parse_mis_rank_never_reports_minus_100_percent():
    """無成交瞬間的整批快照，不得產生 −100% 這種不可能的漲跌幅。"""
    payload = {"msgArray": [
        {"c": "2059", "n": "川湖", "z": "-", "y": "7140.0000",
         "b": "0.0000_7850.0000_7845.0000", "v": "149", "t": "09:40:52"},
        {"c": "2454", "n": "聯發科", "z": "-", "y": "3235.0000",
         "b": "0.0000_3555.0000_3550.0000", "v": "2230", "t": "09:42:43"},
    ]}
    out = mis.parse_mis_rank(payload)
    assert out["2059"]["price"] == 7850.0
    assert out["2059"]["chg"] == 710.0 and out["2059"]["chg_pct"] == 9.94
    assert out["2454"]["chg_pct"] == 9.89
    for rec in out.values():
        assert rec["chg_pct"] is None or rec["chg_pct"] > -100
