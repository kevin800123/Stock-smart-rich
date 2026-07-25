"""FRED（St. Louis Fed）keyless 每日歷史 CSV。

Yahoo（yfinance＋v8 chart API）從 2026-07 起被 Zeabur 出站 IP 429 擋死（見 intl.py 開頭
說明），VIX／日經指數因此在雲端永遠是 NULL。FRED 的 fredgraph.csv 端點不用金鑰、逐日
附日期，且與 TradingView 同日數值交叉驗證一致（NIKKEI225 07-24=64611.15 vs TradingView
64610.93），能穩定頂替這兩個 series——但沒有費半(SOX)、KOSPI 的 series，這兩檔本輪不靠
FRED（見 intl.py 的 KOSPI 帶日期讀取、以及 sox 維持現狀不修的理由）。
"""
import httpx

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# 對齊 config.py INTL_TICKERS 的 key，只列本站實際會用 FRED 頂替的兩檔。
FRED_SERIES = {"vix": "VIXCLS", "n225": "NIKKEI225"}


def parse_fred_csv(text: str) -> dict:
    """FRED CSV（`DATE,<SERIES_ID>` 表頭 + 逐日列）→ {iso_date: float}。

    "."（或空字串）是 FRED 的「當天無觀測值」標記（假日等），跳過而非當成 0。
    單行格式不對（欄位數不對、值非數字）只跳過那一行，不讓整批解析失敗。
    """
    out: dict[str, float] = {}
    lines = (text or "").strip().splitlines()
    for line in lines[1:]:  # 第一行是表頭 DATE,<SERIES_ID>
        parts = line.split(",")
        if len(parts) != 2:
            continue
        ds, raw = parts[0].strip(), parts[1].strip()
        if not ds or not raw or raw == ".":
            continue
        try:
            out[ds] = float(raw)
        except ValueError:
            continue
    return out


def fetch_fred_series(series_id: str, start_date: str) -> dict:
    """抓單一 FRED series 從 start_date 起的每日歷史。非 200／例外一律回空 dict。"""
    try:
        r = httpx.get(FRED_CSV_URL, params={"id": series_id, "cosd": start_date}, timeout=15)
        if r.status_code == 200:
            return parse_fred_csv(r.text)
    except Exception:  # noqa: BLE001
        pass
    return {}
