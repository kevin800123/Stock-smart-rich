"""杯柄訊號歷史回測：訊號 → 等突破壓力線進場 → 持有 N 日報酬統計。純函數。

誠實前提（結果頁需一併呈現）：
- 進場假設為「突破日收盤」；未含手續費/交易稅/滑價（台股來回約 0.6%）。
- 樣本僅含目前仍上市/上櫃的個股（下市股不在庫＝存活者偏差，實際會略高估）。
- 歷史勝率不保證未來；僅用於判斷策略「有沒有優勢的證據」。
"""
import numpy as np

from . import patterns

ENTRY_WINDOW = 15   # 訊號後最多等 15 個交易日突破，否則視為未突破
HORIZONS = (5, 10, 20)
COOLDOWN = 20       # 進場/失效後 20 日內同一檔不再重複計


def backtest_cup(ohlc_by_code: dict) -> dict:
    """對 {code: {name, dates[], highs[], lows[], closes[]}} 全體做杯柄訊號回測。"""
    trades, signals, expired = [], 0, 0
    for code, d in ohlc_by_code.items():
        H, L, C = d.get("highs") or [], d.get("lows") or [], d.get("closes") or []
        dates = d.get("dates") or []
        n = len(C)
        if n < patterns.LOOKBACK + 1:
            continue
        sig, res = patterns.cup_handle_signals(H, L, C)
        Ca = np.asarray(C, dtype=float)
        last_until = -1
        for t in np.flatnonzero(sig):
            if t <= last_until:
                continue
            signals += 1
            r = res[t]
            entry = None
            for e in range(t + 1, min(t + ENTRY_WINDOW, n - 1) + 1):
                if Ca[e] > r:
                    entry = e
                    break
            if entry is None:
                expired += 1
                last_until = t + ENTRY_WINDOW
                continue
            tr = {"code": code, "name": d.get("name") or code,
                  "signal_date": dates[t], "entry_date": dates[entry],
                  "entry": round(float(Ca[entry]), 2), "resistance": round(float(r), 2)}
            for h in HORIZONS:
                x = entry + h
                tr[f"ret{h}"] = (round((float(Ca[x]) / float(Ca[entry]) - 1) * 100, 2)
                                 if x < n else None)
            trades.append(tr)
            last_until = entry + COOLDOWN
    # 各持有期統計（只計有完整未來資料的交易）
    horizons = {}
    for h in HORIZONS:
        rets = [t[f"ret{h}"] for t in trades if t.get(f"ret{h}") is not None]
        if rets:
            horizons[str(h)] = {
                "n": len(rets),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "avg": round(sum(rets) / len(rets), 2),
                "median": round(float(np.median(rets)), 2),
            }
    trades.sort(key=lambda t: t["entry_date"], reverse=True)
    total = signals
    return {
        "signals": total, "expired": expired, "trades_n": len(trades),
        "breakout_rate": round(len(trades) / total * 100, 1) if total else None,
        "horizons": horizons,
        "trades": trades[:40],
    }
