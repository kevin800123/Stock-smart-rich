from stocks_power_rich import patterns


def _make_cup_handle():
    """造一組符合杯柄的 400 根 OHLC：左緣(老高100)→杯→右緣(近高90)→柄(回檔守穩)。"""
    n = 400
    highs = [10.0] * n
    lows = [8.0] * n
    closes = [9.0] * n
    highs[50] = 100.0                       # 左緣：377 窗最高、很老（bars-ago=349）
    highs[360] = 90.0                       # 右緣：55 窗最高（bars-ago=39）
    for i in range(345, 400):
        lows[i] = 70.0                       # 杯/柄區低點約 70
    lows[380] = 60.0                         # 21 窗內、8 窗外的較深低點
    for i in range(387, 400):
        highs[i] = 85.0                      # 柄：近 13 天高 < 90
    closes[-1] = 80.0                        # PercentR(55)=(80-60)/(90-60)=66.7% > 50
    return highs, lows, closes


def test_cup_handle_detects_and_anchors():
    highs, lows, closes = _make_cup_handle()
    sig = patterns.cup_handle(highs, lows, closes)
    assert sig is not None
    assert sig["left_idx"] == 50 and sig["left_price"] == 100.0    # 趨勢線左緣
    assert sig["right_idx"] == 360 and sig["right_price"] == 90.0  # 趨勢線右緣
    assert sig["resistance"] == 90.0
    assert sig["percent_r"] == 66.7


def test_cup_handle_rejects_flat_and_short():
    assert patterns.cup_handle([10.0] * 400, [9.0] * 400, [9.5] * 400) is None  # 無型態
    assert patterns.cup_handle([10.0] * 100, [9.0] * 100, [9.5] * 100) is None  # 不足 377 根


def test_cup_handle_rejects_when_handle_not_pulled_back():
    """近 13 天又創 55 天新高（沒回檔＝沒柄）→ 不符。"""
    highs, lows, closes = _make_cup_handle()
    highs[399] = 95.0   # 今日創 55 窗新高，HIGHEST(H,13) 不再 < HIGHEST(H,55)
    assert patterns.cup_handle(highs, lows, closes) is None


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
