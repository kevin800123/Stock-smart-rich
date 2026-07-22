from stocks_power_rich.analysis import weekly_comparison

LAST = [
    {"code": "A", "name": "a", "custody": 70, "big_holder_ratio": 0.3, "industry": "半導體"},
    {"code": "B", "name": "b", "custody": 50, "big_holder_ratio": 0.2, "industry": "水泥"},
]
THIS = [
    {"code": "A", "name": "a", "custody": 75, "big_holder_ratio": 0.6, "industry": "半導體"},  # 大戶加碼
    {"code": "C", "name": "c", "custody": 40, "big_holder_ratio": 0.5, "industry": "航運"},      # 新進榜
]


def test_weekly_marks_status_and_delta():
    out = weekly_comparison(THIS, LAST)
    by = {r["code"]: r for r in out["stocks"]}
    assert by["A"]["custody_delta"] == 5
    assert by["A"]["status"] == "加速"
    assert by["C"]["status"] == "新進榜"
    assert by["B"]["status"] == "退榜"


def test_pick_weekly_pair_crosses_week_boundary():
    """本期=最新；上期=最新一筆 ISO 週早於本期的快照（上週的最後一份 CSV）。"""
    from stocks_power_rich.analysis import pick_weekly_pair
    # 2026-07-16(四)/07-17(五) 同屬 W29；07-08(三)/07-10(五) 屬 W28
    dates = ["2026-07-08", "2026-07-10", "2026-07-16", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-10")


def test_pick_weekly_pair_same_week_falls_back_to_prev_snapshot():
    """全部同週（還沒有上週資料）→ 退回前一筆，至少能比。"""
    from stocks_power_rich.analysis import pick_weekly_pair
    dates = ["2026-07-16", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-16")


def test_pick_weekly_pair_single_and_multiweek():
    from stocks_power_rich.analysis import pick_weekly_pair
    assert pick_weekly_pair(["2026-07-17"]) == ("2026-07-17", None)
    # 跨多週：取「最近的較早週」（W28 的 07-06），不是更早的 W27
    dates = ["2026-06-24", "2026-07-06", "2026-07-17"]
    assert pick_weekly_pair(dates) == ("2026-07-17", "2026-07-06")


# ===== 週報重點（LINE 週報卡用）=====

def _mk(code, name, industry, big, drop):
    return {"code": code, "name": name, "industry": industry,
            "big_holder_ratio": big, "holder_drop_ratio": drop}


def test_weekly_highlights_merges_listed_and_otc_sectors():
    """「上市半導體」與「上櫃半導體」是同一產業，分開算會互相稀釋、排名失真。"""
    from stocks_power_rich.analysis import weekly_highlights
    rows = ([_mk(f"a{i}", f"上市半導{i}", "上市半導體", 2.0, -1.0) for i in range(6)]
            + [_mk(f"b{i}", f"上櫃半導{i}", "上櫃半導體", 4.0, -1.0) for i in range(6)]
            + [_mk(f"c{i}", f"航運{i}", "上市航運業", 1.0, 0.0) for i in range(10)])
    out = weekly_highlights(rows, min_count=10, top_n=5)
    sectors = {s["sector"]: s for s in out["sectors"]}
    assert "半導體" in sectors and "上市半導體" not in sectors
    assert sectors["半導體"]["count"] == 12          # 6+6 合併後才過 10 檔門檻
    assert sectors["半導體"]["avg_score"] == 4.0     # (3.0*6 + 5.0*6)/12
    assert sectors["航運"]["avg_score"] == 1.0       # _SECTOR_ALIAS：航運業 → 航運


def test_weekly_highlights_drops_thin_sectors():
    """樣本太少的類股平均分數不穩（實測 5 檔的「其他電子」就能衝到第一）→ 設檔數門檻。"""
    from stocks_power_rich.analysis import weekly_highlights
    rows = ([_mk(f"t{i}", f"雜牌{i}", "上市其他電子", 9.0, 0.0) for i in range(4)]
            + [_mk(f"s{i}", f"實在{i}", "上市航運業", 1.0, 0.0) for i in range(10)])
    out = weekly_highlights(rows, min_count=10, top_n=5)
    assert [s["sector"] for s in out["sectors"]] == ["航運"]   # 4 檔的類股不入榜
    # 門檻放寬就會進來，且因分數高排第一
    loose = weekly_highlights(rows, min_count=1, top_n=5)
    assert [s["sector"] for s in loose["sectors"]] == ["其他電子", "航運"]


def test_weekly_highlights_top_stocks_by_signal_score():
    """個股依籌碼訊號分數（大戶增比 − 人數降比）排序——不限「加速」狀態。

    實測本週「加速」0 檔，沿用舊篩選會長期是空榜。
    """
    from stocks_power_rich.analysis import weekly_highlights
    rows = [
        _mk("1101", "小丘", "上市電子零組件", 7.7, -19.03),   # 26.73
        _mk("2222", "聚積", "上櫃半導體", 8.75, -17.91),      # 26.66
        _mk("3333", "普通", "上市航運業", 1.0, 0.0),          # 1.0
        _mk("4444", "退步", "上市化工", -2.0, 3.0),           # -5.0
    ]
    out = weekly_highlights(rows, min_count=1, top_n=3)
    names = [s["name"] for s in out["stocks"]]
    assert names == ["小丘", "聚積", "普通"]                  # 依分數 desc，取 3 檔
    top = out["stocks"][0]
    assert top["score"] == 26.73                              # 7.7 − (−19.03)
    assert top["big_holder_ratio"] == 7.7 and top["holder_drop_ratio"] == -19.03
    assert top["sector"] == "電子零組件"                       # 附正規化類股名


def test_weekly_highlights_empty_and_missing_fields():
    from stocks_power_rich.analysis import weekly_highlights
    assert weekly_highlights([]) == {"sectors": [], "stocks": []}
    # 欄位全缺 → 分數視為 0，不拋例外
    out = weekly_highlights([{"code": "9999", "name": "空白"}], min_count=1, top_n=5)
    assert out["stocks"][0]["score"] == 0.0
    assert out["sectors"][0]["sector"] == "未分類"
