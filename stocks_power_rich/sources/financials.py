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
import re

import httpx

MOPSFIN_URL = "https://mopsfin.twse.com.tw/compare/data"
MOPSFIN_REPORT_URL = "https://mopsfin.twse.com.tw/compare/report"

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


# ---- sub-task 2：完整報表 HTML（比率清單裡沒有的原始科目）----
# lan_score 的 pretax_income/capex，以及 Call_LE 推估季 EPS 用到的營業費用/所得稅費用，
# 都只在完整三大報表裡（比率端點沒有），要走 /compare/report（回 HTML 表格、逐季）。
# key → (mopsfin 報表代碼, 會計科目標籤)。
REPORT_ITEMS = {
    "pretax_income": ("IncomeStatement", "稅前淨利（淨損）"),
    "opex": ("IncomeStatement", "營業費用合計"),
    "income_tax": ("IncomeStatement", "所得稅費用（利益）合計"),
    "capex": ("CashflowStatement", "取得不動產、廠房及設備"),
}

_HEAD_RE = re.compile(r'id="headTable".*?</table>', re.S)
_BODY_RE = re.compile(r'id="bodyTable".*?</table>', re.S)
_LABEL_RE = re.compile(r'text-left"\s*nowrap="">(.*?)</td>', re.S)
_TH_CODE_RE = re.compile(r'<th[^>]*nowrap[^>]*>(\d{3,6})(?:&nbsp;|\s)', re.S)
_TR_RE = re.compile(r'<tr>(.*?)</tr>', re.S)
_VAL_RE = re.compile(r'font-weight:normal">([^<]*)</td>')
_YS_RE = re.compile(r'name="yearseason"\s*value="([^"]*)"')


def parse_report(html: str) -> tuple:
    """完整報表 HTML → (季別字串, {代號: {會計科目: 值}})。

    headTable 的科目標籤與 bodyTable 的列 1:1 對齊，每列 N 個值欄對應表頭 N 家公司（順序一致）。
    季別取自隱藏 input `yearseason`——這是**必要的防呆**：`/compare/report` 不帶 `ys` 時會悄悄
    回一個很舊的固定季（實測 2020Q2）而非最新，呼叫端要據此確認拿到的是不是自己要的季。
    標籤去除縮排全形空白，值去千分位逗號轉 float（無法轉的略過該格）。
    """
    ym = _YS_RE.search(html)
    quarter = ym.group(1) if ym else None
    head = _HEAD_RE.search(html)
    body = _BODY_RE.search(html)
    if not head or not body:
        return quarter, {}
    labels = [re.sub(r"[\s　]", "", lb) for lb in _LABEL_RE.findall(head.group(0))]
    codes = _TH_CODE_RE.findall(body.group(0))
    rows = []
    for tr in _TR_RE.findall(body.group(0)):
        vals = _VAL_RE.findall(tr)
        if vals:
            rows.append(vals)
    out: dict[str, dict] = {c: {} for c in codes}
    for i, label in enumerate(labels):
        if i >= len(rows):
            break
        for j, code in enumerate(codes):
            if j < len(rows[i]):
                v = _f(rows[i][j])
                if v is not None:
                    out[code][label] = v
    return quarter, out


def _prev_quarter(q: str) -> str | None:
    """"2026Q1" → "2025Q4"；"2026Q3" → "2026Q2"。格式不對回 None。"""
    m = re.match(r"^(\d{4})Q([1-4])$", q)
    if not m:
        return None
    year, season = int(m.group(1)), int(m.group(2))
    return f"{year - 1}Q4" if season == 1 else f"{year}Q{season - 1}"


def decumulate_quarterly(cumulative: dict) -> dict:
    """完整報表（/compare/report）的金額科目是年度累計（Q2＝H1、Q3＝前三季…），這裡反推
    每一季自己的值。cumulative＝{季別: 累計值}（如 "2025Q3": 2762963851.0）。

    Q1 的累計＝自己（一年的第一季，沒有更早的累計可減）；Q2~Q4 的單季＝本季累計－上一季累計
    （同一年度內）；跨年度到 Q1 一律重置，不與去年 Q4 相減。缺上一季資料時該季回 None
    （見 estimate_price_range/lan_score 的「算不出回 None」慣例，不用累計值頂替單季值）。
    """
    out: dict[str, float | None] = {}
    for q, cum in cumulative.items():
        m = re.match(r"^(\d{4})Q([1-4])$", q)
        if not m:
            continue
        season = int(m.group(2))
        if season == 1:
            out[q] = cum
        else:
            prev = _prev_quarter(q)
            prev_cum = cumulative.get(prev)
            out[q] = round(cum - prev_cum, 2) if prev_cum is not None else None
    return out


def fetch_report(codes: list, report: str, year: int, season: int) -> tuple:
    """一批代號、單一報表、指定年季 → (季別, {代號: {會計科目: 值}})。失敗回 (None, {})。

    `ys` 一律組出並送（見 parse_report 的防呆說明）；report＝mopsfin 報表代碼
    （IncomeStatement／CashflowStatement／BalanceSheet）。
    """
    data = {
        "compareItem": report,
        "companyId": [str(c) for c in codes],
        "ylabel": "",
        "quarter": "true",
        "revenue": "false",
        "companyAvg": "false",
        "bcodeAvg": "false",
        "ys": f"{year}{season}",
    }
    try:
        # 逾時 20 秒 fail-fast：健康回應約 6 秒，逾時多半是端點在密集請求下退化，
        # 與其卡 30 秒不如放掉這一季（呼叫端會往回抓下一季，該季下輪回補再補）。
        r = httpx.post(MOPSFIN_REPORT_URL, data=data, timeout=20,
                       headers={"User-Agent": "Mozilla/5.0"})
        return parse_report(r.text)
    except Exception:  # noqa: BLE001
        return (None, {})
