from stocks_power_rich.sources import taifex_ssf as ssf

STOCK_MARGIN_CSV = """一、股票期貨契約保證金一覽表
(一) 標的證券為股票之股票期貨契約
更新日期:2026/09/15
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,保證金所屬級距,結算保證金適用比例,維持保證金適用比例,原始保證金適用比例,
61,CDF    ,2330,台積電期貨                    ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
62,QFF    ,2330,小型台積電期貨                ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
33,KUF    ,1802,台玻期貨                      ,"台灣玻璃工業股份有限公司",,16.00%,16.56%,21.60%,
61,JNF    ,3673,TPK-KY期貨                    ,"TPK Holding Co., Ltd.",級距2,12.00%,12.42%,16.20%,
(二) 標的證券為受益憑證之股票期貨契約
更新日期:2026/08/12
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,結算保證金,維持保證金,原始保證金,
1,NYF    ,0050,元大台灣50ETF期貨             ,元大台灣卓越50證券投資信託基金,64000,67000,87000,
2,SRF    ,0050,小型元大台灣50ETF期貨         ,元大台灣卓越50證券投資信託基金,6400,6700,8700,
二、股票選擇權契約保證金一覽表
更新日期:2026/09/09
序號,代碼,名稱,
1,ZZZ,不該被讀進來,
"""

# 正式站的第二段標題其實是「受益憑證」（見上面 STOCK_MARGIN_CSV），程式也接受
# 「ETF」這個舊／其他文件可能出現的寫法。單獨留一份小 fixture 鎖住這條相容分支，
# 不讓它跟著主 fixture 改成真標題後一起失去測試覆蓋。
LEGACY_ETF_HEADING_CSV = """一、股票期貨契約保證金一覽表
(一) 標的證券為股票之股票期貨契約
更新日期:2026/09/15
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,保證金所屬級距,結算保證金適用比例,維持保證金適用比例,原始保證金適用比例,
61,CDF    ,2330,台積電期貨                    ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
(二) 標的證券為ETF之股票期貨契約
更新日期:2026/08/12
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,結算保證金,維持保證金,原始保證金,
1,NYF    ,0050,元大台灣50ETF期貨             ,元大台灣卓越50證券投資信託基金,64000,67000,87000,
"""

INDEX_MARGIN_CSV = """更新日期:2026/08/12
商品別,結算保證金,維持保證金,原始保證金,,
臺股期貨,519000,538000,701000,
小型臺指,129750,134500,175250,
微型臺指期貨,25950,26900,35050,
臺指選擇權風險保證金(A)值,138000,143000,187000,
"""


def test_stock_margin_reads_ratios_and_its_own_update_date():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["stock_updated"] == "2026/09/15"
    cdf = m["stock"]["CDF"]
    assert cdf["code"] == "2330" and cdf["tier"] == "級距1"
    assert cdf["initial_pct"] == 13.50 and cdf["maintenance_pct"] == 10.35
    assert m["stock"]["QFF"]["initial_pct"] == 13.50


def test_stock_margin_accepts_a_blank_tier():
    """風險係數 >15% 的 14 檔沒有級距，級距欄是空字串而不是缺列。"""
    assert ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)["stock"]["KUF"] == {
        "code": "1802", "name": "台玻期貨", "tier": "",
        "clearing_pct": 16.00, "maintenance_pct": 16.56, "initial_pct": 21.60}


def test_stock_margin_handles_a_quoted_name_containing_a_comma():
    """股票期貨標的證券全名有時帶英文逗號並用引號包住，如 JNF（TPK-KY 期貨）的
    "TPK Holding Co., Ltd."。裸 str.split(",") 會把這一格從中間切開，讓後面所有
    欄位（級距／三個比例）全部錯位一格且沒有任何錯誤訊息——實測會把 296 列都
    「解析成功」但其中至少一列的值全部是錯的。改用 csv 模組逐行解析後，這一列
    才能正確對齊。"""
    jnf = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)["stock"]["JNF"]
    assert jnf["tier"] == "級距2"
    assert jnf["clearing_pct"] == 12.00
    assert jnf["maintenance_pct"] == 12.42
    assert jnf["initial_pct"] == 16.20


def test_etf_section_has_fixed_amounts_and_a_different_update_date():
    """ETF 期貨公布固定金額、不套價格×比例，而且生效日與股票區段不同。"""
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["etf_updated"] == "2026/08/12"
    assert m["etf"]["NYF"]["initial"] == 87000
    assert m["etf"]["SRF"]["initial"] == 8700      # 小型恰為標準的 1/10


def test_stock_margin_still_accepts_the_legacy_etf_heading_spelling():
    """程式同時接受「受益憑證」（正式站的真標題）與「ETF」（舊／其他文件可能出現
    的寫法）兩種第二段標題——這裡單獨用 LEGACY_ETF_HEADING_CSV 鎖住後者，
    不讓它在主 fixture 改成真標題後失去測試覆蓋。"""
    m = ssf.parse_stock_margining_csv(LEGACY_ETF_HEADING_CSV)
    assert m["etf_updated"] == "2026/08/12"
    assert m["etf"]["NYF"]["initial"] == 87000


def test_option_sections_are_not_read_as_futures():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert "ZZZ" not in m["stock"] and "ZZZ" not in m["etf"]


def test_index_margin_reads_tmf():
    m = ssf.parse_index_margining_csv(INDEX_MARGIN_CSV)
    assert m["updated"] == "2026/08/12"
    assert m["items"]["微型臺指期貨"] == {"clearing": 25950, "maintenance": 26900,
                                          "initial": 35050}
    assert m["items"]["臺股期貨"]["initial"] == 701000


