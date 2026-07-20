from stocks_power_rich.sources import tdcc

CSV = (
    "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
    "20260626,2330  ,12,100,500000,1.50\n"
    "20260626,2330  ,13,80,300000,0.90\n"
    "20260626,2330  ,14,50,200000,0.60\n"
    "20260626,2330  ,15,30,9000000,70.00\n"
    "20260626,2330  ,17,260,10000000,100.00\n"
)


def test_parse_custody_distribution():
    out = tdcc.parse_custody_distribution(CSV)
    assert out["week_date"] == "2026-06-26"
    d = out["data"]["2330"]
    assert d["big1000_pct"] == 70.0          # 千張大戶（分級15）
    assert d["big400_pct"] == 73.0           # 分級 12+13+14+15
    assert d["big_holders"] == 30            # 千張大戶人數


# TDCC 智能網股權分散表 HTML（單股單週）——欄位：分級序 / 級距 / 人數 / 股數 / 占比%。
# 用 2330 真實數字（2026-07-17）：級15＝84.91%/1477人；級12~15＝1.07+0.94+0.75+84.91＝87.67%。
OWNERSHIP_HTML = """
<table><tbody>
<tr><td>11</td><td>200,001-400,000</td><td>1,345</td><td>377,776,220</td><td>1.45</td></tr>
<tr><td>12</td><td>400,001-600,000</td><td>567</td><td>277,573,821</td><td>1.07</td></tr>
<tr><td>13</td><td>600,001-800,000</td><td>351</td><td>244,033,255</td><td>0.94</td></tr>
<tr><td>14</td><td>800,001-1,000,000</td><td>219</td><td>195,510,892</td><td>0.75</td></tr>
<tr><td>15</td><td>1,000,001以上</td><td>1,477</td><td>22,019,234,369</td><td>84.91</td></tr>
<tr><td>16</td><td>合　計</td><td>2,941,980</td><td>25,932,370,067</td><td>100.00</td></tr>
</tbody></table>
"""


def test_parse_custody_ownership_html():
    d = tdcc.parse_custody_ownership_html(OWNERSHIP_HTML)
    assert d["big1000_pct"] == 84.91         # 級15 占比
    assert d["big400_pct"] == 87.67          # 級12+13+14+15
    assert d["big_holders"] == 1477          # 千張大戶人數


def test_html_and_csv_aggregation_are_equivalent():
    """智能網 HTML 與 opendata CSV 走同一套 15 分級聚合，語意一致。"""
    csv_txt = (
        "資料日期,證券代號,持股分級,人數,股數,占比%\n"
        "20260717,2330  ,12,567,277573821,1.07\n"
        "20260717,2330  ,13,351,244033255,0.94\n"
        "20260717,2330  ,14,219,195510892,0.75\n"
        "20260717,2330  ,15,1477,22019234369,84.91\n"
    )
    from_csv = tdcc.parse_custody_distribution(csv_txt)["data"]["2330"]
    from_html = tdcc.parse_custody_ownership_html(OWNERSHIP_HTML)
    assert from_csv == from_html
