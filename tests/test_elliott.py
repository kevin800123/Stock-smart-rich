from stocks_power_rich.elliott import elliott_waves

# 乾淨的上升五浪：100→120(1)→110(2)→150(3)→140(4)→170(5)
UP_IMPULSE = [100, 110, 120, 115, 110, 130, 150, 145, 140, 155, 170]


def test_labels_clean_up_impulse():
    out = elliott_waves(UP_IMPULSE, pct=0.05)
    assert [(w["index"], w["label"]) for w in out] == [(2, "1"), (4, "2"), (6, "3"), (8, "4"), (10, "5")]


def test_no_label_when_wave3_shortest():
    # w1=100、w3=40、w5=80 → 第3浪最短，違反鐵律，不標
    bad = [100, 200, 170, 210, 205, 285]
    out = elliott_waves(bad, pct=0.02)
    assert out == []


def test_no_label_when_wave4_overlaps_wave1():
    # 第4浪(P4=140)跌進第1浪頂(P1=150)價格區 → 違反鐵律，不標
    overlap = [100, 150, 130, 200, 140, 230]
    out = elliott_waves(overlap, pct=0.02)
    assert out == []


def test_no_label_when_too_few_pivots():
    assert elliott_waves([100, 101, 102, 103], pct=0.05) == []
