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
    # verify=False：TDCC 憑證缺 Subject Key Identifier，否則連線失敗
    r = httpx.get(TDCC_URL, params={"id": "1-5"}, timeout=90, follow_redirects=True, verify=False)
    return parse_custody_distribution(r.content.decode("utf-8-sig", errors="replace"))
