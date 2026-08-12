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
import httpx

TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
OTC_REVENUE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"


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
