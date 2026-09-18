from stocks_power_rich.sources import taifex_ssf as ssf

# 真實資料切片（2026-09-17）。刻意包含：一般列、盤後列、價差列、調整後合約 CM1、
# 未成交列（OHLC 皆 '-' 但有結算價）。
SAMPLE = """交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量
2026/09/17,CDF,202610  ,2425,2453,2410,2433,32,1.33%,6027,2432,25045,2432,2433,2520,2360,,一般,,
2026/09/17,CDF,202611  ,2420,2450,2405,2429,30,1.25%,410,2428,3120,2428,2430,2520,2360,,一般,,
2026/09/17,CDF,202610  ,2405,2423,2405,2422,21,0.87%,1755,-,-,2421,2422,2520,2360,,盤後,,
2026/09/17,CDF,202610/202611    ,9.7,10.01,9.7,9.97,-,-,816,-,-,9.92,9.93,10.01,9.7,,一般,88,
2026/09/17,CMF,202610  ,44.7,44.95,44,44.35,-0.35,-0.78%,1093,44.35,2094,44.3,44.45,45,35.95,,一般,,
2026/09/17,CM1,202612  ,-,-,-,-,-,-,0,44.35,96,-,-,39,23.6,,一般,,
2026/09/17,VQF,202611  ,-,-,-,-,-,-,0,64.7,0,64.3,65.3,-,-,,一般,,
"""

SSF_HEADER_LINE = ssf.SSF_HEADER


def test_parse_reads_every_row_shape():
    rows = ssf.parse_ssf_daily_csv(SAMPLE)
    assert len(rows) == 7
    first = rows[0]
    assert first["date"] == "2026-09-17"          # 轉成站內慣用的 ISO 格式
    assert first["contract"] == "CDF"
    assert first["month"] == "202610"             # 尾隨空白要去掉
    assert first["session"] == "一般"
    assert (first["open"], first["close"]) == (2425.0, 2433.0)
    assert first["chg"] == 32.0 and first["chg_pct"] == 1.33   # 去掉 % 並轉 float
    assert first["volume"] == 6027 and first["oi"] == 25045
    assert first["settlement"] == 2432.0
    assert first["is_spread"] is False


def test_parse_marks_spread_rows_and_blanks_their_change():
    spread = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["is_spread"]]
    assert len(spread) == 1
    assert spread[0]["month"] == "202610/202611"
    assert spread[0]["chg"] is None and spread[0]["chg_pct"] is None


def test_parse_keeps_night_rows_but_their_settlement_and_oi_are_none():
    night = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["session"] == "盤後"]
    assert len(night) == 1
    assert night[0]["volume"] == 1755
    assert night[0]["settlement"] is None and night[0]["oi"] is None


def test_parse_untraded_row_has_no_prices_but_keeps_settlement():
    """沒成交不等於沒價格——保證金全表就靠這個結算價，1,014/1,629 列是這種。"""
    vq = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["contract"] == "VQF"][0]
    assert vq["close"] is None and vq["chg_pct"] is None
    assert vq["volume"] == 0            # 0 是有效觀測，不是缺值
    assert vq["settlement"] == 64.7


def test_parse_rejects_a_changed_header():
    """表頭改版要大聲壞掉。靜默略過會讓排程每天寫進 0 列而沒有人發現。"""
    import pytest
    bad = SAMPLE.replace("未沖銷契約數", "未平倉量")
    with pytest.raises(ValueError, match="表頭"):
        ssf.parse_ssf_daily_csv(bad)


def test_summary_picks_the_most_traded_month_of_the_F_contract():
    rows = ssf.parse_ssf_daily_csv(SAMPLE)
    by_root = {r["root"]: r for r in ssf.summarize_ssf_day(rows)}
    cd = by_root["CD"]
    assert cd["main_month"] == "202610"       # 6027 > 410
    assert cd["close"] == 2433.0 and cd["chg_pct"] == 1.33
    assert cd["settlement"] == 2432.0 and cd["oi"] == 25045
    assert cd["main_volume"] == 6027
    # 官方口數：一般 6027+410 ＋ 盤後 1755，價差列 816 不算
    assert cd["volume"] == 6027 + 410 + 1755


def test_summary_counts_adjusted_contract_volume_but_never_its_price():
    """CM1 的契約乘數是非標準的；拿它的價格套標準乘數，保證金會全錯且看不出來。"""
    rows = ssf.parse_ssf_daily_csv(SAMPLE) + ssf.parse_ssf_daily_csv(
        SSF_HEADER_LINE + "\n2026/09/17,CM1,202610  ,9,9,9,9,0,0.00%,99999,9,5,9,9,9,9,,一般,,\n")
    cm = {r["root"]: r for r in ssf.summarize_ssf_day(rows)}["CM"]
    assert cm["close"] == 44.35          # 來自 CMF，不是成交量最大的 CM1
    assert cm["main_month"] == "202610"
    assert cm["volume"] == 1093 + 0 + 99999   # 量要含 CM1


def test_summary_skips_an_expiring_leg_whose_settlement_is_zero():
    """結算日當天到期腳仍在交易且可能量最大，但結算價是 0、價差也收斂到 0。"""
    text = SSF_HEADER_LINE + "\n" + "\n".join([
        "2026/09/17,CDF,202609  ,100,100,100,100,1,1.00%,9999,0,10,100,100,100,100,,一般,,",
        "2026/09/17,CDF,202610  ,200,200,200,200,2,1.00%,50,200,20,200,200,200,200,,一般,,",
    ]) + "\n"
    cd = ssf.summarize_ssf_day(ssf.parse_ssf_daily_csv(text))[0]
    assert cd["main_month"] == "202610"
    assert cd["close"] == 200.0
    assert cd["volume"] == 9999 + 50     # 量仍含到期腳（官方口徑）


def test_summary_falls_back_to_the_nearest_month_when_nothing_traded():
    vq = {r["root"]: r for r in ssf.summarize_ssf_day(ssf.parse_ssf_daily_csv(SAMPLE))}["VQ"]
    assert vq["main_month"] == "202611"
    assert vq["close"] is None           # 沒成交就沒有收盤價
    assert vq["settlement"] == 64.7      # 但結算價還在，保證金算得出來
