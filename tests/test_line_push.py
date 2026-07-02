from stocks_power_rich import line_push

_ROW = {
    "date": "2026-07-01", "taiex": 47018.99, "taiex_chg": 893.08,
    "inst_foreign": 323.76, "inst_trust": 156.03, "inst_dealer": 59.38,
    "tx_foreign_oi": -84168, "retail_ls_mtx": 0.0769,
    "margin_balance": 9414925, "margin_chg": -20530,
    "n225": 40123.0, "n225_chg": 1.2, "kospi": 2650.0, "kospi_chg": -0.3,
    "gold": 2700.0, "gold_chg": 0.5, "jpy": 0.208, "jpy_chg": 0.1,
    "btc": 98500.0, "btc_chg": -2.1,
}
_SECTORS = [
    {"name": "塑膠", "chg_pct": 6.45}, {"name": "半導體", "chg_pct": 3.21},
    {"name": "數位雲端", "chg_pct": -4.34}, {"name": "玻璃陶瓷", "chg_pct": -4.5},
]
_WATCH = [{"code": "1216.TW", "name": "統一", "close": 75.7, "chg_pct": 0.5, "in_latest": True},
          {"code": "6894.TW", "name": "衛司特", "close": 357.0, "chg_pct": None, "in_latest": False}]
_TSMC = {"close": 2505.0, "chg_pct": 3.94}


def test_compose_brief_light_has_core_no_margin():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, _WATCH, ai_text="• 大盤：偏多",
                                        full=False, tsmc=_TSMC)
    assert "47,018.99" in txt and "▲893.08" in txt and "+1.94%" in txt   # 指數與回推漲跌%
    # 國際：日股/韓股/黃金/日圓/比特幣（緊接大盤之後）
    assert "日經 40,123 (+1.20%)" in txt and "韓股 2,650 (-0.30%)" in txt
    assert "黃金 2,700 (+0.50%)" in txt and "日圓 0.208 (+0.10%)" in txt
    assert "BTC 98,500 (-2.10%)" in txt
    assert txt.index("日經") < txt.index("三大法人")                      # 位置在大盤區、法人之前
    assert "外資 +323.8" in txt and "投信 +156.0" in txt
    assert "-84,168" in txt and "0.0769" in txt
    # 類股：台積電股價漲跌放第一
    assert "台積電 2,505.00 (+3.94%)" in txt
    assert txt.index("台積電") < txt.index("🔥")
    assert "🔥 塑膠+6.45%" in txt and "❄ 玻璃陶瓷-4.50%" in txt
    # 自選股：股名＋股價＋漲跌幅＋在榜
    assert "統一 75.70 +0.50% ●在榜" in txt
    assert "衛司特 357.00" in txt
    assert "AI 解讀" in txt and "• 大盤：偏多" in txt
    assert "融資" not in txt                                             # 16:00 速報無融資券


def test_compose_full_has_margin_and_handles_missing():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, [], ai_text="", full=True)
    assert "融資餘額 9,414,925" in txt and "-20,530" in txt
    assert "AI 解讀" not in txt and "自選股" not in txt                   # 無資料的段落不輸出
    empty = line_push.compose_daily_brief({"date": "2026-07-01"}, [], [], full=True)
    assert "—" in empty and "日經" not in empty                           # 缺值顯示 —；無國際數據整行省略


def test_broadcast_without_token_degrades():
    r = line_push.broadcast_text("", "hi")
    assert r["ok"] is False and "LINE" in r["error"]
