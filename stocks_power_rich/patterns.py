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
