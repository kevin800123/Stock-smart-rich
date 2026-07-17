"""型態偵測：亞當／杯柄（Cup-with-Handle）。純函數，餵日 OHLC 陣列（索引 0＝最舊、-1＝今日）。

基礎條件移植自使用者提供的 XS（忠實保留）：
  VALUE2=HIGHEST(H,377)  左緣（老的大高點）
  VALUE3=HIGHEST(H,55)   右緣（近期高點）
  CONDITION1 = VALUE2>VALUE3 and HIGHEST(H,13)<HIGHEST(H,55)
               and LOWEST(L,8)>LOWEST(L,21) and PercentR(55)>=min_r
  CONDITION2 = HighestBar(H,377) - HighestBar(H,55) > 55
  （PercentR=收盤在近55天高低區間的百分位；原 XS 門檻為 >50，現參數化 min_r 預設 70）

疊加的亞當杯柄品質濾網（2026-07 收緊：原規則命中 79/607≈13% 太寬）：
  ・杯深 12%~50%：左緣高到杯底（左右緣之間最低 low）的跌幅——太淺是橫盤假杯、太深是跌爛的股
  ・柄要淺：柄低點（右緣以來最低 low）≥ 杯底與右緣的中點——柄深代表賣壓仍重
  ・接近突破口：收盤 ≥ 壓力(右緣) × 0.90——距壓力 10% 以上的訊號沒有交易價值
符合時畫：趨勢線（左緣→右緣）＋壓力線（右緣水平延伸到今日）。
"""

LOOKBACK = 377      # 需要的最少 K 棒數
MIN_R_DEFAULT = 70.0  # %R 預設門檻（前端可調 50~90；盤中哨兵/前瞻測試固定用此預設）
CUP_DEPTH_MIN = 0.12
CUP_DEPTH_MAX = 0.50
NEAR_RES_RATIO = 0.90  # 收盤須 ≥ 壓力 × 此比例


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


def cup_handle(highs, lows, closes, min_r: float = MIN_R_DEFAULT) -> dict | None:
    """符合亞當杯柄（XS 基礎條件＋品質濾網）→ 回傳畫線錨點 dict，否則 None。
    陣列需 >= 377 根且等長；min_r 為 %R(55) 門檻（預設 70）。"""
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
             and percent_r >= min_r)
    hb377 = _highest_bar(highs, LOOKBACK)
    hb55 = _highest_bar(highs, 55)
    cond2 = hb377 - hb55 > 55
    if not (cond1 and cond2):
        return None
    left_idx, right_idx = n - 1 - hb377, n - 1 - hb55
    # 品質濾網（亞當杯柄）：杯深 12~50%、柄不破杯身中點、收盤貼近壓力
    cup_low = min(lows[left_idx:right_idx + 1])           # 杯底：左右緣之間最低 low
    depth = (v2 - cup_low) / v2 if v2 else 0.0
    if not (CUP_DEPTH_MIN <= depth <= CUP_DEPTH_MAX):
        return None
    # 柄低點：右緣「之後」最低 low（右緣當日屬杯緣不算柄；HIGHEST(13)<v3 保證 hb55≥13，切片非空）
    handle_low = min(lows[right_idx + 1:])
    if handle_low < (cup_low + v3) / 2:                   # 柄破杯身中點＝賣壓仍重
        return None
    if closes[-1] < v3 * NEAR_RES_RATIO:                  # 距突破口 >10%＝無交易價值
        return None
    return {
        "left_idx": left_idx, "left_price": v2,           # 趨勢線起點（左緣）
        "right_idx": right_idx, "right_price": v3,         # 趨勢線終點（右緣）
        "resistance": v3,                                  # 壓力線價位
        "percent_r": round(percent_r, 1),
        "cup_depth_pct": round(depth * 100, 1),            # 杯深%
        "dist_pct": round((v3 - closes[-1]) / v3 * 100, 1) if v3 else None,  # 收盤距壓力%
    }


def cup_handle_signals(highs, lows, closes, min_r: float = MIN_R_DEFAULT):
    """向量化版：對整段歷史回傳 (每日訊號布林陣列, 每日壓力位陣列)，供回測掃描。

    混合式：XS 基礎條件向量化篩出候選日（稀疏），再逐候選日呼叫 scalar cup_handle()
    做品質濾網（杯深/柄深/距壓力）——以「呼叫同一 scalar」保證與逐日判定完全一致
    （有等價性測試鎖住）；HighestBar 同值取最近一次。
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
    cond1 = (M377 > M55) & (M13 < M55) & (m8 > m21) & (pr >= min_r)
    cond2 = (hb377 - hb55) > 55
    base = np.where(valid, cond1 & cond2, False)
    # 品質濾網：候選日逐一交給 scalar 確認（候選稀疏，效能無虞）
    for t in np.flatnonzero(base):
        if cup_handle(H[:t + 1], L[:t + 1], C[:t + 1], min_r=min_r) is not None:
            sig[t] = True
    return sig, M55


def atr(highs, lows, closes, n: int = 14) -> float | None:
    """ATR(n)＝近 n 根 True Range 的簡單平均（部位管理用，非 Wilder 平滑——
    停損只取粗略波動尺度，簡單平均可測試性高且差異對 2×ATR 停損無實質影響）。

    TR = max(H−L, |H−昨收|, |L−昨收|)——含跳空缺口，不是單純 H−L。
    需至少 n+1 根（首根供昨收）；長度不符或含缺值回 None。
    已知失真：除權息隔日跳空會讓 ATR 短暫偏大（停損偏寬），不另作還原調整。
    """
    m = len(highs)
    if m < n + 1 or len(lows) != m or len(closes) != m:
        return None
    window = highs[-(n + 1):] + lows[-(n + 1):] + closes[-(n + 1):]
    if any(v is None for v in window):
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(m - n, m)]
    return round(sum(trs) / n, 4)


def screen_cup_handle(ohlc_by_code: dict, min_r: float = MIN_R_DEFAULT) -> list[dict]:
    """對 {code: {name, dates[], highs[], lows[], closes[]}} 逐檔篩杯柄，回符合清單（附錨點）。"""
    out = []
    for code, d in ohlc_by_code.items():
        sig = cup_handle(d.get("highs") or [], d.get("lows") or [], d.get("closes") or [], min_r=min_r)
        if sig:
            dates = d.get("dates") or []
            sig["left_date"] = dates[sig["left_idx"]] if sig["left_idx"] < len(dates) else None
            sig["right_date"] = dates[sig["right_idx"]] if sig["right_idx"] < len(dates) else None
            sig["last_close"] = (d.get("closes") or [None])[-1]
            out.append({"code": code, "name": d.get("name"), **sig})
    out.sort(key=lambda m: -(m.get("percent_r") or 0))  # 強度（收盤位置）高→低
    return out
