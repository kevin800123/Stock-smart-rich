"""K 線資料：個股 OHLC（yfinance）、大盤指數 OHLC（yfinance ^TWII）、
台指期 OHLC（由每日 market_daily 快照累積/聚合）。

candles 每筆順序為 [open, close, low, high]（ECharts candlestick 規格）。
"""
import yfinance as yf

from .. import elliott

# 指數代碼對應
INDEX_TICKERS = {"taiex": "^TWII"}
# interval → 抓取期間（1h 為小時K，受 yfinance 期間限制取近一月）
INTERVAL_PERIOD = {"1h": "1mo", "1d": "6mo", "1wk": "2y", "1mo": "5y"}


def _fmt_dt(d, interval: str) -> str:
    return d.strftime("%Y-%m-%d %H:%M" if interval == "1h" else "%Y-%m-%d")


import datetime as _dt

_MAX_DOD_JUMP = 0.35   # 台股個股單日漲跌幅上限 ±10%，日對日收盤跳動 >35% 必為壞值（0/半值/資料錯）


SHARES_PER_LOT = 1000   # 台股一張＝1000 股（個股 K 線量能單位統一用「張」）


_ADJACENT_MAX_GAP_DAYS = 5   # 週末+一天假＝3 天；超過 5 天代表序列有洞，跨洞不可套「單日」門檻


def _sanitize_series(dates: list, candles: list, volumes: list) -> tuple:
    """丟棄明顯壞列，避免 MA/波浪被污染：任一 OHLC 非正、high<low、或收盤對「前一筆有效
    收盤」跳動 >35%（yfinance/官方源偶發 0 或半值時會出現）。回傳過濾後的三個並列陣列。

    **跳動門檻有兩個必要的護欄，少一個都會把好資料整段丟掉**（實測 3022 踩到）：

    1. `just_rejected`：被拒時 `last` 不會更新，所以一根壞值會讓後面**每一根**都相對它
       跳太多而連鎖被拒。實測：41.7 這根壞值相對前一根只跌 30%（低於門檻）因而被接受，
       之後 5 個月的真實資料（~60→95.9）全數被丟棄，圖表就停在 41.7、停在四月，看起來
       像「資料沒更新」，其實資料庫是完整的。所以**一旦拒絕過一根，下一根就不再套門檻**
       ——孤立壞值只丟自己，序列能回到真實水位。
    2. 日期相鄰才套門檻：stock_ohlc 的覆蓋度取決於回補跑到哪，中間有洞是常態，而跨越
       數月的價格變動本來就可能遠超過 35%，當成壞值丟掉會讓「補完資料」反而看不到東西。
    """
    out_d, out_c, out_v = [], [], []
    last = None
    last_date = None
    just_rejected = False
    for i, c in enumerate(candles):
        o, cl, lo, hi = c
        # **NaN 必須明確擋掉，不能只靠 `None in c` 與大小比較**：NaN 不是 None，而且
        # 所有跟 NaN 的比較都回 False（`nan <= 0`、`hi < lo`、跳動門檻全部不成立），
        # 於是壞列一路通過所有守衛，直到 FastAPI 序列化才炸成
        # `ValueError: Out of range float values are not JSON compliant: nan` → 整個
        # 端點 500、該股 K 線完全打不開。實測 yfinance 偶爾會給出這種未完成的 bar
        # （同一支股票早上正常、下午就 500），屬於間歇性故障，很難事後重現。
        # `v != v` 是 NaN 的標準判定，不必 import math 也不挑型別。
        if any(v is None or v != v for v in c) or o <= 0 or cl <= 0 or lo <= 0 or hi <= 0 or hi < lo:
            continue
        if last is not None and last > 0 and not just_rejected and _adjacent(last_date, dates[i])                 and abs(cl / last - 1) > _MAX_DOD_JUMP:
            just_rejected = True
            continue
        just_rejected = False
        out_d.append(dates[i]); out_c.append(c); out_v.append(volumes[i])
        last = cl; last_date = dates[i]
    return out_d, out_c, out_v


def _adjacent(prev_date, cur_date) -> bool:
    """兩根 K 棒是否為「相鄰交易日」——只有相鄰時，單日跳動門檻才有意義。
    日期解析不出來（週/月聚合標籤等）就當作相鄰，維持既有行為、不放寬檢查。"""
    if not prev_date or not cur_date:
        return True
    try:
        a = _dt.date.fromisoformat(str(prev_date)[:10])
        b = _dt.date.fromisoformat(str(cur_date)[:10])
    except ValueError:
        return True
    return (b - a).days <= _ADJACENT_MAX_GAP_DAYS


def _pack_candles(dates: list, candles: list, volumes: list) -> dict:
    """並列陣列 → 組 K 線輸出（含各門檻波浪）。呼叫端須先自行清洗（週/月聚合後不宜再套
    日對日跳動門檻，因整週漲跌可能合理 >35%）。"""
    closes = [c[1] for c in candles]
    waves = {}
    if len(closes) >= 6:
        for pct_int in range(2, 16):
            waves[str(pct_int)] = elliott.elliott_waves(closes, pct_int / 100.0)
    return {"dates": dates, "candles": candles, "volumes": volumes, "waves": waves}


def _df_to_candles(df, interval: str = "1d") -> dict:
    dates = [_fmt_dt(d, interval) for d in df.index]
    candles = [[float(r.Open), float(r.Close), float(r.Low), float(r.High)] for r in df.itertuples()]
    volumes = [float(getattr(r, "Volume", 0) or 0) for r in df.itertuples()]
    return _pack_candles(*_sanitize_series(dates, candles, volumes))


