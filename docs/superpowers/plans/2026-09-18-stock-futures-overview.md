# 股期概況頁 ＋ 原始保證金試算 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在側欄「市場」群組新增一頁「股期概況」，用期交所官方盤後資料呈現熱門股期、量漲跌前 20、期現價差、近 10 日排行熱力圖、未平倉增減，並提供全部股期與微台指的原始保證金試算。

**Architecture:** 純函式解析（`sources/taifex_ssf.py`）→ 每日排程抓 CSV 寫進新表 `ssf_daily`（每檔每日一列摘要）→ 兩支唯讀端點組裝畫面資料 → `web/app.js` 新視圖。保證金走另一條線（官方比例 CSV 月/日快取 ＋ `ssf_daily` 的結算價），並額外接進個股頁與自算選股表。

**Tech Stack:** Python 3.11 / FastAPI / stdlib sqlite3 / httpx / pytest；前端 vanilla JS ＋ 本地 ECharts（`web/vendor/echarts.min.js`）。

設計文件：`docs/superpowers/specs/2026-09-18-stock-futures-overview-design.md`（已核准）。
可達性已於 2026-09-18 10:02 從 Zeabur production 實測 7/7 通過（設計 §8.1）。

## Global Constraints

- **一律繁體中文**：所有畫面文字、註解、commit 訊息。
- **顏色鎖**：紅＝漲、綠＝跌，**只給價格方向**。琥珀 `var(--accent)` 只給「注意這格」。
  **價差正負、未平倉增減、保證金金額一律不著紅綠**（它們不是價格方向），用中性色。
  白字疊色塊（熱力圖）必須用 `--up-fill`／`--down-fill`，不可用 `--up`／`--down`。
- **CSP 是 `script-src 'self'`**：不可寫 inline `on*=` 屬性，動態元素一律用容器上的事件委派。
- **ECharts 每次 `setOption` 後必須 `resize()`**，並把新圖加進 `window` 的 resize handler。
  candlestick 缺值給 `'-'`，**絕不給 `null`**（會丟 TypeError 讓整個 `setOption` 中止且不進 console）。
- **算不出就回 `None`／留白**，不可用別的日期或別的值頂替。
- **快取寫入與讀取兩端都要守衛**：失敗值不可進無 TTL 的快取，讀到不合格的快取視為未命中。
- **commit 一律列出檔案，不用 `git add .`**；不可 commit `CODEX_HANDOFF_*.md`、`HANDOFF-*.md`。
- **commit 訊息結尾**：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **pytest 不可接管線**（`| tail` 會吃掉離開碼）；一律單獨執行並讀摘要行。
- 本機測試指令：`.venv\Scripts\python -m pytest <path> -q`
- **每一道守衛都要反證**：把守衛拿掉，對應測試必須轉紅。做不到就是測試沒在測那件事。

## File Structure

| 檔案 | 責任 |
|---|---|
| `stocks_power_rich/sources/taifex_ssf.py`（新） | 股期的純解析 ＋ 薄網路包裝。**與 `taifex.py` 分開**：CSV 欄位形狀、端點、主力月與 tick 規則全都不同，混進去會讓兩邊都難讀；`taifex.py` 已 440 行。 |
| `stocks_power_rich/db.py`（改） | `ssf_daily` 表、`bulk_upsert_ssf_daily`、`get_ssf_dates`／`get_ssf_rows`、`prune_ssf_daily` |
| `stocks_power_rich/api/helpers.py`（改） | `_ssf_contracts`（月快取）、`_ssf_margin_table`（日快取）、`refresh_ssf_daily`（排程用）、`job_schedule` 加一筆 |
| `stocks_power_rich/api/market.py`（改） | `GET /api/ssf/overview`、`GET /api/ssf/margin` |
| `stocks_power_rich/api/admin.py`（改） | `GET /api/ssf/backfill`；最後一個任務刪掉 `GET /api/ssf/probe` |
| `stocks_power_rich/main.py`（改） | 註冊 `ssf_daily` job |
| `web/index.html`／`app.js`／`styles.css`（改） | 新視圖 `ssf` 與五個區塊、保證金全表；個股頁一行；自算選股一欄 |
| `tests/test_ssf.py`（新） | 解析、摘要、tick、守衛 |
| `tests/test_ssf_margin.py`（新） | 保證金解析與 Decimal 進位 |
| `tests/test_ssf_api.py`（新） | 兩支端點與排程 |

---

## Task 1: 股期日檔 CSV 解析（純函式）

**Files:**
- Create: `stocks_power_rich/sources/taifex_ssf.py`
- Create: `tests/test_ssf.py`

**Interfaces:**
- Consumes: 無
- Produces: `SSF_HEADER: str`、`parse_ssf_daily_csv(text: str) -> list[dict]`
  （每列 `{"date","contract","month","session","open","high","low","close","chg","chg_pct","volume","settlement","oi","is_spread"}`，
  數值欄為 `float | None`，`volume`／`oi` 為 `int | None`，`is_spread` 為 `bool`）

**背景（實測 2026-09-17 真實資料）**

- 表頭 19 欄；月份欄有尾隨空白（`"202610  "`）；每列結尾多一個逗號。
- 盤後列的 `結算價` 與 `未沖銷契約數` 都是 `-`。
- 價差列的月份含 `/`（`202610/202611`），`漲跌價`／`漲跌%` 是 `-`。
- 未成交列 OHLC 全是 `-`、`成交量` 是 `0`，**但仍有結算價**。
- `漲跌%` 形如 `"1.37%"`／`"-0.78%"`／`"-"`。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_ssf.py`：

```python
from stocks_power_rich.sources import taifex_ssf as ssf

# 真實資料切片（2026-09-17）。刻意包含：一般列、盤後列、價差列、調整後合約 CM1、
# 未成交列（OHLC 皆 '-' 但有結算價）。
SAMPLE = """交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量
2026/09/17,CDF,202610  ,2425,2453,2410,2433,32,1.33%,6027,2432,25045,2432,2433,2520,2360,,一般,,
2026/09/17,CDF,202611  ,2420,2450,2405,2429,30,1.25%,410,2428,3120,2428,2430,2520,2360,,一般,,
2026/09/17,CDF,202610  ,2405,2423,2405,2422,21,0.87%,1755,-,-,2421,2422,2520,2360,,盤後,,
2026/09/17,CDF,202610/202611    ,9.7,10.01,9.7,9.97,-,-,816,-,-,9.92,9.93,10.01,9.7,,一般,88,
2026/09/17,CMF,202610  ,44.7,44.95,44,44.35,-0.35,-0.78%,1093,44.35,2094,44.3,44.45,45,35.95,,一般,,
2026/09/17,CM1,202612  ,-,-,-,-,-,-,0,44.35,96,-,-,39,23.6,,一般,,
2026/09/17,VQF,202611  ,-,-,-,-,-,-,0,64.7,0,64.3,65.3,-,-,,一般,,
"""


def test_parse_reads_every_row_shape():
    rows = ssf.parse_ssf_daily_csv(SAMPLE)
    assert len(rows) == 7
    first = rows[0]
    assert first["date"] == "2026-09-17"          # 轉成站內慣用的 ISO 格式
    assert first["contract"] == "CDF"
    assert first["month"] == "202610"             # 尾隨空白要去掉
    assert first["session"] == "一般"
    assert (first["open"], first["close"]) == (2425.0, 2433.0)
    assert first["chg"] == 32.0 and first["chg_pct"] == 1.33   # 去掉 % 並轉 float
    assert first["volume"] == 6027 and first["oi"] == 25045
    assert first["settlement"] == 2432.0
    assert first["is_spread"] is False


def test_parse_marks_spread_rows_and_blanks_their_change():
    spread = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["is_spread"]]
    assert len(spread) == 1
    assert spread[0]["month"] == "202610/202611"
    assert spread[0]["chg"] is None and spread[0]["chg_pct"] is None


def test_parse_keeps_night_rows_but_their_settlement_and_oi_are_none():
    night = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["session"] == "盤後"]
    assert len(night) == 1
    assert night[0]["volume"] == 1755
    assert night[0]["settlement"] is None and night[0]["oi"] is None


def test_parse_untraded_row_has_no_prices_but_keeps_settlement():
    """沒成交不等於沒價格——保證金全表就靠這個結算價，1,014/1,629 列是這種。"""
    vq = [r for r in ssf.parse_ssf_daily_csv(SAMPLE) if r["contract"] == "VQF"][0]
    assert vq["close"] is None and vq["chg_pct"] is None
    assert vq["volume"] == 0            # 0 是有效觀測，不是缺值
    assert vq["settlement"] == 64.7


def test_parse_rejects_a_changed_header():
    """表頭改版要大聲壞掉。靜默略過會讓排程每天寫進 0 列而沒有人發現。"""
    import pytest
    bad = SAMPLE.replace("未沖銷契約數", "未平倉量")
    with pytest.raises(ValueError, match="表頭"):
        ssf.parse_ssf_daily_csv(bad)
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stocks_power_rich.sources.taifex_ssf'`

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/sources/taifex_ssf.py`：

```python
"""股票期貨（股期）官方盤後資料：純解析 ＋ 薄網路包裝。

與 `taifex.py` 分開的理由：CSV 欄位形狀、下載表單、主力月規則與 tick 級距全都不同，
混進去會讓兩邊都難讀（`taifex.py` 已 440 行）。同一個資料來源、不同的產品族。
"""
from __future__ import annotations

import csv
import io

SSF_HEADER = ("交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
              "成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,"
              "是否因訊息面暫停交易,交易時段,價差對單式委託成交量")


def _f(s) -> float | None:
    """'-'／空字串／'1.37%' 都要接得住。算不出就回 None（全站慣例）。"""
    s = (s or "").strip().replace(",", "").rstrip("%")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s) -> int | None:
    v = _f(s)
    return None if v is None else int(v)


def parse_ssf_daily_csv(text: str) -> list[dict]:
    """把官方股期日檔 CSV 解析成逐列 dict。表頭不符一律丟 ValueError。"""
    first = (text or "").splitlines()[0].strip() if text.strip() else ""
    if first != SSF_HEADER:
        raise ValueError(f"股期日檔表頭與預期不符：{first[:120]!r}")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        month = (r.get("到期月份(週別)") or "").strip()
        d = (r.get("交易日期") or "").strip().replace("/", "-")
        out.append({
            "date": d,
            "contract": (r.get("契約") or "").strip(),
            "month": month,
            "session": (r.get("交易時段") or "").strip(),
            "is_spread": "/" in month,
            "open": _f(r.get("開盤價")), "high": _f(r.get("最高價")),
            "low": _f(r.get("最低價")), "close": _f(r.get("收盤價")),
            "chg": _f(r.get("漲跌價")), "chg_pct": _f(r.get("漲跌%")),
            "volume": _i(r.get("成交量")),
            "settlement": _f(r.get("結算價")), "oi": _i(r.get("未沖銷契約數")),
        })
    return out
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 反證表頭守衛**

暫時把 `if first != SSF_HEADER:` 那兩行註解掉，重跑
`.venv\Scripts\python -m pytest tests/test_ssf.py::test_parse_rejects_a_changed_header -q`
Expected: FAIL（`DID NOT RAISE`）。確認後把那兩行改回來，重跑確認 PASS。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py tests/test_ssf.py
git commit -m "feat: 股期日檔 CSV 解析（純函式，表頭不符大聲壞掉）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: 每檔每日一列摘要（純函式）

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Modify: `tests/test_ssf.py`

**Interfaces:**
- Consumes: `parse_ssf_daily_csv`（Task 1）
- Produces: `summarize_ssf_day(rows: list[dict]) -> list[dict]`，每筆
  `{"date","root","main_month","open","high","low","close","chg","chg_pct","settlement","oi","volume","main_volume"}`

**規則（設計 §2.1／§2.2）**

- `root` ＝ 合約代碼前 2 碼。正常合約 `root+F`、調整後 `root+1` 同屬一個 root。
- `volume` ＝ 該 root **所有非價差列、跨全部月份、跨一般與盤後**的成交量加總（官方 STFTop10 口徑）。
- **價格欄一律只取 `root+F` 的一般、非價差列**——調整後合約的契約乘數是非標準的
  （2020、5965.5892…），拿它的價格再套標準乘數，保證金會全錯而且看不出來。
- 主力月 ＝ 那些列中成交量最大者，**排除結算價為 0 的到期腳**；全部無成交時取最小月份。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf.py`：

```python
def test_summary_picks_the_most_traded_month_of_the_F_contract():
    rows = ssf.parse_ssf_daily_csv(SAMPLE)
    by_root = {r["root"]: r for r in ssf.summarize_ssf_day(rows)}
    cd = by_root["CD"]
    assert cd["main_month"] == "202610"       # 6027 > 410
    assert cd["close"] == 2433.0 and cd["chg_pct"] == 1.33
    assert cd["settlement"] == 2432.0 and cd["oi"] == 25045
    assert cd["main_volume"] == 6027
    # 官方口數：一般 6027+410 ＋ 盤後 1755，價差列 816 不算
    assert cd["volume"] == 6027 + 410 + 1755


def test_summary_counts_adjusted_contract_volume_but_never_its_price():
    """CM1 的契約乘數是非標準的；拿它的價格套標準乘數，保證金會全錯且看不出來。"""
    rows = ssf.parse_ssf_daily_csv(SAMPLE) + ssf.parse_ssf_daily_csv(
        SSF_HEADER_LINE + "\n2026/09/17,CM1,202610  ,9,9,9,9,0,0.00%,99999,9,5,9,9,9,9,,一般,,\n")
    cm = {r["root"]: r for r in ssf.summarize_ssf_day(rows)}["CM"]
    assert cm["close"] == 44.35          # 來自 CMF，不是成交量最大的 CM1
    assert cm["main_month"] == "202610"
    assert cm["volume"] == 1093 + 0 + 99999   # 量要含 CM1


def test_summary_skips_an_expiring_leg_whose_settlement_is_zero():
    """結算日當天到期腳仍在交易且可能量最大，但結算價是 0、價差也收斂到 0。"""
    text = SSF_HEADER_LINE + "\n" + "\n".join([
        "2026/09/17,CDF,202609  ,100,100,100,100,1,1.00%,9999,0,10,100,100,100,100,,一般,,",
        "2026/09/17,CDF,202610  ,200,200,200,200,2,1.00%,50,200,20,200,200,200,200,,一般,,",
    ]) + "\n"
    cd = ssf.summarize_ssf_day(ssf.parse_ssf_daily_csv(text))[0]
    assert cd["main_month"] == "202610"
    assert cd["close"] == 200.0
    assert cd["volume"] == 9999 + 50     # 量仍含到期腳（官方口徑）


def test_summary_falls_back_to_the_nearest_month_when_nothing_traded():
    vq = {r["root"]: r for r in ssf.summarize_ssf_day(ssf.parse_ssf_daily_csv(SAMPLE))}["VQ"]
    assert vq["main_month"] == "202611"
    assert vq["close"] is None           # 沒成交就沒有收盤價
    assert vq["settlement"] == 64.7      # 但結算價還在，保證金算得出來
```

測試檔頂端補一行常數（Task 1 的 `SAMPLE` 保持不變）：

```python
SSF_HEADER_LINE = ssf.SSF_HEADER
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'summarize_ssf_day'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`：

```python
def root_of(contract: str) -> str:
    """母合約＝代碼前 2 碼。正常合約 root+F、調整後 root+1 自動歸在一起。"""
    return (contract or "")[:2]


