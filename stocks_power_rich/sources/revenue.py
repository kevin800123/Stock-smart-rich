"""上市／上櫃公司月營收（MOPS，經 TWSE／TPEx OpenAPI 轉出）。

- 上市：openapi.twse.com.tw／opendata/t187ap05_L
- 上櫃：www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O（與上市 t187ap03「公司基本資料」
  同一組 mopsfin_t187apXX_O 命名慣例，見 sources/tpex.py 的 OTC_COMPANY_URL）

兩個端點的回應欄位命名完全相同（皆源自同一套 MOPS 揭露格式），因此共用一支
`parse_monthly_revenue`，不像 twse.py/tpex.py 其餘那樣為每個市場各寫一份。

**這個端點只給「最新一次已公告的月份」，沒有歷史查詢參數**（TWSE 官方文件與實測皆同）：
每次呼叫回傳的是全市場「當下最新資料年月」的月營收，不能用來回補過去月份。這對「近乎全市場
單次涵蓋」是好事——第一次呼叫就有全部個股當月資料，不必像 intl ticker 那樣從零累積；壞處是
若中間漏了某個月沒有呼叫到，那個月的資料永遠補不回來，只能接受歷史有缺口。呼叫端（updater）
因此設計成「每天呼叫、直接覆寫」而非只在偵測到新月份時才呼叫——反正同一個月重複覆寫是無害的
冪等操作，換來的是不必自己判斷「現在是不是有新月份可以抓」這種容易出錯的邊界。
"""
import re

import httpx

TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
OTC_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

# 歷史月營收：openapi 只給「最新已公告月」，要回補過去月份得走 MOPS 的 t21sc03 彙總報表
# （可指定民國年月，一次整月全市場）。上市 sii／上櫃 otc 兩條路徑、Big5(hkscs) 編碼的 HTML。
MOPS_HISTORY_URL = {
    "twse": "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{y}_{m}_0.html",
    "otc": "https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_{y}_{m}_0.html",
}


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _roc_date_to_iso(roc) -> str | None:
    """7 位民國日期（如 1150811）→ 西元 ISO（2026-08-11）。"""
    s = "".join(ch for ch in str(roc or "") if ch.isdigit())
    if len(s) < 7:
        return None
    return f"{int(s[:3]) + 1911:04d}-{s[3:5]}-{s[5:7]}"


def _roc_yearmonth_to_iso(roc) -> str | None:
    """5 位民國年月（如 11507）→ 西元 ISO 年月（2026-07）。"""
    s = "".join(ch for ch in str(roc or "") if ch.isdigit())
    if len(s) < 5:
        return None
    return f"{int(s[:3]) + 1911:04d}-{s[3:5]}"


def parse_monthly_revenue(payload: list) -> dict:
    """MOPS t187ap05（上市/上櫃共用格式）→ {代號: {...}}。

    「去年同月增減(%)」等衍生欄位在無可比基期時（去年同月營收=0）官方給空字串，不是
    "-" 也不是 0——`_f` 對空字串一律回 None，不會被誤判成「年減 0%」。`備註` 的 "-" 才是
    官方用來表示「無備註」的占位符，另外處理成 None。
    """
    out: dict[str, dict] = {}
    for r in payload or []:
        code = str(r.get("公司代號", "")).strip()
        if not code:
            continue
        note = str(r.get("備註", "")).strip()
        out[code] = {
            "name": str(r.get("公司名稱", "")).strip(),
            "industry": str(r.get("產業別", "")).strip(),
            "year_month": _roc_yearmonth_to_iso(r.get("資料年月")),
            "report_date": _roc_date_to_iso(r.get("出表日期")),
            "revenue": _f(r.get("營業收入-當月營收")),
            "revenue_prev_month": _f(r.get("營業收入-上月營收")),
            "revenue_last_year": _f(r.get("營業收入-去年當月營收")),
            "mom_pct": _f(r.get("營業收入-上月比較增減(%)")),
            "yoy_pct": _f(r.get("營業收入-去年同月增減(%)")),
            "revenue_accum": _f(r.get("累計營業收入-當月累計營收")),
            "revenue_accum_last_year": _f(r.get("累計營業收入-去年累計營收")),
            "accum_yoy_pct": _f(r.get("累計營業收入-前期比較增減(%)")),
            "note": note if note and note != "-" else None,
        }
    return out


