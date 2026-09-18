import pytest

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


@pytest.mark.parametrize("price,tick", [
    (9.99, 0.01), (10, 0.05), (49.95, 0.05), (50, 0.1), (99.9, 0.1),
    (100, 0.5), (499.5, 0.5), (500, 1), (2499, 1), (2500, 5), (3000, 5),
])
def test_stock_tick_table(price, tick):
    assert ssf.ssf_tick_size(price, is_etf=False) == tick


@pytest.mark.parametrize("price,tick", [(49.99, 0.01), (50, 0.05), (120, 0.05)])
def test_etf_tick_table(price, tick):
    assert ssf.ssf_tick_size(price, is_etf=True) == tick


def test_basis_walks_the_grid_across_a_band_boundary():
    """實測 KBF 聯茂：現貨 495、期貨 501。走網格 11 檔；
    除以單一 tick 會得 6（用期貨端 1 元）或 12（用現貨端 0.5 元），兩種都錯。"""
    assert ssf.ssf_basis_ticks(501, 495, is_etf=False) == 11


def test_basis_sign_and_simple_cases():
    assert ssf.ssf_basis_ticks(2431, 2430, is_etf=False) == 1      # 2500 以下，1 元一檔
    assert ssf.ssf_basis_ticks(2425, 2430, is_etf=False) == -5
    assert ssf.ssf_basis_ticks(108.35, 108.30, is_etf=True) == 1   # ETF 0.05 一檔


def test_basis_returns_none_when_either_side_is_missing():
    assert ssf.ssf_basis_ticks(None, 100, is_etf=False) is None
    assert ssf.ssf_basis_ticks(100, None, is_etf=False) is None


class _Resp:
    def __init__(self, body: bytes, ct: str):
        self.status_code, self.content, self.headers = 200, body, {"content-type": ct}


