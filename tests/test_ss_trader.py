from datetime import date

from stocks_power_rich import ss_trader


def _row(**kw):
    base = {"date": "2026-07-01", "taiex": 20000.0, "turnover": 4000.0}
    base.update(kw)
    return base


def _find(checklist, key):
    return next(i for i in checklist if i["key"] == key)


def test_margin_maintenance_low_is_bull():
    rows = [_row(margin_maintenance=133.0)]
    it = _find(ss_trader.market_checklist(rows), "margin_maint")
    assert it["status"] == "bull" and "133" in str(it["value"])


def test_margin_maintenance_missing_is_na():
    it = _find(ss_trader.market_checklist([_row()]), "margin_maint")
    assert it["status"] == "na"


def test_margin_wash_bottom_signal():
    # 近5日融資 -6%、大盤 -2% → 融資跌幅大於大盤 = 底部訊號
    rows = [_row(date=f"2026-07-0{i}", taiex=t, margin_balance=m)
            for i, (t, m) in enumerate([(20000, 9.0e6), (19900, 8.8e6), (19800, 8.7e6),
                                        (19700, 8.6e6), (19600, 8.46e6)], start=1)]
    it = _find(ss_trader.market_checklist(rows), "margin_wash")
    assert it["status"] == "bull"


def test_vix_extreme_fear_is_contrarian_bull():
    it = _find(ss_trader.market_checklist([_row(vix=33.0)]), "vix")
    assert it["status"] == "bull"
    it2 = _find(ss_trader.market_checklist([_row(vix=13.0)]), "vix")
    assert it2["status"] == "warn"


def test_twd_appreciation_is_bull():
    # twd = USD/TWD，twd_chg 為 %；台幣升值（USD/TWD 下跌）→ 熱錢流入
    it = _find(ss_trader.market_checklist([_row(twd=31.2, twd_chg=-0.4)]), "twd")
    assert it["status"] == "bull"
    it2 = _find(ss_trader.market_checklist([_row(twd=32.5, twd_chg=0.8)]), "twd")
    assert it2["status"] == "bear"


def test_volume_burst_at_high_is_bear_at_low_is_bull():
    # 60日區間 19000~21000；今量 = 前5日均量的2倍
    lows = [_row(date=f"2026-05-{i:02d}", taiex=19000 + i * 30, turnover=3000.0) for i in range(1, 29)]
    high_rows = lows + [_row(date="2026-06-30", taiex=20990.0, turnover=6000.0)]
    it = _find(ss_trader.market_checklist(high_rows), "volume")
    assert it["status"] == "bear"  # 高檔爆量要跑
    low_rows = lows + [_row(date="2026-06-30", taiex=19010.0, turnover=6000.0)]
    it2 = _find(ss_trader.market_checklist(low_rows), "volume")
    assert it2["status"] == "bull"  # 低檔爆量：加速趕底/明確方向


def test_fund_flow_nq_over_dj_is_tech_bull():
    osfut = {"categories": [{"category": "指數期貨", "items": [
        {"name": "小道瓊", "chg_pct": -0.5},
        {"name": "小那斯達克", "chg_pct": 0.8},
        {"name": "小費半", "chg_pct": 1.2},
    ]}]}
    it = _find(ss_trader.market_checklist([_row()], osfut=osfut), "fund_flow")
    assert it["status"] == "bull" and "科技" in it["note"]


def test_settlement_week_third_wednesday():
    assert ss_trader.is_settlement_week(date(2026, 7, 15)) is True   # 2026-07-15 = 第三個週三
    assert ss_trader.is_settlement_week(date(2026, 7, 13)) is True   # 同一週的週一
    assert ss_trader.is_settlement_week(date(2026, 7, 6)) is False
    assert ss_trader.is_settlement_week(date(2026, 7, 22)) is False