def summarize_ssf_day(rows: list[dict]) -> list[dict]:
    """逐列 → 每個 root 每日一列摘要。

    量與價**刻意用不同的母體**：
    - 量：所有非價差列、全月份、一般＋盤後、含調整後合約（官方 STFTop10 口徑）
    - 價：只取 `root+F` 的一般、非價差列（調整後合約乘數非標準，價格不可混用）
    """
    vol: dict[str, int] = {}
    price_rows: dict[str, list[dict]] = {}
    dates: dict[str, str] = {}
    for r in rows:
        if r["is_spread"]:
            continue
        root = root_of(r["contract"])
        if not root:
            continue
        vol[root] = vol.get(root, 0) + (r["volume"] or 0)
        dates.setdefault(root, r["date"])
        if r["session"] == "一般" and r["contract"] == root + "F":
            price_rows.setdefault(root, []).append(r)

    out = []
    for root, cands in price_rows.items():
        # 結算價為 0 的是到期腳：價格與價差都不可採用（但量已經算進去了）
        usable = [r for r in cands if (r["settlement"] or 0) > 0] or cands
        traded = [r for r in usable if (r["volume"] or 0) > 0]
        main = (max(traded, key=lambda r: r["volume"]) if traded
                else min(usable, key=lambda r: r["month"]))
        out.append({
            "date": dates.get(root) or main["date"],
            "root": root, "main_month": main["month"],
            "open": main["open"], "high": main["high"],
            "low": main["low"], "close": main["close"],
            "chg": main["chg"], "chg_pct": main["chg_pct"],
            "settlement": main["settlement"], "oi": main["oi"],
            "volume": vol.get(root, 0), "main_volume": main["volume"] or 0,
        })
    return out
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 反證「調整後合約不進價格」**

把 `and r["contract"] == root + "F"` 拿掉，重跑
`.venv\Scripts\python -m pytest tests/test_ssf.py::test_summary_counts_adjusted_contract_volume_but_never_its_price -q`
Expected: FAIL（`close` 變成 9.0）。確認後改回來。

再反證到期腳：把 `usable = [...]` 那行改成 `usable = cands`，重跑
`test_summary_skips_an_expiring_leg_whose_settlement_is_zero`
Expected: FAIL（`main_month` 變 `202609`）。確認後改回來，重跑全檔確認 PASS。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py tests/test_ssf.py
git commit -m "feat: 股期每日摘要——量依官方口徑含調整後合約，價格只取正常合約

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: tick 級距與期現價差（純函式）

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Modify: `tests/test_ssf.py`

**Interfaces:**
- Consumes: 無
- Produces: `ssf_tick_size(price: float, is_etf: bool) -> float`、
  `ssf_basis_ticks(fut: float, spot: float, is_etf: bool) -> int | None`

**規則（設計 §2.4）**

股期 tick（2026-07-06 起，**與現貨不同**）：`<10:0.01 / 10–50:0.05 / 50–100:0.1 /
100–500:0.5 / 500–2500:1 / ≥2500:5`；ETF 期貨 `<50:0.01 / ≥50:0.05`。
現貨在 1000–2500 跳 5，期貨跳 1。

**必須沿網格走**：實測聯茂 KBF 現貨 495、期貨 501 ＝ **11 ticks**；
除以 tick(F)=1 得 6、除以 tick(S)=0.5 得 12，兩種都錯。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf.py`：

```python
import pytest


@pytest.mark.parametrize("price,tick", [
    (9.99, 0.01), (10, 0.05), (49.95, 0.05), (50, 0.1), (99.9, 0.1),
    (100, 0.5), (499.5, 0.5), (500, 1), (2499, 1), (2500, 5), (3000, 5),
])
def test_stock_tick_table(price, tick):
    assert ssf.ssf_tick_size(price, is_etf=False) == tick


@pytest.mark.parametrize("price,tick", [(49.99, 0.01), (50, 0.05), (120, 0.05)])
def test_etf_tick_table(price, tick):
    assert ssf.ssf_tick_size(price, is_etf=True) == tick


def test_basis_walks_the_grid_across_a_band_boundary():
    """實測 KBF 聯茂：現貨 495、期貨 501。走網格 11 檔；
    除以單一 tick 會得 6（用期貨端 1 元）或 12（用現貨端 0.5 元），兩種都錯。"""
    assert ssf.ssf_basis_ticks(501, 495, is_etf=False) == 11


def test_basis_sign_and_simple_cases():
    assert ssf.ssf_basis_ticks(2435, 2430, is_etf=False) == 1      # 2500 以下，1 元一檔
    assert ssf.ssf_basis_ticks(2425, 2430, is_etf=False) == -5
    assert ssf.ssf_basis_ticks(108.35, 108.30, is_etf=True) == 1   # ETF 0.05 一檔


def test_basis_returns_none_when_either_side_is_missing():
    assert ssf.ssf_basis_ticks(None, 100, is_etf=False) is None
    assert ssf.ssf_basis_ticks(100, None, is_etf=False) is None
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -k "tick or basis" -q`
Expected: FAIL — `AttributeError: ... 'ssf_tick_size'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`（頂端補 `from decimal import Decimal`）：

```python
# (上限, 一檔) —— 上限為開區間。股期自 2026-07-06 起與現貨不同：
# 現貨在 1000–2500 跳 5 元，股期跳 1 元。
_TICKS_STOCK = ((Decimal("10"), Decimal("0.01")), (Decimal("50"), Decimal("0.05")),
                (Decimal("100"), Decimal("0.1")), (Decimal("500"), Decimal("0.5")),
                (Decimal("2500"), Decimal("1")), (None, Decimal("5")))
_TICKS_ETF = ((Decimal("50"), Decimal("0.01")), (None, Decimal("0.05")))


def _bands(is_etf: bool):
    return _TICKS_ETF if is_etf else _TICKS_STOCK


def ssf_tick_size(price: float, is_etf: bool = False) -> float:
    p = Decimal(str(price))
    for hi, tick in _bands(is_etf):
        if hi is None or p < hi:
            return float(tick)
    return float(_bands(is_etf)[-1][1])


def _grid_index(price: Decimal, is_etf: bool) -> Decimal:
    """從 0 走到 price 共幾檔。跨級距時每一段各用自己的檔位累加。"""
    left, idx = Decimal("0"), Decimal("0")
    for hi, tick in _bands(is_etf):
        if hi is None or price < hi:
            return idx + (price - left) / tick
        idx += (hi - left) / tick
        left = hi
    return idx


def ssf_basis_ticks(fut, spot, is_etf: bool = False) -> int | None:
    """期現價差換算成「幾檔」。

    **必須沿網格走**：兩端落在不同級距時，除以單一 tick 一定錯——實測聯茂
    現貨 495／期貨 501，走網格 11 檔，除以期貨端得 6、除以現貨端得 12。
    """
    if fut is None or spot is None:
        return None
    diff = _grid_index(Decimal(str(fut)), is_etf) - _grid_index(Decimal(str(spot)), is_etf)
    return int(diff.to_integral_value())
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: PASS（全部通過）

- [ ] **Step 5: 反證「走網格」不是恆真**

暫時把 `ssf_basis_ticks` 的最後兩行換成
`return int(round((fut - spot) / ssf_tick_size(fut, is_etf)))`，重跑
`.venv\Scripts\python -m pytest tests/test_ssf.py::test_basis_walks_the_grid_across_a_band_boundary -q`
Expected: FAIL（得到 6 而不是 11）。確認後改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py tests/test_ssf.py
git commit -m "feat: 股期 tick 級距與期現價差（沿網格走，不可除以單一 tick）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: 抓取器與三道「失敗長得像成功」守衛

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Modify: `tests/test_ssf.py`

**Interfaces:**
- Consumes: `parse_ssf_daily_csv`（Task 1）
- Produces: `fetch_ssf_daily(start: str, end: str) -> list[dict]`
  （`start`／`end` 為 `"YYYY/MM/DD"`；回傳逐列 dict，與 `parse_ssf_daily_csv` 同形狀）

**背景（實測，設計 §1.1）**

| 情況 | HTTP | Content-Type | 大小 | 內容 |
|---|---|---|---|---|
| 正常 | 200 | `text/html;charset=MS950` | 150 KB | CSV |
| 區間超過一個月 | 200 | `text/html;charset=UTF-8` | 616 B | **HTML 警告頁** |
| 非交易日 | 200 | `charset=MS950` | 197 B | **只有表頭** |
| 當天早上 | 200 | `charset=MS950` | 4.6 KB | **只有盤後列，一般 0 列** |

既有的 `taifex._post_csv` 只驗 `status_code == 200 and r.content`，四種都會通過。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf.py`：

```python
class _Resp:
    def __init__(self, body: bytes, ct: str):
        self.status_code, self.content, self.headers = 200, body, {"content-type": ct}


class _Client:
    """把 httpx.Client 的最小介面樁掉。`post` 回下一個排好的回應。"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.posted = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **kw):
        return _Resp(b"", "text/html")

    def post(self, url, **kw):
        self.posted.append(kw.get("data"))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    client = _Client(responses)
    monkeypatch.setattr(ssf.httpx, "Client", lambda *a, **kw: client)
    return client


def test_fetch_returns_rows_on_a_normal_response(monkeypatch):
    body = (SSF_HEADER_LINE + "\n" +
            "\n".join([f"2026/09/17,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,1,1,1,1,1,1,,一般,,"
                       for i in range(1300)]) + "\n")
    client = _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    rows = ssf.fetch_ssf_daily("2026/09/17", "2026/09/17")
    assert len(rows) == 1300
    assert client.posted[0]["commodity_id"] == "specialid"
    assert client.posted[0]["commodity_id2"] == "all"


def test_fetch_rejects_the_utf8_alert_page_for_an_over_long_range(monkeypatch):
    """區間超過一個月時伺服器回 HTTP 200 的 HTML 警告頁，照 MS950 解碼會變亂碼。"""
    _patch_client(monkeypatch, [_Resp(b"<!DOCTYPE HTML><html>too long</html>",
                                      "text/html;charset=UTF-8")])
    assert ssf.fetch_ssf_daily("2026/08/16", "2026/09/17") == []


def test_fetch_rejects_a_header_only_response(monkeypatch):
    """非交易日回 197 B 只有表頭。它會通過『200 且有 body』。"""
    _patch_client(monkeypatch, [_Resp((SSF_HEADER_LINE + "\n").encode("ms950"),
                                      "text/html;charset=MS950")])
    assert ssf.fetch_ssf_daily("2026/09/13", "2026/09/13") == []


def test_fetch_rejects_a_night_session_only_response(monkeypatch):
    """實測當天早上 10:02 打，回 51 列全是盤後、一般 0 列。

    少了這道守衛，17:15 的排程在資料還沒出來時會寫進一天只有夜盤的資料。
    """
    body = (SSF_HEADER_LINE + "\n" +
            "\n".join([f"2026/09/18,X{i:02d}F,202610  ,1,1,1,1,0,0.00%,1,-,-,1,1,1,1,,盤後,,"
                       for i in range(51)]) + "\n")
    _patch_client(monkeypatch, [_Resp(body.encode("ms950"), "text/html;charset=MS950")])
    assert ssf.fetch_ssf_daily("2026/09/18", "2026/09/18") == []
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -k fetch -q`
Expected: FAIL — `AttributeError: ... 'fetch_ssf_daily'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`（頂端補 `import httpx` 與 `import logging`）：

```python
log = logging.getLogger("spr")

SSF_FORM = "https://www.taifex.com.tw/cht/3/futDataDown"
SSF_DOWN = "https://www.taifex.com.tw/cht/3/dlFutDataDown"
# 一天正常有 1,629 列非價差的一般列。抓到的比這個少一截就是資料還沒齊，
# 不是「今天比較冷清」——寧可不寫，也不要寫進半套。
MIN_GENERAL_ROWS = 1200


def fetch_ssf_daily(start: str, end: str) -> list[dict]:
    """抓指定區間（`YYYY/MM/DD`，**不可超過一個月**）的股期日檔。

    三種失敗都是 HTTP 200 且有 body，所以逐一擋掉：
    1. Content-Type 不是 MS950 → 區間超過一個月的 UTF-8 HTML 警告頁
    2. 表頭不符或只有表頭 → 非交易日
    3. 「一般、非價差」列數不足 → 日盤資料還沒發佈（早上打只會有盤後列）

    任何一種都回空 list（呼叫端據此略過，不寫入），並留下一行 warning。
    """
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as cli:
        cli.get(SSF_FORM, headers={"Referer": SSF_FORM})
        r = cli.post(SSF_DOWN, headers={"Referer": SSF_FORM}, data={
            "down_type": "1", "commodity_id": "specialid", "commodity_id2": "all",
            "queryStartDate": start, "queryEndDate": end,
        })
    ct = (r.headers.get("content-type") or "").upper()
    if "MS950" not in ct:
        log.warning("[ssf] %s~%s 回應不是 MS950（多半是區間超過一個月的警告頁）：%s",
                    start, end, ct)
        return []
    try:
        rows = parse_ssf_daily_csv(r.content.decode("ms950", errors="replace"))
    except ValueError as e:
        log.warning("[ssf] %s~%s 解析失敗：%s", start, end, e)
        return []
    general = [x for x in rows if x["session"] == "一般" and not x["is_spread"]]
    if len(general) < MIN_GENERAL_ROWS:
        log.warning("[ssf] %s~%s 一般列只有 %d 列（<%d），視為資料未發佈",
                    start, end, len(general), MIN_GENERAL_ROWS)
        return []
    return rows
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: PASS

- [ ] **Step 5: 逐一反證三道守衛**

1. 把 `if "MS950" not in ct:` 那三行拿掉 → `test_fetch_rejects_the_utf8_alert_page_for_an_over_long_range` 轉紅。
2. 把 `except ValueError` 改成直接 `rows = parse_ssf_daily_csv(...)` 並拿掉 try → `test_fetch_rejects_a_header_only_response` 轉紅（改成丟例外）。
3. 把 `if len(general) < MIN_GENERAL_ROWS:` 那四行拿掉 → `test_fetch_rejects_a_night_session_only_response` 轉紅。

每次確認轉紅後改回來，最後重跑全檔確認 PASS。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py tests/test_ssf.py
git commit -m "feat: 股期日檔抓取器，三種『失敗長得像成功』各有守衛

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: `ssf_daily` 資料表

**Files:**
- Modify: `stocks_power_rich/db.py`
- Create: `tests/test_ssf_db.py`

**Interfaces:**
- Consumes: `summarize_ssf_day` 的輸出形狀（Task 2）
- Produces:
  - `bulk_upsert_ssf_daily(conn, rows: list[dict]) -> int`
  - `get_ssf_dates(conn, limit: int = 10) -> list[str]`（新到舊）
  - `get_ssf_rows(conn, dates: list[str]) -> list[dict]`
  - `prune_ssf_daily(conn, keep_days: int = 60) -> int`

- [ ] **Step 1: 寫失敗的測試**

`tests/test_ssf_db.py`：

```python
from stocks_power_rich.db import (get_connection, init_db, bulk_upsert_ssf_daily,
                                  get_ssf_dates, get_ssf_rows, prune_ssf_daily)


def _row(date, root, **kw):
    base = {"date": date, "root": root, "main_month": "202610", "open": 1.0, "high": 2.0,
            "low": 0.5, "close": 1.5, "chg": 0.1, "chg_pct": 7.1, "settlement": 1.5,
            "oi": 100, "volume": 10, "main_volume": 8}
    base.update(kw)
    return base


def test_upsert_and_read_back(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    assert bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD"), _row("2026-09-17", "QF")]) == 2
    assert get_ssf_dates(conn) == ["2026-09-17"]
    rows = get_ssf_rows(conn, ["2026-09-17"])
    assert {r["root"] for r in rows} == {"CD", "QF"}
    assert rows[0]["main_month"] == "202610"


def test_upsert_is_coalescing_so_a_partial_rewrite_does_not_blank_columns(tmp_path):
    """重抓前兩個交易日時，官方偶爾少給某欄；COALESCE 讓它保留既有值而不是洗成空。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD", close=2433.0, oi=25045)])
    bulk_upsert_ssf_daily(conn, [_row("2026-09-17", "CD", close=2440.0, oi=None)])
    row = get_ssf_rows(conn, ["2026-09-17"])[0]
    assert row["close"] == 2440.0     # 新值覆蓋
    assert row["oi"] == 25045         # None 不洗掉舊值


