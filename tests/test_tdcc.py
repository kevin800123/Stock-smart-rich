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
