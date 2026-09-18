"""股票期貨（股期）官方盤後資料：純解析 ＋ 薄網路包裝。

與 `taifex.py` 分開的理由：CSV 欄位形狀、下載表單、主力月規則與 tick 級距全都不同，
混進去會讓兩邊都難讀（`taifex.py` 已 440 行）。同一個資料來源、不同的產品族。
"""
from __future__ import annotations

import csv
import io

SSF_HEADER = ("交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
              "成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,"
              "是否因訊息面暫停交易,交易時段,價差對單式委託成交量")


def _f(s) -> float | None:
    """'-'／空字串／'1.37%' 都要接得住。算不出就回 None（全站慣例）。"""
    s = (s or "").strip().replace(",", "").rstrip("%")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s) -> int | None:
    v = _f(s)
    return None if v is None else int(v)


def parse_ssf_daily_csv(text: str) -> list[dict]:
    """把官方股期日檔 CSV 解析成逐列 dict。表頭不符一律丟 ValueError。"""
    first = (text or "").splitlines()[0].strip() if text.strip() else ""
    if first != SSF_HEADER:
        raise ValueError(f"股期日檔表頭與預期不符：{first[:120]!r}")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        month = (r.get("到期月份(週別)") or "").strip()
        d = (r.get("交易日期") or "").strip().replace("/", "-")
        out.append({
            "date": d,
            "contract": (r.get("契約") or "").strip(),
            "month": month,
            "session": (r.get("交易時段") or "").strip(),
            "is_spread": "/" in month,
            "open": _f(r.get("開盤價")), "high": _f(r.get("最高價")),
            "low": _f(r.get("最低價")), "close": _f(r.get("收盤價")),
            "chg": _f(r.get("漲跌價")), "chg_pct": _f(r.get("漲跌%")),
            "volume": _i(r.get("成交量")),
            "settlement": _f(r.get("結算價")), "oi": _i(r.get("未沖銷契約數")),
        })
    return out