def test_dates_are_newest_first_and_limited(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row(f"2026-09-{d:02d}", "CD") for d in range(1, 16)])
    assert get_ssf_dates(conn, limit=3) == ["2026-09-15", "2026-09-14", "2026-09-13"]


def test_prune_keeps_only_the_newest_n_trading_days(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    bulk_upsert_ssf_daily(conn, [_row(f"2026-09-{d:02d}", "CD") for d in range(1, 16)])
    assert prune_ssf_daily(conn, keep_days=5) == 10
    assert len(get_ssf_dates(conn, limit=99)) == 5
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_db.py -q`
Expected: FAIL — `ImportError: cannot import name 'bulk_upsert_ssf_daily'`

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/db.py` 的 `init_db` 內，緊接在 `stock_ohlc` 建表之後加：

```python
    # 股期概況：每個母合約每日一列摘要（約 340 列/日）。逐月份原始列不落地——
    # v1 的四個區塊都用不到，而全市場逐月份是 5 倍的量（見設計 §0 決定 5）。
    conn.execute("CREATE TABLE IF NOT EXISTS ssf_daily (date TEXT, root TEXT, "
                 "main_month TEXT, open REAL, high REAL, low REAL, close REAL, "
                 "chg REAL, chg_pct REAL, settlement REAL, oi INTEGER, "
                 "volume INTEGER, main_volume INTEGER, PRIMARY KEY (date, root))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ssf_root ON ssf_daily(root, date)")
```

檔案尾端（接在 `bulk_upsert_ohlc` 附近）加：

```python
_SSF_COLS = ("main_month", "open", "high", "low", "close", "chg", "chg_pct",
             "settlement", "oi", "volume", "main_volume")


def bulk_upsert_ssf_daily(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """股期每日摘要批次入庫。每筆自帶 date（回補一次會跨多天）。

    **COALESCE，null 不洗掉既有值**（同 `bulk_upsert_ohlc` 的規矩）：排程每次會重抓
    前 2 個交易日，官方偶爾少給某欄，不能讓「這次沒抓到」變成「把既有值清空」。
    """
    data = [(r["date"], r["root"], *(r.get(c) for c in _SSF_COLS)) for r in rows]
    if not data:
        return 0
    assign = ", ".join(f"{c}=COALESCE(excluded.{c}, ssf_daily.{c})" for c in _SSF_COLS)
    conn.executemany(
        "INSERT INTO ssf_daily (date, root, " + ", ".join(_SSF_COLS) + ") VALUES ("
        + ",".join("?" * (2 + len(_SSF_COLS))) + ") "
        "ON CONFLICT(date, root) DO UPDATE SET " + assign, data)
    conn.commit()
    return len(data)


def get_ssf_dates(conn: sqlite3.Connection, limit: int = 10) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM ssf_daily ORDER BY date DESC LIMIT ?", (limit,))]


def get_ssf_rows(conn: sqlite3.Connection, dates: list[str]) -> list[dict]:
    if not dates:
        return []
    q = ",".join("?" * len(dates))
    cur = conn.execute("SELECT date, root, " + ", ".join(_SSF_COLS) +
                       f" FROM ssf_daily WHERE date IN ({q}) ORDER BY date DESC, volume DESC",
                       dates)
    cols = ["date", "root", *_SSF_COLS]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def prune_ssf_daily(conn: sqlite3.Connection, keep_days: int = 60) -> int:
    """只留最新 keep_days 個**交易日**（不是日曆天）。約 340 列/日。"""
    keep = get_ssf_dates(conn, limit=keep_days)
    if not keep:
        return 0
    q = ",".join("?" * len(keep))
    cur = conn.execute(f"DELETE FROM ssf_daily WHERE date NOT IN ({q})", keep)
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_db.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 反證 COALESCE**

把 `assign` 改成 `", ".join(f"{c}=excluded.{c}" for c in _SSF_COLS)`，重跑
`test_upsert_is_coalescing_so_a_partial_rewrite_does_not_blank_columns`
Expected: FAIL（`oi` 變成 None）。確認後改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/db.py tests/test_ssf_db.py
git commit -m "feat: ssf_daily 表（每檔每日一列摘要，COALESCE 不洗既有值）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: 合約對照表（代號／名稱／契約乘數）

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Modify: `tests/test_ssf.py`
- Modify: `stocks_power_rich/api/helpers.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `parse_stock_lists(html: str) -> dict`
    → `{root: {"code","stock_name","name","multiplier","is_etf","is_mini"}}`
  - `fetch_ssf_contract_map() -> dict`（同上形狀，抓不到回 `{}`）
  - `helpers._ssf_contracts(c) -> dict`（月快取）

**規則（設計 §1.2）**

`https://www.taifex.com.tw/cht/2/stockLists` 的表格每列 14 個 `<td>`：
欄[0] 2 碼字首、欄[2] 證券代號、欄[3] 簡稱、欄[9]/[10] ETF 標記（`◎`）、欄[11] 標準型股數。
乘數 100／1000 ＝ 小型。`name` ＝（小型 ?）＋ 簡稱。
讀旗標前要先去掉 `<span class="sr-only">` 的文字。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf.py`：

```python
STOCK_LISTS_HTML = """
<table id="myTable"><tbody>
<tr><td>CD</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td>
<td><span class="sr-only">是</span>●</td><td></td><td></td>
<td>◎</td><td></td><td></td><td></td><td>2,000</td><td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>QF</td><td>台灣積體電路製造股份有限公司</td><td>2330</td><td>台積電</td>
<td>●</td><td></td><td></td><td>◎</td><td></td><td></td><td></td><td>100</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>NY</td><td>元大台灣卓越50證券投資信託基金</td><td>0050</td><td>元大台灣50</td>
<td>●</td><td></td><td></td><td></td><td></td><td>◎</td><td></td><td>10,000</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
<tr><td>SR</td><td>元大台灣卓越50證券投資信託基金</td><td>0050</td><td>元大台灣50</td>
<td>●</td><td></td><td></td><td></td><td></td><td>◎</td><td></td><td>1,000</td>
<td>08:45~13:45</td><td>17:25~05:00</td></tr>
</tbody></table>
"""


def test_contract_map_reads_code_name_and_multiplier():
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert m["CD"] == {"code": "2330", "stock_name": "台積電", "name": "台積電",
                       "multiplier": 2000, "is_etf": False, "is_mini": False,
                       "session_end": "13:45", "late_session": False}
    assert m["QF"]["multiplier"] == 100
    assert m["QF"]["is_mini"] is True
    assert m["QF"]["name"] == "小型台積電"      # 小型是不同產品，名稱要分得出來


def test_contract_map_flags_the_contracts_that_trade_past_the_spot_close():
    """14 檔 ETF 期貨交易到 16:15，收盤比現貨晚 2.5 小時；價差要另標，不能混在一起讀。"""
    html = STOCK_LISTS_HTML.replace(
        "<td>08:45~13:45</td><td>17:25~05:00</td></tr>\n<tr><td>SR</td>",
        "<td>08:45~16:15</td><td></td></tr>\n<tr><td>SR</td>")
    m = ssf.parse_stock_lists(html)
    assert m["NY"]["late_session"] is True and m["NY"]["session_end"] == "16:15"
    assert m["CD"]["late_session"] is False


def test_contract_map_flags_etf_underlyings():
    """ETF 期貨的 tick 級距與保證金規則都與股票標的不同，必須分得出來。"""
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert m["NY"]["is_etf"] is True and m["NY"]["multiplier"] == 10000
    assert m["SR"]["is_etf"] is True and m["SR"]["is_mini"] is True
    assert m["CD"]["is_etf"] is False


def test_contract_map_ignores_sr_only_text_when_reading_flags():
    """欄位裡藏著給螢幕閱讀器的『是』，直接讀 innerText 會把它當成標記。"""
    m = ssf.parse_stock_lists(STOCK_LISTS_HTML)
    assert m["CD"]["is_etf"] is False     # 欄[7] 是上市普通股 ◎，不是 ETF
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -k contract_map -q`
Expected: FAIL — `AttributeError: ... 'parse_stock_lists'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`（頂端補 `import re`）：

```python
STOCK_LISTS_URL = "https://www.taifex.com.tw/cht/2/stockLists"

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_SR_ONLY = re.compile(r"<span[^>]*class=\"sr-only\"[^>]*>.*?</span>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _cell(html: str) -> str:
    """去掉螢幕閱讀器專用文字再剝標籤——直接讀文字會把那個『是』當成標記。"""
    return _TAG.sub("", _SR_ONLY.sub("", html)).replace("&nbsp;", " ").strip()


def parse_stock_lists(html: str) -> dict:
    """stockLists 表 → {root: {code, stock_name, name, multiplier, is_etf, is_mini}}。

    一個來源就給齊代號、簡稱與契約乘數；小型與 ETF 由乘數與 ◎ 標記判定。
    """
    out: dict[str, dict] = {}
    for tr in _TR.findall(html):
        tds = [_cell(x) for x in _TD.findall(tr)]
        if len(tds) < 12:
            continue
        root, code, short = tds[0], tds[2], tds[3]
        if not re.fullmatch(r"[A-Z]{2}", root) or not code:
            continue
        mult = _i(tds[11])
        if not mult:
            continue
        is_etf = "◎" in tds[9] or "◎" in tds[10]
        is_mini = mult in (100, 1000)
        # 交易時段：306 檔到 13:45，但有 14 檔（成分股在海外的 ETF）到 16:15。
        # 那 14 檔的收盤比現貨晚 2.5 小時，期現價差的誤導程度遠大於一般的 15 分鐘落差。
        session = tds[12].replace(" ", "")
        out[root] = {"code": code, "stock_name": short,
                     "name": ("小型" if is_mini else "") + short,
                     "multiplier": mult, "is_etf": is_etf, "is_mini": is_mini,
                     "session_end": session.split("~")[-1] if "~" in session else "",
                     "late_session": "13:45" not in session}
    return out


def fetch_ssf_contract_map() -> dict:
    """抓合約對照表。失敗回空 dict——呼叫端的月快取兩端都擋空值，不會把失敗永久化。"""
    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as cli:
            r = cli.get(STOCK_LISTS_URL)
        return parse_stock_lists(r.content.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        log.warning("[ssf] 合約對照表抓取失敗：%s: %s", type(e).__name__, e)
        return {}
```

`stocks_power_rich/api/helpers.py`：在 `_otc_industry` 之後加（並在檔頭的 sources import 補 `taifex_ssf`）：

```python
def _ssf_contracts(c) -> dict:
    """股期合約對照表 {root: {...}}，月快取（比照 `_industry_map`）。

    **讀取端也要守衛**：正常有 320 筆，少於 300 一律視為未命中重抓——
    只有寫入守衛擋不住「已經寫進去的半套結果」（本專案兩次快取事故的教訓）。
    """
    key = f"ssf_contracts:{datetime.now().strftime('%Y-%m')}"
    m = get_ai_cache(c, key)
    if not m or len(m) < 300:
        fresh = taifex_ssf.fetch_ssf_contract_map()
        if len(fresh) >= 300:
            set_ai_cache(c, key, fresh)
            return fresh
        return m or fresh or {}
    return m
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf.py -q`
Expected: PASS

- [ ] **Step 5: 用真實頁面驗一次（非 mock）**

Run:

```bash
.venv\Scripts\python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); from stocks_power_rich.sources import taifex_ssf as s; m=s.fetch_ssf_contract_map(); print(len(m)); print(m.get('CD')); print(m.get('QF')); print(sum(1 for v in m.values() if v['is_mini']), sum(1 for v in m.values() if v['is_etf']))"
```

Expected: `320`、CD 是 2330／台積電／2000、QF 是小型台積電／100、小型 50 檔、ETF 24 檔。
**數字對不上就停下來查解析，不要往下做**——後面每一個區塊都吃這張表。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py stocks_power_rich/api/helpers.py tests/test_ssf.py
git commit -m "feat: 股期合約對照表（代號/名稱/契約乘數，月快取讀寫兩端守衛）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: 保證金比例表解析與快取

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Create: `tests/test_ssf_margin.py`
- Modify: `stocks_power_rich/api/helpers.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `parse_stock_margining_csv(text) -> {"stock_updated", "etf_updated", "stock": {contract: {...}}, "etf": {contract: {...}}}`
  - `parse_index_margining_csv(text) -> {"updated", "items": {name: {...}}}`
  - `fetch_ssf_margin_table() -> dict`（合併三者，失敗回 `{}`）
  - `helpers._ssf_margin_table(c) -> dict`（日快取）

**規則（設計 §1.3）**

- **用官方 CSV 不用 OpenAPI**：兩者資料實測 0 差異，但 OpenAPI 的 `Date` 是每日快照、
  **不是生效日**；真正的 `更新日期` 只存在於 CSV。
- `stockMarginingDown` 是 MS950、四個區段，各自帶自己的 `更新日期`；
  區段一 296 列比例、區段二 24 列固定金額，區段三四（選擇權）略過。
- 比例欄形如 `13.50%`；代碼與名稱有尾隨空白。
- 級距欄可能是空字串（風險係數 >15% 的 14 檔）。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_ssf_margin.py`：

```python
from stocks_power_rich.sources import taifex_ssf as ssf

STOCK_MARGIN_CSV = """一、股票期貨契約保證金一覽表
(一) 標的證券為股票之股票期貨契約
更新日期:2026/09/15
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,保證金所屬級距,結算保證金適用比例,維持保證金適用比例,原始保證金適用比例,
61,CDF    ,2330,台積電期貨                    ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
62,QFF    ,2330,小型台積電期貨                ,"台灣積體電路製造股份有限公司",級距1,10.00%,10.35%,13.50%,
33,KUF    ,1802,台玻期貨                      ,"台灣玻璃工業股份有限公司",,16.00%,16.56%,21.60%,
(二) 標的證券為ETF之股票期貨契約
更新日期:2026/08/12
序號,股票期貨英文代碼,股票期貨標的證券代號,股票期貨中文簡稱,股票期貨標的證券,結算保證金,維持保證金,原始保證金,
1,NYF    ,0050,元大台灣50ETF期貨             ,元大台灣卓越50證券投資信託基金,64000,67000,87000,
2,SRF    ,0050,小型元大台灣50ETF期貨         ,元大台灣卓越50證券投資信託基金,6400,6700,8700,
二、股票選擇權契約保證金一覽表
更新日期:2026/09/09
序號,代碼,名稱,
1,ZZZ,不該被讀進來,
"""

INDEX_MARGIN_CSV = """更新日期:2026/08/12
商品別,結算保證金,維持保證金,原始保證金,,
臺股期貨,519000,538000,701000,
小型臺指,129750,134500,175250,
微型臺指期貨,25950,26900,35050,
臺指選擇權風險保證金(A)值,138000,143000,187000,
"""


def test_stock_margin_reads_ratios_and_its_own_update_date():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["stock_updated"] == "2026/09/15"
    cdf = m["stock"]["CDF"]
    assert cdf["code"] == "2330" and cdf["tier"] == "級距1"
    assert cdf["initial_pct"] == 13.50 and cdf["maintenance_pct"] == 10.35
    assert m["stock"]["QFF"]["initial_pct"] == 13.50


def test_stock_margin_accepts_a_blank_tier():
    """風險係數 >15% 的 14 檔沒有級距，級距欄是空字串而不是缺列。"""
    assert ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)["stock"]["KUF"] == {
        "code": "1802", "name": "台玻期貨", "tier": "",
        "clearing_pct": 16.00, "maintenance_pct": 16.56, "initial_pct": 21.60}


def test_etf_section_has_fixed_amounts_and_a_different_update_date():
    """ETF 期貨公布固定金額、不套價格×比例，而且生效日與股票區段不同。"""
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert m["etf_updated"] == "2026/08/12"
    assert m["etf"]["NYF"]["initial"] == 87000
    assert m["etf"]["SRF"]["initial"] == 8700      # 小型恰為標準的 1/10


def test_option_sections_are_not_read_as_futures():
    m = ssf.parse_stock_margining_csv(STOCK_MARGIN_CSV)
    assert "ZZZ" not in m["stock"] and "ZZZ" not in m["etf"]


def test_index_margin_reads_tmf():
    m = ssf.parse_index_margining_csv(INDEX_MARGIN_CSV)
    assert m["updated"] == "2026/08/12"
    assert m["items"]["微型臺指期貨"] == {"clearing": 25950, "maintenance": 26900,
                                          "initial": 35050}
    assert m["items"]["臺股期貨"]["initial"] == 701000
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_margin.py -q`
Expected: FAIL — `AttributeError: ... 'parse_stock_margining_csv'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`：

```python
STOCK_MARGIN_URL = "https://www.taifex.com.tw/cht/5/stockMarginingDown"
INDEX_MARGIN_URL = "https://www.taifex.com.tw/cht/5/indexMargingDown"

_UPDATED = re.compile(r"更新日期[:：]\s*(\d{4}/\d{2}/\d{2})")


def _pct(s) -> float | None:
    return _f(s)          # '13.50%' → 13.5


def parse_stock_margining_csv(text: str) -> dict:
    """四個區段的 CSV → 股票標的比例 ＋ ETF 固定金額，各帶自己的更新日期。

    **更新日期只存在於這份 CSV**：OpenAPI 的 `Date` 是每日快照、天天跳，
    拿它當生效日是那種讀者永遠發現不了的錯（設計 §1.3）。
    選擇權那兩個區段直接略過。
    """
    out = {"stock_updated": None, "etf_updated": None, "stock": {}, "etf": {}}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "標的證券為股票之股票期貨" in line:
            section = "stock"; continue
        if "標的證券為ETF之股票期貨" in line:
            section = "etf"; continue
        if line.startswith("二、") or "選擇權" in line:
            section = None; continue
        m = _UPDATED.search(line)
        if m:
            if section == "stock":
                out["stock_updated"] = m.group(1)
            elif section == "etf":
                out["etf_updated"] = m.group(1)
            continue
        if section is None or line.startswith("序號"):
            continue
        p = [x.strip().strip('"') for x in line.split(",")]
        if len(p) < 9 or not p[0].isdigit():
            continue
        contract = p[1]
        if section == "stock":
            out["stock"][contract] = {
                "code": p[2], "name": p[3], "tier": p[5],
                "clearing_pct": _pct(p[6]), "maintenance_pct": _pct(p[7]),
                "initial_pct": _pct(p[8])}
        else:
            out["etf"][contract] = {
                "code": p[2], "name": p[3],
                "clearing": _i(p[5]), "maintenance": _i(p[6]), "initial": _i(p[7])}
    return out


def parse_index_margining_csv(text: str) -> dict:
    """指數期貨固定金額（台指／小台／微台）。"""
    out = {"updated": None, "items": {}}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _UPDATED.search(line)
        if m:
            out["updated"] = m.group(1); continue
        p = [x.strip() for x in line.split(",")]
        if len(p) < 4 or p[0] in ("商品別", ""):
            continue
        if _i(p[1]) is None:
            continue
        out["items"][p[0]] = {"clearing": _i(p[1]), "maintenance": _i(p[2]),
                              "initial": _i(p[3])}
    return out


def fetch_ssf_margin_table() -> dict:
    """抓兩份保證金 CSV 並合併。任何一份不合格就整份回 {}（不寫半套快取）。"""
    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as cli:
            s = cli.get(STOCK_MARGIN_URL).content.decode("ms950", errors="replace")
            i = cli.get(INDEX_MARGIN_URL).content.decode("ms950", errors="replace")
        stock = parse_stock_margining_csv(s)
        index = parse_index_margining_csv(i)
    except Exception as e:  # noqa: BLE001
        log.warning("[ssf] 保證金表抓取失敗：%s: %s", type(e).__name__, e)
        return {}
    if len(stock["stock"]) < 290 or len(stock["etf"]) < 20 or not stock["stock_updated"]:
        log.warning("[ssf] 保證金表不完整：股期 %d 列／ETF %d 列／更新日 %s",
                    len(stock["stock"]), len(stock["etf"]), stock["stock_updated"])
        return {}
    return {**stock, "index_updated": index["updated"], "index": index["items"]}
```

`stocks_power_rich/api/helpers.py` 追加：

```python
def _ssf_margin_table(c) -> dict:
    """股期保證金比例表，**逐日快取**。

    處置股會臨時加成 1.5／2／3 倍，2026-08~09 每隔幾天就有一次公告，
    抓一次放著會給出過期數字（設計 §1.3）。
    讀取端要求 `stock_updated` 存在——缺這個鍵的舊快取一律視為未命中。
    """
    key = f"ssfmargin:v1:{datetime.now().strftime('%Y-%m-%d')}"
    m = get_ai_cache(c, key)
    if not isinstance(m, dict) or not m.get("stock_updated"):
        fresh = taifex_ssf.fetch_ssf_margin_table()
        if fresh.get("stock_updated"):
            set_ai_cache(c, key, fresh)
            return fresh
        return m if isinstance(m, dict) else {}
    return m
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_margin.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 用真實 CSV 驗一次（非 mock）**

Run:

```bash
.venv\Scripts\python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); from stocks_power_rich.sources import taifex_ssf as s; m=s.fetch_ssf_margin_table(); print(len(m['stock']), len(m['etf'])); print(m['stock_updated'], m['etf_updated'], m['index_updated']); print(m['stock']['CDF']); print(m['index']['微型臺指期貨'])"
```

Expected: `296 24`、三個更新日期皆為 `YYYY/MM/DD`、CDF 的 `initial_pct` 是 13.5（或當日公布值）、
微台 `initial` 是 `35050`。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py stocks_power_rich/api/helpers.py tests/test_ssf_margin.py
git commit -m "feat: 保證金比例表解析與日快取（生效日取自 CSV 的更新日期，非 OpenAPI 的 Date）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: 保證金金額計算（Decimal ROUND_HALF_UP）

**Files:**
- Modify: `stocks_power_rich/sources/taifex_ssf.py`
- Modify: `tests/test_ssf_margin.py`

**Interfaces:**
- Consumes: 無
- Produces: `margin_amount(price, multiplier: int, ratio_pct) -> int | None`

**規則（設計 §2.5）**

`單口 = ROUND_HALF_UP(期貨價格 × 契約乘數 × 適用比例)` 到整數元。
**必須 Decimal**：實測 1,181 個合約月中有 101 個 `round()` 與 ROUND_HALF_UP 結果不同。
**用官方公布的兩位小數比例欄**，不可用「結算比例 × 1.35」反推。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf_margin.py`：

```python
def test_margin_matches_the_officially_published_examples():
    # CDF 202610，2026-09-17 結算 2,432，級距1 原始 13.50%
    assert ssf.margin_amount(2432, 2000, 13.50) == 656640
    # DHF 202610，251.5，級距2 16.20%
    assert ssf.margin_amount(251.5, 2000, 16.20) == 81486


def test_margin_uses_round_half_up_not_bankers_rounding():
    """兩個實測的半元案例，券商公布數字對得上的是 ROUND_HALF_UP。

    Python 的 round() 用銀行家進位，這兩個各會少 1 元。實測 1,181 個合約月中
    有 101 個兩者結果不同，所以這不是理論風險。
    """
    assert ssf.margin_amount(238.5, 2000, 20.25) == 96593      # 南亞 CAF，96,592.5
    assert ssf.margin_amount(2453, 100, 13.50) == 33116        # 小型台積電，33,115.5
    assert round(96592.5) == 96592 and round(33115.5) == 33116  # 證明 round() 真的不同


def test_margin_returns_none_when_an_input_is_missing():
    assert ssf.margin_amount(None, 2000, 13.5) is None
    assert ssf.margin_amount(100, 2000, None) is None
    assert ssf.margin_amount(100, 0, 13.5) is None
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_margin.py -k margin_ -q`
Expected: FAIL — `AttributeError: ... 'margin_amount'`

- [ ] **Step 3: 寫最小實作**

追加到 `stocks_power_rich/sources/taifex_ssf.py`（頂端把 import 補成 `from decimal import Decimal, ROUND_HALF_UP`）：

```python
def margin_amount(price, multiplier: int, ratio_pct) -> int | None:
    """單口保證金 ＝ 期貨價格 × 契約乘數 × 適用比例，四捨五入到整數元。

    **必須用 Decimal 的 ROUND_HALF_UP，不可用 Python 的 round()**（銀行家進位）：
    實測 1,181 個合約月中有 101 個兩者不同，例如南亞 CAF 96,592.5 應為 96,593。
    N 口是「先四捨五入單口再乘 N」，所以呼叫端拿到的整數直接相乘即可。
    """
    if price is None or ratio_pct is None or not multiplier:
        return None
    amount = (Decimal(str(price)) * Decimal(int(multiplier))
              * Decimal(str(ratio_pct)) / Decimal("100"))
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_margin.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 反證 Decimal 不是裝飾**

把 `margin_amount` 的最後兩行換成
`return round(float(price) * multiplier * float(ratio_pct) / 100)`，重跑
`.venv\Scripts\python -m pytest tests/test_ssf_margin.py::test_margin_uses_round_half_up_not_bankers_rounding -q`
Expected: FAIL（96592 ≠ 96593）。確認後改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/sources/taifex_ssf.py tests/test_ssf_margin.py
git commit -m "feat: 保證金金額計算（Decimal ROUND_HALF_UP，round() 會少 1 元）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: 每日排程與回補端點

**Files:**
- Modify: `stocks_power_rich/api/helpers.py`
- Modify: `stocks_power_rich/main.py`
- Modify: `stocks_power_rich/api/admin.py`
- Create: `tests/test_ssf_api.py`

**Interfaces:**
- Consumes: `fetch_ssf_daily`（T4）、`summarize_ssf_day`（T2）、`bulk_upsert_ssf_daily`／`get_ssf_dates`／`prune_ssf_daily`（T5）、`_ssf_margin_table`（T7）
- Produces: `helpers.refresh_ssf_daily(c, day=None) -> dict`
  （`{"date","roots","margin_ok","pruned"}` 或 `{"skipped": "weekend"|"already_done"|"data_not_ready"}`）
  ＋ `GET /api/ssf/backfill?days=30`

**排程時段**：平日 `hour="17,18,20", minute="15"`。現有時段已占
17:00（news）／17:30、18:30、19:30（self_screen_early）／21:00／21:10／21:30／21:40。

- [ ] **Step 1: 寫失敗的測試**

`tests/test_ssf_api.py`：

```python
import datetime as _dt

from stocks_power_rich.db import get_connection, init_db, get_ssf_dates, get_ssf_rows
from stocks_power_rich.api import helpers as H
from stocks_power_rich.sources import taifex_ssf as ssf


def _db(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    return conn


def _fake_rows(date, n=1300):
    """n 列一般列（過得了 MIN_GENERAL_ROWS）＋ 每檔一列盤後。"""
    out = []
    for i in range(n):
        out.append({"date": date, "contract": f"X{i:03d}F"[:4], "month": "202610",
                    "session": "一般", "is_spread": False, "open": 1.0, "high": 2.0,
                    "low": 0.5, "close": 1.5, "chg": 0.1, "chg_pct": 7.1,
                    "volume": 10 + i, "settlement": 1.5, "oi": 100})
    return out


def test_refresh_writes_the_day_and_prunes(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily",
                        lambda s, e: _fake_rows(s.replace("/", "-")))
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "2026/09/15"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["date"] == "2026-09-17"
    assert res["roots"] > 0 and res["margin_ok"] is True
    assert get_ssf_dates(conn) == ["2026-09-17"]


def test_refresh_skips_weekends(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily",
                        lambda s, e: (_ for _ in ()).throw(AssertionError("週末不該連外")))
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {})
    assert H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 19))["skipped"] == "weekend"   # 週六


def test_refresh_reports_data_not_ready_when_the_fetcher_guards_reject(tmp_path, monkeypatch):
    """早上跑時抓取器會因『一般列不足』回空——那不是錯誤，是資料還沒發佈。"""
    conn = _db(tmp_path)
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: [])
    monkeypatch.setattr(H, "_ssf_margin_table", lambda c: {"stock_updated": "x"})
    res = H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert res["skipped"] == "data_not_ready"
    assert get_ssf_dates(conn) == []      # 絕不寫進半套


def test_refresh_still_updates_the_margin_table_when_quotes_are_not_ready(tmp_path, monkeypatch):
    """保證金與行情無關；行情沒到齊不該連帶讓保證金整天不更新。"""
    conn = _db(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(ssf, "fetch_ssf_daily", lambda s, e: [])

    def _margin(c):
        called["n"] += 1
        return {"stock_updated": "2026/09/15"}
    monkeypatch.setattr(H, "_ssf_margin_table", _margin)
    H.refresh_ssf_daily(conn, day=_dt.date(2026, 9, 17))
    assert called["n"] == 1


def test_job_schedule_registers_ssf_on_weekday_evenings():
    """時段刻意避開現有全部排程：17:00 news／17:30、18:30、19:30 self_screen_early／
    21:00 daily_update／21:10 news／21:30 osfut／21:40 picks_new_daily。"""
    from stocks_power_rich.config import Config
    cfg = Config(telegram_token="t", telegram_chat_id="c", line_token="l",
                 weekly_push_time="17:00")     # 同 tests/test_job_runs.py::_cfg
    by_id = {s["id"]: s for s in H.job_schedule(cfg, "21:00")}
    assert by_id["ssf_daily"]["dow"] == "mon-fri"
    assert by_id["ssf_daily"]["hour"] == "17,18,20"
    assert by_id["ssf_daily"]["minute"] == "15"
    taken = {(s["hour"], s["minute"]) for k, s in by_id.items() if k != "ssf_daily"}
    assert ("17,18,20", "15") not in taken     # 不與既有時段同分鐘
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'refresh_ssf_daily'`

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/api/helpers.py`：

```python
def refresh_ssf_daily(c, day=None) -> dict:
    """股期每日摘要更新（排程 `ssf_daily` 用）。

    回傳一個看得懂的 dict 進 `job_runs.note`——「今天沒做」與「做了但沒資料」要分得出來。
    保證金與行情是**兩件獨立的事**：行情沒到齊時保證金照樣更新（處置股加成天天在變）。
    """
    d = day or _now().date()
    margin_ok = bool(_ssf_margin_table(c).get("stock_updated"))
    if d.weekday() >= 5:
        return {"skipped": "weekend", "margin_ok": margin_ok}
    ds = d.strftime("%Y-%m-%d")
    if ds in get_ssf_dates(c, limit=3):
        return {"skipped": "already_done", "date": ds, "margin_ok": margin_ok}

    # 一併重抓前 2 個交易日：官方偶有更正，而 COALESCE 讓重寫是安全的
    start = (d - timedelta(days=6)).strftime("%Y/%m/%d")
    rows = taifex_ssf.fetch_ssf_daily(start, d.strftime("%Y/%m/%d"))
    if not rows:
        return {"skipped": "data_not_ready", "date": ds, "margin_ok": margin_ok}
    by_date: dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    summary = []
    for one in by_date.values():
        summary.extend(taifex_ssf.summarize_ssf_day(one))
    bulk_upsert_ssf_daily(c, summary)
    pruned = prune_ssf_daily(c, keep_days=60)
    return {"date": ds, "days": len(by_date), "roots": len(summary),
            "margin_ok": margin_ok, "pruned": pruned}
```

（檔頭補 `from ..db import bulk_upsert_ssf_daily, get_ssf_dates, prune_ssf_daily`
與 `from datetime import timedelta`，若尚未匯入。）

`job_schedule` 的 `specs` list 追加一筆（緊接 `self_screen_early` 之後）：

```python
        # 股期概況：平日 17:15／18:15／20:15 各試一次。D 的日盤資料幾點發佈尚未量到
        # （逐筆檔的 Last-Modified 是 16:38），三次重試就是為了吸收這個未知；
        # 抓取器的守衛會擋掉「只有夜盤」的半套資料，所以早試不會寫壞。
        {"id": "ssf_daily", "family": "ssf_daily",
         "hour": "17,18,20", "minute": "15", "dow": "mon-fri"},
```

`stocks_power_rich/main.py` 的 `raw_jobs` 加：

```python
        "ssf_daily": lambda: _helpers.refresh_ssf_daily(conn()),
```

（照該檔既有寫法對齊；`grep -n "raw_jobs" stocks_power_rich/main.py` 找到位置。）

`stocks_power_rich/api/admin.py` 追加：

```python
@router.get("/ssf/backfill")
def ssf_backfill(days: int = 30):
    """一次性回補股期歷史（熱力圖要近 10 個交易日）。

    官方限制查詢區間不可超過一個月，所以逐段切；實測 14 天一段在 Zeabur 是
    1.65MB／2.7 秒，離代理逾時很遠，維持同步即可（設計 §8.1）。
    """
    from datetime import date as _d, timedelta as _td
    c = conn()
    end = _d.today()
    total, wrote = 0, 0
    while total < days:
        span = min(25, days - total)
        start = end - _td(days=span)
        rows = taifex_ssf.fetch_ssf_daily(start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d"))
        by_date = {}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        summary = []
        for one in by_date.values():
            summary.extend(taifex_ssf.summarize_ssf_day(one))
        wrote += bulk_upsert_ssf_daily(c, summary)
        total += span + 1
        end = start - _td(days=1)
    return {"wrote": wrote, "dates": len(get_ssf_dates(c, limit=90))}
```

（`admin.py` 檔頭補 `from ..sources import taifex_ssf` 與
`from ..db import bulk_upsert_ssf_daily, get_ssf_dates`。）

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 反證兩道守衛**

1. 把 `if not rows: return {"skipped": "data_not_ready"...}` 拿掉 →
   `test_refresh_reports_data_not_ready_when_the_fetcher_guards_reject` 轉紅。
2. 把 `margin_ok = ...` 移到 `if d.weekday() >= 5` 之後、且移到 `if not rows` 之後 →
   `test_refresh_still_updates_the_margin_table_when_quotes_are_not_ready` 轉紅。

確認後各自改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/api/helpers.py stocks_power_rich/main.py stocks_power_rich/api/admin.py tests/test_ssf_api.py
git commit -m "feat: 股期每日排程（平日 17:15/18:15/20:15）與回補端點

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: `GET /api/ssf/margin` 端點

**Files:**
- Modify: `stocks_power_rich/api/market.py`
- Modify: `tests/test_ssf_api.py`

**Interfaces:**
- Consumes: `_ssf_contracts`（T6）、`_ssf_margin_table`（T7）、`margin_amount`（T8）、`get_ssf_rows`／`get_ssf_dates`（T5）
- Produces: `GET /api/ssf/margin` →
  ```json
  {"price_date": "2026-09-17",
   "stock_updated": "2026/09/15", "etf_updated": "2026/08/12", "index_updated": "2026/08/12",
   "rows": [{"root","contract","name","code","multiplier","initial_pct","initial","maintenance","kind"}],
   "by_stock": {"2330": [{...}, {...}]},
   "index": [{"name","initial","maintenance"}]}
  ```
  `kind` ∈ `"stock" | "etf" | "index"`。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf_api.py`：

```python
import os, tempfile


def _client(monkeypatch, tmp_path):
    os.environ["SPR_DB_PATH"] = str(tmp_path / "api.sqlite")
    from fastapi.testclient import TestClient
    from stocks_power_rich.main import create_app
    return TestClient(create_app(enable_scheduler=False))


_CONTRACTS = {
    "CD": {"code": "2330", "stock_name": "台積電", "name": "台積電",
           "multiplier": 2000, "is_etf": False, "is_mini": False},
    "QF": {"code": "2330", "stock_name": "台積電", "name": "小型台積電",
           "multiplier": 100, "is_etf": False, "is_mini": True},
    "NY": {"code": "0050", "stock_name": "元大台灣50", "name": "元大台灣50",
           "multiplier": 10000, "is_etf": True, "is_mini": False},
}
_MARGIN = {
    "stock_updated": "2026/09/15", "etf_updated": "2026/08/12", "index_updated": "2026/08/12",
    "stock": {"CDF": {"code": "2330", "name": "台積電期貨", "tier": "級距1",
                      "clearing_pct": 10.0, "maintenance_pct": 10.35, "initial_pct": 13.50},
              "QFF": {"code": "2330", "name": "小型台積電期貨", "tier": "級距1",
                      "clearing_pct": 10.0, "maintenance_pct": 10.35, "initial_pct": 13.50}},
    "etf": {"NYF": {"code": "0050", "name": "元大台灣50ETF期貨",
                    "clearing": 64000, "maintenance": 67000, "initial": 87000}},
    "index": {"微型臺指期貨": {"clearing": 25950, "maintenance": 26900, "initial": 35050},
              "臺股期貨": {"clearing": 519000, "maintenance": 538000, "initial": 701000}},
}


def _seed_margin(monkeypatch, tmp_path):
    from stocks_power_rich.api import market as M
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_ssf_margin_table", lambda c: _MARGIN)
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    bulk_upsert_ssf_daily(conn, [
        {"date": "2026-09-17", "root": "CD", "main_month": "202610", "settlement": 2432.0,
         "close": 2433.0, "chg_pct": 1.33, "volume": 8192, "oi": 25045, "main_volume": 6027,
         "open": None, "high": None, "low": None, "chg": None},
        {"date": "2026-09-17", "root": "QF", "main_month": "202610", "settlement": 2432.0,
         "close": 2434.0, "chg_pct": 1.37, "volume": 36062, "oi": 53174, "main_volume": 26032,
         "open": None, "high": None, "low": None, "chg": None},
        {"date": "2026-09-17", "root": "NY", "main_month": "202610", "settlement": 108.3,
         "close": 108.3, "chg_pct": 0.5, "volume": 100, "oi": 500, "main_volume": 100,
         "open": None, "high": None, "low": None, "chg": None},
    ])


def test_margin_endpoint_computes_stock_futures_from_settlement(monkeypatch, tmp_path):
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    assert d["price_date"] == "2026-09-17"
    assert d["stock_updated"] == "2026/09/15"
    by_root = {r["root"]: r for r in d["rows"]}
    assert by_root["CD"]["initial"] == 656640      # 2432 × 2000 × 13.50%
    assert by_root["QF"]["initial"] == 32832       # 2432 × 100 × 13.50%


def test_margin_endpoint_uses_the_published_amount_for_etf_futures(monkeypatch, tmp_path):
    """ETF 期貨公布固定金額，不可套價格×比例（108.3 × 10000 × 比例 會完全不同）。"""
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    ny = {r["root"]: r for r in d["rows"]}["NY"]
    assert ny["initial"] == 87000 and ny["kind"] == "etf"
    assert ny["initial_pct"] is None


def test_margin_endpoint_includes_tmf(monkeypatch, tmp_path):
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    tmf = [x for x in d["index"] if "微型" in x["name"]]
    assert tmf and tmf[0]["initial"] == 35050


def test_margin_endpoint_builds_the_by_stock_index_on_the_server(monkeypatch, tmp_path):
    """個股頁要用股票代號反查。索引在後端組，前端不得自己掃 320 列組第二份。"""
    _seed_margin(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/margin").json()
    assert {x["root"] for x in d["by_stock"]["2330"]} == {"CD", "QF"}
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -k margin_endpoint -q`
Expected: FAIL — 404

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/api/market.py` 追加（檔頭補
`from .helpers import _ssf_contracts, _ssf_margin_table` 與
`from ..sources import taifex_ssf`、`from ..db import get_ssf_dates, get_ssf_rows`）：

```python
@router.get("/ssf/margin")
def ssf_margin():
    """各檔股期／ETF 期貨／台指系列的**單口**原始與維持保證金。

    口數換算刻意留給前端乘：規則是「先四捨五入單口再乘 N」，所以前端乘整數完全正確，
    不必為了改口數往返伺服器。
    """
    c = conn()
    contracts = _ssf_contracts(c)
    margin = _ssf_margin_table(c)
    dates = get_ssf_dates(c, limit=1)
    price_date = dates[0] if dates else None
    prices = {r["root"]: r for r in get_ssf_rows(c, dates)} if dates else {}

    rows, by_stock = [], {}
    for root, info in sorted(contracts.items()):
        contract = root + "F"
        rec = {"root": root, "contract": contract, "name": info["name"],
               "code": info["code"], "multiplier": info["multiplier"],
               "initial_pct": None, "initial": None, "maintenance": None,
               "kind": "etf" if info["is_etf"] else "stock"}
        if info["is_etf"]:
            # ETF 期貨公布固定金額，不套價格×比例
            m = (margin.get("etf") or {}).get(contract)
            if m:
                rec["initial"], rec["maintenance"] = m["initial"], m["maintenance"]
        else:
            m = (margin.get("stock") or {}).get(contract)
            settle = (prices.get(root) or {}).get("settlement")
            if m:
                rec["initial_pct"] = m["initial_pct"]
                rec["initial"] = taifex_ssf.margin_amount(
                    settle, info["multiplier"], m["initial_pct"])
                rec["maintenance"] = taifex_ssf.margin_amount(
                    settle, info["multiplier"], m["maintenance_pct"])
        rows.append(rec)
        by_stock.setdefault(info["code"], []).append(rec)

    index = [{"name": k.replace("期貨", ""), "initial": v["initial"],
              "maintenance": v["maintenance"], "kind": "index"}
             for k, v in (margin.get("index") or {}).items()
             if "選擇權" not in k]
    return {"price_date": price_date,
            "stock_updated": margin.get("stock_updated"),
            "etf_updated": margin.get("etf_updated"),
            "index_updated": margin.get("index_updated"),
            "rows": rows, "by_stock": by_stock, "index": index}
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 反證 ETF 不套公式**

把 `if info["is_etf"]:` 那一段拿掉（讓 ETF 也走比例路徑），重跑
`test_margin_endpoint_uses_the_published_amount_for_etf_futures`
Expected: FAIL（`initial` 不再是 87000）。確認後改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/api/market.py tests/test_ssf_api.py
git commit -m "feat: GET /api/ssf/margin（股期比例算、ETF 用公布金額、含微台）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: `GET /api/ssf/overview` 端點

**Files:**
- Modify: `stocks_power_rich/api/market.py`
- Modify: `tests/test_ssf_api.py`

**Interfaces:**
- Consumes: `get_ssf_dates`／`get_ssf_rows`（T5）、`_ssf_contracts`（T6）、`ssf_basis_ticks`（T3）、`_quotes_for`／`_otc_quotes_for`（既有）
- Produces: `GET /api/ssf/overview?date=` →
  ```json
  {"date","dates":[...],
   "hot":[{root,name,code,close,chg,chg_pct,volume,oi,main_month}],
   "ranks":{"volume":[...],"gainers":[...],"losers":[...]},
   "basis":[{root,name,code,futures,spot,diff,ticks}],
   "oi_change":{"up":[...],"down":[...]},
   "heatmap":{"dates":[...],"rows":[[cell|null,...],...]},
   "coverage":{"stored_days","roots","no_stock_code","no_spot"}}
  ```
  `ranks` 的每筆另帶 `open_pct/high_pct/low_pct/close_pct/amplitude/ref`（相對前日結算價的 %）。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf_api.py`：

```python
def _seed_overview(monkeypatch, tmp_path):
    from stocks_power_rich.api import market as M
    monkeypatch.setattr(M, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(M, "_quotes_for", lambda c, d: {"2330": {"close": 2425.0}})
    monkeypatch.setattr(M, "_otc_quotes_for", lambda c, d: {})
    conn = get_connection(str(tmp_path / "api.sqlite"))
    init_db(conn)
    from stocks_power_rich.db import bulk_upsert_ssf_daily
    rows = []
    for day, oi_cd in (("2026-09-16", 24000), ("2026-09-17", 25045)):
        rows += [
            {"date": day, "root": "CD", "main_month": "202610", "open": 2425.0,
             "high": 2453.0, "low": 2410.0, "close": 2433.0, "chg": 32.0, "chg_pct": 1.33,
             "settlement": 2432.0, "oi": oi_cd, "volume": 8192, "main_volume": 6027},
            {"date": day, "root": "QF", "main_month": "202610", "open": 2423.0,
             "high": 2453.0, "low": 2412.0, "close": 2434.0, "chg": 33.0, "chg_pct": -2.5,
             "settlement": 2432.0, "oi": 53174, "volume": 36062, "main_volume": 26032},
        ]
    bulk_upsert_ssf_daily(conn, rows)


def test_overview_ranks_hot_by_official_lot_count(monkeypatch, tmp_path):
    """官方 STFTop10 口徑：依口數排，小型合約自成一檔（規模不同，副標要註明）。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["date"] == "2026-09-17"
    assert [x["root"] for x in d["hot"]] == ["QF", "CD"]     # 36062 > 8192
    assert d["hot"][0]["name"] == "小型台積電"


def test_overview_splits_gainers_and_losers_by_official_chg_pct(monkeypatch, tmp_path):
    """漲幅榜只收真的漲的、跌幅榜只收真的跌的——湊滿榜單的那一列會直接說謊。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["root"] for x in d["ranks"]["gainers"]] == ["CD"]
    assert [x["root"] for x in d["ranks"]["losers"]] == ["QF"]


def test_overview_candle_percentages_are_against_the_prior_settlement(monkeypatch, tmp_path):
    """參考價＝收盤−漲跌（官方漲跌是對前日結算價，不是對前日收盤）。"""
    _seed_overview(monkeypatch, tmp_path)
    cd = [x for x in _client(monkeypatch, tmp_path).get(
        "/api/ssf/overview").json()["ranks"]["volume"] if x["root"] == "CD"][0]
    assert cd["ref"] == 2401.0                                  # 2433 − 32
    assert round(cd["close_pct"], 2) == 1.33
    assert round(cd["amplitude"], 2) == round((2453 - 2410) / 2401 * 100, 2)


def test_overview_basis_is_in_ticks_and_lists_each_underlying_once(monkeypatch, tmp_path):
    """標準與小型共用同一個結算價，同一檔標的只列一列。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert [x["code"] for x in d["basis"]] == ["2330"]
    b = d["basis"][0]
    assert b["futures"] == 2432.0 and b["spot"] == 2425.0
    assert b["ticks"] == 7          # 2425→2432，2500 以下 1 元一檔


def test_overview_oi_change_needs_both_days(monkeypatch, tmp_path):
    """前一日缺列就整檔不列——缺值不可當成 0（那會捏造一筆大增）。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    ups = {x["root"]: x for x in d["oi_change"]["up"]}
    assert ups["CD"]["oi_change"] == 1045
    assert "QF" not in ups          # QF 兩天相同，不算增加


def test_overview_heatmap_leaves_a_missing_day_empty(monkeypatch, tmp_path):
    """缺的交易日是空欄，絕不拿別天的資料頂替。"""
    _seed_overview(monkeypatch, tmp_path)
    d = _client(monkeypatch, tmp_path).get("/api/ssf/overview").json()
    assert d["heatmap"]["dates"] == ["2026-09-16", "2026-09-17"]
    assert len(d["heatmap"]["rows"]) >= 1
    assert d["heatmap"]["rows"][0][0]["root"] in ("QF", "CD")
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -k overview -q`
Expected: FAIL — 404

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/api/market.py` 追加：

```python
SSF_HOT_N = 10
SSF_RANK_N = 20
SSF_HEATMAP_DAYS = 10
SSF_HEATMAP_ROWS = 10


def _ssf_cell(row: dict, info: dict) -> dict:
    return {"root": row["root"], "name": (info or {}).get("name") or row["root"],
            "code": (info or {}).get("code"),
            "close": row.get("close"), "chg": row.get("chg"),
            "chg_pct": row.get("chg_pct"), "volume": row.get("volume") or 0,
            "oi": row.get("oi"), "main_month": row.get("main_month")}


def _ssf_candle(row: dict, info: dict) -> dict:
    """相對前日結算價的 %。參考價＝收盤−漲跌（官方漲跌是對前日**結算價**）。

    絕對價格也要帶上——tooltip 同時顯示價與 %，少了 open/high/low 那三行會全是「—」。
    """
    out = _ssf_cell(row, info)
    out.update({k: row.get(k) for k in ("open", "high", "low")})
    close, chg = row.get("close"), row.get("chg")
    ref = (close - chg) if (close is not None and chg is not None) else None
    out["ref"] = ref
    for key, src in (("open_pct", "open"), ("high_pct", "high"),
                     ("low_pct", "low"), ("close_pct", "close")):
        v = row.get(src)
        out[key] = round((v - ref) / ref * 100, 2) if (ref and v is not None) else None
    hi, lo = row.get("high"), row.get("low")
    out["amplitude"] = (round((hi - lo) / ref * 100, 2)
                        if (ref and hi is not None and lo is not None) else None)
    return out


@router.get("/ssf/overview")
def ssf_overview(date: str | None = None):
    """股期概況：熱門、量漲跌前 20、期現價差、未平倉增減、近 10 日排行熱力圖。"""
    c = conn()
    dates = get_ssf_dates(c, limit=SSF_HEATMAP_DAYS)
    if not dates:
        return {"date": None, "dates": [], "hot": [], "ranks": {}, "basis": [],
                "oi_change": {"up": [], "down": []}, "heatmap": {"dates": [], "rows": []},
                "coverage": {"stored_days": 0}}
    day = date if date in dates else dates[0]
    contracts = _ssf_contracts(c)
    rows_all = get_ssf_rows(c, dates)
    by_day: dict[str, list] = {}
    for r in rows_all:
        by_day.setdefault(r["date"], []).append(r)
    today_rows = sorted(by_day.get(day, []), key=lambda r: r.get("volume") or 0, reverse=True)

    hot = [_ssf_cell(r, contracts.get(r["root"])) for r in today_rows[:SSF_HOT_N]]
    vol_rank = [_ssf_candle(r, contracts.get(r["root"])) for r in today_rows[:SSF_RANK_N]]
    with_pct = [r for r in today_rows if r.get("chg_pct") is not None]
    gainers = [_ssf_candle(r, contracts.get(r["root"]))
               for r in sorted(with_pct, key=lambda r: r["chg_pct"], reverse=True)
               if r["chg_pct"] > 0][:SSF_RANK_N]
    losers = [_ssf_candle(r, contracts.get(r["root"]))
              for r in sorted(with_pct, key=lambda r: r["chg_pct"])
              if r["chg_pct"] < 0][:SSF_RANK_N]

    # 期現價差：現貨收盤走既有的逐日快取（含 ETF；stock_ohlc 濾掉 ETF 且稀疏，不可用）
    spots = {**_quotes_for(c, day), **_otc_quotes_for(c, day)}
    basis, seen, no_code, no_spot = [], set(), 0, 0
    for r in today_rows:
        info = contracts.get(r["root"])
        if not info:
            no_code += 1
            continue
        if info["code"] in seen:        # 標準與小型共用結算價，同一檔標的只列一次
            continue
        spot = (spots.get(info["code"]) or {}).get("close")
        fut = r.get("settlement")
        if spot is None or fut is None:
            no_spot += 1
            continue
        seen.add(info["code"])
        basis.append({"root": r["root"], "name": info["name"], "code": info["code"],
                      "futures": fut, "spot": spot, "diff": round(fut - spot, 4),
                      "ticks": taifex_ssf.ssf_basis_ticks(fut, spot, info["is_etf"]),
                      # 收盤晚於現貨 13:30 的（14 檔 ETF 期貨到 16:15）要另標，
                      # 它們的落差是 2.5 小時而不是 15 分鐘，不能與其他列一起讀
                      "late_session": bool(info.get("late_session")),
                      "session_end": info.get("session_end") or ""})
        if len(basis) >= SSF_RANK_N:
            break

    # 未平倉增減：前一日缺列就整檔不列（缺值當 0 會捏造一筆大增）
    idx = dates.index(day)
    prev = {r["root"]: r for r in by_day.get(dates[idx + 1], [])} if idx + 1 < len(dates) else {}
    changes = []
    for r in today_rows:
        p = prev.get(r["root"])
        if not p or r.get("oi") is None or p.get("oi") is None:
            continue
        d = r["oi"] - p["oi"]
        if d:
            changes.append({**_ssf_cell(r, contracts.get(r["root"])), "oi_change": d,
                            "oi_prev": p["oi"]})
    ups = sorted([x for x in changes if x["oi_change"] > 0],
                 key=lambda x: x["oi_change"], reverse=True)[:SSF_HOT_N]
    downs = sorted([x for x in changes if x["oi_change"] < 0],
                   key=lambda x: x["oi_change"])[:SSF_HOT_N]

    # 熱力圖：名次 × 交易日（舊到新）。缺的交易日是空欄，不拿別天頂替。
    hm_dates = sorted(dates)
    # 每天只排一次序——放進名次迴圈裡會對同一份資料排 10 次（10 名次 × 10 天＝100 次）
    ranked = {d: sorted(by_day.get(d, []), key=lambda r: r.get("volume") or 0, reverse=True)
              for d in hm_dates}
    grid = []
    for rank in range(SSF_HEATMAP_ROWS):
        line = []
        for d in hm_dates:
            day_rows = ranked[d]
            if rank < len(day_rows):
                rr = day_rows[rank]
                info = contracts.get(rr["root"]) or {}
                line.append({"root": rr["root"], "name": info.get("name") or rr["root"],
                             "chg_pct": rr.get("chg_pct")})
            else:
                line.append(None)
        grid.append(line)

    return {"date": day, "dates": dates, "hot": hot,
            "ranks": {"volume": vol_rank, "gainers": gainers, "losers": losers},
            "basis": basis, "oi_change": {"up": ups, "down": downs},
            "heatmap": {"dates": hm_dates, "rows": grid},
            "coverage": {"stored_days": len(dates), "roots": len(today_rows),
                         "no_stock_code": no_code, "no_spot": no_spot}}
```

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -q`
Expected: PASS（15 passed）

- [ ] **Step 5: 反證兩條**

1. 把 `if info["code"] in seen: continue` 拿掉 →
   `test_overview_basis_is_in_ticks_and_lists_each_underlying_once` 轉紅（2330 出現兩次）。
2. 把 `if not p or ... continue` 改成 `p = prev.get(r["root"]) or {"oi": 0}` →
   `test_overview_oi_change_needs_both_days` 轉紅（QF 被算成大增）。

確認後各自改回來。

- [ ] **Step 6: Commit**

```bash
git add stocks_power_rich/api/market.py tests/test_ssf_api.py
git commit -m "feat: GET /api/ssf/overview（熱門/前20/價差/未平倉增減/10日熱力圖）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: 前端頁面骨架、導覽與熱門股期卡片

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `GET /api/ssf/overview`（T11）
- Produces: `loadSsf()`、`ssfData`（全域，後續區塊共用同一份回應）、`ssfLoaded` 旗標

- [ ] **Step 1: 加導覽與視圖容器**

`web/index.html` 的「市場」`nav-group` 內、`hiprice` 那一列之後加：

```html
        <a class="nav" data-view="ssf" data-short="股期"><span class="ico">📐</span><span class="lbl">股期概況</span></a>
```

（**不給 `data-primary`**：手機底部列只有 4 格常駐，這頁進「更多」抽屜。）

在其他 `<section class="view" id="view-...">` 之後加：

```html
    <section class="view" id="view-ssf">
      <div class="pane-heading">
        <h2 class="group-title">股期概況</h2>
        <span class="pane-count" id="ssf-note"></span>
        <!-- 沿用全站既有的新鮮度徽章（`:empty { display:none }` 讓它平時不佔位），
             不另開一套樣式——`.csv-stale` 那次就是複製一份然後各自漂移。 -->
        <span class="freshness" id="ssf-fresh"></span>
      </div>

      <div class="card-group" data-key="ssf-hot">
        <div class="group-title">熱門股期　<span class="gt-sub">依官方成交口數；口數含小型合約，規模不同</span></div>
        <div class="cards" id="ssf-hot"></div>
      </div>
    </section>
```

- [ ] **Step 2: 寫載入函式**

`web/app.js`，在 `selfScreenLoaded` 那組旗標附近加：

```js
// 股期概況：進頁才載入（比照 cupLoaded）。四個區塊共用同一份 /api/ssf/overview 回應。
let ssfLoaded = false, ssfData = null;
```

在 `showView` 的分派區（`self-screen` 那行之後）加：

```js
  if (name === "ssf" && !ssfLoaded) { ssfLoaded = true; loadSsf(); }
```

新增函式（放在 `loadSelfScreen` 附近）：

```js
async function loadSsf() {
  const note = $("ssf-note");
  try {
    ssfData = await getJSON("/api/ssf/overview");
  } catch (e) {
    if (note) note.textContent = "讀取失敗";
    return;
  }
  const d = ssfData;
  if (!d || !d.date) {
    if (note) note.textContent = "尚無股期資料，請先執行 /api/ssf/backfill";
    return;
  }
  const cov = d.coverage || {};
  if (note) {
    note.textContent = `資料日 ${d.date}　${cov.roots || 0} 檔　已存 ${cov.stored_days || 0} 個交易日`
      + (cov.no_stock_code ? `　${cov.no_stock_code} 檔查不到標的代號` : "");
  }
  renderSsfFreshness(d.date);
  renderSsfHot(d.hot || []);
}

// SSF_STALE_DAYS = 2：落後 1 天是常態（盤後才更新、假日不開盤），1 天就叫會變成
// 永遠亮著的裝飾。落差用**日曆天**，沿用 `renderFreshness` 的既有決定——跨週末說
// 「落後 3 天」是事實，硬換算成交易日會讓週一早上看起來像資料很新。
const SSF_STALE_DAYS = 2;

function renderSsfFreshness(date) {
  const el = $("ssf-fresh"); if (!el) return;
  el.textContent = ""; el.classList.remove("stale");
  if (!date) return;
  const days = Math.floor((new Date().setHours(0, 0, 0, 0)
    - new Date(date + "T00:00:00").getTime()) / 86400000);
  if (days >= SSF_STALE_DAYS) { el.textContent = `落後 ${days} 天`; el.classList.add("stale"); }
}

function renderSsfHot(rows) {
  const el = $("ssf-hot"); if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty">尚無資料</div>'; return; }
  el.innerHTML = rows.map(r => {
    const dir = r.chg_pct == null ? "" : (r.chg_pct >= 0 ? "up" : "down");
    return `<div class="card"><div class="card-title">${esc(r.name)}</div>`
      + `<div class="card-val">${fmt(r.close)}</div>`
      + `<div class="card-chg ${dir}">${r.chg_pct == null ? "—" : (r.chg_pct >= 0 ? "+" : "") + r.chg_pct.toFixed(2) + "%"}</div>`
      + `<div class="card-note">${esc(r.code || "")}　${fmt(r.volume, 0)} 口　OI ${fmt(r.oi, 0)}</div></div>`;
  }).join("");
}
```

> `esc(s)`／`fmt(v, d = 2)`／`$(id)`／`getJSON(url)` 都是 `web/app.js` 既有函式
> （`app.js:6`、`app.js:8`），**不要新造**。`fmt` 預設 2 位小數，整數欄位傳 `0`。

- [ ] **Step 3: 在瀏覽器實測**

啟動：`.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000`
先呼叫 `http://127.0.0.1:8000/api/ssf/backfill?days=20` 灌資料，再開頁面切到「股期概況」。

Expected：卡片列出 10 檔、有名稱／價格／紅綠漲跌／口數；說明列顯示資料日與檔數；
console 無錯誤、無 CSP 警告。

**新鮮度徽章要實跑兩種狀態驗，不能只確認它沒報錯**（它平時不顯示，做錯了看不出來）。
console：

```js
renderSsfFreshness(new Date(Date.now() - 86400000).toISOString().slice(0, 10));
console.log("昨天：", JSON.stringify($("ssf-fresh").textContent));   // 期望 ""
renderSsfFreshness("2026-09-01");
console.log("很舊：", $("ssf-fresh").textContent,
            getComputedStyle($("ssf-fresh")).color);                 // 期望有字、琥珀
```

Expected：落後 1 天不顯示、落後很多天顯示「落後 N 天」且顏色是 `--accent` 的琥珀。
驗完重新載入頁面。

- [ ] **Step 4: 手機寬度檢查**

用瀏覽器開發者工具設 375×812，確認整頁**無水平溢出**、卡片不被擠爆
（`.card` 已有 `min-width: 0`，不需另加）。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 股期概況頁骨架與熱門股期卡片

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: 成交量／漲幅／跌幅前 20 迷你 K

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `ssfData.ranks`（T11／T12）
- Produces: `ssfCharts`（三個 ECharts 實例，供 resize handler 用）

**要點**：**三張圖各 20 根 K 棒**（不是 60 張小圖）。X 軸＝合約簡稱、Y 軸＝相對前日結算價的 %。
K 棒用亮色 `C.up`／`C.down`（上面沒有文字）。**缺值給 `'-'` 絕不給 `null`**。

- [ ] **Step 1: 加容器**

`web/index.html` 的 `#view-ssf` 內追加：

```html
      <div class="card-group span-full" data-key="ssf-ranks">
        <div class="group-title">成交量／漲幅／跌幅前 20　<span class="gt-sub">K 棒為相對前一日結算價的 %</span></div>
        <div class="chart" id="ssf-chart-volume" style="height:260px"></div>
        <div class="chart" id="ssf-chart-gainers" style="height:260px"></div>
        <div class="chart" id="ssf-chart-losers" style="height:260px"></div>
      </div>
```

- [ ] **Step 2: 寫繪圖函式**

`web/app.js`：

```js
let ssfCharts = {};

function ssfCandleOption(rows) {
  const names = rows.map(r => r.name);
  // ECharts candlestick 是 [open, close, low, high]；**缺值給 '-' 絕不給 null**
  // （null 會在 getInitialData 丟 TypeError 讓整個 setOption 中止且不進 console）。
  const data = rows.map(r => {
    const v = [r.open_pct, r.close_pct, r.low_pct, r.high_pct];
    return v.some(x => x == null) ? "-" : v;
  });
  return {
    grid: { left: 48, right: 16, top: 16, bottom: 64 },
    xAxis: { type: "category", data: names, axisLabel: { rotate: 45, fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: "{value}%" }, splitNumber: 4 },
    tooltip: {
      trigger: "axis",
      formatter: (ps) => {
        const r = rows[ps[0].dataIndex]; if (!r) return "";
        const pct = (x) => x == null ? "—" : x.toFixed(2) + "%";
        return `${esc(r.name)}（${esc(r.main_month || "")}）<br>`
          + `開 ${fmt(r.open)} ${pct(r.open_pct)}<br>`
          + `高 ${fmt(r.high)} ${pct(r.high_pct)}<br>`
          + `低 ${fmt(r.low)} ${pct(r.low_pct)}<br>`
          + `收 ${fmt(r.close)} ${pct(r.close_pct)}<br>`
          + `參考價 ${fmt(r.ref)}（前日結算）<br>`
          + `量 ${fmt(r.volume, 0)} 口　OI ${fmt(r.oi, 0)}<br>`
          + `振幅 ${pct(r.amplitude)}`;
      },
    },
    series: [{
      type: "candlestick", data,
      itemStyle: { color: C.up, color0: C.down, borderColor: C.up, borderColor0: C.down },
    }],
  };
}

function renderSsfRanks(ranks) {
  [["volume", "ssf-chart-volume"], ["gainers", "ssf-chart-gainers"],
   ["losers", "ssf-chart-losers"]].forEach(([key, id]) => {
    const el = $(id); if (!el) return;
    const rows = (ranks && ranks[key]) || [];
    if (!rows.length) { el.innerHTML = '<div class="empty">尚無資料</div>'; return; }
    const ch = ssfCharts[key] || (ssfCharts[key] = echarts.init(el));
    ch.setOption(ssfCandleOption(rows), true);
    ch.resize();   // echarts.init 會凍結它看到的容器尺寸，setOption 後一定要 resize
  });
}
```

在 `loadSsf()` 的 `renderSsfHot(...)` 之後加 `renderSsfRanks(d.ranks);`。

在 `window` 的 resize handler（`grep -n "addEventListener(\"resize\"" web/app.js`）
把三張圖加進去：

```js
  Object.values(ssfCharts).forEach(ch => ch && ch.resize());
```

- [ ] **Step 3: 在瀏覽器實測**

Expected：三張圖各 20 根 K 棒、紅漲綠跌、X 軸是合約簡稱、Y 軸是 %；
hover 顯示 OHLC（價與 %）、參考價、量、OI、振幅、主力月；console 無錯誤。

- [ ] **Step 4: 驗證缺值不會炸圖**

在 console 執行：

```js
renderSsfRanks({volume: [{name:"測試", open_pct:null, close_pct:1, low_pct:0, high_pct:2, ref:100}]});
```

Expected：圖照常畫出（該根是空的），**不出現 `Cannot read properties of null`**。
驗完重新載入頁面。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js
git commit -m "feat(ui): 股期量/漲/跌前 20 迷你 K（三張圖各 20 根，缺值給 '-'）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 14: 期現價差表

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `ssfData.basis`（T11）

**要點**：**正負不著紅綠**（那不是價格方向），用中性色 ＋ 文字標「正價差／逆價差」。
標題必須寫出時間不同步。

- [ ] **Step 1: 加容器**

`web/index.html` 的 `#view-ssf` 內追加：

```html
      <div class="card-group" data-key="ssf-basis">
        <div class="group-title">熱門股期對現貨價差　<span class="gt-sub">期貨 13:45 結算價 vs 現貨 13:30 收盤，兩者時間不同步</span></div>
        <div class="table-wrap" id="ssf-basis-wrap">
          <table id="ssf-basis-table"><thead><tr>
            <th scope="col">標的</th><th scope="col">期貨</th><th scope="col">現貨</th>
            <th scope="col">價差</th><th scope="col">檔數</th><th scope="col">方向</th>
          </tr></thead><tbody></tbody></table>
        </div>
      </div>
```

- [ ] **Step 2: 寫繪製函式**

`web/app.js`：

```js
function renderSsfBasis(rows) {
  const tb = document.querySelector("#ssf-basis-table tbody"); if (!tb) return;
  if (!rows.length) { tb.innerHTML = '<tr><td colspan="6">尚無資料</td></tr>'; return; }
  // 價差的正負**不是漲跌方向**，所以不套紅綠；方向用文字，數值用中性色。
  tb.innerHTML = rows.map(r => `<tr>
    <td class="ssf-name">${esc(r.name)}<span class="ssf-code">${esc(r.code || "")}</span>${
      // 收盤到 16:15 的那 14 檔 ETF 期貨，落差是 2.5 小時而非 15 分鐘，必須標出來
      r.late_session ? `<span class="ssf-late" title="期貨交易到 ${esc(r.session_end)}，與現貨 13:30 收盤落差更大">⏱</span>` : ""}</td>
    <td>${fmt(r.futures)}</td><td>${fmt(r.spot)}</td>
    <td>${r.diff == null ? "—" : (r.diff > 0 ? "+" : "") + r.diff}</td>
    <td>${r.ticks == null ? "—" : (r.ticks > 0 ? "+" : "") + r.ticks}</td>
    <td class="ssf-basis-dir">${r.ticks == null ? "—" : (r.ticks > 0 ? "正價差" : r.ticks < 0 ? "逆價差" : "持平")}</td>
  </tr>`).join("");
}
```

在 `loadSsf()` 內加 `renderSsfBasis(d.basis || []);`。

`web/styles.css` 追加：

```css
/* 價差表：正負是「期貨相對現貨的位置」，不是漲跌，所以一律中性色。 */
#ssf-basis-table .ssf-basis-dir { color: var(--muted); }
#ssf-basis-table .ssf-code { color: var(--muted); font-size: var(--fs-xxs); margin-left: 6px; }
#ssf-basis-table .ssf-late { margin-left: 4px; color: var(--muted); cursor: help; }
```

- [ ] **Step 3: 在瀏覽器實測**

Expected：表格列出至多 20 檔、每檔標的只出現一次、檔數是整數；**畫面上沒有任何紅或綠**。

驗證顏色不是靠眼睛看：在 console 執行

```js
[...document.querySelectorAll("#ssf-basis-table td")]
  .map(td => getComputedStyle(td).color)
  .filter((v, i, a) => a.indexOf(v) === i);
```

Expected：只有中性灰白，**不含 `--up`／`--down` 的值**。

- [ ] **Step 4: 手機寬度檢查**

375px 下確認表格在 `.table-wrap` 內橫捲、頁面本身無水平溢出。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 期現價差表（檔數，正負不著紅綠，標明時間不同步）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 15: 近 10 交易日排行熱力圖

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `ssfData.heatmap`（T11）、既有 `sectorColor`
- Produces: `sectorColor(chg, scale)` 多一個選用參數（預設 3，**不改既有呼叫端**）

**要點**：格子帶白字 → **必須用 `--up-fill`／`--down-fill`**（`sectorColor` 已是），
飽和上限改 7%（股期漲跌幅可達 ±10%，用 3% 會整片飽和、看不出強弱）。

- [ ] **Step 1: 給 `sectorColor` 加 scale 參數**

`web/app.js`，把既有函式改成：

```js
function sectorColor(chg, scale = 3) {
  if (chg == null) return "#2b3038";
  // scale＝飽和上限（%）。類股平均日漲跌多在 ±3%，個別股期可達 ±10%，
  // 所以股期熱力圖傳 7；既有呼叫端不傳、維持 3。
  const t = 0.35 + 0.65 * Math.min(Math.abs(chg) / scale, 1);
  return _hex("#2b3038", chg >= 0 ? C.upFill : C.downFill, t);
}
```

- [ ] **Step 2: 加容器與繪製函式**

`web/index.html` 的 `#view-ssf` 內追加：

```html
      <div class="card-group span-full" data-key="ssf-heatmap">
        <div class="group-title">近期排行熱力圖　<span class="gt-sub">每日成交口數前 10，格子為當日漲跌%</span></div>
        <div class="table-wrap" id="ssf-hm-wrap">
          <table id="ssf-hm-table"><thead><tr id="ssf-hm-head"></tr></thead><tbody></tbody></table>
        </div>
      </div>
```

`web/app.js`：

```js
function renderSsfHeatmap(hm) {
  const head = $("ssf-hm-head");
  const tb = document.querySelector("#ssf-hm-table tbody");
  if (!head || !tb) return;
  const dates = (hm && hm.dates) || [];
  if (!dates.length) { tb.innerHTML = '<tr><td>尚無資料</td></tr>'; return; }
  head.innerHTML = '<th scope="col">#</th>'
    + dates.map(d => `<th scope="col">${esc(d.slice(5))}</th>`).join("");
  tb.innerHTML = (hm.rows || []).map((line, i) => '<tr><th scope="row">' + (i + 1) + "</th>"
    + line.map(cell => {
        if (!cell) return '<td class="ssf-hm-cell"></td>';   // 缺的交易日留空，不頂替
        const bg = sectorColor(cell.chg_pct, 7);
        const pct = cell.chg_pct == null ? "" : (cell.chg_pct >= 0 ? "+" : "") + cell.chg_pct.toFixed(1) + "%";
        return `<td class="ssf-hm-cell" style="background:${bg}">`
          + `<span class="ssf-hm-name">${esc(cell.name)}</span>`
          + `<span class="ssf-hm-pct">${pct}</span></td>`;
      }).join("") + "</tr>").join("");
}
```

在 `loadSsf()` 內加 `renderSsfHeatmap(d.heatmap);`。

`web/styles.css` 追加：

```css
#ssf-hm-table .ssf-hm-cell { color: #fff; text-align: center; padding: 6px 4px; min-width: 76px; }
#ssf-hm-table .ssf-hm-name { display: block; font-size: var(--fs-xxs); }
#ssf-hm-table .ssf-hm-pct { display: block; font-weight: 650; }
```

- [ ] **Step 3: 驗證既有呼叫端沒被改到**

在 console 執行：

```js
[sectorColor(1), sectorColor(3), sectorColor(-3), sectorColor(null)]
```

Expected：與改動前相同（`sectorColor(3)` 應為完全飽和的 `C.upFill`）。
再確認總覽熱力圖與權值股卡片顏色沒變（切到總覽目視比對）。

- [ ] **Step 4: 手機寬度檢查**

375px 下 10 欄一定溢出 → 確認在 `.table-wrap` 內橫捲、頁面本身不溢出。
名次欄（第一欄）加凍結：

```css
@media (max-width: 600px) {
  #ssf-hm-table th[scope="row"] { position: sticky; left: 0; z-index: 1; }
  #ssf-hm-table th[scope="row"]::after {
    content: ""; position: absolute; inset: 0; background: var(--card); z-index: -1;
  }
}
```

（用 `::after` 疊不透明底，不直接改 `background`——避免與帶 ID 的斑馬紋規則打特異度戰爭。）

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 近 10 交易日排行熱力圖（白字用 upFill/downFill，飽和上限 7%）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 16: 未平倉增減榜

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `ssfData.oi_change`（T11）

**要點**：OI 增減**不是價格方向**→ **不著紅綠**，用中性色 ＋ ▲▼ 字彙（沿用全站既有用法）。

- [ ] **Step 1: 加容器**

`web/index.html` 的 `#view-ssf` 內追加：

```html
      <div class="card-group" data-key="ssf-oi">
        <div class="group-title">未平倉增減　<span class="gt-sub">大量搭配 OI 大增是建倉，大量搭配 OI 大減是平倉</span></div>
        <div class="ssf-oi-cols">
          <div><div class="ssf-oi-title">增加最多</div><ol id="ssf-oi-up" class="ssf-oi-list"></ol></div>
          <div><div class="ssf-oi-title">減少最多</div><ol id="ssf-oi-down" class="ssf-oi-list"></ol></div>
        </div>
      </div>
```

- [ ] **Step 2: 寫繪製函式**

`web/app.js`：

```js
function renderSsfOi(oi) {
  [["up", "ssf-oi-up"], ["down", "ssf-oi-down"]].forEach(([key, id]) => {
    const el = $(id); if (!el) return;
    const rows = (oi && oi[key]) || [];
    if (!rows.length) { el.innerHTML = '<li class="empty">尚無資料（需要前一交易日的資料）</li>'; return; }
    // OI 增減不是價格方向，**不著紅綠**；方向用 ▲▼（全站既有字彙）。
    el.innerHTML = rows.map(r => `<li>
      <span class="ssf-oi-name">${esc(r.name)}</span>
      <span class="ssf-oi-delta">${r.oi_change > 0 ? "▲" : "▼"}${fmt(Math.abs(r.oi_change), 0)}</span>
      <span class="ssf-oi-base">OI ${fmt(r.oi, 0)}</span></li>`).join("");
  });
}
```

在 `loadSsf()` 內加 `renderSsfOi(d.oi_change);`。

`web/styles.css` 追加：

```css
.ssf-oi-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.ssf-oi-title { color: var(--muted); font-size: var(--fs-xs); margin-bottom: 6px; }
.ssf-oi-list { margin: 0; padding-left: 20px; }
.ssf-oi-list li { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; }
.ssf-oi-list .ssf-oi-name { flex: 1 1 auto; min-width: 0; }
/* 未平倉增減不是漲跌，一律中性色 */
.ssf-oi-list .ssf-oi-delta { font-weight: 650; }
.ssf-oi-list .ssf-oi-base { color: var(--muted); font-size: var(--fs-xxs); }
```

- [ ] **Step 3: 在瀏覽器實測 ＋ 驗證無紅綠**

在 console 執行：

```js
[...document.querySelectorAll("#ssf-oi-up li *, #ssf-oi-down li *")]
  .map(x => getComputedStyle(x).color).filter((v,i,a) => a.indexOf(v) === i);
```

Expected：只有中性灰白。

- [ ] **Step 4: 手機寬度檢查**

375px 下兩欄自動折成一欄、無水平溢出。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 未平倉增減榜（不著紅綠，用 ▲▼）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 17: 原始保證金試算全表

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `GET /api/ssf/margin`（T10）
- Produces: `ssfMargin`（全域，供 Task 18 的個股頁共用）

**要點**：口數換算**在前端乘整數**（規則就是「先四捨五入單口再乘 N」）。
金額**不著紅綠**。搜尋與排序用容器上的事件委派（CSP 禁 inline handler）。

- [ ] **Step 1: 加容器**

`web/index.html` 的 `#view-ssf` 內追加：

```html
      <div class="card-group span-full" data-key="ssf-margin">
        <div class="group-title">原始保證金試算</div>
        <div class="ssf-margin-bar">
          <label>口數 <input id="ssf-lots" type="number" min="1" max="999" value="1"></label>
          <label>搜尋 <input id="ssf-margin-q" type="search" placeholder="代號或名稱"></label>
          <span class="pane-count" id="ssf-margin-note"></span>
        </div>
        <div class="table-wrap" id="ssf-margin-wrap">
          <table id="ssf-margin-table"><thead><tr>
            <th scope="col">標的</th><th scope="col">契約乘數</th><th scope="col">原始比例</th>
            <th scope="col">1 口原始保證金</th><th scope="col">維持保證金</th>
            <th scope="col" id="ssf-margin-total-th">N 口合計</th>
          </tr></thead><tbody></tbody></table>
        </div>
      </div>
```

- [ ] **Step 2: 寫載入與繪製**

`web/app.js`：

```js
let ssfMargin = null;

async function loadSsfMargin() {
  try {
    ssfMargin = await getJSON("/api/ssf/margin");
  } catch (e) { return; }
  renderSsfMargin();
}

function ssfMarginRows() {
  if (!ssfMargin) return [];
  const idx = (ssfMargin.index || []).map(x => ({
    root: "", contract: "", name: x.name, code: "", multiplier: null,
    initial_pct: null, initial: x.initial, maintenance: x.maintenance, kind: "index",
  }));
  return idx.concat(ssfMargin.rows || []);
}

function renderSsfMargin() {
  const tb = document.querySelector("#ssf-margin-table tbody"); if (!tb) return;
  const lots = Math.max(1, parseInt(($("ssf-lots") || {}).value, 10) || 1);
  const q = (($("ssf-margin-q") || {}).value || "").trim().toLowerCase();
  const th = $("ssf-margin-total-th"); if (th) th.textContent = `${lots} 口合計`;

  const rows = ssfMarginRows().filter(r => !q
    || (r.name || "").toLowerCase().includes(q) || (r.code || "").includes(q));
  const note = $("ssf-margin-note");
  if (note && ssfMargin) {
    note.textContent = `以 ${ssfMargin.price_date || "—"} 結算價估算；`
      + `比例更新日 ${ssfMargin.stock_updated || "—"}（ETF ${ssfMargin.etf_updated || "—"}、`
      + `指數 ${ssfMargin.index_updated || "—"}）；實際以期貨商收取為準　${rows.length} 檔`;
  }
  // 「N 口＝單口四捨五入後再乘 N」——所以前端直接乘整數完全正確。
  // 金額不是漲跌，一律不著紅綠。
  tb.innerHTML = rows.map(r => `<tr>
    <td class="ssf-name">${esc(r.name)}${r.code ? `<span class="ssf-code">${esc(r.code)}</span>` : ""}</td>
    <td>${r.multiplier == null ? "—" : fmt(r.multiplier, 0)}</td>
    <td>${r.initial_pct == null ? "—" : r.initial_pct.toFixed(2) + "%"}</td>
    <td>${r.initial == null ? "—" : fmt(r.initial, 0)}</td>
    <td>${r.maintenance == null ? "—" : fmt(r.maintenance, 0)}</td>
    <td class="ssf-total">${r.initial == null ? "—" : fmt(r.initial * lots, 0)}</td>
  </tr>`).join("") || '<tr><td colspan="6">查無符合的合約</td></tr>';
}
```

在 `loadSsf()` 結尾加 `loadSsfMargin();`。

事件委派（放在其他委派 listener 附近，**不可寫 inline `oninput=`**）：

```js
// CSP 是 script-src 'self'，inline on* 屬性會被瀏覽器靜默丟棄，所以一律委派。
const ssfMarginBar = document.querySelector(".ssf-margin-bar");
if (ssfMarginBar) ssfMarginBar.addEventListener("input", (e) => {
  if (e.target.closest("#ssf-lots, #ssf-margin-q")) renderSsfMargin();
});
```

`web/styles.css` 追加：

```css
.ssf-margin-bar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 8px; }
.ssf-margin-bar input[type="number"] { width: 72px; }
/* 保證金金額不是漲跌，不著紅綠 */
#ssf-margin-table .ssf-total { font-weight: 650; }
#ssf-margin-table .ssf-code { color: var(--muted); font-size: var(--fs-xxs); margin-left: 6px; }
```

- [ ] **Step 3: 在瀏覽器實測**

Expected：
- 表格含 320 檔股期 ＋ ETF 期貨 ＋ 台指／小台／微台
- 搜尋「2330」只剩台積電期與小型台積電期；搜尋「微型」找得到微台
- 口數改成 3，台積電期的「3 口合計」＝ `1 口 × 3`（用 console 對一次：
  `document.querySelector('#ssf-margin-table tbody tr td:nth-child(6)')`）
- 微台 1 口顯示 **35,050**
- 說明列同時出現三個更新日期與「實際以期貨商收取為準」

- [ ] **Step 4: 驗證無紅綠 ＋ 手機寬度**

console：

```js
[...document.querySelectorAll("#ssf-margin-table td")]
  .map(td => getComputedStyle(td).color).filter((v,i,a)=>a.indexOf(v)===i);
```

Expected：只有中性灰白。
375px 下表格在 `.table-wrap` 內橫捲、第一欄凍結（比照 Task 15 的規則，選擇器改成
`#ssf-margin-table td:first-child`），頁面本身無水平溢出。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 原始保證金試算全表（口數欄，含微台，標明估算與生效日）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 18: 個股頁順帶顯示該檔股期保證金

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- Consumes: `GET /api/ssf/margin` 的 `by_stock`（T10）

**要點**：全表回答「哪一檔門檻低」，個股頁回答「我正在看的這檔要多少」。
**反查索引在後端組**（`by_stock`），前端不得自己掃 320 列組第二份。
沒有股期的個股整行不出現（`:empty { display: none }`，同 `#today-focus` 既有做法）。

- [ ] **Step 1: 加容器**

`web/index.html` 的個股頁基本面區塊內（`grep -n 'id="view-stock"' web/index.html` 找位置）
加一行容器：

```html
        <div id="stock-ssf-margin"></div>
```

- [ ] **Step 2: 寫繪製函式**

`web/app.js`：

```js
async function renderStockSsfMargin(code) {
  const el = $("stock-ssf-margin"); if (!el) return;
  el.innerHTML = "";
  if (!code) return;
  if (!ssfMargin) { try { ssfMargin = await getJSON("/api/ssf/margin"); } catch (e) { return; } }
  // 反查索引由後端提供；前端不自己掃 320 列組第二份（兩份會漂移）。
  const list = (ssfMargin.by_stock || {})[String(code).split(".")[0]] || [];
  const usable = list.filter(x => x.initial != null);
  if (!usable.length) return;          // 沒有股期就整行不出現
  el.innerHTML = '<span class="ssm-label">股期原始保證金</span>'
    + usable.map(x => `<span class="ssm-item">${esc(x.name)}期 1 口 ${fmt(x.initial, 0)}</span>`).join("")
    + `<span class="ssm-note">以 ${esc(ssfMargin.price_date || "—")} 結算價估算，實際以期貨商收取為準</span>`;
}
```

在個股查詢完成的地方呼叫（`grep -n "function loadStock" web/app.js`，
在 K 線畫完之後加）：`renderStockSsfMargin(code);`

`web/styles.css` 追加：

```css
/* 沒有股期的個股整行不出現——標題也由 JS 產生，否則空的日子會留一個空框。 */
#stock-ssf-margin:empty { display: none; }
#stock-ssf-margin { display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline;
                    padding: 8px 0; }
#stock-ssf-margin .ssm-label { color: var(--muted); font-size: var(--fs-xs); }
#stock-ssf-margin .ssm-item { font-weight: 650; }   /* 金額不是漲跌，不著紅綠 */
#stock-ssf-margin .ssm-note { color: var(--muted); font-size: var(--fs-xxs); }
```

- [ ] **Step 3: 在瀏覽器實測**

- 查 2330 → 出現「台積電期 1 口 656,640　小型台積電期 1 口 32,832」
- 查一檔沒有股期的股票（例如 6488）→ **整行不出現**，且**沒有留下空白框**
  （console 驗：`getComputedStyle($("stock-ssf-margin")).display === "none"`）

- [ ] **Step 4: 驗證無紅綠**

console：

```js
[...document.querySelectorAll("#stock-ssf-margin *")]
  .map(x => getComputedStyle(x).color).filter((v,i,a)=>a.indexOf(v)===i);
```

Expected：只有中性灰白。

- [ ] **Step 5: Commit**

```bash
git add web/index.html web/app.js web/styles.css
git commit -m "feat(ui): 個股頁顯示該檔股期 1 口原始保證金（沒有股期就整行不出現）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 19: 自算選股表加「有股期」欄

**Files:**
- Modify: `stocks_power_rich/api/admin.py`
- Modify: `web/app.js`
- Modify: `tests/test_ssf_api.py`

**Interfaces:**
- Consumes: `_ssf_contracts`（T6）、`_ssf_margin_table`（T7）、`margin_amount`（T8）
- Produces: `/api/picks/self-screen` 的每一列多兩個鍵 `ssf`（bool）、`ssf_margin`（int | None）

**要點**：這是整個功能唯一「別人做不出來」的部分——期天那一頁誰都看得到，
但「我今天自算選出來的名單裡，哪幾檔能用股期做、各要準備多少錢」只有這裡答得出。
**只當參考欄**：不進 `screen_pass` 的篩選條件、不進木質木率計分
（同「融資3日」的既有取捨——不動搖既有的木質刻度與門檻）。

- [ ] **Step 1: 寫失敗的測試**

追加到 `tests/test_ssf_api.py`：

```python
def test_self_screen_rows_carry_the_stock_futures_margin(monkeypatch, tmp_path):
    """自算選股表的參考欄：有沒有股期、1 口要多少錢。"""
    from stocks_power_rich.api import admin as A
    monkeypatch.setattr(A, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(A, "_ssf_margin_table", lambda c: _MARGIN)
    rows = [{"code": "2330", "mu_value": 90}, {"code": "6488", "mu_value": 80}]
    out = A._attach_ssf_margin(get_connection(str(tmp_path / "x.sqlite")), rows,
                               settlements={"CD": 2432.0, "QF": 2432.0})
    assert out[0]["ssf"] is True and out[0]["ssf_margin"] == 656640   # 取標準約，非小型
    assert out[1]["ssf"] is False and out[1]["ssf_margin"] is None


def test_attaching_ssf_margin_never_changes_the_screening_result(monkeypatch, tmp_path):
    """它是參考欄：不進篩選、不進計分，只多兩個鍵。"""
    from stocks_power_rich.api import admin as A
    monkeypatch.setattr(A, "_ssf_contracts", lambda c: _CONTRACTS)
    monkeypatch.setattr(A, "_ssf_margin_table", lambda c: _MARGIN)
    rows = [{"code": "2330", "mu_value": 90, "mu_score": 12}]
    before = dict(rows[0])
    out = A._attach_ssf_margin(get_connection(str(tmp_path / "y.sqlite")), rows,
                               settlements={"CD": 2432.0})
    assert len(out) == 1
    assert {k: v for k, v in out[0].items() if k not in ("ssf", "ssf_margin")} == before
```

- [ ] **Step 2: 執行測試確認它失敗**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -k ssf_margin -q`
Expected: FAIL — `AttributeError: ... '_attach_ssf_margin'`

- [ ] **Step 3: 寫最小實作**

`stocks_power_rich/api/admin.py` 追加（檔頭補
`from .helpers import _ssf_contracts, _ssf_margin_table`、
`from ..sources import taifex_ssf`、`from ..db import get_ssf_dates, get_ssf_rows`）：

```python
def _attach_ssf_margin(c, rows: list[dict], settlements: dict | None = None) -> list[dict]:
    """幫每一列補上「有沒有股期、1 口原始保證金」。

    **純參考欄**：不進 `screen_pass` 的篩選條件、也不進木質／木率計分
    （同「融資3日」的既有取捨——不動搖既有的木質刻度與門檻）。
    同一檔標的有標準與小型時**取標準約**：那才是「一口股期」的一般認知。
    """
    contracts = _ssf_contracts(c)
    margin = (_ssf_margin_table(c).get("stock") or {})
    if settlements is None:
        dates = get_ssf_dates(c, limit=1)
        settlements = {r["root"]: r.get("settlement") for r in get_ssf_rows(c, dates)}
    by_code: dict[str, list] = {}
    for root, info in contracts.items():
        if not info["is_etf"]:
            by_code.setdefault(info["code"], []).append((root, info))
    for r in rows:
        code = str(r.get("code", "")).split(".")[0]
        best = None
        for root, info in sorted(by_code.get(code, []), key=lambda x: -x[1]["multiplier"]):
            m = margin.get(root + "F")
            amt = taifex_ssf.margin_amount(settlements.get(root), info["multiplier"],
                                           (m or {}).get("initial_pct"))
            if amt is not None:
                best = amt
                break
        r["ssf"] = bool(by_code.get(code))
        r["ssf_margin"] = best
    return rows
```

在 `picks_self_screen` 回傳之前，對 `rows` 呼叫一次：

```python
    _attach_ssf_margin(c, result["rows"])
```

（實際變數名以該函式既有寫法為準；`grep -n "def picks_self_screen" -A 45 stocks_power_rich/api/admin.py`。）

`web/app.js` 的 `SS_FIELDS` 追加一欄（`grep -n "SS_FIELDS" web/app.js`）：

```js
  { key: "ssf_margin", label: "股期保證金", unit: "1 口",
    fmt: (v, row) => row.ssf ? (v == null ? "—" : fmt(v, 0)) : "—" },
```

> 金額**不著紅綠**（不是漲跌），沿用該表既有的中性欄位樣式，不新增 class。

- [ ] **Step 4: 執行測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_ssf_api.py -q`
Expected: PASS

- [ ] **Step 5: 反證「取標準約」**

把 `sorted(..., key=lambda x: -x[1]["multiplier"])` 的負號拿掉（變成先取小型），重跑
`test_self_screen_rows_carry_the_stock_futures_margin`
Expected: FAIL（得到 32832 而不是 656640）。確認後改回來。

- [ ] **Step 6: 在瀏覽器實測**

切到「自算籌碼/基本選股」，確認多出「股期保證金」欄；有股期的顯示金額、沒有的顯示 `—`；
**入選檔數與排序與加這一欄之前完全相同**（加欄前後各截一次 `#ss-picked-note` 的文字比對）。

- [ ] **Step 7: Commit**

```bash
git add stocks_power_rich/api/admin.py web/app.js tests/test_ssf_api.py
git commit -m "feat: 自算選股表加「股期保證金」參考欄（不進篩選、不進計分）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 20: 收尾——快取版號、文件、刪除探測端點

**Files:**
- Modify: `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py`
- Modify: `CLAUDE.md`、`AGENTS.md`
- Delete: `stocks_power_rich/ssf_probe.py`
- Modify: `stocks_power_rich/api/admin.py`（移除 `/api/ssf/probe`）

- [ ] **Step 1: 更新快取版號（四處）**

版號改為 `20260918-ui59`。用以下指令確認四處都改到：

```bash
grep -rn "20260817-ui58" web/index.html stocks_power_rich/api/public.py tests/test_api.py
```

Expected：改完後這個指令**沒有任何輸出**；
`grep -rn "20260918-ui59" web/index.html stocks_power_rich/api/public.py tests/test_api.py`
應有 4 處以上（`index.html` 的 link 與 script、`public.py` 兩條 replace、`test_api.py` 的斷言）。

- [ ] **Step 2: 刪除探測端點**

```bash
git rm stocks_power_rich/ssf_probe.py
```

並移除 `stocks_power_rich/api/admin.py` 尾端那一段（`# --- 一次性診斷，驗完即刪 ---`
到 `return _p.start_or_status(restart=bool(restart))`，共 8 行）。

確認沒有殘留：`grep -rn "ssf_probe" stocks_power_rich tests` 應無輸出。

- [ ] **Step 3: 更新 CLAUDE.md 與 AGENTS.md（**兩份一起**）**

在兩份文件各加一節（`AGENTS.md` 用精簡版），內容至少涵蓋：

- 股期日檔的三種「失敗長得像成功」（197 B 表頭、616 B UTF-8 警告頁、只有盤後列），
  以及對應的三道守衛；`taifex._post_csv` 擋不住它們。
- 官方漲跌% 的參考價是**前一日結算價**不是收盤價（實測 4.80% vs 自算 4.38%）。
- 股期 tick 表與現貨在 1000–2500 不同；價差**必須沿網格走**（聯茂 495→501 是 11 檔）。
- 調整後合約（`X1`）**只併成交量、不併價格**（乘數非標準，錯了看不出來）。
- 保證金必須 `Decimal` ROUND_HALF_UP（1,181 個裡有 101 個與 `round()` 不同），
  且用**公布的比例欄**不可用 ×1.35 反推（467 個不同）。
- 生效日取自 CSV 的 `更新日期`，**不可用 OpenAPI 的 `Date`**（那是每日快照）。
- 2026-09-18 的 production 可達性實測結果（設計 §8.1），含 MIS 即時也打得到。
- 顏色：價差正負、OI 增減、保證金金額**都不著紅綠**。

- [ ] **Step 4: 跑全套測試**

Run（**不可接管線**）：`.venv\Scripts\python -m pytest -q`
讀最後的摘要行，確認 `X passed` 且沒有 failed。
若有失敗，先修到全綠再往下。

- [ ] **Step 5: 全頁面回歸檢查**

1280px 與 375px 各切過 14 個視圖一次，確認：
- 沒有頁面級水平溢出
- 總覽的熱力圖與權值股卡片顏色**與改動前相同**（`sectorColor` 的預設參數沒破壞既有呼叫端）
- console 無錯誤、無 CSP 警告

- [ ] **Step 6: Commit**

```bash
git add web/index.html stocks_power_rich/api/public.py tests/test_api.py CLAUDE.md AGENTS.md stocks_power_rich/api/admin.py
git rm stocks_power_rich/ssf_probe.py
git commit -m "chore: 股期概況收尾——快取版號 ui59、文件、移除一次性探測端點

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 部署後驗證

推上 `main` 後（Zeabur 自動重新部署，約 2–3 分鐘）：

1. `GET /api/ssf/backfill?days=30` — 回補歷史，重複呼叫直到 `dates` 不再增加。
2. `GET /api/ssf/overview` — 確認 `coverage.stored_days >= 10`、`hot` 有 10 筆、
   `basis` 不是空的（空的表示現貨報價快取沒命中，檢查 `_quotes_for`）。
3. `GET /api/ssf/margin` — 確認 `stock_updated` 是近期日期、微台是 35,050。
4. 當天 20:15 之後看 `GET /api/health` 的 `jobs.ssf_daily`，
   應為 `status: "ok"` 且 `note` 含 `roots`。
   若三個時段都是 `data_not_ready`，代表日盤資料發佈得比 20:15 晚，
   把 `job_schedule` 的 `hour` 往後調（設計 §7 待辦 1）。
