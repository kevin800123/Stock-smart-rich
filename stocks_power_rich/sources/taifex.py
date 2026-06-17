"""期交所資料抓取：台指期行情、期貨三大法人未平倉、微台/小台散戶多空比。

散戶多空比（玩股網式定義）：期貨零和，以三大法人反面近似散戶部位，
    散戶多空比 = -(三大法人淨未平倉) / 全市場未平倉量
正值＝散戶偏多、負值＝散戶偏空（反指標）。

設計：純解析/計算函式（可單元測試）＋ 薄網路包裝（整合測試）。
"""
import httpx

BASE = "https://openapi.taifex.com.tw/v1"
Q_FUT = "/DailyMarketReportFut"
Q_INST = "/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"

# 行情用代號 → 法人用中文品名
MTX_NAME = "小型臺指期貨"
TMF_NAME = "微型臺指期貨"


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _get(path: str):
    return httpx.get(f"{BASE}{path}", timeout=60, follow_redirects=True).json()


# ---- 純計算函式 ----

def retail_long_short_ratio(inst_net_oi, total_oi):
    if not total_oi:
        return None
    return round(-inst_net_oi / total_oi, 4)


def total_oi_for(fut_records: list, contract: str):
    """某契約全市場未平倉量＝該契約各月份數值 OI 加總（OI 為 '-' 的盤後列自動略過）。"""
    s = 0.0
    found = False
    for r in fut_records:
        if r.get("Contract") == contract:
            oi = _f(r.get("OpenInterest"))
            if oi is not None:
                s += oi
                found = True
    return s if found else None


def inst_net_oi_for(inst_records: list, contract_name: str):
    """某契約三大法人淨未平倉＝該品名 自營/投信/外資 三列 OpenInterest(Net) 加總。"""
    s = 0.0
    found = False
    for r in inst_records:
        if r.get("ContractCode") == contract_name:
            net = _f(r.get("OpenInterest(Net)"))
            if net is not None:
                s += net
                found = True
    return s if found else None


def parse_tx_price(fut_records: list, contract: str = "TX") -> dict:
    """近月（最小到期月、排除週契約與盤後）台指期收盤與漲跌。"""
    rows = [
        r for r in fut_records
        if r.get("Contract") == contract
        and _f(r.get("OpenInterest")) is not None
        and "W" not in str(r.get("ContractMonth(Week)", ""))
    ]
    if not rows:
        return {"tx_price": None, "tx_chg": None}
    rows.sort(key=lambda r: str(r.get("ContractMonth(Week)", "")))
    near = rows[0]
    return {"tx_price": _f(near.get("Last")), "tx_chg": _f(near.get("Change"))}


def compute_retail_ratios(fut_records: list, inst_records: list) -> dict:
    mtx_oi = total_oi_for(fut_records, "MTX")
    mtx_net = inst_net_oi_for(inst_records, MTX_NAME)
    tmf_oi = total_oi_for(fut_records, "TMF")
    tmf_net = inst_net_oi_for(inst_records, TMF_NAME)
    return {
        "fut_inst_net": mtx_net,
        "retail_ls_mtx": retail_long_short_ratio(mtx_net, mtx_oi) if (mtx_net is not None and mtx_oi) else None,
        "retail_ls_tmf": retail_long_short_ratio(tmf_net, tmf_oi) if (tmf_net is not None and tmf_oi) else None,
    }


# ---- 網路包裝 ----

def fetch_tx_quote() -> dict:
    return parse_tx_price(_get(Q_FUT))


def fetch_retail_ratios() -> dict:
    return compute_retail_ratios(_get(Q_FUT), _get(Q_INST))
