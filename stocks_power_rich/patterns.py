"""型態偵測：亞當／杯柄（Cup-with-Handle）。純函數，餵日 OHLC 陣列（索引 0＝最舊、-1＝今日）。

移植自使用者提供的 XS：
  VALUE2=HIGHEST(H,377)  左緣（老的大高點）
  VALUE3=HIGHEST(H,55)   右緣（近期高點）
  CONDITION1 = VALUE2>VALUE3 and HIGHEST(H,13)<HIGHEST(H,55)
               and LOWEST(L,8)>LOWEST(L,21) and Call_5W>0
  CONDITION2 = HighestBar(H,377) - HighestBar(H,55) > 55
  Call_5W = PercentR(55) - 50   （PercentR=收盤在近55天高低區間的百分位；>0 即位於上半部）
符合時畫：趨勢線（左緣→右緣）＋壓力線（右緣水平延伸到今日）。
"""

LOOKBACK = 377  # 需要的最少 K 棒數


def _highest(vals, n):
    return max(vals[-n:])


def _lowest(vals, n):
    return min(vals[-n:])


def _highest_bar(vals, n):
    """近 n 根中最高值的『幾根前』（0＝今日）；同值取最近一次（XS HighestBar 慣例）。"""
    window = vals[-n:]
    m = max(window)
    for back, v in enumerate(reversed(window)):  # back=0 是今日，由新到舊
        if v == m:
            return back
    return 0


def cup_handle(highs, lows, closes) -> dict | None:
    """符合杯柄型態→回傳畫線錨點 dict，否則 None。陣列需 >= 377 根且等長。"""
    n = len(highs)
    if n < LOOKBACK or len(lows) != n or len(closes) != n:
        return None
    v2 = _highest(highs, LOOKBACK)          # 左緣：近 377 天最高
    v3 = _highest(highs, 55)                # 右緣：近 55 天最高
    low55 = _lowest(lows, 55)
    rng = v3 - low55
    percent_r = (closes[-1] - low55) / rng * 100 if rng else 0.0
    cond1 = (v2 > v3
             and _highest(highs, 13) < v3
             and _lowest(lows, 8) > _lowest(lows, 21)
             and percent_r - 50 > 0)                     # Call_5W > 0
    hb377 = _highest_bar(highs, LOOKBACK)
    hb55 = _highest_bar(highs, 55)
    cond2 = hb377 - hb55 > 55
    if not (cond1 and cond2):
        return None
    return {
        "left_idx": n - 1 - hb377, "left_price": v2,     # 趨勢線起點（左緣）
        "right_idx": n - 1 - hb55, "right_price": v3,     # 趨勢線終點（右緣）
        "resistance": v3,                                 # 壓力線價位
        "percent_r": round(percent_r, 1),
    }


def cup_handle_signals(highs, lows, closes):
    """向量化版：對整段歷史回傳 (每日訊號布林陣列, 每日壓力位陣列)，供回測掃描。

    與 cup_handle() 的逐日判定完全一致（有等價性測試鎖住）；HighestBar 同值取最近一次。
    """
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view as swv

    H = np.asarray(highs, dtype=float)
    L = np.asarray(lows, dtype=float)
    C = np.asarray(closes, dtype=float)
    n = len(H)
    sig = np.zeros(n, dtype=bool)
    res = np.full(n, np.nan)
    if n < LOOKBACK or len(L) != n or len(C) != n:
        return sig, res

    def tail(arr, w):
        """rolling 結果對齊到「視窗結尾＝當日」的完整長度陣列（前段補 NaN）。"""
        out = np.full(n, np.nan)
        out[w - 1:] = arr
        return out

    w377, w55, w13 = swv(H, LOOKBACK), swv(H, 55), swv(H, 13)
    M377, M55, M13 = tail(w377.max(1), LOOKBACK), tail(w55.max(1), 55), tail(w13.max(1), 13)
    m8 = tail(swv(L, 8).min(1), 8)
    m21 = tail(swv(L, 21).min(1), 21)
    m55 = tail(swv(L, 55).min(1), 55)
    # HighestBar：反轉視窗取 argmax＝「幾根前」（同值取最近，與 _highest_bar 一致）
    hb377 = tail(w377[:, ::-1].argmax(1).astype(float), LOOKBACK)
    hb55 = tail(w55[:, ::-1].argmax(1).astype(float), 55)
    rng = M55 - m55
    with np.errstate(invalid="ignore", divide="ignore"):
        pr = np.where(rng > 0, (C - m55) / rng * 100, 0.0)
    valid = ~np.isnan(M377)
    cond1 = (M377 > M55) & (M13 < M55) & (m8 > m21) & (pr - 50 > 0)
    cond2 = (hb377 - hb55) > 55
    sig = np.where(valid, cond1 & cond2, False)
    return sig, M55


def screen_cup_handle(ohlc_by_code: dict) -> list[dict]:
    """對 {code: {name, dates[], highs[], lows[], closes[]}} 逐檔篩杯柄，回符合清單（附錨點）。"""
    out = []
    for code, d in ohlc_by_code.items():
        sig = cup_handle(d.get("highs") or [], d.get("lows") or [], d.get("closes") or [])
        if sig:
            dates = d.get("dates") or []
            sig["left_date"] = dates[sig["left_idx"]] if sig["left_idx"] < len(dates) else None
            sig["right_date"] = dates[sig["right_idx"]] if sig["right_idx"] < len(dates) else None
            sig["last_close"] = (d.get("closes") or [None])[-1]
            out.append({"code": code, "name": d.get("name"), **sig})
    out.sort(key=lambda m: -(m.get("percent_r") or 0))  # 強度（收盤位置）高→低
    return out