class _FakeResp:
    """`fetch_ssf_margin_table` 用的最小回應樁：`raise_for_status` 委派給真正的
    httpx.Response，丟出來的例外型別與訊息文字才是 httpx 自己的，而不是對著
    自己編的字串斷言（同 tests/test_ssf.py::_ListResp 的做法）。"""
    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            req = ssf.httpx.Request("GET", ssf.STOCK_MARGIN_URL)
            ssf.httpx.Response(self.status_code, request=req).raise_for_status()


class _FakeClient:
    """依 URL 回傳不同內容，模擬 `fetch_ssf_margin_table` 對兩個端點各自的 GET。"""
    def __init__(self, by_url: dict):
        self._by_url = by_url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, *a, **kw):
        return self._by_url[url]


def test_fetch_margin_table_returns_empty_on_non_2xx_and_logs_the_status(monkeypatch, caplog):
    """TAIFEX 回錯誤頁時，httpx 預設不會自動丟例外——不加 raise_for_status 的話，
    錯誤頁的位元組會被硬拿去解析，多半解出一份「不合格的表」，log 只會講
    「保證金表不完整」而完全看不出是伺服器回了 500（同 sources/revenue.py 修過的
    那個失敗歸因問題）。加了 raise_for_status 後，非 2xx 要落進既有的
    except Exception 並把真正的原因（狀態碼）記下來，對外仍是回傳 {}、不往上炸。"""
    import logging
    monkeypatch.setattr(ssf.httpx, "Client", lambda *a, **kw: _FakeClient({
        ssf.STOCK_MARGIN_URL: _FakeResp(b"", 500),
        ssf.INDEX_MARGIN_URL: _FakeResp(b""),
    }))
    with caplog.at_level(logging.WARNING, logger="spr"):
        result = ssf.fetch_ssf_margin_table()
    assert result == {}
    # 斷言同時包含 except 分支自己的措辭與真實的 HTTP 狀態碼——
    # 前者證明走的是 fetch_ssf_margin_table 裡那行 log.warning，
    # 後者證明例外訊息確實來自 httpx 對 500 的 raise_for_status，而不是隨便一個例外。
    assert any("保證金表抓取失敗" in r.getMessage() and "500" in r.getMessage()
               for r in caplog.records)


def test_fetch_margin_table_rejects_an_implausible_index_section(monkeypatch, caplog):
    """docstring 說「任何一份不合格就整份回 {}」，但原本的合理性檢查漏了指數段——
    股票與 ETF 兩段正常、指數 CSV 解析成空表時，舊版仍會把這個殘缺的指數併入
    回傳值，讓「三段有兩段成功」偷偷冒充「整體成功」逃出去。

    這裡直接樁掉兩個 parse 函式、只驗證 fetch_ssf_margin_table 自己的合併／
    守衛邏輯，不必為了餵飽股票 290 檔／ETF 20 檔的門檻手刻一份上千行的假 CSV
    ——兩個 parse 函式各自的解析正確性已由前面的測試涵蓋。"""
    import logging
    stock = {f"S{i:03d}": {"code": str(1000 + i), "name": "x", "tier": "級距1",
                            "clearing_pct": 10.0, "maintenance_pct": 10.35,
                            "initial_pct": 13.5} for i in range(290)}
    etf = {f"E{i:02d}": {"code": str(50 + i), "name": "y",
                          "clearing": 1000, "maintenance": 1000, "initial": 1000}
           for i in range(20)}
    monkeypatch.setattr(ssf, "parse_stock_margining_csv", lambda text: {
        "stock_updated": "2026/09/15", "etf_updated": "2026/08/12",
        "stock": stock, "etf": etf})
    monkeypatch.setattr(ssf, "parse_index_margining_csv",
                        lambda text: {"updated": None, "items": {}})
    monkeypatch.setattr(ssf.httpx, "Client", lambda *a, **kw: _FakeClient({
        ssf.STOCK_MARGIN_URL: _FakeResp(b"irrelevant"),
        ssf.INDEX_MARGIN_URL: _FakeResp(b"irrelevant"),
    }))
    with caplog.at_level(logging.WARNING, logger="spr"):
        result = ssf.fetch_ssf_margin_table()
    assert result == {}
    assert "指數期貨保證金不完整" in caplog.text


def test_margin_matches_the_officially_published_examples():
    # CDF 202610，2026-09-17 結算 2,432，級距1 原始 13.50%
    assert ssf.margin_amount(2432, 2000, 13.50) == 656640
    # DHF 202610，251.5，級距2 16.20%
    assert ssf.margin_amount(251.5, 2000, 16.20) == 81486


def test_margin_uses_round_half_up_not_bankers_rounding():
    """兩個實測的半元案例，券商公布數字對得上的是 ROUND_HALF_UP。

    Python 的 round() 用銀行家進位，這兩個各會少 1 元。實測 1,181 個合約月中
    有 101 個兩者結果不同，所以這不是理論風險。
    """
    assert ssf.margin_amount(238.5, 2000, 20.25) == 96593      # 南亞 CAF，96,592.5
    assert ssf.margin_amount(2453, 100, 13.50) == 33116        # 小型台積電，33,115.5
    assert round(96592.5) == 96592 and round(33115.5) == 33116  # 證明 round() 真的不同


def test_margin_returns_none_when_an_input_is_missing():
    assert ssf.margin_amount(None, 2000, 13.5) is None
    assert ssf.margin_amount(100, 2000, None) is None
    assert ssf.margin_amount(100, 0, 13.5) is None
