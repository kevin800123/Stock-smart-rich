"""「SS」操盤手：把 ss_trader.py 的純規則引擎包成統一的 analyze() 契約。

質化方法論全文見 .claude/skills/ss-trader/SKILL.md；本檔只負責取數 + 組裝通用區塊。
"""
from datetime import date

from .. import ss_trader
from ..db import get_ai_cache, get_snapshot_dates, get_snapshot
from ..api.helpers import _ohlc_names

META = {
    "id": "ss",
    "name": "SS 操盤手",
    "emoji": "🧭",
    "tagline": "「任何線形都是主力畫出來的，籌碼（量、主力分點）最可靠」",
    "desc": "20 年外資操盤手經驗：主力思維、波浪架構、季季高選股、台指期紀律。",
}

_QOQ_COLS = [
    {"key": "code", "label": "個股", "kind": "stock"},
    {"key": "close", "label": "收盤", "kind": "num"},
    {"key": "month_inc", "label": "月增%", "kind": "num1"},
    {"key": "rev_yoy", "label": "年增%", "kind": "num1"},
    {"key": "accum_inc", "label": "累增", "kind": "num1"},
    {"key": "big_holder_ratio", "label": "大戶增比", "kind": "num2"},
    {"key": "lan_value", "label": "蘭值", "kind": "lan"},
]
_DISCLAIMER = ("以上為 Ss 經驗法則的量化近似對照，僅供研究參考，非投資建議；"
               "買賣決策請自行判斷並自負風險。")


def analyze(conn) -> dict:
    c = conn
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM market_daily ORDER BY date DESC LIMIT 60").fetchall()][::-1]
    tx = c.execute("SELECT volume, night_volume FROM tx_history ORDER BY date DESC LIMIT 1").fetchone()
    night_ratio = (tx["night_volume"] / tx["volume"]) if tx and tx["night_volume"] and tx["volume"] else None
    checklist = ss_trader.market_checklist(
        rows, osfut=get_ai_cache(c, "osfut:current"), night_ratio=night_ratio,
        settlement_week=ss_trader.is_settlement_week(date.today()))

    snap_dates = get_snapshot_dates(c)
    picks = ss_trader.qoq_rising_picks(get_snapshot(c, snap_dates[-1]))[:15] if snap_dates else []

    # 一紅吃三黑（近 4 個交易日全市場掃描）
    last4 = [r[0] for r in c.execute(
        "SELECT DISTINCT date FROM stock_ohlc ORDER BY date DESC LIMIT 4").fetchall()][::-1]
    red3 = []
    if len(last4) == 4:
        series: dict = {}
        ph = ",".join("?" * 4)
        for code, o, cl in c.execute(
                f"SELECT code, open, close FROM stock_ohlc WHERE date IN ({ph}) ORDER BY code, date", last4):
            series.setdefault(code, []).append((o, cl))
        names = _ohlc_names(c)
        for code, bars in series.items():
            if len(bars) == 4 and ss_trader.red_engulfs_three_black(
                    [b[0] for b in bars], [b[1] for b in bars]):
                red3.append({"code": code, "name": names.get(code, code), "close": bars[-1][1]})

    r = ss_trader.ROUTINE
    red3_title = "K線訊號：一紅吃三黑（轉強可試單）"
    if len(last4) == 4:
        red3_title += f"（訊號日 {last4[-1]}）"
    return {
        "date": rows[-1]["date"] if rows else None,
        "sections": [
            {"type": "checklist", "title": "大盤檢核表（可量化規則對照）", "items": checklist},
            {"type": "routine", "title": "每日例行檢查", "groups": [
                {"label": "盤前", "items": r["pre"]},
                {"label": "盤中", "items": r["intra"]},
                {"label": "盤後", "items": r["post"]},
                {"label": "心法", "items": r["mind"]},
            ]},
            {"type": "table", "title": "選股訊號：季季高（近似）×大戶增",
             "note": "月增>0 ∧ 年增>0 ∧ 累增>0 ∧ 大戶增比>0（CSV 無季營收，以此近似），依蘭值排序",
             "columns": _QOQ_COLS, "rows": picks,
             "empty": "今日無符合（或尚未匯入選股 CSV）"},
            {"type": "table", "title": red3_title,
             "columns": [{"key": "code", "label": "個股", "kind": "stock"},
                         {"key": "close", "label": "收盤", "kind": "num"}],
             "rows": red3[:30], "empty": "今日無「一紅吃三黑」訊號"},
        ],
        "disclaimer": _DISCLAIMER,
    }