def fetch_twse_revenue() -> dict:
    """上市月營收 {代號: {...}}。查無/失敗回空 dict。"""
    try:
        j = httpx.get(TWSE_REVENUE_URL, timeout=30, follow_redirects=True).json()
        return parse_monthly_revenue(j)
    except Exception:  # noqa: BLE001
        return {}


def fetch_otc_revenue() -> dict:
    """上櫃月營收 {代號: {...}}。查無/失敗回空 dict。

    verify=False：www.tpex.org.tw 憑證缺 Subject Key Identifier，與本模組其餘打
    同一主機的 fetcher（見 sources/tpex.py）同一個毛病，Windows 容忍、Zeabur(Linux) 不容忍。
    """
    try:
        j = httpx.get(OTC_REVENUE_URL, timeout=30, verify=False,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
        return parse_monthly_revenue(j)
    except Exception:  # noqa: BLE001
        return {}


_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


def _cell_text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html)).strip()


def parse_monthly_revenue_html(html: str | None, year_month: str, report_date: str) -> dict:
    """MOPS t21sc03 月營收彙總 HTML → {代號: {...}}，形狀與 `parse_monthly_revenue` 相同，
    因此可直接餵給既有的 `db.bulk_upsert_revenue`（同一張 stock_revenue_monthly）。

    每筆資料列固定 10 個 <td>：代號/名稱/當月營收/上月營收/去年當月營收/上月比較增減%/
    當月累計/去年累計/前期比較增減%/備註。**t21sc03 不提供「單月 YoY」欄**——它只有「上月
    比較(MoM)」與「累計前期比較」；單月 YoY 依 openapi 的定義自算＝(當月/去年當月−1)×100，
    去年當月為 0 或缺時回 None（不可除以零，對齊 openapi 空字串→None 的語意）。

    `year_month`／`report_date` 由呼叫端帶入（HTML 本身不含年月）：report_date 用「次月 10 日」
    這個法定申報截止日近似，確保 revenue_yoy_map/get_latest_revenue 的 `report_date<=as_of`
    篩得到這些回補列（若留 NULL，那些 as_of 查詢會把整批歷史排除掉）。
    只收代號為 4 碼數字的資料列——表頭、產業標題、合計列一律略過。
    """
    out: dict[str, dict] = {}
    for row_html in _ROW_RE.findall(html or ""):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row_html)]
        if len(cells) < 10:
            continue
        code = cells[0]
        if not re.fullmatch(r"\d{4}", code):
            continue
        rev = _f(cells[2])
        last_year = _f(cells[4])
        yoy = round((rev / last_year - 1) * 100, 6) if (rev is not None and last_year) else None
        note = cells[9]
        out[code] = {
            "name": cells[1],
            "industry": "",                       # 歷史 HTML 依產業分段、逐列不帶產業別，留空
            "year_month": year_month,
            "report_date": report_date,
            "revenue": rev,
            "revenue_prev_month": _f(cells[3]),
            "revenue_last_year": last_year,
            "mom_pct": _f(cells[5]),
            "yoy_pct": yoy,
            "revenue_accum": _f(cells[6]),
            "revenue_accum_last_year": _f(cells[7]),
            "accum_yoy_pct": _f(cells[8]),
            "note": note if note and note != "-" else None,
        }
    return out


def fetch_monthly_revenue_history(roc_year: int, month: int, market: str) -> dict:
    """指定民國年月的整月全市場月營收（market＝"twse"/"otc"）。查無/失敗回空 dict。

    西曆年月由民國年月換算，report_date 取次月 10 日（申報截止日近似）。MOPS 回 Big5(hkscs)
    位元組，明確以 bytes 解碼——httpx 的 .text 對 Big5 會猜錯編碼。
    """
    url = MOPS_HISTORY_URL.get(market)
    if not url:
        return {}
    year_month = f"{roc_year + 1911:04d}-{month:02d}"
    ny, nm = (roc_year + 1912, 1) if month == 12 else (roc_year + 1911, month + 1)
    report_date = f"{ny:04d}-{nm:02d}-10"
    try:
        r = httpx.get(url.format(y=roc_year, m=month), timeout=40,
                      headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        html = r.content.decode("big5hkscs", errors="replace")
        return parse_monthly_revenue_html(html, year_month, report_date)
    except Exception:  # noqa: BLE001
        return {}
