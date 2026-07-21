"""證交所盤中快照（mis.twse.com.tw）：盤中突破警示的價格來源。

非官方介面（證交所網站自用），無服務承諾——僅低頻輪詢（每 5 分鐘 1~2 個請求），
失效時由呼叫端負責告警，不可默默失敗。上市 tse_、上櫃 otc_ 前綴皆支援。
"""
import datetime

import httpx

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
CHUNK = 50  # 每請求最多查的檔數（保守，避免 URL 過長/被擋）


def _price(m: dict):
    """最新成交價 z；盤中無成交瞬間 z='-' → 退回最佳買價 b 的第一檔（保守估）。"""
    z = str(m.get("z") or "")
    try:
        return float(z)
    except ValueError:
        pass
    b = str(m.get("b") or "").split("_")[0]
    try:
        return float(b)
    except ValueError:
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
    """getStockInfo → {代號: {price, chg, chg_pct, time, name}}（高價股排行用的完整欄位）。

    現價沿用 _price（z，無成交退買一）；漲跌以昨收 y 計；t=成交時間取 HH:MM。無價的檔略過。
    """
    out: dict[str, dict] = {}
    for m in payload.get("msgArray") or []:
        code = str(m.get("c") or "").strip()
        p = _price(m)
        if not code or p is None:
            continue
        rec = {"price": p, "chg": None, "chg_pct": None,
               "time": None, "name": str(m.get("n") or "").strip()}
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
