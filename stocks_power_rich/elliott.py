"""簡易艾略特波浪偵測：用 zigzag 找轉折樞紐，依三大鐵律驗證最近的五浪推動段。

三大不可違反鐵律：
  1. 第2浪不可跌破第1浪起點（回檔 < 100%）。
  2. 第3浪不可是第1、3、5浪中最短的。
  3. 第4浪不可重疊第1浪的價格區間。
只有最近的轉折序列同時符合上述規則時，才標註 1~5 浪；否則不標。
"""


def _zigzag(vals: list, pct: float) -> list:
    """回傳交替的轉折樞紐索引（以反轉幅度 ≥ pct 認定）。"""
    n = len(vals)
    if n == 0:
        return []
    pivots: list[int] = []
    piv_idx, piv_val, trend = 0, vals[0], 0
    for i in range(1, n):
        v = vals[i]
        if trend == 0:
            if piv_val and abs(v - piv_val) / abs(piv_val) >= pct:
                trend = 1 if v > piv_val else -1
                pivots.append(piv_idx)
                piv_idx, piv_val = i, v
        elif trend == 1:
            if v > piv_val:
                piv_idx, piv_val = i, v
            elif piv_val and (piv_val - v) / abs(piv_val) >= pct:
                pivots.append(piv_idx)
                trend, piv_idx, piv_val = -1, i, v
        else:  # trend == -1
            if v < piv_val:
                piv_idx, piv_val = i, v
            elif piv_val and (v - piv_val) / abs(piv_val) >= pct:
                pivots.append(piv_idx)
                trend, piv_idx, piv_val = 1, i, v
    pivots.append(piv_idx)
    return pivots


def _impulse_labels(closes: list, seg: list) -> list:
    """驗證 6 個樞紐是否構成合法五浪推動，是則回 1~5 標註，否則回 []。"""
    p = [closes[i] for i in seg]
    up = p[1] > p[0]
    if up:
        shape = p[1] > p[0] and p[2] < p[1] and p[3] > p[2] and p[4] < p[3] and p[5] > p[4]
        rule2, rule3top, rule4 = p[2] > p[0], p[3] > p[1], p[4] > p[1]
    else:
        shape = p[1] < p[0] and p[2] > p[1] and p[3] < p[2] and p[4] > p[3] and p[5] < p[4]
        rule2, rule3top, rule4 = p[2] < p[0], p[3] < p[1], p[4] < p[1]
    w1, w3, w5 = abs(p[1] - p[0]), abs(p[3] - p[2]), abs(p[5] - p[4])
    rule3short = not (w3 < w1 and w3 < w5)
    if not (shape and rule2 and rule3top and rule4 and rule3short):
        return []
    return [{"index": seg[k + 1], "label": str(k + 1)} for k in range(5)]


def _abc_labels(closes: list, seg: list, up: bool) -> list:
    """seg=[第5浪頂, A, B, C] 四樞紐；修正浪方向與推動相反才標 A-B-C。"""
    p = [closes[i] for i in seg]
    ok = (p[1] < p[0] and p[2] > p[1] and p[3] < p[2]) if up else (p[1] > p[0] and p[2] < p[1] and p[3] > p[2])
    if not ok:
        return []
    return [{"index": seg[1], "label": "A"}, {"index": seg[2], "label": "B"}, {"index": seg[3], "label": "C"}]


def elliott_waves(closes: list, pct: float = 0.05) -> list:
    """偵測最近一段五浪推動（1~5），若其後接合法修正浪再加標 A-B-C；不符合則回 []。"""
    piv = _zigzag(closes, pct)
    if len(piv) >= 9:
        imp = _impulse_labels(closes, piv[-9:-3])
        if imp:
            up = closes[piv[-8]] > closes[piv[-9]]
            abc = _abc_labels(closes, piv[-4:], up)
            if abc:
                return imp + abc
    if len(piv) >= 6:
        return _impulse_labels(closes, piv[-6:])
    return []
