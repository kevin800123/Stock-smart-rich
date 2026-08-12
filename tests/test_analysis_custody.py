"""大戶增比／人數降比：反推自使用者提供的 XS 原始碼並用真實歷史集保資料驗證（見 CLAUDE.md）。

VALUE21=GetField("大戶持股比例","W",param:=400)-GetField("大戶持股比例","W",param:=400)[1];
VALUE22=((GetField("總持股人數","W")-GetField("總持股人數","W")[1])/GetField("總持股人數","W")[1])*100;

即：大戶增比＝本週 big400_pct－上週（百分點差）；人數降比＝總持股人數相對變化%。
下面四組真實案例（透過智能網股權分散表抓 2026-06-26／2026-06-18 兩週，2026-06-30 CSV 快照
當時「本週/上週」）：1101/1102/2330 三項全部與 CSV 精確吻合；2317 的大戶增比差 0.01——
TDCC 官方頁面只顯示到小數點後 2 位，兩個已四捨五入的百分比相減，最後一位偶爾會有 0.01 的
誤差，不是公式錯誤（人數降比因為是整數人數相除、不受這個誤差影響，四案例全部精確吻合）。
"""
from stocks_power_rich.analysis import custody_change


def test_custody_change_real_case_1101():
    cur = {"big400_pct": 55.52, "total_holders": 514507}
    prev = {"big400_pct": 55.41, "total_holders": 516680}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] == 0.11    # CSV 實際值 0.11
    assert out["holder_drop_ratio"] == -0.42  # CSV 實際值 -0.42


def test_custody_change_real_case_1102():
    cur = {"big400_pct": 81.8, "total_holders": 103241}
    prev = {"big400_pct": 81.48, "total_holders": 104294}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] == 0.32
    assert out["holder_drop_ratio"] == -1.01


def test_custody_change_real_case_2330():
    cur = {"big400_pct": 87.83, "total_holders": 2879938}
    prev = {"big400_pct": 87.92, "total_holders": 2835392}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] == -0.09
    assert out["holder_drop_ratio"] == 1.57


def test_custody_change_real_case_2317_rounding_noise():
    """CSV 實際大戶增比是 -0.53，這裡算出 -0.54——TDCC 官方頁面本身只顯示到小數點後 2 位，
    兩個已四捨五入的百分比相減，最後一位偶爾差 0.01，不是公式錯誤（見模組 docstring）。"""
    cur = {"big400_pct": 69.93, "total_holders": 1150251}
    prev = {"big400_pct": 70.47, "total_holders": 1126490}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] == -0.54
    assert out["holder_drop_ratio"] == 2.11   # CSV 實際值 2.11，精確吻合


def test_custody_change_missing_week_returns_none():
    assert custody_change(None, {"big400_pct": 50.0, "total_holders": 100}) == \
        {"big_holder_ratio": None, "holder_drop_ratio": None}
    assert custody_change({"big400_pct": 50.0, "total_holders": 100}, None) == \
        {"big_holder_ratio": None, "holder_drop_ratio": None}


def test_custody_change_zero_prev_holders_avoids_division_by_zero():
    cur = {"big400_pct": 50.0, "total_holders": 100}
    prev = {"big400_pct": 48.0, "total_holders": 0}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] == 2.0     # 這項不受影響，照算
    assert out["holder_drop_ratio"] is None   # 除以零，算不出回 None


def test_custody_change_partial_fields_missing():
    cur = {"big400_pct": None, "total_holders": 100}
    prev = {"big400_pct": 48.0, "total_holders": 90}
    out = custody_change(cur, prev)
    assert out["big_holder_ratio"] is None
    assert out["holder_drop_ratio"] == 11.11
