from stocks_power_rich import patterns


def _make_cup_handle(close=89.0, handle_dip=84.0, cup_dip_idx=200):
    """造一組符合亞當杯柄（含品質濾網）的 400 根 OHLC：
    左緣(老高100@50) → 杯(底75@cup_dip_idx，杯深25%) → 右緣(近高90@360)
    → 柄(回檔 handle_dip 守住杯身中點 (75+90)/2=82.5) → 收盤 close 貼近壓力90。
    預設 %R(55)=(89-84)/(90-84)=83.3、距壓力 1.1%。"""
    n = 400
    highs = [88.0] * n
    lows = [85.0] * n
    closes = [86.0] * n
    highs[50] = 100.0            # 左緣：377 窗最高（349 根前）
    highs[360] = 90.0            # 右緣：55 窗最高（39 根前）
    lows[cup_dip_idx] = 75.0     # 杯底（深 25%）
    lows[385] = handle_dip       # 柄低點：21 窗內、8 窗外（LOWEST(8)>LOWEST(21) 成立）
    highs[-1] = max(88.0, close)
    closes[-1] = close
    return highs, lows, closes


def test_cup_handle_detects_and_anchors():
    highs, lows, closes = _make_cup_handle()
    sig = patterns.cup_handle(highs, lows, closes)
    assert sig is not None
    assert sig["left_idx"] == 50 and sig["left_price"] == 100.0    # 趨勢線左緣
    assert sig["right_idx"] == 360 and sig["right_price"] == 90.0  # 趨勢線右緣
    assert sig["resistance"] == 90.0
    assert sig["percent_r"] == 83.3
    assert sig["cup_depth_pct"] == 25.0                            # (100-75)/100
    assert sig["dist_pct"] == 1.1                                  # (90-89)/90


def test_cup_handle_rejects_flat_and_short():
    assert patterns.cup_handle([10.0] * 400, [9.0] * 400, [9.5] * 400) is None  # 無型態
    assert patterns.cup_handle([10.0] * 100, [9.0] * 100, [9.5] * 100) is None  # 不足 377 根


def test_cup_handle_rejects_when_handle_not_pulled_back():
    """近 13 天又創 55 天新高（沒回檔＝沒柄）→ 不符。"""
    highs, lows, closes = _make_cup_handle()
    highs[399] = 95.0   # 今日創 55 窗新高，HIGHEST(H,13) 不再 < HIGHEST(H,55)
    assert patterns.cup_handle(highs, lows, closes) is None


def test_cup_handle_rejects_shallow_cup():
    """杯深 <12%（橫盤假杯）→ 拒絕；13% 則通過（證明是杯深條件在起作用）。"""
    def variant(floor):
        highs, lows, closes = _make_cup_handle()
        for i in range(50, 361):
            lows[i] = floor              # 整個杯區墊高 → 杯深 = (100-floor)/100
        for i in range(361, 400):
            lows[i] = 89.8               # 柄守住中點 (floor+90)/2
        lows[385] = 89.6                 # LOWEST(8)=89.8 > LOWEST(21)=89.6
        closes[-1] = 89.8; highs[-1] = 89.8   # %R 高、距壓力 0.2%
        return highs, lows, closes
    assert patterns.cup_handle(*variant(89.0)) is None       # 深 11% → 拒
    assert patterns.cup_handle(*variant(87.0)) is not None   # 深 13% → 過


def test_cup_handle_rejects_deep_handle():
    """柄低點跌破杯身中點 82.5 → 拒絕；83（守住）→ 通過。"""
    assert patterns.cup_handle(*_make_cup_handle(handle_dip=80.0)) is None
    assert patterns.cup_handle(*_make_cup_handle(handle_dip=83.0)) is not None


def test_cup_handle_rejects_far_from_resistance():
    """收盤距壓力 >10% → 拒絕（杯底移入 55 窗、min_r=0，把 %R 條件隔離掉）。"""
    highs, lows, closes = _make_cup_handle(close=79.0, cup_dip_idx=350)   # 距壓力 12.2%
    assert patterns.cup_handle(highs, lows, closes, min_r=0) is None
    highs, lows, closes = _make_cup_handle(close=82.0, cup_dip_idx=350)   # 距壓力 8.9%
    assert patterns.cup_handle(highs, lows, closes, min_r=0) is not None


