from stocks_power_rich import line_push

_ROW = {
    "date": "2026-07-01", "taiex": 47018.99, "taiex_chg": 893.08,
    "turnover": 10780.3, "tx_price": 47100.0, "tx_chg": -35.0,
    "inst_foreign": 323.76, "inst_trust": 156.03, "inst_dealer": 59.38,
    "tx_foreign_oi": -84168, "retail_ls_mtx": 0.0769, "retail_ls_tmf": -0.123,
    "margin_balance": 9414925, "margin_chg": -20530,
    "margin_value": 6074.6, "margin_value_chg": 135.3, "margin_maintenance": 165.2,
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
    assert "融資維持率 165.2%" in txt         # prev 空 → 無(昨…)
    assert "【AI 解讀】" not in txt and "【自選股】" not in txt   # 無資料的段落整段省略
    # 無昨值（prev 空）→ 法人行不出現 (昨…)
    assert "(昨" not in txt
    empty = line_push.compose_daily_brief({"date": "2026-07-01"}, [], [], full=True)
    assert "—" in empty and "日經" not in empty and "融資" not in empty


def test_broadcast_without_token_degrades():
    r = line_push.broadcast_text("", "hi")
    assert r["ok"] is False and "LINE" in r["error"]


def test_compose_cup_section_breakout_and_new():
    cup = {"count": 82,
           "breakout": [{"code": "8069", "name": "元太", "close": 45.2, "resistance": 44.8}],
           "new": [{"code": "2812", "name": "台中銀"}, {"code": "1227", "name": "佳格"}]}
    txt = line_push.compose_daily_brief(_ROW, [], [], full=False, cup=cup)
    assert "【杯柄型態】符合 82 檔" in txt
    assert "🚀 突破 元太 45.20(壓44.80)" in txt
    assert "🆕 新符合 台中銀、佳格" in txt
    # 沒有新訊號也沒突破 → 整段省略
    quiet = line_push.compose_daily_brief(_ROW, [], [], full=False,
                                          cup={"count": 82, "breakout": [], "new": []})
    assert "杯柄" not in quiet
    # picks=True（清單已過「籌碼/基本選股」交集）→ 標題明示交集
    inter = line_push.compose_daily_brief(_ROW, [], [], full=False, cup={**cup, "picks": True})
    assert "【杯柄型態&籌碼/基本】符合 82 檔" in inter and "【杯柄型態】" not in inter


def test_compose_breakout_alert():
    hits = [{"code": "8069", "name": "元太", "price": 213.5, "resistance": 212.0},
            {"code": "2812", "name": "台中銀", "price": 19.85, "resistance": 19.8, "pick": True}]
    txt = line_push.compose_breakout_alert(hits, "10:35")
    assert txt.startswith("🚀 盤中突破壓力 10:35")
    assert "元太 213.50(壓212.00)" in txt
    assert "⭐台中銀 19.85(壓19.80)" in txt              # 交集股標⭐
    assert txt.index("台中銀") < txt.index("元太")        # ⭐排前面
    assert "⭐=同時符合籌碼/基本選股" in txt
    assert "確認量價後再行動" in txt
    # 無交集股 → 不出現圖例
    plain = line_push.compose_breakout_alert([hits[0]], "10:35")
    assert "⭐" not in plain


def test_compose_weekly_brief_sections_and_order():
    comparison = {
        "this_date": "2026-07-17", "last_date": "2026-07-10",
        "stocks": [
            {"code": "1316.TW", "name": "上曜", "status": "加速", "big_holder_ratio": 3.95},
            {"code": "2313.TW", "name": "華通", "status": "加速", "big_holder_ratio": 1.96},
            {"code": "1709.TW", "name": "和益", "status": "加速", "big_holder_ratio": 2.77},
            {"code": "9999.TW", "name": "新股", "status": "新進榜", "big_holder_ratio": 0.5},
            {"code": "8888.TW", "name": "走了", "status": "退榜", "big_holder_ratio": None},
            {"code": "7777.TW", "name": "平平", "status": "持平", "big_holder_ratio": 0.1},
        ],
    }
    txt = line_push.compose_weekly_brief(comparison, ai_text="• 本週大戶進駐半導體")
    assert "籌碼週報" in txt and "2026-07-10 → 2026-07-17" in txt
    # 加速榜依大戶增比 desc：上曜(3.95) > 和益(2.77) > 華通(1.96)
    i_sy, i_hy, i_ht = txt.index("上曜"), txt.index("和益"), txt.index("華通")
    assert i_sy < i_hy < i_ht
    assert "3.95" in txt
    assert "新進榜" in txt and "新股" in txt
    assert "退榜 1 檔" in txt
    assert "本週大戶進駐半導體" in txt
    assert "平平" not in txt   # 持平不進週報


def test_compose_weekly_brief_degrades_when_empty():
    txt = line_push.compose_weekly_brief({"this_date": "2026-07-17", "last_date": None, "stocks": []},
                                         ai_text="")
    assert "籌碼週報" in txt
    assert "本週無" in txt or "尚無" in txt


# ===== Webhook（使用者傳訊息 → 回覆；回覆訊息不計入 LINE 免費額度）=====

def test_verify_signature_hmac_sha256_base64():
    """X-Line-Signature＝base64(HMAC-SHA256(channel_secret, raw_body))；差一個位元組就不放行。"""
    import base64
    import hashlib
    import hmac

    secret, body = "s3cr3t", b'{"events":[]}'
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    assert line_push.verify_signature(secret, body, sig) is True
    assert line_push.verify_signature(secret, body + b" ", sig) is False   # body 被竄改
    assert line_push.verify_signature("other", body, sig) is False         # 金鑰不符
    assert line_push.verify_signature(secret, body, "") is False
    assert line_push.verify_signature("", body, sig) is False              # 未設定 secret＝一律不放行


def test_parse_webhook_events_keeps_only_text_messages():
    """只取文字訊息事件；follow/貼圖等其他事件與 Console 驗證用的全零 replyToken 一律略過。"""
    payload = {"events": [
        {"type": "message", "replyToken": "rt1", "message": {"type": "text", "id": "1", "text": " 大盤 "}},
        {"type": "message", "replyToken": "rt2", "message": {"type": "sticker", "id": "2"}},
        {"type": "follow", "replyToken": "rt3"},
        {"type": "message", "replyToken": "0" * 32, "message": {"type": "text", "text": "hi"}},
    ]}
    assert line_push.parse_webhook_events(payload) == [{"reply_token": "rt1", "text": "大盤"}]
    assert line_push.parse_webhook_events({}) == []


def test_route_command_maps_synonyms_and_unknown():
    """關鍵字→指令；大小寫/前後空白不影響；認不得的字回 None（呼叫端回說明）。"""
    for t in ("大盤", "簡報", "速報"):
        assert line_push.route_command(t) == "brief"
    for t in ("完整", "總結"):
        assert line_push.route_command(t) == "full"
    assert line_push.route_command("週報") == "weekly"
    assert line_push.route_command("高價股") == "rank"
    assert line_push.route_command("Help") == "help"
    assert line_push.route_command("今天要買什麼") is None


def test_compose_rank_brief_two_line_compact_layout():
    """高價股每檔壓成一行：排名 名稱 價 漲跌% 量 額增減。

    一行放得下的前提是砍掉冗餘——價格取整數（高價股 tick ≥1 元，且「15,510.00」會被 LINE
    誤判成電話號碼自動加藍色連結）、漲跌%取整數、成交額只留增減（絕對值已由量價可推）。
    盤中估算值以 * 標記＋末尾註腳，不用 (估)——那 4 格會把最長的一行撐到折行。
    """
    d = {"prev_date": "2026-07-21", "items": [
        {"code": "5274", "name": "信驊", "price": 15510.0, "chg_pct": 10.0,
         "vol": 319, "amount": 4_950_000_000.0, "amount_est": True,
         "amount_chg": None, "amount_chg_pct": None},
        {"code": "2059", "name": "川湖", "price": 8125.0, "chg_pct": 1.88,
         "vol": 381, "amount": 3_400_000_000.0, "amount_est": False,
         "amount_chg": -4_710_000_000.0, "amount_chg_pct": -58.1},
        {"code": "6669", "name": "緯穎", "price": 5500.0, "chg_pct": 10.0,
         "vol": 3320, "amount": 20_980_000_000.0, "amount_est": False,
         "amount_chg": 15_390_000_000.0, "amount_chg_pct": 275.0},
    ]}
    txt = line_push.compose_rank_brief(d)
    lines = txt.split("\n")
    assert "2026-07-21" in lines[1]
    assert "15,510" in txt and "15,510.00" not in txt        # 整數價，不觸發電話號碼連結
    # 名稱補到 3 字寬、排名右靠補到 2 位 → 價格欄大致落在同一條垂直線上
    assert lines[3] == " 1 信驊　　15,510　+10%　319張　*"     # 估算且無前一日基準：只留 * 記號
    assert lines[4] == " 2 川湖　　8,125　+2%　381張　▼47.1億"  # 1.88 四捨五入成 +2%
    assert lines[5] == " 3 緯穎　　5,500　+10%　3,320張　▲153.9億"
    assert lines[6] == "* 盤中估算（官方成交金額收盤後才發布）"
    # 每行的顯示寬度都要壓在 LINE 訊息框內（約 22 個全形字＝44 半形單位），否則就折行了
    def width(s):
        return sum(2 if ord(ch) > 0x2000 else 1 for ch in s)
    assert max(width(l) for l in lines) <= 44


def test_compose_rank_brief_no_footnote_when_nothing_estimated():
    d = {"items": [{"name": "川湖", "price": 8125.0, "chg_pct": 1.88, "vol": 381,
                    "amount": 3.4e9, "amount_est": False, "amount_chg": -4.71e9,
                    "amount_chg_pct": -58.1}]}
    assert "*" not in line_push.compose_rank_brief(d)


def test_compose_rank_brief_empty_and_missing_fields():
    assert "尚無" in line_push.compose_rank_brief({"items": []})
    txt = line_push.compose_rank_brief({"items": [
        {"name": "某股", "price": 1200.0, "chg_pct": None,
         "vol": None, "amount": None, "amount_chg": None, "amount_chg_pct": None}]})
    assert txt.split("\n")[-1] == " 1 某股　　1,200"   # 缺值欄位整段省略，不印「—」佔位
