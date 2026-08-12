"""季報財務比率／金額（TWSE 官方「財務比較E點通」mopsfin.twse.com.tw）。

`lan_score()`（蘭質）需要 10 個季報指標，其中 8 個能從這個官方子系統的 `/compare/data`
端點以**乾淨 JSON、單次請求拿 13 年季度歷史**取得（sub-task 1，本模組）；另外兩個
（稅前淨利、資本支出）只在完整報表 HTML 裡，屬 sub-task 2。

**為什麼是這個來源**：TWSE OpenAPI（openapi.twse.com.tw）有損益表、資產負債表，但
**沒有現金流量表**，資產負債表也**沒拆出應收/存貨明細**，所以營運現金流、應收/存貨週轉率
都算不出來（實測掃過全部 143 個端點確認）。mopsfin 是同屬證交所的官方子系統，把這些比率
都預先算好，上市櫃通吃。

**端點特性**（實測）：`POST /compare/data`，`application/x-www-form-urlencoded`，
**免 CSRF、免 session、憑證正常（不必 verify=False）**。一次可帶多個 `companyId`
→ 多序列（實測 50 檔一次到位），由 `showNameList` 的順序對應回代號。回應：
`xaxisList`（季別如 "2013Q1"）＋ `graphData[i].data`＝`[xIndex, value, 型別]` tuples
（型別 "C"＝合併報表）。

**累計 vs 單季（給 sub-task 2/wiring 的提醒，本模組不處理）**：Revenue／OperatingCashflow
這類金額在 MOPS 是**年度累計**（Q2＝H1、Q3＝前三季…），而 `lan_score` 的季增/四季加總
邏輯預期**單季**值。本模組忠實存下原始季度序列，累計→單季的換算留給接線階段，避免資料層
擅自改動來源語意（同 revenue 那片「資料層只存、不判讀」的原則）。
"""
import httpx

MOPSFIN_URL = "https://mopsfin.twse.com.tw/compare/data"

# lan_score 財報 key → mopsfin compareItem 代碼（只列「乾淨 JSON」可取的 8 個；
# pretax_income／capex 需 HTML 報表，屬 sub-task 2，不在此）。
RATIO_ITEMS = {
    "revenue": "Revenue",                        # 營業收入（金額）
    "gross_margin": "GrossMargin",               # 毛利率（%）
    "net_income": "NetProfit",                   # 稅後純益（金額）
    "ocf": "OperatingCashflow",                  # 營業活動現金流量（金額）
    "debt_ratio": "DebtRatio",                   # 負債佔資產比率（%）
    "roe": "ROE",                                # 權益報酬率（%）
    "ar_turnover": "AccountsReceivableTurnover",  # 應收款項週轉率（次）
    "inv_turnover": "InventoryTurnover",         # 存貨週轉率（次）
}


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_ratio_series(payload: dict) -> dict:
    """/compare/data 回應 → {代號: {季別: 值}}。

    以 `showNameList` 的首個 token 取代號，順序對應 `graphData`。值為 null 或無法轉數字的
    季別直接略過（缺席＝無資料），不落成 None——下游要判斷時用「季別在不在 dict 裡」即可。
    """
    xaxis = payload.get("xaxisList") or []
    names = payload.get("showNameList") or []
    graph = payload.get("graphData") or []
    out: dict[str, dict] = {}
    for i, series in enumerate(graph):
        if i >= len(names):
            break
        code = str(names[i]).split(" ")[0].strip()
        if not code:
            continue
        vals: dict[str, float] = {}
        for point in series.get("data") or []:
            if not point:
                continue
            idx = point[0]
            v = _f(point[1]) if len(point) > 1 else None
            if v is None or not isinstance(idx, int) or idx >= len(xaxis):
                continue
            vals[xaxis[idx]] = v
        out[code] = vals
    return out


def fetch_financial_ratio(codes: list, indicator: str) -> dict:
    """單一指標、一批代號 → {代號: {季別: 值}}。查無/失敗回空 dict。

    indicator 是 lan_score 的財報 key（如 "roe"），對照 RATIO_ITEMS 轉成 mopsfin 代碼；
    未知 key 直接拋 KeyError（呼叫端 bug，不該靜默）。
    """
    item = RATIO_ITEMS[indicator]  # 未知 key → KeyError（刻意）
    data = {
        "compareItem": item,
        "companyId": [str(c) for c in codes],
        "ylabel": "",
        "quarter": "true",
        "revenue": "false",
        "companyAvg": "false",
        "bcodeAvg": "false",
    }
    try:
        r = httpx.post(MOPSFIN_URL, data=data, timeout=30,
                       headers={"User-Agent": "Mozilla/5.0"})
        return parse_ratio_series(r.json())
    except Exception:  # noqa: BLE001
        return {}