def test_cup_handle_min_r_threshold():
    """%R 門檻參數化：預設 70 拒絕 %R=33 的個股，降到 30 則通過。"""
    highs, lows, closes = _make_cup_handle(close=86.0)   # %R=(86-84)/6=33.3
    assert patterns.cup_handle(highs, lows, closes) is None
    assert patterns.cup_handle(highs, lows, closes, min_r=30) is not None


def test_atr_true_range_includes_gaps():
    """ATR 用 True Range（含跳空缺口），非單純 H-L；資料不足/含 None 回 None。"""
    highs = [10.0, 12.0, 11.0]
    lows = [9.0, 10.0, 9.5]
    closes = [9.5, 11.0, 10.0]
    # TR1=max(12-10, |12-9.5|, |10-9.5|)=2.5；TR2=max(1.5, 0, 1.5)=1.5 → ATR(2)=2.0
    assert patterns.atr(highs, lows, closes, n=2) == 2.0
    # 向下跳空：整根 K 在昨收下方 → TR 取 |L-昨收|=2.5，而非 H-L=0.5
    assert patterns.atr([12.0, 9.0], [10.0, 8.5], [11.0, 8.6], n=1) == 2.5
    assert patterns.atr([10.0], [9.0], [9.5], n=14) is None       # 不足 n+1 根
    assert patterns.atr([10.0, None], [9.0, 8.0], [9.5, 8.5], n=1) is None  # 缺值防呆


def test_screen_runs_over_multiple_codes():
    highs, lows, closes = _make_cup_handle()
    data = {
        "2330": {"name": "台積電", "highs": highs, "lows": lows, "closes": closes},
        "9999": {"name": "平盤股", "highs": [10.0] * 400, "lows": [9.0] * 400, "closes": [9.5] * 400},
    }
    out = patterns.screen_cup_handle(data)
    assert [m["code"] for m in out] == ["2330"]
    assert out[0]["name"] == "台積電" and out[0]["right_price"] == 90.0


def test_signals_equivalent_to_scalar_on_random_walks():
    """向量化 cup_handle_signals 的最後一日判定，必須與逐日 cup_handle 完全一致。"""
    import random
    rng = random.Random(42)
    agree = 0
    for _ in range(60):
        n = 420
        px = [100.0]
        for _ in range(n - 1):
            px.append(max(1.0, px[-1] * (1 + rng.uniform(-0.05, 0.052))))
        highs = [p * (1 + rng.uniform(0, 0.02)) for p in px]
        lows = [p * (1 - rng.uniform(0, 0.02)) for p in px]
        sig, res = patterns.cup_handle_signals(highs, lows, px)
        scalar = patterns.cup_handle(highs, lows, px)
        assert bool(sig[-1]) == (scalar is not None)
        if scalar is not None:
            assert abs(res[-1] - scalar["resistance"]) < 1e-9
        agree += 1
    assert agree == 60


def test_backtest_cup_breakout_and_stats():
    """合成杯柄→隔日突破→續漲：回測應記 1 筆交易且各持有期報酬為正。"""
    from stocks_power_rich import backtest

    highs, lows, closes = _make_cup_handle()      # 400 根，訊號在最後一日、壓力=90
    px = 92.0
    for i in range(25):                            # 加 25 天：立刻突破後每日 +1%
        closes.append(round(px, 2)); highs.append(round(px * 1.01, 2)); lows.append(round(px * 0.99, 2))
        px *= 1.01
    dates = [f"D{i:03d}" for i in range(len(closes))]
    data = {"2330": {"name": "台積電", "dates": dates, "highs": highs, "lows": lows, "closes": closes}}
    r = backtest.backtest_cup(data)
    assert r["signals"] >= 1 and r["trades_n"] == 1 and r["expired"] == r["signals"] - 1
    tr = r["trades"][0]
    assert tr["entry"] > tr["resistance"] == 90.0
    for h in (5, 10, 20):
        assert tr[f"ret{h}"] > 0
    assert r["horizons"]["5"]["win_rate"] == 100.0


def test_backtest_cup_counts_expired_when_no_breakout():
    from stocks_power_rich import backtest

    highs, lows, closes = _make_cup_handle()
    for _ in range(25):                            # 加 25 天橫盤(收 80 < 壓力 90)：不突破
        closes.append(80.0); highs.append(84.0); lows.append(76.0)
    dates = [f"D{i:03d}" for i in range(len(closes))]
    data = {"9999": {"name": "盤整股", "dates": dates, "highs": highs, "lows": lows, "closes": closes}}
    r = backtest.backtest_cup(data)
    assert r["trades_n"] == 0 and r["expired"] >= 1
