from stocks_power_rich import analysis


def test_change_histogram_buckets_by_floor():
    # −2.5% 落在 [−3,−2) 桶（下界 −3）；+0.4% 落在 [0,1) 桶（下界 0，屬上漲）
    h = analysis.change_histogram([-2.5, 0.4, 0.4, 3.1])
    counts = {b["bucket"]: b["count"] for b in h["buckets"]}
    assert counts[-3] == 1        # −2.5 → 下界 −3
    assert counts[0] == 2         # 兩個 +0.4
    assert counts[3] == 1         # +3.1 → 下界 3
    assert h["up"] == 3 and h["down"] == 1 and h["flat"] == 0


def test_change_histogram_zero_is_flat_not_up():
    h = analysis.change_histogram([0.0, 0.0, 1.0, -1.0])
    assert h["flat"] == 2         # 剛好 0% 算平盤
    assert h["up"] == 1 and h["down"] == 1


def test_change_histogram_clamps_extremes_into_end_buckets():
    # 漲跌停 ±10 與（理論上不會有的）超界值都併入端桶，不產生 −11/+11 桶
    h = analysis.change_histogram([10.0, 9.97, -10.0, -12.0, 15.0])
    counts = {b["bucket"]: b["count"] for b in h["buckets"]}
    buckets = [b["bucket"] for b in h["buckets"]]
    assert min(buckets) == -10 and max(buckets) == 10
    assert counts[10] == 2        # 10.0 與 15.0 都併入 +10 端桶
    assert counts[-10] == 2       # −10.0 與 −12.0 都併入 −10 端桶
    assert counts[9] == 1         # 9.97 → 下界 9


def test_change_histogram_avg_and_totals():
    h = analysis.change_histogram([2.0, -1.0, 0.0])
    assert h["n"] == 3
    assert h["avg"] == 0.33        # (2−1+0)/3=0.333… → 顯示用 2 位小數


def test_change_histogram_empty():
    h = analysis.change_histogram([])
    assert h["n"] == 0 and h["avg"] is None
    assert h["up"] == 0 and h["down"] == 0 and h["flat"] == 0
    # 桶架構仍在（-10..10 共 21 個下界），全 0，方便前端固定 X 軸
    assert [b["bucket"] for b in h["buckets"]] == list(range(-10, 11))
    assert all(b["count"] == 0 for b in h["buckets"])
