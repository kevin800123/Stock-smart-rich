"""證交所盤中快照（mis.twse.com.tw）：盤中突破警示的價格來源。

非官方介面（證交所網站自用），無服務承諾——僅低頻輪詢（每 5 分鐘 1~2 個請求），
失效時由呼叫端負責告警，不可默默失敗。上市 tse_、上櫃 otc_ 前綴皆支援。
"""
import datetime

import httpx

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
CHUNK = 50  # 每請求最多查的檔數（保守，避免 URL 過長/被擋）


def _pos(s):
    """字串 → 正數；非數字或 ≤0 一律回 None。價格沒有 0 這個合法值。"""
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _price(m: dict):
    """最新成交價 z；盤中無成交瞬間 z='-' → 退回最佳買價 b 的第一個「有效」檔。

    b 是 '_' 分隔的五檔委買，**第一檔可能是佔位的 0.0000**，所以要往後找第一個正數，
    不能盲取 index 0。實測 2026-07-30 09:43 川湖 b='0.0000_7850.0000_7845.0000...'：
    舊碼取 0.0 當現價 → 漲跌算成 0−昨收＝−7,140、−100%，而 rank_price 的
    `price or close` 又把 0 當假值退回昨收，畫面於是變成「正常價格配 −100%」。
    完全無有效報價時回 None，讓呼叫端整檔略過（寧可不顯示，也不要顯示假跌停）。
    """
    p = _pos(m.get("z"))
    if p is not None:
        return p
    for lv in str(m.get("b") or "").split("_"):
        p = _pos(lv)
        if p is not None:
            return p
    return None


def parse_mis_quotes(payload: dict) -> dict:
    """getStockInfo 回應 → {代號: 現價}。無法取得價格的檔略過。"""
    out: dict[str, float] = {}
    for m in payload.get("msgArray") or []:
        code = str(m.get("c") or "").strip()
        p = _price(m)
        if code and p is not None:
            out[code] = p
    return out


def parse_mis_rank(payload: dict) -> dict:
    """getStockInfo → {代號: {price, chg, chg_pct, vol, time, name}}（高價股排行用的完整欄位）。

    現價沿用 _price（z，無成交退買一）；漲跌以昨收 y 計；v=當日累積成交量（張，MIS 原生單位，
    無成交金額欄位）；t=成交時間取 HH:MM。無價的檔略過。
    """
    out: dict[str, dict] = {}
    for m in payload.get("msgArray") or []:
        code = str(m.get("c") or "").strip()
        p = _price(m)
        if not code or p is None:
            continue
        rec = {"price": p, "chg": None, "chg_pct": None, "vol": None,
               "time": None, "name": str(m.get("n") or "").strip()}
        try:
            rec["vol"] = int(str(m.get("v") or ""))
        except ValueError:
            pass
        try:
            y = float(str(m.get("y") or ""))
            rec["chg"] = round(p - y, 2)
            rec["chg_pct"] = round((p - y) / y * 100, 2) if y else None
        except ValueError:
            pass
        t = str(m.get("t") or "")
        if ":" in t:
            rec["time"] = t[:5]
        out[code] = rec
    return out


def fetch_mis_rank(tokens: list[str]) -> dict:
    """批次查排行報價（完整欄位版）。tokens 同 fetch_mis_quotes，自動分塊；查無/失敗回空。"""
    out: dict[str, dict] = {}
    for i in range(0, len(tokens), CHUNK):
        chunk = tokens[i:i + CHUNK]
        try:
            j = httpx.get(MIS_URL,
                          params={"ex_ch": "|".join(chunk), "json": "1", "delay": "0",
                                  "_": str(int(datetime.datetime.now().timestamp() * 1000))},
                          timeout=15, headers={"User-Agent": "Mozilla/5.0"}).json()
            if j.get("rtcode") == "0000":
                out.update(parse_mis_rank(j))
        except Exception:  # noqa: BLE001 — 單塊失敗略過
            pass
    return out


def fetch_mis_quotes(tokens: list[str]) -> dict:
    """批次查盤中現價。tokens＝['tse_2330.tw','otc_8069.tw',...]，自動分塊。查無/失敗回空。"""
    out: dict[str, float] = {}
    for i in range(0, len(tokens), CHUNK):
        chunk = tokens[i:i + CHUNK]
        try:
            j = httpx.get(MIS_URL,
                          params={"ex_ch": "|".join(chunk), "json": "1", "delay": "0",
                                  "_": str(int(datetime.datetime.now().timestamp() * 1000))},
                          timeout=15, headers={"User-Agent": "Mozilla/5.0"}).json()
            if j.get("rtcode") == "0000":
                out.update(parse_mis_quotes(j))
        except Exception:  # noqa: BLE001 — 單塊失敗略過，呼叫端以「全空」判斷離線
            pass
    return out