def test_red_engulfs_three_black():
    # 前三黑（close<open），今紅且收盤 >= 三黑最高開盤
    opens = [100, 98, 96, 91]
    closes = [97, 95, 92, 101]
    assert ss_trader.red_engulfs_three_black(opens, closes) is True
    # 今紅但只吃掉兩根 → False
    closes2 = [97, 95, 92, 99]
    assert ss_trader.red_engulfs_three_black(opens, closes2) is False
    # 前三根不是全黑 → False
    opens3 = [100, 94, 96, 91]
    closes3 = [97, 95, 92, 101]
    assert ss_trader.red_engulfs_three_black(opens3, closes3) is False


def test_qoq_rising_picks_filters_and_sorts():
    rows = [
        {"code": "1111", "name": "全符合高蘭", "month_inc": 5, "rev_yoy": 10, "accum_inc": 3,
         "big_holder_ratio": 0.5, "lan_value": 80, "close": 100},
        {"code": "2222", "name": "全符合低蘭", "month_inc": 1, "rev_yoy": 2, "accum_inc": 1,
         "big_holder_ratio": 0.1, "lan_value": 20, "close": 50},
        {"code": "3333", "name": "年增為負", "month_inc": 5, "rev_yoy": -1, "accum_inc": 3,
         "big_holder_ratio": 0.5, "lan_value": 90, "close": 10},
        {"code": "4444", "name": "大戶減", "month_inc": 5, "rev_yoy": 10, "accum_inc": 3,
         "big_holder_ratio": -0.2, "lan_value": 95, "close": 10},
    ]
    out = ss_trader.qoq_rising_picks(rows)
    assert [r["code"] for r in out] == ["1111", "2222"]  # 過濾負值、依蘭值排序


def test_margin_verdict_reads_each_market_against_its_own_breakeven():
    """同一個數字在兩個市場意義相反——這正是舊的固定門檻(135/165)漏掉的東西。

    上市融資成數 60% → 兩平線 166.7%，180.1% 是帳面獲利；
    上櫃成數 50% → 兩平線 200%，166.8% 反而是套牢一成六。
    舊門檻會把兩者都判成「偏熱」(>165)，等於把反向訊號抹平。
    """
    _, tse_rel, tse_note = ss_trader.margin_verdict(180.1, ss_trader.MARGIN_RATIO_TSE)
    _, otc_rel, otc_note = ss_trader.margin_verdict(166.8, ss_trader.MARGIN_RATIO_OTC)
    assert ss_trader.margin_breakeven(ss_trader.MARGIN_RATIO_TSE) == 166.7
    assert ss_trader.margin_breakeven(ss_trader.MARGIN_RATIO_OTC) == 200.0
    # 原始數字 180.1 > 166.8，相對各自兩平線卻是一個獲利、一個套牢——正負號相反才是重點
    assert round(tse_rel) == 8 and round(otc_rel) == -17
    assert "獲利" in tse_note and "套牢" in otc_note


def test_margin_verdict_treats_low_maintenance_as_contrarian_bull():
    """方向與 VIX 一致——低維持率是斷頭清洗，Ss 視為抄底而非利空。

    這條釘住的是「不要把它寫成 bear」：逼近追繳線在直覺上像利空，但整套方法論
    （見 SKILL.md「散戶都怕的時候，就是很好的底部」）把它當反指標。
    """
    assert ss_trader.margin_verdict(135.0, ss_trader.MARGIN_RATIO_TSE)[0] == "bull"
    assert ss_trader.margin_verdict(138.0, ss_trader.MARGIN_RATIO_OTC)[0] == "bull"
    # 深度套牢(相對兩平 <= -20%)也算抄底區，即使離追繳線還遠
    assert ss_trader.margin_verdict(150.0, ss_trader.MARGIN_RATIO_OTC)[0] == "bull"
    # 相對兩平獲利 >= 20% ＝ 水位偏熱
    assert ss_trader.margin_verdict(205.0, ss_trader.MARGIN_RATIO_TSE)[0] == "warn"


