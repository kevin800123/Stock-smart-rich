"""集保戶股權分散表（TDCC opendata）：個股大戶持股比。

只提供「當週」資料，趨勢需逐週累積快照。注意：TDCC 憑證設定有瑕疵（缺 SKI），
需停用 SSL 驗證才能連線（僅針對此主機）。

持股分級（張＝1000股）：12=400~600張、13=600~800、14=800~1000、15=>1000張（千張大戶）。
"""
import csv
import io

import httpx

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx"


def _ymd(s) -> str | None:
    s = "".join(ch for ch in str(s or "") if ch.isdigit())
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


def parse_custody_distribution(text: str) -> dict:
    """CSV → {week_date, data:{代號: {big1000_pct, big400_pct, big_holders}}}。"""
    rows = list(csv.reader(io.StringIO(text)))
    week = None
    agg: dict = {}
    for r in rows[1:]:
        if len(r) < 6:
            continue
        week = r[0].strip()
        code = r[1].strip()
        lvl = r[2].strip()
        try:
            holders = int(float(r[3]))
            pct = float(r[5])
        except ValueError:
            continue
        d = agg.setdefault(code, {"big1000_pct": 0.0, "big400_pct": 0.0, "big_holders": 0})
        if lvl == "15":               # 千張大戶
            d["big1000_pct"] += pct
            d["big400_pct"] += pct
            d["big_holders"] += holders
        elif lvl in ("12", "13", "14"):  # 400~1000 張
            d["big400_pct"] += pct
    data = {code: {"big1000_pct": round(v["big1000_pct"], 2),
                   "big400_pct": round(v["big400_pct"], 2),
                   "big_holders": v["big_holders"]}
            for code, v in agg.items()}
    return {"week_date": _ymd(week), "data": data}


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