def _history(code: str, period: str, interval: str, tries: int = 3):
    """抓 yfinance 歷史；雲端 IP 常被 Yahoo 偶發限流，故重試數次，全失敗回空 df。"""
    import time

    import pandas as pd

    for i in range(tries):
        try:
            df = yf.Ticker(code).history(period=period, interval=interval)
            if df is not None and not df.empty:
                return df
        except Exception:  # noqa: BLE001 — 限流/錯誤 → 重試
            pass
        if i < tries - 1:
            time.sleep(0.8)
    return pd.DataFrame()


def fetch_kline(code: str, period: str = "1y", interval: str = "1d") -> dict:
    df = _history(code, period, interval)
    # 上櫃/興櫃股 .TW 查不到 → 改試 .TWO（CSV 一律給 .TW）
    if (df is None or df.empty) and code.endswith(".TW"):
        alt = code[:-3] + ".TWO"
        alt_df = _history(alt, period, interval)
        if alt_df is not None and not alt_df.empty:
            code, df = alt, alt_df
    if df is None or df.empty:
        return {"code": code, "dates": [], "candles": [], "volumes": [], "waves": {}}
    out = _df_to_candles(df, interval)
    # **個股 K 線的量統一成「張」**：yfinance 的 Volume 是股數，後備來源 stock_ohlc 的
    # volume_lots 是張。同一張圖在兩個環境會取到不同來源（雲端 yfinance 被擋一律走後備），
    # 不統一就會變成「本機正常、雲端數字差 1000 倍」這種只在部署後才發現的錯。
    # 只在這裡除——_df_to_candles 是與指數共用的，指數量能另有口徑，跟著除會改掉既有刻度。
    out["volumes"] = [v / SHARES_PER_LOT for v in out.get("volumes") or []]
    return {"code": code, **out}


def fetch_index_kline(symbol: str, interval: str = "1d") -> dict:
    """大盤指數 K 線（目前支援 taiex=^TWII）。"""
    ticker = INDEX_TICKERS.get(symbol)
    empty = {"symbol": symbol, "dates": [], "candles": [], "volumes": [], "waves": {}}
    if not ticker:
        return empty
    period = INTERVAL_PERIOD.get(interval, "6mo")
    df = _history(ticker, period, interval)
    if df is None or df.empty:
        return empty
    return {"symbol": symbol, **_df_to_candles(df, interval)}


def merge_tail(base: dict, rows: list, interval: str = "1d") -> dict:
    """把 rows 裡「比 base 更新」的日期補到 base 的尾巴，回新的 K 線輸出。

    **備援不能是全有全無的。** 原本 `/api/index/kline` 只在主來源（yfinance）回不到
    5 根時才改用官方 TWSE，於是主來源只是「落後一天」時完全沒有補救：實測
    2026-08-06 08:17，yfinance `^TWII` 只到 08-04，而 TWSE MI_5MINS_HIST 已有 08-05
    （收盤 44611.6，與 `market_daily` 一致）。結果就是「大盤×籌碼對照」的籌碼窗格
    有 08-05、K 線卻沒有——最新一天看不到指數，而那通常正是使用者最想看的一天。

    只補**嚴格比 base 最後一天更新**的列，所以既有的日期不會被覆蓋也不會重複；
    缺收盤價的列直接跳過（補不出 K 棒，半根比沒有更誤導）。
    沒有東西可補時原樣回傳同一個物件，不做多餘的重算與重跑波浪。
    """
    dates, candles = base.get("dates") or [], base.get("candles") or []
    if not dates or not rows:
        return base
    last = dates[-1]
    extra = sorted((r for r in rows
                    if r.get("date") and r["date"] > last and r.get("close") is not None),
                   key=lambda r: r["date"])
    if not extra:
        return base
    vols = base.get("volumes") or []
    merged = [{"date": d, "open": c[0], "close": c[1], "low": c[2], "high": c[3],
               "volume": vols[i] if i < len(vols) else 0}
              for i, (d, c) in enumerate(zip(dates, candles))]
    merged += [{"date": r["date"], "open": r.get("open"), "high": r.get("high"),
                "low": r.get("low"), "close": r["close"], "volume": r.get("volume") or 0}
               for r in extra]
    return ohlc_candles(merged, interval)


def ohlc_candles(rows: list, interval: str = "1d") -> dict:
    """通用 OHLC 組 K 線：rows 含 date/open/high/low/close(/volume)；週/月以 pandas 聚合。"""
    import pandas as pd

    recs = [{"date": r["date"], "o": r["open"], "h": r["high"], "l": r["low"],
             "c": r["close"], "v": r.get("volume") or 0}
            for r in rows if r.get("close") is not None]
    if not recs:
        return {"dates": [], "candles": [], "volumes": [], "waves": {}}

    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    # 先在日線層級清洗壞列（0/半值），避免污染 MA/波浪與週月聚合的 min/max
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in df.itertuples()]
    volumes = [float(r.v) for r in df.itertuples()]
    dates, candles, volumes = _sanitize_series(dates, candles, volumes)

    if interval in ("1wk", "1mo"):
        clean = pd.DataFrame({"date": pd.to_datetime(dates),
                              "o": [c[0] for c in candles], "c": [c[1] for c in candles],
                              "l": [c[2] for c in candles], "h": [c[3] for c in candles],
                              "v": volumes}).set_index("date")
        rule = "W" if interval == "1wk" else "ME"
        clean = clean.resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
        dates = [d.strftime("%Y-%m-%d") for d in clean.index]
        candles = [[float(r.o), float(r.c), float(r.l), float(r.h)] for r in clean.itertuples()]
        volumes = [float(r.v) for r in clean.itertuples()]
    return _pack_candles(dates, candles, volumes)
