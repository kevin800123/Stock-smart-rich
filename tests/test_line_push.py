from stocks_power_rich import line_push

_ROW = {
    "date": "2026-07-01", "taiex": 47018.99, "taiex_chg": 893.08,
    "turnover": 10780.3, "tx_price": 47100.0, "tx_chg": -35.0,
    "inst_foreign": 323.76, "inst_trust": 156.03, "inst_dealer": 59.38,
    "tx_foreign_oi": -84168, "retail_ls_mtx": 0.0769, "retail_ls_tmf": -0.123,
    "margin_balance": 9414925, "margin_chg": -20530,
    "margin_value": 6074.6, "margin_value_chg": 135.3,
    "short_balance": 202194, "short_chg": -2061,
    "n225": 40123.0, "n225_chg": 1.2, "kospi": 2650.0, "kospi_chg": -0.3,
    "gold": 3340.0, "gold_chg": 0.5, "jpy": 151.25, "jpy_chg": -0.07,
    "btc": 98500.0, "btc_chg": -2.1,
}
_PREV = {
    "date": "2026-06-30", "turnover": 12860.0,
    "inst_foreign": -1431.89, "inst_trust": 55.21, "inst_dealer": -707.34,
    "tx_foreign_oi": -83000, "retail_ls_mtx": 0.0811, "retail_ls_tmf": -0.101,
}
_SECTORS = [
    {"name": "塑膠", "chg_pct": 6.45}, {"name": "半導體", "chg_pct": 3.21},
    {"name": "數位雲端", "chg_pct": -4.34}, {"name": "玻璃陶瓷", "chg_pct": -4.5},
]
_WATCH = [{"code": "1216.TW", "name": "統一", "close": 75.7, "chg_pct": 0.5, "in_latest": True},
          {"code": "6894.TW", "name": "衛司特", "close": 357.0, "chg_pct": -1.24, "in_latest": False}]
_TSMC = {"close": 2505.0, "chg_pct": 3.94}


def test_compose_brief_aligned_format():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, _WATCH, ai_text="• 大盤：偏多",
                                        full=False, tsmc=_TSMC, prev=_PREV)
    # 大盤區：加權＋台指期＋台積電
    assert "【大盤】" in txt
    assert "加權指數 47,018.99" in txt
    assert "漲跌幅　 ▲893.08（+1.94%）" in txt
    assert "成交金額 10,780億(昨12,860億)" in txt
    assert "台指期　 47,100.00 ▼35.00（-0.07%）" in txt
    assert "台積電　 2,505.00（+3.94%）" in txt
    # 國際：日圓＝美元兌日圓
    assert "【國際行情】" in txt
    assert "日經　 40,123　+1.20%" in txt
    assert "日圓　 151.25　-0.07%" in txt
    assert "比特幣 98,500　-2.10%" in txt
    # 法人：標題標明買賣超金額、附昨值且同一行（不換行）
    assert "【三大法人】買賣超金額(億)" in txt
    assert "外資　+323.8(昨-1,431.9)" in txt
    assert "投信　+156.0(昨+55.2)" in txt
    # 期貨：附昨值同一行；多空比百分比
    assert "外資台指OI　-84,168口(昨-83,000)" in txt
    assert "小台多空比　+7.69%(昨+8.11%)" in txt
    assert "微台多空比　-12.30%(昨-10.10%)" in txt
    # 類股每行一項；自選股附股價與漲跌幅
    assert "🔥 塑膠　　　 +6.45%" in txt
    assert "❄ 玻璃陶瓷　 -4.50%" in txt
    assert "⭐ 統一　　 75.70　+0.50% ●在榜" in txt
    assert "⭐ 衛司特　 357.00　-1.24%" in txt
    assert "【AI 解讀】" in txt and "• 大盤：偏多" in txt
    assert "融資" not in txt                       # 16:00 速報無融資券
    assert line_push.SEP in txt                    # 分區線


def test_compose_full_margin_three_lines_and_handles_missing():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, [], ai_text="", full=True)
    assert "【融資券】" in txt
    assert "融資 9,414,925張(-20,530)" in txt
    assert "融資金額 6,074.6億(+135.3)" in txt
    assert "融券 202,194張(-2,061)" in txt
    assert "【AI 解讀】" not in txt and "【自選股】" not in txt   # 無資料的段落整段省略
    # 無昨值（prev 空）→ 法人行不出現 (昨…)
    assert "(昨" not in txt
    empty = line_push.compose_daily_brief({"date": "2026-07-01"}, [], [], full=True)
    assert "—" in empty and "日經" not in empty and "融資" not in empty


def test_broadcast_without_token_degrades():
    r = line_push.broadcast_text("", "hi")
    assert r["ok"] is False and "LINE" in r["error"]
