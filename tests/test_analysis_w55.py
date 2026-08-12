"""W55 翻多訊號：PercentR(55) > 50 → 1，否則 0。

門檻與方向已用真實歷史股價反推驗證（見 CLAUDE.md「W55」段）：用 yfinance 抓 8 檔股票
2026-06-30 收盤前 55 天 OHLC，自行算出的 PercentR(55) 與同日 XQ CSV 的 W55 欄位 8/8 全部
對上，包含此處鎖住的 2330（多方）與 1101（空方）兩個真實案例。
"""
from stocks_power_rich.analysis import w55_signal


def _flat(n, high, low, close_last):
    highs = [high] * n
    lows = [low] * n
    closes = [low] * n
    closes[-1] = close_last
    return highs, lows, closes


def test_w55_signal_bullish_above_midpoint():
    highs, lows, closes = _flat(55, high=100.0, low=0.0, close_last=60.0)  # %R=60 > 50
    assert w55_signal(highs, lows, closes) == 1.0


def test_w55_signal_bearish_below_midpoint():
    highs, lows, closes = _flat(55, high=100.0, low=0.0, close_last=40.0)  # %R=40 < 50
    assert w55_signal(highs, lows, closes) == 0.0


def test_w55_signal_exactly_at_midpoint_is_bearish():
    """剛好卡在 50 的邊界案例：實測樣本裡沒有出現過（連續價格落在整數 50.000 的機率趨近於零），
    這裡採用嚴格 > 50 才算翻多（符合原 XS `Call_5W=PercentR(55)-50` 的慣例：條件式一般寫成
    `>0` 而非 `>=0`）——不是從樣本驗證出來的，是沿用 XS 慣例的假設，如遇實測不符需要重新檢視。"""
    highs, lows, closes = _flat(55, high=100.0, low=0.0, close_last=50.0)
    assert w55_signal(highs, lows, closes) == 0.0


def test_w55_signal_real_case_2330_bullish():
    """2330 台積電 2026-06-30：high55=2535.0, low55=1969.74, close=2410.0 →
    PercentR(55)=77.89（yfinance 實測），CSV 當日 W55=1，方向一致。"""
    n = 55
    highs = [2000.0] * n
    lows = [2000.0] * n
    highs[10] = 2535.0
    lows[20] = 1969.74
    closes = [2000.0] * n
    closes[-1] = 2410.0
    assert w55_signal(highs, lows, closes) == 1.0


def test_w55_signal_real_case_1101_bearish():
    """1101 台泥 2026-06-30：high55=24.85, low55=22.77, close=23.25 →
    PercentR(55)=23.26（yfinance 實測），CSV 當日 W55=0，方向一致。"""
    n = 55
    highs = [23.0] * n
    lows = [23.0] * n
    highs[10] = 24.85
    lows[20] = 22.77
    closes = [23.0] * n
    closes[-1] = 23.25
    assert w55_signal(highs, lows, closes) == 0.0


def test_w55_signal_insufficient_data_returns_none():
    highs, lows, closes = _flat(30, high=100.0, low=0.0, close_last=60.0)
    assert w55_signal(highs, lows, closes) is None


def test_w55_signal_mismatched_lengths_returns_none():
    assert w55_signal([1.0] * 55, [1.0] * 55, [1.0] * 54) is None
