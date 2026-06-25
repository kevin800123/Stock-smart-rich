"""期交所資料抓取：台指期行情、期貨三大法人未平倉、微台/小台散戶多空比。

散戶多空比（玩股網式定義）：期貨零和，以三大法人反面近似散戶部位，
    散戶多空比 = -(三大法人淨未平倉) / 全市場未平倉量
正值＝散戶偏多、負值＝散戶偏空（反指標）。

設計：純解析/計算函式（可單元測試）＋ 薄網路包裝（整合測試）。
"""
import csv
import io
from datetime import date, timedelta

import httpx

BASE = "https://openapi.taifex.com.tw/v1"
# 期交所官方「歷史每日行情」下載（openapi 無歷史，改用此官方下載）
TX_DOWNLOAD = "https://www.taifex.com.tw/cht/3/dlFutDataDown"
TX_FORM = "https://www.taifex.com.tw/cht/3/futDataDown"
Q_FUT = "/DailyMarketReportFut"
Q_INST = "/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"

# 行情用代號 → 法人用中文品名
TX_NAME = "臺股期貨"      # 大台
MTX_NAME = "小型臺指期貨"  # 小台
TMF_NAME = "微型臺指期貨"  # 微台


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


def inst_net_oi_for(inst_records: list, contract_name: str, item: str | None = None):
    """某契約法人淨未平倉＝OpenInterest(Net) 加總。

    item=None → 自營/投信/外資 三列加總（三大法人合計）；
    item='外資' → 僅外資列（含「外資及陸資」，以子字串比對）。
    """
    s = 0.0
    found = False
    for r in inst_records:
        if r.get("ContractCode") != contract_name:
            continue
        if item is not None and item not in str(r.get("Item") or ""):
            continue
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
        return {"tx_price": None, "tx_chg": None, "tx_open": None, "tx_high": None, "tx_low": None}
    rows.sort(key=lambda r: str(r.get("ContractMonth(Week)", "")))
    near = rows[0]
    return {
        "tx_price": _f(near.get("Last")),
        "tx_chg": _f(near.get("Change")),
        "tx_open": _f(near.get("Open")),
        "tx_high": _f(near.get("High")),
        "tx_low": _f(near.get("Low")),
    }


def compute_retail_ratios(fut_records: list, inst_records: list) -> dict:
    mtx_oi = total_oi_for(fut_records, "MTX")
    mtx_net = inst_net_oi_for(inst_records, MTX_NAME)
    tmf_oi = total_oi_for(fut_records, "TMF")
    tmf_net = inst_net_oi_for(inst_records, TMF_NAME)
    tx_foreign_oi = inst_net_oi_for(inst_records, TX_NAME, item="外資")  # 外資台指淨未平倉（口）
    return {
        "fut_inst_net": mtx_net,
        "retail_ls_mtx": retail_long_short_ratio(mtx_net, mtx_oi) if (mtx_net is not None and mtx_oi) else None,
        "retail_ls_tmf": retail_long_short_ratio(tmf_net, tmf_oi) if (tmf_net is not None and tmf_oi) else None,
        "tx_foreign_oi": int(tx_foreign_oi) if tx_foreign_oi is not None else None,
        # 散戶小台淨未平倉（口）≈ -(三大法人小台淨額)，期貨零和近似
        "retail_oi_mtx": int(-mtx_net) if mtx_net is not None else None,
    }


# ---- 網路包裝 ----

def fetch_tx_quote() -> dict:
    return parse_tx_price(_get(Q_FUT))


def fetch_retail_ratios() -> dict:
    return compute_retail_ratios(_get(Q_FUT), _get(Q_INST))


# ---- 台指期歷史日K（期交所官方下載） ----

def parse_tx_history_csv(text: str, contract: str = "TX") -> list:
    """解析期交所歷史每日行情 CSV → 每日近月（一般盤、成交量最大者）OHLC。"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}

    def g(r, name):
        i = idx.get(name)
        return r[i].strip() if i is not None and i < len(r) else ""

    best: dict = {}  # date -> (volume, row dict)
    for r in rows[1:]:
        if not r or g(r, "契約") != contract or g(r, "交易時段") != "一般":
            continue
        o, h, l, c = g(r, "開盤價"), g(r, "最高價"), g(r, "最低價"), g(r, "收盤價")
        if any(v in ("", "-") for v in (o, h, l, c)):
            continue
        try:
            vol = float(g(r, "成交量").replace(",", ""))
            d = g(r, "交易日期").replace("/", "-")
            item = {"date": d, "open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": vol}
        except ValueError:
            continue
        if item["date"] not in best or vol > best[item["date"]][0]:
            best[item["date"]] = (vol, item)
    return [best[d][1] for d in sorted(best)]


def fetch_tx_history(days: int = 365, contract: str = "TX", chunk: int = 28) -> list:
    """下載近 days 天台指期歷史日K。期交所單次下載上限約 30 天，故分段(≤chunk 天)抓取後合併。

    流程：先 GET 表單頁取 cookie，再對每個時間窗 POST 下載 CSV、解析、依日期去重合併。
    """
    end = date.today()
    start = end - timedelta(days=days)
    merged: dict = {}
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0", "Referer": TX_FORM}) as s:
        s.get(TX_FORM)
        cur = start
        while cur <= end:
            win_end = min(cur + timedelta(days=chunk - 1), end)
            try:
                r = s.post(TX_DOWNLOAD, data={
                    "down_type": "1", "commodity_id": contract,
                    "queryStartDate": cur.strftime("%Y/%m/%d"),
                    "queryEndDate": win_end.strftime("%Y/%m/%d"),
                })
                for item in parse_tx_history_csv(r.content.decode("ms950", errors="replace"), contract):
                    merged[item["date"]] = item
            except Exception:  # noqa: BLE001 — 單一時間窗失敗略過
                pass
            cur = win_end + timedelta(days=1)
    return [merged[d] for d in sorted(merged)]