def test_market_checklist_lists_both_margin_markets():
    rows = [{"margin_maintenance": 180.1, "otc_margin_maintenance": 166.8}]
    items = {i["key"]: i for i in ss_trader.market_checklist(rows)}
    assert items["margin_maint"]["value"] == 180.1
    assert items["margin_maint_otc"]["value"] == 166.8
    assert "上市" in items["margin_maint"]["name"] and "上櫃" in items["margin_maint_otc"]["name"]


def _item(status, key="k"):
    return {"key": key, "name": key, "status": status, "value": None, "note": ""}


def test_market_pulse_insufficient_sample_returns_no_score_not_zero():
    """採計項數低於 PULSE_MIN_ITEMS 時，總分／健康／正面因素／風險溫度一律回 None，
    不可硬湊出一個看似精準的 0 分——那會被誤讀成「市場很差」而不是「資料不夠」。"""
    checklist = [_item("bull"), _item("na"), _item("na")]
    pulse = ss_trader.market_pulse(checklist)
    assert pulse["overall"] is None
    assert pulse["health"] is None
    assert pulse["positive"] is None
    assert pulse["risk"] is None
    assert pulse["sample_n"] == 1
    assert pulse["sample_total"] == 3
    # 資料完整度不受樣本門檻限制——它的定義就是「有幾項能算」，樣本再少也算得出來，
    # 而且正是這種時候最需要被看見。
    assert pulse["completeness"] == round(1 / 3 * 100, 1)


def test_market_pulse_na_items_never_enter_any_denominator():
    """na 項不可拉低任何分項的分數——它代表「不知道」，不是「利空」。"""
    all_bull = [_item("bull") for _ in range(4)]
    with_na = all_bull + [_item("na") for _ in range(4)]
    p1 = ss_trader.market_pulse(all_bull)
    p2 = ss_trader.market_pulse(with_na)
    assert p1["health"] == p2["health"] == 100.0
    assert p1["positive"] == p2["positive"] == 100.0
    assert p1["risk"] == p2["risk"] == 0.0
    assert p1["overall"] == p2["overall"]
    # 但完整度要反映出差異——這是「na 不進分子分母」與「完整度看得到缺口」不矛盾的地方
    assert p2["completeness"] < p1["completeness"]


def test_market_pulse_components_measure_different_things():
    """市場健康（加權分）與正面因素（純 bull 占比）刻意可以不同：
    全部 warn 時健康是 35 分（warn 的權重），但正面因素是 0（沒有任何 bull）。"""
    checklist = [_item("warn") for _ in range(4)]
    pulse = ss_trader.market_pulse(checklist)
    assert pulse["health"] == 35.0
    assert pulse["positive"] == 0.0
    assert pulse["risk"] == 100.0       # 風險溫度＝warn 占比，全 warn 就是 100


def test_market_pulse_overall_formula_and_bands():
    """總分＝0.5×健康 + 0.3×正面因素 + 0.2×(100−風險)。混合訊號時三個分項互相拉扯，
    用具體數字鎖住公式，不是只驗證「有算出東西」。"""
    checklist = [_item("bull"), _item("bull"), _item("warn"), _item("neutral")]
    pulse = ss_trader.market_pulse(checklist)
    # health = (100+100+35+50)/4 = 71.25; positive = 2/4*100 = 50; risk = 1/4*100 = 25
    assert pulse["health"] == 71.2 or pulse["health"] == 71.3   # 四捨五入邊界，容忍一格
    assert pulse["positive"] == 50.0
    assert pulse["risk"] == 25.0
    expected = round(0.5 * pulse["health"] + 0.3 * 50.0 + 0.2 * 75.0, 1)
    assert pulse["overall"] == expected
    assert pulse["label"]["overall"] in ("偏弱", "中性", "偏強")
    assert pulse["label"]["risk"] == "中等"          # 25 落在 (20, 40) 之間