class _Client:
    """把 httpx.Client 的最小介面樁掉。`post` 回下一個排好的回應。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.posted = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        return _Resp(b"", "text/html")

    def post(self, url, **kw):
        self.posted.append(kw.get("data"))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    client = _Client(responses)
    monkeypatch.setattr(ssf.httpx, "Client", lambda *a, **kw: client)
    return client


def test_fetch_returns_rows_on_a_normal_response(monkeypatch):
    body = (SSF_HEADER_LINE + "\n" +
            "\n".join([f"2026/09/17,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,1,1,1,1,1,1,,一般,,"
                       for i in range(1300)]) + "\n")
    client = _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    rows = ssf.fetch_ssf_daily("2026/09/17", "2026/09/17")
    assert len(rows) == 1300
    assert client.posted[0]["commodity_id"] == "specialid"
    assert client.posted[0]["commodity_id2"] == "all"


def test_fetch_rejects_the_utf8_alert_page_for_an_over_long_range(monkeypatch):
    """區間超過一個月時伺服器回 HTTP 200 的 HTML 警告頁，照 MS950 解碼會變亂碼。"""
    _patch_client(monkeypatch, [_Resp(b"<!DOCTYPE HTML><html>too long</html>",
                                      "text/html;charset=UTF-8")])
    assert ssf.fetch_ssf_daily("2026/08/16", "2026/09/17") == []


def test_fetch_rejects_a_header_only_response(monkeypatch):
    """非交易日回 197 B 只有表頭。它會通過『200 且有 body』。"""
    _patch_client(monkeypatch, [_Resp((SSF_HEADER_LINE + "\n").encode("ms950"),
                                      "text/html;charset=MS950")])
    assert ssf.fetch_ssf_daily("2026/09/13", "2026/09/13") == []


def test_fetch_rejects_a_night_session_only_response(monkeypatch):
    """實測當天早上 10:02 打，回 51 列全是盤後、一般 0 列。

    少了這道守衛，17:15 的排程在資料還沒出來時會寫進一天只有夜盤的資料。
    """
    body = (SSF_HEADER_LINE + "\n" +
            "\n".join([f"2026/09/18,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,-,-,1,1,1,1,,盤後,,"
                       for i in range(51)]) + "\n")
    _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    assert ssf.fetch_ssf_daily("2026/09/18", "2026/09/18") == []


def test_fetch_drops_a_day_that_only_has_night_rows_from_a_multi_day_range(monkeypatch):
    """排程實際會打的是重疊補最近幾個交易日的多日區間，不是單日。

    13 個完整交易日 × 1,629 列 ＋ 今天只有盤後列（一般 0 列）：若用整個回應加總，
    21,177 列遠超門檻，守衛會誤判整批通過，今天那半天的資料就被寫進 DB。
    這裡簡化成兩天（一天齊全、一天只有盤後）驗證同一個原則：逐日判定，只剔除
    沒達標的那一天，齊全的那一天不受連累。
    """
    complete_day = "\n".join([
        f"2026/09/16,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,1,1,1,1,1,1,,一般,,"
        for i in range(1300)
    ])
    night_only_day = "\n".join([
        f"2026/09/17,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,-,-,1,1,1,1,,盤後,,"
        for i in range(51)
    ])
    body = SSF_HEADER_LINE + "\n" + complete_day + "\n" + night_only_day + "\n"
    _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    rows = ssf.fetch_ssf_daily("2026/09/12", "2026/09/17")
    assert len(rows) == 1300
    assert {r["date"] for r in rows} == {"2026-09-16"}


def test_fetch_rejects_wrong_content_type_even_with_a_perfectly_valid_body(monkeypatch):
    """孤立測試：body 本身是合法 CSV、一般列數也遠超門檻，只有 Content-Type 錯。

    只有守衛 1（Content-Type）擋得下這筆——body 若真的被拿去解析會完全通過守衛
    2（表頭正確）與守衛 3（列數足夠），所以拿掉守衛 1 時這筆必然會被放行。
    """
    body = (SSF_HEADER_LINE + "\n" +
            "\n".join([f"2026/09/17,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,1,1,1,1,1,1,,一般,,"
                       for i in range(1300)]) + "\n")
    _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=UTF-8")])
    assert ssf.fetch_ssf_daily("2026/09/17", "2026/09/17") == []


def test_fetch_rejects_a_genuinely_different_header_even_with_plenty_of_rows(monkeypatch):
    """孤立測試：Content-Type 正確、列數遠超門檻，但表頭真的被官方改版。

    只有守衛 2（接住 `parse_ssf_daily_csv` 的 ValueError）擋得下這筆——Content-Type
    正確會通過守衛 1，若解析不因表頭而中止、列數本身遠超門檻也會通過守衛 3。
    拿掉守衛 2 時，ValueError 會直接從 `fetch_ssf_daily` 炸出來，而不是被吞成 []。
    """
    bad_header = ssf.SSF_HEADER.replace("未沖銷契約數", "未平倉量")
    body = (bad_header + "\n" +
            "\n".join([f"2026/09/17,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,1,1,1,1,1,1,,一般,,"
                       for i in range(1300)]) + "\n")
    _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    assert ssf.fetch_ssf_daily("2026/09/17", "2026/09/17") == []


STOCK_LISTS_HTML = """
<table id="myTable"><tbody>
<tr><td>CD</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td>
<td><span class="sr-only">是</span>●</td><td></td><td></td>
<td>◎</td><td></td><td></td><td></td><td><span class="sr-only">股數</span>2,000</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>QF</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td>
<td>●</td><td></td><td></td><td>◎</td><td></td><td></td><td></td><td>100</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>NY</td><td>元大台灣卓越50證券投資信託基金</td><td>0050</td><td>元大台灣50</td>
<td>●</td><td></td><td></td><td></td><td></td><td>◎</td><td></td><td>10,000</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>SR</td><td>元大台灣卓越50證券投資信託基金</td><td>0050</td><td>元大台灣50</td>
<td>●</td><td></td><td></td><td></td><td></td><td>◎</td><td></td><td>1,000</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
</tbody></table>
"""


def test_contract_map_reads_code_name_and_multiplier():
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert m["CD"] == {"code": "2330", "stock_name": "台積電", "name": "台積電",
                       "multiplier": 2000, "is_etf": False, "is_mini": False,
                       "session_end": "13:45", "late_session": False}
    assert m["QF"]["multiplier"] == 100
    assert m["QF"]["is_mini"] is True
    assert m["QF"]["name"] == "小型台積電"      # 小型是不同產品，名稱要分得出來


def test_contract_map_flags_the_contracts_that_trade_past_the_spot_close():
    """14 檔 ETF 期貨交易到 16:15，收盤比現貨晚 2.5 小時；價差要另標，不能混在一起讀。"""
    html = STOCK_LISTS_HTML.replace(
        "<td>08:45~13:45</td><td>17:25~05:00</td></tr>\n<tr><td>SR</td>",
        "<td>08:45~16:15</td><td></td></tr>\n<tr><td>SR</td>")
    m = ssf.parse_stock_lists(html)
    assert m["NY"]["late_session"] is True and m["NY"]["session_end"] == "16:15"
    assert m["CD"]["late_session"] is False


def test_contract_map_flags_etf_underlyings():
    """ETF 期貨的 tick 級距與保證金規則都與股票標的不同，必須分得出來。"""
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert m["NY"]["is_etf"] is True and m["NY"]["multiplier"] == 10000
    assert m["SR"]["is_etf"] is True and m["SR"]["is_mini"] is True
    assert m["CD"]["is_etf"] is False


def test_contract_map_strips_sr_only_text_from_the_multiplier_cell():
    """乘數欄（[11]）混著給螢幕閱讀器用的『股數』字樣，不 strip 後果比誤判 is_etf 嚴重得多：

    欄[4] 那個『是』沒有被任何欄位讀取，藏在那裡的 sr-only 不 strip 也測不出任何差異
    （這正是舊版此測試的問題——它斷言的 is_etf 只看欄[9]/[10]，跟欄[4]完全無關，
    monkeypatch 掉 `_SR_ONLY` 後舊斷言照樣通過）。欄[11] 不一樣：`_i()` 解析失敗會
    直接回 None，`parse_stock_lists` 對 mult 為 None 的列整列 `continue`——不 strip
    的話這檔股期合約會從表裡完全消失，而不只是某個旗標判斷錯。
    """
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert "CD" in m                     # 沒 strip 的話 _i("股數2,000") 回 None，整列消失
    assert m["CD"]["multiplier"] == 2000


class _ListResp:
    """`fetch_ssf_contract_map` 用的最小回應樁：`raise_for_status` 可控制是否丟例外。"""
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            # 簡單地拋出異常，文案包含狀態碼，這樣測試的斷言能找到"500"字樣
            raise Exception(f"HTTP {self.status_code} error")


class _ListClient:
    """把 `fetch_ssf_contract_map` 用到的 `httpx.Client.get` 樁掉。"""
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        return self._resp


def test_fetch_contract_map_returns_empty_on_non_2xx_without_raising(monkeypatch, caplog):
    """TAIFEX 回錯誤頁時，httpx 預設不會自動丟例外——不加 raise_for_status 的話，
    regex 在錯誤頁裡找不到任何合格列、安靜地回傳 {}，`except` 那行 log 永遠不會跑，
    整個失敗完全無聲。加了 raise_for_status 後，非 2xx 要變成例外被既有的
    `except Exception` 接住並記錄下來，對外仍是回傳 {}、不往上炸。"""
    import logging
    # monkeypatch ssf 模塊本身的 httpx 物件，因為 ssf.py 中是 `import httpx` 而非 `from ... import`
    mock_httpx = type('MockHTTPX', (), {
        'Client': lambda *a, **kw: _ListClient(_ListResp(b"<html>error</html>", 500))
    })()
    monkeypatch.setattr(ssf, "httpx", mock_httpx)
    with caplog.at_level(logging.WARNING, logger="spr"):
        result = ssf.fetch_ssf_contract_map()
    assert result == {}
    assert any("抓取失敗" in r.message and "500" in r.message for r in caplog.records)


def test_fetch_contract_map_logs_a_warning_when_the_parsed_map_is_implausibly_small(
        monkeypatch, caplog):
    """200 但只解析到 4 檔（正常 320），代表頁面版型可能變了——這種「有回應但
    只懂一小撮」的狀況不能無聲無息，即使仍然把這個小 map 照常回傳。"""
    import logging
    monkeypatch.setattr(ssf.httpx, "Client",
                        lambda *a, **kw: _ListClient(
                            _ListResp(STOCK_LISTS_HTML.encode("utf-8"), 200)))
    with caplog.at_level(logging.WARNING, logger="spr"):
        m = ssf.fetch_ssf_contract_map()
    assert len(m) == 4                          # STOCK_LISTS_HTML 只有 4 檔，遠低於正常的 320
    assert "只解析到 4 檔" in caplog.text
