"""集保戶股權分散表：個股大戶持股比。

兩個來源，共用同一套 15 分級聚合（`_aggregate_levels`）：
- opendata（`getOD.ashx?id=1-5`）：只提供「當週」全市場，趨勢逐週累積（`parse_custody_distribution`）。
- 智能網股權分散表（`smWeb/qryStock`）：單股單週，含約一年歷史週次，供回補 6 月前
  （`fetch_custody_weeks` / `fetch_custody_history` / `parse_custody_ownership_html`）。

注意：TDCC 憑證設定有瑕疵（缺 SKI），需停用 SSL 驗證才能連線（僅針對此主機）。
持股分級（張＝1000股）：12=400~600張、13=600~800、14=800~1000、15=>1000張（千張大戶）。
"""
import csv
import io
import re

import httpx

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
SMWEB_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"


def _ymd(s) -> str | None:
    s = "".join(ch for ch in str(s or "") if ch.isdigit())
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


def _num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _aggregate_levels(levels) -> dict:
    """levels＝[(分級序, 人數, 占比%), ...] → {big1000_pct, big400_pct, big_holders}。
    千張大戶＝級15；400張↑＝級12~15（兩來源共用，語意一致）。"""
    d = {"big1000_pct": 0.0, "big400_pct": 0.0, "big_holders": 0}
    for lvl, holders, pct in levels:
        if lvl == "15":               # 千張大戶
            d["big1000_pct"] += pct
            d["big400_pct"] += pct
            d["big_holders"] += int(holders)
        elif lvl in ("12", "13", "14"):  # 400~1000 張
            d["big400_pct"] += pct
    return {"big1000_pct": round(d["big1000_pct"], 2),
            "big400_pct": round(d["big400_pct"], 2),
            "big_holders": d["big_holders"]}


def parse_custody_distribution(text: str) -> dict:
    """opendata CSV → {week_date, data:{代號: {big1000_pct, big400_pct, big_holders}}}。"""
    rows = list(csv.reader(io.StringIO(text)))
    week = None
    by_code: dict = {}
    for r in rows[1:]:
        if len(r) < 6:
            continue
        week = r[0].strip()
        code = r[1].strip()
        lvl = r[2].strip()
        holders, pct = _num(r[3]), _num(r[5])
        if holders is None or pct is None:
            continue
        by_code.setdefault(code, []).append((lvl, holders, pct))
    data = {code: _aggregate_levels(levels) for code, levels in by_code.items()}
    return {"week_date": _ymd(week), "data": data}


def parse_custody_ownership_html(html: str) -> dict:
    """智能網單股單週 HTML → {big1000_pct, big400_pct, big_holders}。
    表列格式：分級序 / 級距 / 人數 / 股數 / 占比%（級16 合計自動略過）。"""
    levels = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("\xa0", " ").strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        holders, pct = _num(cells[2]), _num(cells[4])
        if holders is not None and pct is not None:
            levels.append((cells[0], holders, pct))
    return _aggregate_levels(levels)


def _hidden(html: str, name: str) -> str:
    m = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def _scadate_options(html: str) -> list:
    sel = re.search(r'name="scaDate".*?</select>', html, re.S)
    return re.findall(r'value="(\d{8})"', sel.group(0)) if sel else []


def fetch_custody_weeks() -> list:
    """智能網可查的資料日期（YYYYMMDD，新到舊，約一年週次）。"""
    r = httpx.get(SMWEB_URL, timeout=60, follow_redirects=True, verify=False)
    return _scadate_options(r.text)


def fetch_custody_history(code: str, weeks=None, max_weeks: int = 60) -> dict:
    """單股逐週抓智能網股權分散 → {week_iso: {big1000_pct, big400_pct, big_holders}}。

    smWeb 的 SYNCHRONIZER_TOKEN 為 CSRF、單次有效且每次回應輪替，故必須從上一筆回應摘出
    新 token 再送下一筆（已實測：重用舊 token 會取不到表）。weeks 缺省＝全部可查週次。
    """
    out: dict = {}
    with httpx.Client(timeout=60, follow_redirects=True, verify=False) as cli:
        html = cli.get(SMWEB_URL).text
        token = _hidden(html, "SYNCHRONIZER_TOKEN")
        uri = _hidden(html, "SYNCHRONIZER_URI") or "/portal/zh/smWeb/qryStock"
        meth = _hidden(html, "method") or "submit"
        avail = _scadate_options(html)
        fir = avail[0] if avail else ""
        target = [w for w in (weeks or avail) if w in avail][:max_weeks]
        for w in target:
            if not token:
                break
            try:
                p = cli.post(SMWEB_URL, data={
                    "SYNCHRONIZER_TOKEN": token, "SYNCHRONIZER_URI": uri, "method": meth,
                    "firDate": fir, "scaDate": w, "sqlMethod": "StockNo",
                    "stockNo": code, "stockName": ""})
                token = _hidden(p.text, "SYNCHRONIZER_TOKEN")   # 輪替：下一筆用新 token
                rec = parse_custody_ownership_html(p.text)
                iso = _ymd(w)
                if iso and (rec["big_holders"] or rec["big1000_pct"]):
                    out[iso] = rec
            except Exception:  # noqa: BLE001 — 單週失敗即中止（token 鏈斷），已抓的仍回傳
                break
    return out


def fetch_custody_distribution() -> dict:
    # verify=False：TDCC 憑證（CN=epassbook.tdcc.com.tw，TWCA 簽發）缺 Subject Key Identifier
    # 擴充欄位，Python 嚴格鏈驗證會拒絕，故此主機停用 TLS 驗證（已評估、範圍窄，見下）。
    #
    # 已評估的替代方案與捨棄原因（docs/SECURITY.md M4）：
    # - 釘選此葉憑證指紋：憑證效期至 2026-09-04，到期即失效，需人工追蹤更新，維運成本高。
    # - 手動驗證憑證鏈（跳過 SKI 檢查）：需自刻加密驗證邏輯，複雜度與潛在 bug 風險
    #   高於現狀，且憑證輪替時仍要同步維護。
    # 資料本身是 TDCC 每週公開發布的集保戶股權分散統計（非帳密、非個資），
    # 即使遭竄改頂多造成分析數字失準，非機敏資料外洩。故維持現狀，僅窄範圍套用於此主機。
    r = httpx.get(TDCC_URL, params={"id": "1-5"}, timeout=90, follow_redirects=True, verify=False)
    return parse_custody_distribution(r.content.decode("utf-8-sig", errors="replace"))
