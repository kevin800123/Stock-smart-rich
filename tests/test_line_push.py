from stocks_power_rich import line_push

_ROW = {
    "date": "2026-07-01", "taiex": 47018.99, "taiex_chg": 893.08,
    "inst_foreign": 323.76, "inst_trust": 156.03, "inst_dealer": 59.38,
    "tx_foreign_oi": -84168, "retail_ls_mtx": 0.0769,
    "margin_balance": 9414925, "margin_chg": -20530,
}
_SECTORS = [
    {"name": "塑膠", "chg_pct": 6.45}, {"name": "半導體", "chg_pct": 3.21},
    {"name": "數位雲端", "chg_pct": -4.34}, {"name": "玻璃陶瓷", "chg_pct": -4.5},
]
_WATCH = [{"code": "1216.TW", "name": "統一", "chg_pct": 0.5, "in_latest": True},
          {"code": "6894.TW", "name": "衛司特", "chg_pct": None, "in_latest": False}]


def test_compose_brief_light_has_core_no_margin():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, _WATCH, ai_text="• 大盤：偏多", full=False)
    assert "47,018.99" in txt and "▲893.08" in txt and "+1.94%" in txt   # 指數與回推漲跌%
    assert "外資 +323.8" in txt and "投信 +156.0" in txt
    assert "-84,168" in txt and "0.0769" in txt
    assert "🔥 塑膠+6.45%" in txt and "❄ 玻璃陶瓷-4.50%" in txt          # 領跌依跌幅排序
    assert "統一 +0.50% ●在榜" in txt and "衛司特" in txt
    assert "AI 解讀" in txt and "• 大盤：偏多" in txt
    assert "融資" not in txt                                             # 16:00 速報無融資券


def test_compose_full_has_margin_and_handles_missing():
    txt = line_push.compose_daily_brief(_ROW, _SECTORS, [], ai_text="", full=True)
    assert "融資餘額 9,414,925" in txt and "-20,530" in txt
    assert "AI 解讀" not in txt and "自選股" not in txt                   # 無資料的段落不輸出
    empty = line_push.compose_daily_brief({"date": "2026-07-01"}, [], [], full=True)
    assert "—" in empty                                                   # 缺值顯示 — 不噴錯


def test_broadcast_without_token_degrades():
    r = line_push.broadcast_text("", "hi")
    assert r["ok"] is False and "LINE" in r["error"]
