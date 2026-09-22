# 族群輪動「法人 × 大戶」資金流向四象限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把族群輪動頁壞掉的「近 N 日類股漲跌表」換成一張以 X＝法人近 5 日淨買賣、Y＝大戶週增比（皆除以類股市值）的四象限泡泡圖，一眼看出錢正流向哪些族群、正在離開哪些族群、往哪個方向移動。

**Architecture:** 純函式 `analysis.sector_flow` 只做加總與換算（無 I/O，可單元測試）；`db.py` 補三支小查詢（法人窗口加總、集保兩週差、交易日曆）；薄端點 `GET /api/sectors/flow` 組輸入、算一次、依「法人最新日＋集保週」快取；前端 ECharts scatter 畫四象限＋尾巴，右側四區排行是 canvas 的鍵盤替代，點任一邊都篩下方交叉選股。壞掉的 `/api/sectors/rotation` 與 `loadRotation` 直接移除。

**Tech Stack:** FastAPI、SQLite（stdlib `sqlite3`）、vanilla JS、ECharts（本地 `web/vendor/echarts.min.js`）、pytest。

Spec：`docs/superpowers/specs/2026-09-22-sector-flow-quadrant-design.md`。

## Global Constraints

- 類股＝32 個官方產業別（`_industry_map` ∪ `_otc_industry`），母體只收 **4 碼、非 `00` 開頭**的代號。
- `FLOW_DAYS = 5`；端點 `days` 夾在 `[3, 20]`。
- 法人交易日曆來自 **`stock_flow_daily` 的 distinct date**，不是 `market_daily`。
- 大戶用 `custody_compare_weeks` 挑**完整週**；`x_prev`/`y_prev` 缺資料時是 `None`，不是 `0`。
- 大戶缺某檔 Δ：不進分子、**仍進市值分母**；該類股一檔都沒有 Δ 時 `y = None`。
- 快取鍵 `sectorflow:v1:{flow_dates[-1]}:{custody_weeks[0] or "none"}:{days}`，讀取端只接受 `has_custody` 與當下一致的快取。
- 大戶金額只能有一份算式：`analysis.big_holder_amount(delta_pct, shares, close)`，`selfcheck.build_self_screen` 也要改呼叫它。
- **顏色不用紅綠**（紅綠鎖給行情漲跌），資金流向一律 `C.info`；價格漲跌只在 tooltip 的 `chg_pct`。
- 圖高桌機 460px、≤600px 360px；`.rotation-flow` ≤1180px 改單欄；排行列 `min-height: 28px`。
- ECharts 一律 `initChart(el)`；順序**先寫容器 inline height → `setOption` → `resize()`**；圖要加進 `window` resize 清單與 `showView` 重新進頁的 resize。
- **不寫 inline `on*=` 屬性**（CSP `script-src 'self'` 會靜默丟掉），事件一律委派在靜態祖先上。
- 快取版號 `20260817-ui68` → `20260817-ui69`，四處：`web/index.html`×2、`stocks_power_rich/api/public.py`×2 行、`tests/test_api.py`×4。
- 這個 repo 的 `web/*`、`stocks_power_rich/*.py`、`tests/*.py`、`CLAUDE.md`、`AGENTS.md` 都是 **CRLF**；改完用 bytes 計數確認 `\n` 數＝`\r\n` 數。**不要用 `sed -i`**。
- **pytest 不接管線**（管線會吃掉離開碼）；要看摘要就 `> file; echo EXIT=$?; tail file`。
- Windows 終端機會吃 CJK：含中文的檢查輸出一律寫 UTF-8 檔再讀。
- 提交訊息結尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。**不要 push**（push 到 `main`＝正式部署，只有使用者說 commit and push 才做）。工作分支 `sector-flow-quadrant`。

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `stocks_power_rich/analysis.py` | 新增 `FLOW_DAYS`、`big_holder_amount()`、`sector_flow()`（純函式） |
| `stocks_power_rich/selfcheck.py` | `build_self_screen` 的 `buy_value` 改呼叫 `analysis.big_holder_amount` |
| `stocks_power_rich/db.py` | `custody_compare_weeks(..., limit=2)`、新增 `stock_flow_dates()`、`institutional_window_map()`、`custody_delta_map()` |
| `stocks_power_rich/api/market.py` | 移除 `/sectors/rotation`，新增 `/sectors/flow` |
| `web/index.html` | `#rotation` 換成 `#rotation-flow`（圖＋四區排行），說明文字更新，版號 |
| `web/app.js` | 移除 `loadRotation`；新增 `loadSectorFlow`／`renderSectorFlow`／`renderFlowQuadrants`／drill-down；resize 掛鉤 |
| `web/styles.css` | `.rotation-flow`、`.flow-*` 樣式與兩個斷點 |
| `stocks_power_rich/api/public.py`、`tests/test_api.py` | 版號 |
| `tests/test_analysis_sector_flow.py` | 純函式測試（新檔） |
| `tests/test_db.py`、`tests/test_api.py` | db 小查詢與端點測試 |
| `CLAUDE.md`、`AGENTS.md` | 各補一節 |

---

### Task 1: `analysis.big_holder_amount` 抽出成唯一權威算式

**Files:**
- Modify: `stocks_power_rich/analysis.py`（在 `custody_change` 之後）
- Modify: `stocks_power_rich/selfcheck.py:205-208`
- Test: `tests/test_analysis_sector_flow.py`（新檔）

**Interfaces:**
- Produces: `analysis.big_holder_amount(delta_pct: float | None, shares: float | None, close: float | None) -> float | None`

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_analysis_sector_flow.py`：

```python
"""族群輪動「法人 × 大戶」資金流向：純函式測試（spec 2026-09-22-sector-flow-quadrant-design.md §2）。"""
import inspect

from stocks_power_rich import analysis


def test_big_holder_amount_is_delta_pct_times_mcap():
    # Δ400張↑ +1%、10 億股、收盤 100 → 大戶淨買進 10 億元
    assert analysis.big_holder_amount(1.0, 1_000_000_000, 100.0) == 1_000_000_000.0
    assert analysis.big_holder_amount(-0.5, 2_000_000, 50.0) == -500_000.0


def test_big_holder_amount_returns_none_when_any_input_missing():
    assert analysis.big_holder_amount(None, 1000, 10.0) is None
    assert analysis.big_holder_amount(1.0, None, 10.0) is None
    assert analysis.big_holder_amount(1.0, 0, 10.0) is None
    assert analysis.big_holder_amount(1.0, 1000, None) is None
    assert analysis.big_holder_amount(1.0, 1000, 0) is None


def test_build_self_screen_uses_the_shared_formula_not_an_inline_copy():
    """大戶淨買進金額只能有一份算式。selfcheck 要呼叫 analysis.big_holder_amount，
    不能自己再寫一次 `bhr / 100 * shares * price`（兩份會漂移）。"""
    from stocks_power_rich import selfcheck
    src = inspect.getsource(selfcheck)
    assert "big_holder_amount(" in src
    assert "/ 100 * shares * price" not in src
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_sector_flow.py -q --no-header`
Expected: 3 failed（`AttributeError: module ... has no attribute 'big_holder_amount'`；第三條 `assert "big_holder_amount(" in src` 失敗）

- [ ] **Step 3: 實作 `big_holder_amount`**

在 `stocks_power_rich/analysis.py` 的 `custody_change` 函式**結束之後**（`return out` 的下一個空行後）加：

```python
def big_holder_amount(delta_pct, shares, close):
    """大戶淨買進金額估計（元）＝ Δ400張↑% ÷ 100 × 股數 × 收盤。

    這是全站**唯一**的一份算式：自算選股的「大戶買進版圖」（selfcheck.build_self_screen）與
    族群輪動的 Y 軸（sector_flow）都呼叫這裡，不各自再寫一次——兩份會漂移。
    任一輸入缺、股數或收盤為 0 都回 None（算不出就說算不出，不用 0 頂替）。
    """
    if delta_pct is None or not shares or not close:
        return None
    return delta_pct / 100 * shares * close
```

- [ ] **Step 4: selfcheck 改呼叫它**

先確認 `selfcheck.py` 已 import analysis：

Run: `grep -n "^from \. import\|^from \.\|^import" stocks_power_rich/selfcheck.py`

若清單裡沒有 `analysis`，在檔頭既有的 `from . import ...` 那行加上 `analysis`（例如 `from . import analysis, db`）。

然後把 `stocks_power_rich/selfcheck.py:205-208` 這四行：

```python
        shares = info.get("shares")
        closes = (ohlc.get(code) or {}).get("closes")
        price = closes[-1] if closes else None
        buy_value = (bhr / 100 * shares * price) if (shares and price) else None
```

改成：

```python
        shares = info.get("shares")
        closes = (ohlc.get(code) or {}).get("closes")
        price = closes[-1] if closes else None
        # 算式只有一份：analysis.big_holder_amount（族群輪動的 Y 軸也用它）
        buy_value = analysis.big_holder_amount(bhr, shares, price)
```

- [ ] **Step 5: 跑測試確認通過（含既有 selfcheck 測試沒壞）**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_sector_flow.py tests/test_selfcheck.py -q --no-header`
Expected: 全部 passed（`test_selfcheck.py` 既有的 `buy_value` 斷言 `1.59e9` 仍成立）

- [ ] **Step 6: 行尾檢查並提交**

Run:
```bash
.venv\Scripts\python -c "for p in ('stocks_power_rich/analysis.py','stocks_power_rich/selfcheck.py','tests/test_analysis_sector_flow.py'): b=open(p,'rb').read(); print(p, b.count(b'\n')-b.count(b'\r\n'))"
```
Expected: 三行都印 `0`（沒有裸 LF；新檔若印非 0，用 `python -c "p='tests/test_analysis_sector_flow.py'; b=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'); open(p,'wb').write(b)"` 轉成 CRLF）

```bash
git add stocks_power_rich/analysis.py stocks_power_rich/selfcheck.py tests/test_analysis_sector_flow.py
git commit -m "refactor(analysis): 大戶淨買進金額抽成 big_holder_amount，selfcheck 改呼叫共用算式" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `analysis.sector_flow` 純函式

**Files:**
- Modify: `stocks_power_rich/analysis.py`（緊接 `big_holder_amount` 之後）
- Test: `tests/test_analysis_sector_flow.py`

**Interfaces:**
- Consumes: `analysis.big_holder_amount`（Task 1）
- Produces:
  ```python
  FLOW_DAYS = 5
  def sector_flow(universe: dict, closes: dict, flow_cur: dict,
                  flow_prev: dict | None = None, cust_cur: dict | None = None,
                  cust_prev: dict | None = None, sector_chg: dict | None = None) -> dict
  # universe: {code: {"sector": str, "name": str, "shares": float}}
  # closes:   {code: float}
  # flow_cur / flow_prev: {code: 近 N 日 (外資+投信+自營) 淨張數}
  # cust_cur / cust_prev: {code: Δbig400_pct}
  # sector_chg: {sector: 當日類股指數漲跌%}
  # 回 {"sectors": [ {sector, x, y, x_prev, y_prev, mcap, n, n_cust, top3, chg_pct} ... 依 mcap 降冪 ],
  #     "excluded": {"no_price": int, "no_sector": int, "sectors_no_mcap": int}}
  ```

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_analysis_sector_flow.py` 尾端加：

```python
U = {  # 兩檔半導體、一檔水泥、一檔缺收盤、一檔缺類股、三檔非普通股
    "2330": {"sector": "半導體", "name": "台積電", "shares": 1_000_000_000},
    "2454": {"sector": "半導體", "name": "聯發科", "shares": 100_000_000},
    "1101": {"sector": "水泥",   "name": "台泥",   "shares": 500_000_000},
    "9999": {"sector": "水泥",   "name": "沒收盤", "shares": 1_000_000},
    "8888": {"sector": None,     "name": "沒類股", "shares": 1_000_000},
    "0050": {"sector": "半導體", "name": "ETF",    "shares": 1_000_000},
    "00878": {"sector": "半導體", "name": "ETF2",  "shares": 1_000_000},
    "12345": {"sector": "半導體", "name": "五碼",  "shares": 1_000_000},
}
CLOSES = {"2330": 100.0, "2454": 1000.0, "1101": 20.0, "8888": 10.0,
          "0050": 100.0, "00878": 10.0, "12345": 10.0}
FLOW = {"2330": 10_000, "2454": -2_000, "1101": 500, "9999": 99, "8888": 99,
        "0050": 99_999, "00878": 99_999, "12345": 99_999}


def _by_sector(res):
    return {s["sector"]: s for s in res["sectors"]}


def test_sector_flow_x_is_amount_sum_over_mcap_sum_not_mean_of_pcts():
    res = analysis.sector_flow(U, CLOSES, FLOW)
    semi = _by_sector(res)["半導體"]
    # 市值：2330 = 1e9×100 = 1e11；2454 = 1e8×1000 = 1e11 → 合計 2e11
    # 法人金額：2330 = 10000×1000×100 = 1e9；2454 = -2000×1000×1000 = -2e9 → 合計 -1e9
    assert semi["mcap"] == 200_000_000_000
    assert semi["n"] == 2
    assert semi["x"] == round(-1e9 / 2e11 * 100, 3)   # -0.5%
    # 若錯寫成「各檔 % 的平均」會是 (1% + -2%)/2 = -0.5%——這個例子刻意讓兩者相同，
    # 所以再看水泥：只有一檔，x = 500×1000×20 / (5e8×20) ×100 = 0.1%
    assert _by_sector(res)["水泥"]["x"] == 0.1


def test_sector_flow_excludes_non_common_stocks_and_counts_missing_inputs():
    res = analysis.sector_flow(U, CLOSES, FLOW)
    semi = _by_sector(res)["半導體"]
    assert semi["n"] == 2                       # 0050／00878／12345 都沒進來
    assert res["excluded"] == {"no_price": 1,   # 9999 缺收盤
                               "no_sector": 1,  # 8888 缺類股
                               "sectors_no_mcap": 0}


def test_sector_flow_counts_a_sector_with_no_priced_stock_as_no_mcap():
    u = {"7777": {"sector": "造紙", "name": "只有它", "shares": 1000}}
    res = analysis.sector_flow(u, {}, {"7777": 5})   # 沒有收盤
    assert res["sectors"] == []
    assert res["excluded"]["sectors_no_mcap"] == 1
    assert res["excluded"]["no_price"] == 1


def test_sector_flow_prev_and_custody_are_none_when_inputs_missing():
    res = analysis.sector_flow(U, CLOSES, FLOW)   # 沒給 flow_prev / cust_cur
    for s in res["sectors"]:
        assert s["x_prev"] is None and s["y"] is None and s["y_prev"] is None
        assert s["n_cust"] == 0


def test_sector_flow_custody_missing_stock_stays_in_denominator():
    # 半導體兩檔只有 2330 有集保 Δ：分子只算 2330，分母仍是兩檔市值 2e11
    cust = {"2330": 1.0}                          # Δ +1% → 1e9×100×1% = 1e9
    res = analysis.sector_flow(U, CLOSES, FLOW, cust_cur=cust)
    semi = _by_sector(res)["半導體"]
    assert semi["y"] == round(1e9 / 2e11 * 100, 3)   # 0.5%，不是 1%
    assert semi["n_cust"] == 1
    # 水泥一檔都沒有 Δ → y 是 None（不是 0）
    assert _by_sector(res)["水泥"]["y"] is None


def test_sector_flow_prev_period_uses_prev_inputs():
    res = analysis.sector_flow(U, CLOSES, FLOW, flow_prev={"1101": 1000},
                               cust_cur={"1101": 2.0}, cust_prev={"1101": -1.0})
    cem = _by_sector(res)["水泥"]
    assert cem["x_prev"] == 0.2                   # 1000×1000×20 / 1e10 ×100
    assert cem["y"] == 2.0 and cem["y_prev"] == -1.0
    semi = _by_sector(res)["半導體"]
    assert semi["x_prev"] is None                 # flow_prev 裡沒有半導體的股


def test_sector_flow_sorts_by_mcap_and_reports_top3_by_inst_amount():
    u = dict(U)
    u["2303"] = {"sector": "半導體", "name": "聯電", "shares": 1_000_000}
    closes = dict(CLOSES, **{"2303": 50.0})
    flow = dict(FLOW, **{"2303": 3_000})          # 3000×1000×50 = 1.5e8
    res = analysis.sector_flow(u, closes, flow)
    assert [s["sector"] for s in res["sectors"]] == ["半導體", "水泥"]
    semi = _by_sector(res)["半導體"]
    assert [t["code"] for t in semi["top3"]] == ["2330", "2303", "2454"]
    assert semi["top3"][0] == {"code": "2330", "name": "台積電", "amount": 1_000_000_000}


def test_sector_flow_attaches_sector_chg_pct_for_tooltip():
    res = analysis.sector_flow(U, CLOSES, FLOW, sector_chg={"半導體": 1.23})
    assert _by_sector(res)["半導體"]["chg_pct"] == 1.23
    assert _by_sector(res)["水泥"]["chg_pct"] is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_sector_flow.py -q --no-header`
Expected: 新增的 8 條 failed（`AttributeError: ... 'sector_flow'`），前 3 條 passed

- [ ] **Step 3: 實作 `sector_flow`**

在 `stocks_power_rich/analysis.py` 的 `big_holder_amount` 之後加：

```python
FLOW_DAYS = 5   # 族群輪動法人窗口（交易日）；端點可帶 days 覆寫，夾在 [3, 20]


def _is_common_code(code) -> bool:
    """4 碼、非 00 開頭＝普通股（同 top_movers／change_histogram 的過濾）。ETF／權證／特別股不進加總。"""
    c = str(code or "")
    return len(c) == 4 and c.isdigit() and not c.startswith("00")


def sector_flow(universe: dict, closes: dict, flow_cur: dict,
                flow_prev: dict | None = None, cust_cur: dict | None = None,
                cust_prev: dict | None = None, sector_chg: dict | None = None) -> dict:
    """族群輪動的兩軸（spec §2）：每個類股 X＝Σ法人淨買賣金額 ÷ Σ市值 ×100、Y＝Σ大戶淨買進金額 ÷ Σ市值 ×100。

    除以市值是為了跨類股可比——用絕對金額的話半導體永遠在最右邊，圖只會告訴你「半導體很大」。
    缺收盤或股數的檔整檔排除（法人金額也要收盤換算），計入 excluded.no_price；缺類股計入 no_sector。
    大戶缺某檔 Δ：不進分子、**仍進分母**（分母是類股規模，不隨分子缺漏縮小），n_cust 另外回報樣本數；
    整個類股一檔都沒有 Δ 時 y 是 None——0 是「有資料且淨額為零」，不能拿來頂替「沒資料」。
    flow_prev／cust_prev 為 None 時 x_prev／y_prev 一律 None（同理）。
    """
    flow_cur = flow_cur or {}
    sector_chg = sector_chg or {}
    seen_sectors: set = set()
    groups: dict = {}
    excl = {"no_price": 0, "no_sector": 0, "sectors_no_mcap": 0}
    for code, info in (universe or {}).items():
        if not _is_common_code(code):
            continue
        info = info or {}
        sector = info.get("sector")
        if not sector:
            excl["no_sector"] += 1
            continue
        seen_sectors.add(sector)
        shares, close = info.get("shares"), closes.get(code)
        if not shares or not close:
            excl["no_price"] += 1
            continue
        mcap = shares * close
        g = groups.setdefault(sector, {
            "mcap": 0.0, "n": 0, "inst": 0.0, "n_inst": 0, "inst_prev": 0.0, "n_inst_prev": 0,
            "big": 0.0, "n_cust": 0, "big_prev": 0.0, "n_cust_prev": 0, "stocks": []})
        g["mcap"] += mcap
        g["n"] += 1
        lots = flow_cur.get(code)
        if lots is not None:
            amt = lots * 1000 * close
            g["inst"] += amt
            g["n_inst"] += 1
            g["stocks"].append({"code": code, "name": info.get("name") or code, "amount": round(amt)})
        if flow_prev is not None and flow_prev.get(code) is not None:
            g["inst_prev"] += flow_prev[code] * 1000 * close
            g["n_inst_prev"] += 1
        if cust_cur is not None:
            b = big_holder_amount(cust_cur.get(code), shares, close)
            if b is not None:
                g["big"] += b
                g["n_cust"] += 1
        if cust_prev is not None:
            b = big_holder_amount(cust_prev.get(code), shares, close)
            if b is not None:
                g["big_prev"] += b
                g["n_cust_prev"] += 1

    def pct(num, den, have):
        return round(num / den * 100, 3) if (have and den) else None

    sectors = []
    for sector, g in groups.items():
        if not g["mcap"]:
            continue
        g["stocks"].sort(key=lambda s: s["amount"], reverse=True)
        sectors.append({
            "sector": sector,
            "x": pct(g["inst"], g["mcap"], g["n_inst"] > 0),
            "y": pct(g["big"], g["mcap"], cust_cur is not None and g["n_cust"] > 0),
            "x_prev": pct(g["inst_prev"], g["mcap"], flow_prev is not None and g["n_inst_prev"] > 0),
            "y_prev": pct(g["big_prev"], g["mcap"], cust_prev is not None and g["n_cust_prev"] > 0),
            "mcap": round(g["mcap"]), "n": g["n"], "n_cust": g["n_cust"],
            "top3": g["stocks"][:3],
            "chg_pct": sector_chg.get(sector),
        })
    sectors.sort(key=lambda s: s["mcap"], reverse=True)   # 大泡泡先畫、小泡泡疊上面才點得到
    excl["sectors_no_mcap"] = len(seen_sectors - {s["sector"] for s in sectors})
    return {"sectors": sectors, "excluded": excl}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv\Scripts\python -m pytest tests/test_analysis_sector_flow.py -q --no-header`
Expected: 11 passed

- [ ] **Step 5: 反證一條守衛（做完要還原）**

把 `g["mcap"] += mcap` 那行暫時移到 `if cust_cur is not None:` 區塊裡面（讓分母只算有集保 Δ 的檔），跑：

Run: `.venv\Scripts\python -m pytest tests/test_analysis_sector_flow.py -q --no-header -k denominator`
Expected: 1 failed（`y == 1.0` 而非 `0.5`）——證明「缺 Δ 仍進分母」不是恆真。**然後 `git checkout stocks_power_rich/analysis.py` 之前先確認只動了那一行，或手動搬回原位**，再跑一次全綠。

- [ ] **Step 6: 提交**

```bash
git add stocks_power_rich/analysis.py tests/test_analysis_sector_flow.py
git commit -m "feat(analysis): sector_flow 純函式——法人與大戶資金流向除以類股市值" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: db 三支小查詢 ＋ `custody_compare_weeks(limit)`

**Files:**
- Modify: `stocks_power_rich/db.py`（`custody_compare_weeks` 加 `limit`；三支新函式放在 `custody_change_map` 之後）
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  ```python
  def custody_compare_weeks(conn, as_of=None, limit: int = 2) -> list      # 新到舊，最多 limit 個完整週
  def stock_flow_dates(conn, limit: int) -> list                            # 舊→新，最近 limit 個有法人資料的日期
  def institutional_window_map(conn, dates: list) -> dict                   # {code: Σ(外資+投信+自營) 張}
  def custody_delta_map(conn, week_cur: str, week_prev: str) -> dict        # {code: big400_pct(cur) - big400_pct(prev)}
  ```

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_db.py` 尾端加：

```python
def test_custody_compare_weeks_limit_returns_three_complete_weeks(tmp_path):
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_custody, custody_compare_weeks
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    full = {f"{i:04d}": {"big400_pct": 50.0} for i in range(1000, 1010)}
    for wk in ("2026-08-28", "2026-09-04", "2026-09-11", "2026-09-18"):
        bulk_upsert_custody(c, wk, full)
    bulk_upsert_custody(c, "2026-09-25", {"1000": {"big400_pct": 50.0}})   # 殘缺週（只有 1 檔）
    assert custody_compare_weeks(c) == ["2026-09-18", "2026-09-11"]          # 預設 2，行為不變
    assert custody_compare_weeks(c, limit=3) == ["2026-09-18", "2026-09-11", "2026-09-04"]


def test_stock_flow_dates_returns_oldest_to_newest_from_the_flow_table(tmp_path):
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_stock_flow, upsert_market_daily, stock_flow_dates
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    for d in ("2026-09-15", "2026-09-16", "2026-09-17"):
        bulk_upsert_stock_flow(c, d, "TWSE", {"2330": {"foreign_lots": 1}})
    upsert_market_daily(c, {"date": "2026-09-18", "taiex": 1.0})   # market_daily 有、法人沒有 → 不算
    assert stock_flow_dates(c, 2) == ["2026-09-16", "2026-09-17"]
    assert stock_flow_dates(c, 10) == ["2026-09-15", "2026-09-16", "2026-09-17"]


def test_institutional_window_map_sums_three_groups_and_skips_all_null(tmp_path):
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_stock_flow, institutional_window_map
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    bulk_upsert_stock_flow(c, "2026-09-16", "TWSE", {
        "2330": {"foreign_lots": 100, "trust_lots": 10, "dealer_lots": -5},
        "2454": {"foreign_lots": None, "trust_lots": None, "dealer_lots": None},   # 全空＝當日沒法人資料
        "1101": {"foreign_lots": 7}})                                                # 缺欄位當 0
    bulk_upsert_stock_flow(c, "2026-09-17", "TWSE", {"2330": {"foreign_lots": -20}})
    m = institutional_window_map(c, ["2026-09-16", "2026-09-17"])
    assert m["2330"] == 85          # (100+10-5) + (-20)
    assert m["1101"] == 7
    assert "2454" not in m          # 三欄全 NULL 的檔不出現（不是 0）
    assert institutional_window_map(c, []) == {}


def test_custody_delta_map_needs_both_weeks(tmp_path):
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_custody, custody_delta_map
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    bulk_upsert_custody(c, "2026-09-11", {"2330": {"big400_pct": 87.0}, "1101": {"big400_pct": 40.0}})
    bulk_upsert_custody(c, "2026-09-18", {"2330": {"big400_pct": 87.5}, "2454": {"big400_pct": 60.0}})
    m = custody_delta_map(c, "2026-09-18", "2026-09-11")
    assert m == {"2330": 0.5}       # 1101 只有舊週、2454 只有新週 → 都不出現
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_db.py -q --no-header -k "compare_weeks_limit or stock_flow_dates or institutional_window or custody_delta"`
Expected: 4 failed（`TypeError: ... unexpected keyword argument 'limit'` 與 `ImportError`）

- [ ] **Step 3: 實作**

`stocks_power_rich/db.py` 的 `custody_compare_weeks`：簽章改成 `def custody_compare_weeks(conn: sqlite3.Connection, as_of: str | None = None, limit: int = 2) -> list:`，最後一行 `[:2]` 改成 `[:limit]`，docstring 第一行改成「挑出算大戶增比要用的『最近 limit 週完整集保週』（新到舊，預設 2），略過殘缺週。」

然後在 `custody_change_map` 函式之後（`seed_sub_industry_ref` 之前）加三支：

```python
def stock_flow_dates(conn: sqlite3.Connection, limit: int) -> list:
    """最近 limit 個**有法人資料**的日期（舊→新）。族群輪動的法人窗口用這個當交易日曆，
    不用 market_daily——後者當天早上就有列而法人 16:00 後才公布，會把今天的空列算進窗口。"""
    rows = conn.execute(
        "SELECT DISTINCT date FROM stock_flow_daily ORDER BY date DESC LIMIT ?", (int(limit),)).fetchall()
    return [r[0] for r in reversed(rows)]


def institutional_window_map(conn: sqlite3.Connection, dates: list) -> dict:
    """{code: Σ(外資+投信+自營) 淨張數} 於指定日期集合。缺欄位當 0（T86 沒列＝當日無該類法人淨額），
    但三欄在窗口內**全部** NULL 的檔不出現（那是「沒資料」，不是 0）。"""
    if not dates:
        return {}
    q = ",".join("?" * len(dates))
    rows = conn.execute(
        "SELECT code, SUM(COALESCE(foreign_lots,0)+COALESCE(trust_lots,0)+COALESCE(dealer_lots,0)), "
        "COUNT(foreign_lots)+COUNT(trust_lots)+COUNT(dealer_lots) "
        f"FROM stock_flow_daily WHERE date IN ({q}) GROUP BY code", list(dates)).fetchall()
    return {code: total for code, total, non_null in rows if non_null > 0}


def custody_delta_map(conn: sqlite3.Connection, week_cur: str, week_prev: str) -> dict:
    """{code: big400_pct(week_cur) − big400_pct(week_prev)}，兩週都有值的檔才出現
    （大戶增比的定義，同 analysis.custody_change 的 big_holder_ratio）。"""
    rows = conn.execute(
        "SELECT n.code, n.big400_pct - o.big400_pct FROM custody_dist n "
        "JOIN custody_dist o ON o.code = n.code AND o.week = ? "
        "WHERE n.week = ? AND n.big400_pct IS NOT NULL AND o.big400_pct IS NOT NULL",
        (week_prev, week_cur)).fetchall()
    return {code: round(delta, 2) for code, delta in rows}
```

- [ ] **Step 4: 跑測試確認通過（含既有集保測試沒壞）**

Run: `.venv\Scripts\python -m pytest tests/test_db.py tests/test_custody_watch.py -q --no-header`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add stocks_power_rich/db.py tests/test_db.py
git commit -m "feat(db): 族群輪動用的三支小查詢；custody_compare_weeks 加 limit" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: 端點 `GET /api/sectors/flow`，移除 `/api/sectors/rotation`

**Files:**
- Modify: `stocks_power_rich/api/market.py:474-501`（整支 `sectors_rotation` 換掉）
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `analysis.sector_flow`／`analysis.FLOW_DAYS`（Task 2）、`db.stock_flow_dates`／`institutional_window_map`／`custody_delta_map`／`custody_compare_weeks(limit=3)`（Task 3）、既有 `_industry_map`／`_otc_industry`／`_quotes_for`／`_otc_quotes_for`／`_sectors_for`／`get_ai_cache`／`set_ai_cache`
- Produces: `GET /api/sectors/flow?days=5` 回 spec §3 的 JSON；`GET /api/sectors/rotation` 404

- [ ] **Step 1: 寫失敗的測試**

在 `tests/test_api.py` 尾端加：

```python
def _seed_sector_flow(tmp_path, monkeypatch, *, flow_days=12, custody_weeks=("2026-09-04", "2026-09-11", "2026-09-18")):
    """族群輪動端點的固定場景：2330（半導體、1e9 股、收盤 100）每天法人淨買 +100 張；
    1101（水泥、5e8 股、收盤 20）每天 -10 張；三檔非普通股法人各 +99999 張（不得進加總）。
    market_daily 多一個「今天」的空列（法人窗口不得含它）。"""
    from datetime import date, timedelta
    from stocks_power_rich.db import (get_connection, init_db, upsert_market_daily,
                                      bulk_upsert_stock_flow, bulk_upsert_custody, set_ai_cache)
    from stocks_power_rich.sources import twse, tpex
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    c = get_connection(str(tmp_path / "t.sqlite")); init_db(c)
    days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(flow_days)]
    for d in days:
        upsert_market_daily(c, {"date": d, "taiex": 1.0})
        bulk_upsert_stock_flow(c, d, "TWSE", {
            "2330": {"foreign_lots": 100}, "1101": {"foreign_lots": -10},
            "0050": {"foreign_lots": 99_999}, "00878": {"foreign_lots": 99_999}, "12345": {"foreign_lots": 99_999}})
    upsert_market_daily(c, {"date": "2026-12-31", "taiex": None})      # 今天的空列
    for i, wk in enumerate(custody_weeks):
        bulk_upsert_custody(c, wk, {"2330": {"big400_pct": 80.0 + i}, "1101": {"big400_pct": 40.0}})
    universe = {"2330": {"sector": "半導體", "name": "台積電", "shares": 1_000_000_000},
                "1101": {"sector": "水泥", "name": "台泥", "shares": 500_000_000},
                "0050": {"sector": "半導體", "name": "ETF", "shares": 1_000_000},
                "00878": {"sector": "半導體", "name": "ETF2", "shares": 1_000_000},
                "12345": {"sector": "半導體", "name": "五碼", "shares": 1_000_000}}
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: universe)
    monkeypatch.setattr(tpex, "fetch_otc_industry", lambda: {})
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [{"name": "半導體", "close": 1, "chg_pct": 1.5}])
    set_ai_cache(c, f"stock_quotes:{days[-1]}", {"2330": {"close": 100.0, "chg_pct": 0},
                                                 "1101": {"close": 20.0, "chg_pct": 0},
                                                 "0050": {"close": 100.0, "chg_pct": 0},
                                                 "00878": {"close": 10.0, "chg_pct": 0},
                                                 "12345": {"close": 10.0, "chg_pct": 0}})
    return c, days


def test_sectors_flow_window_comes_from_stock_flow_daily_and_only_common_stocks(tmp_path, monkeypatch):
    c, days = _seed_sector_flow(tmp_path, monkeypatch)
    r = TestClient(create_app()).get("/api/sectors/flow").json()
    assert r["flow_dates"] == days[-5:]                 # 今天的空列不在窗口裡
    assert r["flow_prev_dates"] == days[-10:-5]
    assert r["has_tail"] is True and r["has_custody"] is True
    by = {s["sector"]: s for s in r["sectors"]}
    semi = by["半導體"]
    assert semi["n"] == 1                                # 0050／00878／12345 沒進來
    # 5 日 × 100 張 × 1000 × 100 元 = 5e7；市值 1e11 → 0.05%
    assert semi["x"] == 0.05 and semi["x_prev"] == 0.05
    # 集保 09-11→09-18：81→82 = +1% → 1e9 元；÷1e11 = 1%；上一期 80→81 也是 1%
    assert semi["y"] == 1.0 and semi["y_prev"] == 1.0
    assert semi["chg_pct"] == 1.5
    assert r["custody_weeks"] == ["2026-09-18", "2026-09-11"]
    assert by["水泥"]["y"] == 0.0                        # 40→40：有資料、淨額為零


def test_sectors_flow_without_two_complete_custody_weeks_has_no_y(tmp_path, monkeypatch):
    _seed_sector_flow(tmp_path, monkeypatch, custody_weeks=("2026-09-18",))
    r = TestClient(create_app()).get("/api/sectors/flow").json()
    assert r["has_custody"] is False and r["custody_weeks"] == []
    assert all(s["y"] is None and s["y_prev"] is None for s in r["sectors"])
    assert r["sectors"]                                  # X 軸照樣有


def test_sectors_flow_read_guard_rejects_cache_written_without_custody(tmp_path, monkeypatch):
    """寫入守衛擋不住已經寫進去的半套：預先塞一份 has_custody=false 的舊快取，
    集保現在已有兩週 → 必須重算，不得回舊的。"""
    from stocks_power_rich.db import set_ai_cache
    c, days = _seed_sector_flow(tmp_path, monkeypatch)
    stale = {"flow_dates": days[-5:], "flow_prev_dates": [], "custody_weeks": [], "custody_prev_weeks": [],
             "has_custody": False, "has_tail": False, "sectors": [], "excluded": {}}
    set_ai_cache(c, f"sectorflow:v1:{days[-1]}:none:5", stale)
    r = TestClient(create_app()).get("/api/sectors/flow").json()
    assert r["has_custody"] is True and r["sectors"]


def test_sectors_flow_cache_key_changes_with_custody_week(tmp_path, monkeypatch):
    from stocks_power_rich.db import get_ai_cache
    c, days = _seed_sector_flow(tmp_path, monkeypatch)
    TestClient(create_app()).get("/api/sectors/flow")
    assert get_ai_cache(c, f"sectorflow:v1:{days[-1]}:2026-09-18:5") is not None


def test_sectors_flow_days_is_clamped_between_3_and_20(tmp_path, monkeypatch):
    _, days = _seed_sector_flow(tmp_path, monkeypatch, flow_days=45)
    cl = TestClient(create_app())
    assert len(cl.get("/api/sectors/flow?days=100").json()["flow_dates"]) == 20
    assert len(cl.get("/api/sectors/flow?days=1").json()["flow_dates"]) == 3


def test_sectors_flow_short_history_has_no_tail(tmp_path, monkeypatch):
    _seed_sector_flow(tmp_path, monkeypatch, flow_days=7)   # 只有 7 天 < 2×5
    r = TestClient(create_app()).get("/api/sectors/flow").json()
    assert r["has_tail"] is False and r["flow_prev_dates"] == []
    assert all(s["x_prev"] is None for s in r["sectors"])


def test_sectors_rotation_endpoint_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    assert TestClient(create_app()).get("/api/sectors/rotation").status_code == 404
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -q --no-header -k "sectors_flow or sectors_rotation_endpoint"`
Expected: 7 failed（`/api/sectors/flow` 404；`rotation` 那條 200 而非 404）

- [ ] **Step 3: 實作端點、移除舊端點**

`stocks_power_rich/api/market.py`：
1. `from ..db import get_ssf_dates, get_ssf_rows, count_ssf_dates` 那行改成
   `from ..db import get_ssf_dates, get_ssf_rows, count_ssf_dates, stock_flow_dates, institutional_window_map, custody_delta_map, custody_compare_weeks`
2. 把第 474–501 行（從 `@router.get("/sectors/rotation")` 到該函式的 `return result`）**整段刪掉**，原位換成：

```python
FLOW_DAYS_MIN, FLOW_DAYS_MAX = 3, 20


@router.get("/sectors/flow")
def sectors_flow(days: int = analysis.FLOW_DAYS):
    """族群輪動：法人（X）× 大戶（Y）資金流向，逐類股（spec 2026-09-22-sector-flow-quadrant-design.md §3）。

    取代 bfd0a53 之後就再也畫不出來的 /sectors/rotation（後端回 dict、前端當陣列用）。
    **不連外**：全部輸入來自本地表與既有逐日快取。快取鍵含法人最新日與集保週——集保一換週就是新鍵，
    不會拿舊集保的圖冒充新的；讀取端另外比對 has_custody，擋掉集保從「沒有」變「有」之前寫進去的半套。
    """
    c = conn()
    days = max(FLOW_DAYS_MIN, min(int(days), FLOW_DAYS_MAX))
    empty = {"flow_dates": [], "flow_prev_dates": [], "custody_weeks": [], "custody_prev_weeks": [],
             "has_custody": False, "has_tail": False, "sectors": [],
             "excluded": {"no_price": 0, "no_sector": 0, "sectors_no_mcap": 0}}
    dates = stock_flow_dates(c, days * 2)
    if not dates:
        return empty
    cur_dates = dates[-days:]
    prev_dates = dates[:-days] if len(dates) >= 2 * days else []
    weeks = custody_compare_weeks(c, limit=3)
    has_custody = len(weeks) >= 2
    ckey = f"sectorflow:v1:{cur_dates[-1]}:{weeks[0] if has_custody else 'none'}:{days}"
    cached = get_ai_cache(c, ckey)
    if cached is not None and cached.get("has_custody") == has_custody:
        return cached
    universe = {**_otc_industry(c), **_industry_map(c)}          # 代號不衝突；上市優先
    d0 = cur_dates[-1]
    closes = {code: q["close"] for code, q in {**_otc_quotes_for(c, d0), **_quotes_for(c, d0)}.items()
              if q and q.get("close") is not None}
    res = analysis.sector_flow(
        universe, closes,
        flow_cur=institutional_window_map(c, cur_dates),
        flow_prev=institutional_window_map(c, prev_dates) if prev_dates else None,
        cust_cur=custody_delta_map(c, weeks[0], weeks[1]) if has_custody else None,
        cust_prev=custody_delta_map(c, weeks[1], weeks[2]) if len(weeks) >= 3 else None,
        sector_chg={s["name"]: s.get("chg_pct") for s in _sectors_for(c, d0) if s.get("name")},
    )
    out = {"flow_dates": cur_dates, "flow_prev_dates": prev_dates,
           "custody_weeks": weeks[:2] if has_custody else [],
           "custody_prev_weeks": weeks[1:3] if len(weeks) >= 3 else [],
           "has_custody": has_custody, "has_tail": bool(prev_dates), **res}
    if res["sectors"]:                       # 空結果不寫快取（失敗值不可永久化）
        set_ai_cache(c, ckey, out)
    return out
```

- [ ] **Step 4: 跑測試確認通過（含整個 test_api）**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -q --no-header > pt_task4.txt 2>&1; echo EXIT=$?; tail -2 pt_task4.txt`
Expected: `EXIT=0`，全部 passed

- [ ] **Step 5: 用本機真實 DB 打一次，把回應寫檔看數字合不合理**

Run:
```bash
.venv\Scripts\python -c "import os,json,logging; logging.disable(logging.INFO); os.environ['SPR_DB_PATH']='data/spr.sqlite'; from fastapi.testclient import TestClient; from stocks_power_rich.main import create_app; r=TestClient(create_app()).get('/api/sectors/flow').json(); open('flow_probe.txt','w',encoding='utf-8').write(json.dumps({k:r[k] for k in ('flow_dates','custody_weeks','has_custody','has_tail','excluded')},ensure_ascii=False)+'\n'+'\n'.join(f\"{s['sector']}: x={s['x']} y={s['y']} xp={s['x_prev']} yp={s['y_prev']} n={s['n']}/{s['n_cust']} mcap={s['mcap']:,}\" for s in r['sectors']))"
```
然後 Read `flow_probe.txt`。Expected：`has_custody: true`、`has_tail: true`、約 30 個類股、`x`/`y` 多在 ±2% 內、`n_cust` ≤ `n`、沒有類股的 `x`/`y` 是 `null`（本機 DB 有 67 天法人與 60 週集保）。若 `excluded.no_price` 很大，代表 `stock_quotes:{d0}` 快取缺——那是本機資料，不是程式錯，記在報告裡即可。跑完刪掉 `flow_probe.txt`。

- [ ] **Step 6: 提交**

```bash
git add stocks_power_rich/api/market.py tests/test_api.py
git commit -m "feat(api): /api/sectors/flow 法人×大戶資金流向端點，移除壞掉的 /sectors/rotation" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: 前端——四象限泡泡圖、四區排行、drill-down、降級、版號

**Files:**
- Modify: `web/index.html`（`#view-rotation` 區塊；版號×2）
- Modify: `web/app.js`（移除 `loadRotation`；新增 `loadSectorFlow`／`renderSectorFlow`／`renderFlowBars`／`renderFlowQuadrants`／drill-down；`showView`；resize 清單；事件委派）
- Modify: `web/styles.css`（`.rotation-flow` 一組）
- Modify: `stocks_power_rich/api/public.py`、`tests/test_api.py`（版號）

**Interfaces:**
- Consumes: `GET /api/sectors/flow`（Task 4 的 JSON）、既有 `initChart`／`financeTooltip`／`withAlpha`／`C.info`／`fmt`／`fmtSigned`／`esc`／`$`／`getJSON`
- Produces: 全域 `flowChart`（ECharts 實例或 `null`）、`lastSectorFlow`、`rotationSectorFilter`；函式 `loadSectorFlow()`、`toggleRotationFilter(sector|null)`

- [ ] **Step 1: 版號進版（四處）**

Run:
```bash
.venv\Scripts\python -c "
OLD,NEW='20260817-ui68','20260817-ui69'
for p in ('web/index.html','stocks_power_rich/api/public.py','tests/test_api.py'):
    s=open(p,'rb').read().decode('utf-8'); n=s.count(OLD); s=s.replace(OLD,NEW)
    assert s.count('\n')==s.count('\r\n'), p
    open(p,'wb').write(s.encode('utf-8')); print(p, n)
"
```
Expected: `web/index.html 2`、`stocks_power_rich/api/public.py 4`、`tests/test_api.py 4`

- [ ] **Step 2: index.html 換 markup**

`web/index.html` 的 `#view-rotation` 區塊裡：
- `<p>近 N 日各類股累計漲跌%（依強弱排序，紅漲綠跌），看資金在族群間的輪動。</p>` 改成
  `<p>X＝法人近 5 日淨買賣、Y＝大戶 400張↑ 週增，皆除以類股市值。右上是錢正流入的族群、左下是正流出的；尾巴指向本期，顯示輪動方向。點泡泡或右側排行可篩下方交叉選股。</p>`
- `<div id="rotation" class="table-wrap fill-half"></div>` 改成：

```html
        <div id="rotation-flow" class="rotation-flow">
          <div id="flow-chart" class="chart flow-chart" role="img" aria-label="族群資金流向四象限：X 軸法人、Y 軸大戶"></div>
          <div id="flow-quadrants" class="flow-quadrants" aria-label="四區排行（鍵盤可操作）"></div>
        </div>
```

- [ ] **Step 3: styles.css 加樣式**

在 `web/styles.css` 的 `.cross-stocks { ... }` 那行**之後**加：

```css
/* 族群輪動：法人 × 大戶 四象限。資金流向不是漲跌，一律 --info 藍，不碰紅綠。 */
.rotation-flow { display: grid; grid-template-columns: 8fr 4fr; gap: 16px; align-items: start; }
.flow-chart { height: 460px; }
.flow-quadrants { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.flow-q { background: var(--card); border: 1px solid var(--border); border-radius: var(--r); padding: 8px 10px; min-width: 0; }
.flow-q h4 { margin: 0 0 6px; font-size: var(--fs-sm); color: var(--label); font-weight: 700; }
.flow-q-in { border-color: var(--info); }
.flow-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; width: 100%; min-height: 28px;
  padding: 3px 6px; background: transparent; border: 1px solid transparent; border-radius: var(--r);
  color: var(--text); font: inherit; font-size: var(--fs-sm); cursor: pointer; text-align: left; }
.flow-row:hover { background: var(--card-hover); }
.flow-row[aria-pressed="true"] { border-color: var(--info); background: rgba(108, 182, 255, 0.10); }
.flow-row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flow-row-val { color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
/* 降級：集保不足兩週時只剩 X 軸，畫置中零點的水平長條 */
.flow-bars { display: flex; flex-direction: column; gap: 6px; padding: 10px; }
.flow-bar-row { display: grid; grid-template-columns: 92px 1fr 64px; align-items: center; gap: var(--space-sm); }
.flow-bar-name { font-size: var(--fs-sm); color: var(--label); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.flow-bar-track { position: relative; height: 8px; background: var(--sunken); border-radius: 4px; }
.flow-bar-track::before { content: ""; position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--border); }
.flow-bar-fill { position: absolute; top: 0; height: 100%; border-radius: 4px; background: var(--info); }
.flow-bar-val { font-size: var(--fs-sm); font-weight: 700; text-align: right; white-space: nowrap; }
.flow-filter { margin-left: 8px; }
.flow-filter .tf { margin-left: 6px; }
@media (max-width: 1180px) { .rotation-flow { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .flow-chart { height: 360px; } .flow-quadrants { grid-template-columns: 1fr; } }
```

- [ ] **Step 4: app.js——移除 `loadRotation`，新增載入／渲染／排行／drill-down**

`web/app.js`：

(a) 把整支 `async function loadRotation() { ... }`（從 `async function loadRotation()` 到 `loadCross` 前那個 `}`）刪掉，原位換成下面整段：

```js
// ========== 族群輪動：法人 × 大戶 資金流向四象限 ==========
// 取代 bfd0a53 之後就再也畫不出來的「近 N 日類股漲跌表」（後端回 dict、前端當陣列用）。
// X＝法人近 5 日淨買賣 ÷ 類股市值、Y＝大戶 400張↑ 週增 ÷ 類股市值，尾巴＝上一期→本期。
// 資金流向不是漲跌 → 一律 C.info，不碰紅綠（紅綠鎖給行情；價格漲跌只在 tooltip）。
let flowChart = null, lastSectorFlow = null, rotationSectorFilter = null, crossNoteBase = "";

function flowQuadrant(s) {
  if (s.x > 0 && s.y > 0) return "in";
  if (s.x <= 0 && s.y > 0) return "big";
  if (s.x > 0 && s.y <= 0) return "inst";
  return "out";
}
const FLOW_Q = [["in", "雙流入"], ["big", "大戶增・法人賣"], ["inst", "法人買・大戶減"], ["out", "雙流出"]];

async function loadSectorFlow() {
  const el = $("flow-chart"), note = $("rotation-note");
  if (!el) return;
  try {
    const d = await getJSON("/api/sectors/flow");
    lastSectorFlow = d;
    const secs = d.sectors || [];
    if (!secs.length) {
      disposeFlowChart();
      el.innerHTML = '<div class="muted small" style="padding:12px">尚無法人資料（stock_flow_daily 尚未累積）</div>';
      $("flow-quadrants").innerHTML = "";
      if (note) note.textContent = "";
      return;
    }
    const md = (s) => (s || "").slice(5);
    const bits = [`法人 ${md(d.flow_dates[0])}～${md(d.flow_dates[d.flow_dates.length - 1])}（${d.flow_dates.length} 日）`];
    bits.push(d.has_custody ? `集保 ${md(d.custody_weeks[1])}→${md(d.custody_weeks[0])}` : "集保不足兩個完整週，暫以法人單軸顯示");
    if (!d.has_tail) bits.push(`法人資料不足 ${d.flow_dates.length * 2} 日，尚無上一期`);
    bits.push(`${secs.length} 類股`);
    const ex = d.excluded || {};
    const gaps = [];
    if (ex.no_price) gaps.push(`${ex.no_price} 檔查不到收盤`);
    if (ex.sectors_no_mcap) gaps.push(`${ex.sectors_no_mcap} 類算不出市值未列`);
    if (note) note.textContent = `（${bits.join("・")}${gaps.length ? "；" + gaps.join("、") : ""}）`;
    if (d.has_custody) renderSectorFlow(d); else renderFlowBars(d);
    renderFlowQuadrants(d);
  } catch (e) {
    disposeFlowChart();
    el.innerHTML = '<div class="muted small" style="padding:12px">族群輪動載入失敗</div>';
  }
}

function disposeFlowChart() {
  if (flowChart) { flowChart.dispose(); flowChart = null; }
}

function renderSectorFlow(d) {
  const el = $("flow-chart");
  const secs = d.sectors.filter((s) => s.x != null && s.y != null);
  // 順序：先寫容器高 → setOption → resize（echarts.init 會凍住它看到的尺寸）
  el.style.height = (matchMedia("(max-width: 600px)").matches ? 360 : 460) + "px";
  if (!flowChart) { el.innerHTML = ""; flowChart = initChart(el); }
  const absMax = (arr) => Math.max(0.05, ...arr.filter((v) => v != null).map((v) => Math.abs(v)));
  const xm = Math.ceil(absMax(secs.flatMap((s) => [s.x, s.x_prev])) * 1.1 * 100) / 100;
  const ym = Math.ceil(absMax(secs.flatMap((s) => [s.y, s.y_prev])) * 1.1 * 100) / 100;
  const mcMax = Math.max(1, ...secs.map((s) => s.mcap));
  const size = (m) => Math.max(10, Math.min(56, 10 + 46 * Math.sqrt(m / mcMax)));   // √市值，台積電那類不獨大
  const alpha = (s) => 0.45 + 0.4 * Math.min(1, (Math.abs(s.x) / xm + Math.abs(s.y) / ym) / 2);
  const md = (s) => (s || "").slice(5);
  const tails = d.has_tail ? secs.filter((s) => s.x_prev != null && s.y_prev != null).map((s) => ({
    type: "line", silent: true, showSymbol: false, z: 1, data: [[s.x_prev, s.y_prev], [s.x, s.y]],
    lineStyle: { width: 1, color: withAlpha(C.info, 0.45) },
  })) : [];
  const prevDots = d.has_tail ? {
    type: "scatter", silent: true, z: 2, symbolSize: 4, itemStyle: { color: withAlpha(C.info, 0.5) },
    data: secs.filter((s) => s.x_prev != null && s.y_prev != null).map((s) => [s.x_prev, s.y_prev]),
  } : null;
  const bubbles = {
    type: "scatter", z: 3,
    data: secs.map((s) => ({
      name: s.sector, value: [s.x, s.y], sector: s, symbolSize: size(s.mcap),
      itemStyle: { color: withAlpha(C.info, alpha(s)), borderColor: C.info, borderWidth: 1 },
      label: { show: size(s.mcap) >= 22, position: "inside", formatter: s.sector, fontSize: 11, color: C.text, fontFamily: HM_FONT },
    })),
    markLine: { silent: true, symbol: "none", lineStyle: { color: C.border, type: "solid" }, label: { show: false }, data: [{ xAxis: 0 }, { yAxis: 0 }] },
    markArea: { silent: true, data: [
      [{ xAxis: 0, yAxis: 0, itemStyle: { color: withAlpha(C.info, 0.10) } }, { xAxis: xm, yAxis: ym }],   // 雙流入略亮
      [{ xAxis: -xm, yAxis: 0, itemStyle: { color: withAlpha(C.info, 0.04) } }, { xAxis: 0, yAxis: ym }],
      [{ xAxis: 0, yAxis: -ym, itemStyle: { color: withAlpha(C.info, 0.04) } }, { xAxis: xm, yAxis: 0 }],
      [{ xAxis: -xm, yAxis: -ym, itemStyle: { color: withAlpha(C.info, 0.02) } }, { xAxis: 0, yAxis: 0 }],
    ] },
  };
  const qLabel = (text, left, top) => ({ type: "text", left, top, silent: true,
    style: { text, fill: C.muted, fontSize: 11, fontFamily: HM_FONT } });
  const tip = financeTooltip({ trigger: "item", formatter: (p) => {
    const s = p.data && p.data.sector; if (!s) return "";
    const chg = s.chg_pct == null ? "—" : `<span class="${chgClass(s.chg_pct)}">${s.chg_pct > 0 ? "▲" : s.chg_pct < 0 ? "▼" : ""}${fmt(Math.abs(s.chg_pct), 2)}%</span>`;
    const top = (s.top3 || []).map((t) => `${esc(t.code)} ${esc(t.name)} ${fmt(t.amount / 1e8, 1)} 億`).join("<br>");
    return `<b>${esc(s.sector)}</b>　當日 ${chg}<br>`
      + `法人 ${d.flow_dates.length} 日 ${fmtSigned(s.x, 2)}%（${md(d.flow_dates[0])}～${md(d.flow_dates[d.flow_dates.length - 1])}）<br>`
      + `大戶週增 ${fmtSigned(s.y, 2)}%（${md(d.custody_weeks[1])}→${md(d.custody_weeks[0])}，樣本 ${s.n_cust}/${s.n} 檔）<br>`
      + `市值 ${fmt(s.mcap / 1e8, 0)} 億　${s.n} 檔` + (top ? `<br><span class="muted">法人買最多：</span><br>${top}` : "");
  } });
  flowChart.setOption({
    tooltip: tip,
    grid: { left: 60, right: 24, top: 30, bottom: 48 },
    xAxis: { type: "value", min: -xm, max: xm, name: `法人近 ${d.flow_dates.length} 日淨買賣 ÷ 市值（%）`, nameLocation: "middle", nameGap: 30,
      axisLabel: { color: C.muted, fontSize: 11 }, nameTextStyle: { color: C.label, fontSize: 11 }, splitLine: { show: false } },
    yAxis: { type: "value", min: -ym, max: ym, name: "大戶 400張↑ 週增 ÷ 市值（%）", nameLocation: "middle", nameGap: 44,
      axisLabel: { color: C.muted, fontSize: 11 }, nameTextStyle: { color: C.label, fontSize: 11 }, splitLine: { show: false } },
    graphic: [qLabel("大戶增・法人賣", 68, 34), qLabel("雙流入", "right", 34), qLabel("雙流出", 68, "bottom"), qLabel("法人買・大戶減", "right", "bottom")]
      .map((g, i) => (i === 1 || i === 3) ? { ...g, right: 30, left: undefined } : g)
      .map((g, i) => (i >= 2) ? { ...g, bottom: 56, top: undefined } : g),
    series: [...tails, ...(prevDots ? [prevDots] : []), bubbles],
  }, true);
  flowChart.resize();
  flowChart.off("click");
  flowChart.on("click", (p) => { if (p.data && p.data.sector) toggleRotationFilter(p.data.sector.sector); });
}

// 降級：集保不足兩個完整週 → 只有 X 軸，畫置中零點的水平長條（不畫沒有 Y 的散點）
function renderFlowBars(d) {
  const el = $("flow-chart");
  disposeFlowChart();
  el.style.height = "auto";
  const secs = d.sectors.filter((s) => s.x != null).slice().sort((a, b) => b.x - a.x);
  const xm = Math.max(0.05, ...secs.map((s) => Math.abs(s.x)));
  el.innerHTML = `<div class="flow-bars">${secs.map((s) => {
    const w = Math.abs(s.x) / xm * 50, left = s.x < 0 ? 50 - w : 50;
    return `<div class="flow-bar-row"><span class="flow-bar-name" title="${esc(s.sector)}">${esc(s.sector)}</span>`
      + `<span class="flow-bar-track"><span class="flow-bar-fill" style="left:${left}%;width:${w}%"></span></span>`
      + `<span class="flow-bar-val">${fmtSigned(s.x, 2)}%</span></div>`;
  }).join("")}</div>`;
}

function renderFlowQuadrants(d) {
  const el = $("flow-quadrants"); if (!el) return;
  const secs = d.sectors.filter((s) => s.x != null);
  const dist = (s) => Math.hypot(s.x, s.y == null ? 0 : s.y);
  if (!d.has_custody) {   // 單軸時只分「法人買／法人賣」兩欄
    const cols = [["in", "法人買超", (s) => s.x > 0], ["out", "法人賣超", (s) => s.x <= 0]];
    el.innerHTML = cols.map(([k, title, pred]) => flowColumn(k, title, secs.filter(pred).sort((a, b) => dist(b) - dist(a)), false)).join("");
    return;
  }
  el.innerHTML = FLOW_Q.map(([k, title]) =>
    flowColumn(k, title, secs.filter((s) => s.y != null && flowQuadrant(s) === k).sort((a, b) => dist(b) - dist(a)), true)).join("");
}
function flowColumn(k, title, rows, withY) {
  return `<div class="flow-q flow-q-${k}"><h4>${title} <span class="muted small">${rows.length}</span></h4>`
    + (rows.map((s) => `<button type="button" class="flow-row" data-sector="${esc(s.sector)}" aria-pressed="${rotationSectorFilter === s.sector}">`
      + `<span class="flow-row-name">${esc(s.sector)}</span><span class="flow-row-val">${fmtSigned(s.x, 2)}${withY ? "／" + fmtSigned(s.y, 2) : ""}</span></button>`).join("")
      || '<div class="muted small">—</div>') + "</div>";
}

// drill-down：篩下方交叉選股（不重打 API，只切 .hidden）；再點同一個取消。狀態不持久化。
function toggleRotationFilter(sector) {
  rotationSectorFilter = (sector && rotationSectorFilter !== sector) ? sector : null;
  applyRotationFilter();
  if (lastSectorFlow) renderFlowQuadrants(lastSectorFlow);
}
function applyRotationFilter() {
  document.querySelectorAll("#cross .cross-grp").forEach((g) => {
    g.classList.toggle("hidden", !!rotationSectorFilter && g.dataset.sector !== rotationSectorFilter);
  });
  const note = $("cross-note"); if (!note) return;
  note.innerHTML = esc(crossNoteBase) + (rotationSectorFilter
    ? `<span class="flow-filter">篩選：${esc(rotationSectorFilter)}<button type="button" id="cross-clear" class="tf">顯示全部</button></span>` : "");
}
```

(b) `loadCross` 裡兩處小改：
- `if (note) note.textContent = \`（${src}，共 ${d.groups.length} 族群\`` 這行改成 `crossNoteBase = \`（${src}，共 ${d.groups.length} 族群\``＋原本後面的字串（把 `note.textContent =` 換成 `crossNoteBase =`）；沒有 groups 那條 `if (note) note.textContent = ""` 改成 `crossNoteBase = ""; applyRotationFilter();`。
- `return \`<div class="cross-grp ${cls}">` 改成 `return \`<div class="cross-grp ${cls}" data-sector="${esc(g.sector)}">`。
- `el.innerHTML = d.groups.map(...).join("");` 之後補一行 `applyRotationFilter();`。

(c) `showView` 裡 `if (name === "rotation") { loadRotation(); loadCross(); }` 改成
`if (name === "rotation") { if (flowChart) flowChart.resize(); loadSectorFlow(); loadCross(); }`

(d) `window.addEventListener("resize", () => {` 那個陣列 `[chipChart, pulseChart, cupChart, distChart, instBreadthChart, instAlphaChart]` 加進 `flowChart`：`[chipChart, pulseChart, cupChart, distChart, instBreadthChart, instAlphaChart, flowChart]`。

(e) 在那個 `window.addEventListener("resize", ...)` 區塊**結束的 `});` 之後**加事件委派（CSP 不吃 inline handler）：

```js
// 族群輪動：四區排行與「顯示全部」都委派在靜態祖先上（CSP script-src 'self' 會丟掉 inline on*=）
const flowQEl = $("flow-quadrants");
if (flowQEl) flowQEl.addEventListener("click", (e) => { const b = e.target.closest(".flow-row"); if (b) toggleRotationFilter(b.dataset.sector); });
const crossNoteEl = $("cross-note");
if (crossNoteEl) crossNoteEl.addEventListener("click", (e) => { if (e.target.closest("#cross-clear")) toggleRotationFilter(null); });
```

- [ ] **Step 5: 確認 `loadRotation` 與 `/sectors/rotation` 已無殘留、行尾正確**

Run: `grep -n "loadRotation\|sectors/rotation\|id=\"rotation\"" web/app.js web/index.html stocks_power_rich/api/*.py`
Expected: 無輸出

Run:
```bash
.venv\Scripts\python -c "for p in ('web/app.js','web/index.html','web/styles.css','stocks_power_rich/api/public.py','tests/test_api.py'): b=open(p,'rb').read(); print(p, 'loneLF', b.count(b'\n')-b.count(b'\r\n'))"
```
Expected: 五行都 `loneLF 0`

- [ ] **Step 6: 跑既有前端相關測試**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -q --no-header -k "public or frontend or overview" > pt_task5.txt 2>&1; echo EXIT=$?; tail -1 pt_task5.txt`
Expected: `EXIT=0`（版號四處一致）

- [ ] **Step 7: 瀏覽器實測（用 preview_start `spr`，不要用 Bash 起伺服器；伺服器若已在跑要先 preview_stop 再 start，否則吃到舊的 Python 程式碼）**

進「族群輪動」頁，逐項用 `javascript_tool` 量、把結果寫進回報：

1. **請求**：`read_network_requests` 過濾 `sectors/flow` → 一筆 200；過濾 `sectors/rotation` → 零筆。
2. **圖有畫**：`document.querySelectorAll('#flow-chart canvas').length >= 1`，且 `#flow-chart` 的 `clientHeight` 在桌機是 460。
3. **四區排行**：`document.querySelectorAll('#flow-quadrants .flow-q').length === 4`（本機集保有兩完整週）；每個 `.flow-row` 的 `getBoundingClientRect().height >= 28`。
4. **象限落點反證**：在 Console 對 `lastSectorFlow.sectors` 每一筆算 `flowQuadrant(s)`，斷言與它落在哪一欄一致（`document.querySelector('.flow-q-'+q).textContent.includes(s.sector)`），輸出不一致的數量 → 期望 0。
5. **drill-down**：點第一個 `.flow-row` → 該鈕 `aria-pressed="true"`、`#cross .cross-grp:not(.hidden)` 全部 `dataset.sector` 等於該類股、`#cross-clear` 存在；再點一次 → 全部 `.cross-grp` 無 `.hidden`、`#cross-clear` 不存在。點泡泡（用 `flowChart.dispatchAction({type:'...'})` 不好模擬，改用 `flowChart.trigger`／直接呼叫 `toggleRotationFilter('半導體')`）驗同一條路徑。
6. **降級分支不是死碼**：暫時覆寫 `window.fetch` 讓 `/api/sectors/flow` 回應的 `has_custody=false`、`y` 全 `null`，重跑 `loadSectorFlow()` → `#flow-chart .flow-bars` 存在、`.flow-q` 只有 2 欄、說明列含「暫以法人單軸顯示」；還原 `fetch` 後再跑一次回到散點。
7. **顏色**：掃 `#flow-quadrants` 與 `#rotation-note` 內所有元素的 computed `color`，不得出現 `--up`／`--down` 的色值（`getComputedStyle(document.documentElement).getPropertyValue('--up')`）。
8. **三個寬度**：`resize_window` 1904／1280／375 各量 `document.documentElement.scrollWidth <= clientWidth`（零頁面溢出）、375 時 `#flow-chart` 高 360 且 `.rotation-flow` 是單欄（`getComputedStyle(...).gridTemplateColumns.split(' ').length === 1`）。
9. **切走再切回**：切到總覽再切回族群輪動，`#flow-chart canvas` 的 `width` 屬性不為 0。
10. 最後 `resize_window` preset `desktop`。

任一項不符就修到符合再往下；把量到的數字寫進任務報告，不要只寫「看起來正常」。

- [ ] **Step 8: 提交**

```bash
git add web/index.html web/app.js web/styles.css stocks_power_rich/api/public.py tests/test_api.py
git commit -m "feat(ui): 族群輪動改法人×大戶資金流向四象限（ui69）——泡泡＋尾巴、四區排行、篩交叉選股、單軸降級" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: 文件與全套測試

**Files:**
- Modify: `CLAUDE.md`（在 `### Public pages` 之前插一節）
- Modify: `AGENTS.md`（在 `## Conventions` 之前插一節）

- [ ] **Step 1: CLAUDE.md 加一節**

在 `### Public pages (\`/public/*\`)` 那一行之前插入（CRLF）：

```markdown
### 族群輪動改「法人 × 大戶」資金流向四象限（ui69，2026-09）

**先講一個從 `bfd0a53` 起就存在的壞掉功能**：族群輪動頁的主圖「近 N 日類股漲跌表」再也畫不出來——
那次 main.py 拆 APIRouter 時把 `/api/sectors/rotation` 的 `sectors` 從陣列 `[{name, series, sum}]` 改成
dict `{name: [...]}`，前端 `loadRotation` 沒跟著改，`d.sectors.length` 恆為 `undefined` → 永遠「尚無類股資料」。
資料一直都在（37 類股、20 天）。**refactor 改回傳形狀時，唯一會抓到的是端到端測試或真的打開頁面**；
兩條 chips 測試也是同型的「綠燈但沒測到重點」。這次直接移除那條端點與表格。

新圖（spec `docs/superpowers/specs/2026-09-22-sector-flow-quadrant-design.md`）：X＝法人近 5 日淨買賣
÷ 類股市值、Y＝大戶 400張↑ 週增 ÷ 類股市值，泡泡＝類股（√市值）、尾巴＝上一期→本期。純函式
`analysis.sector_flow`；端點 `GET /api/sectors/flow`；db 三支小查詢 `stock_flow_dates`／
`institutional_window_map`／`custody_delta_map`。

- **除以市值不是美化**：用絕對金額半導體永遠在最右邊，圖只會告訴你「半導體很大」。
- **大戶金額只有一份算式** `analysis.big_holder_amount`，`selfcheck.build_self_screen` 也改呼叫它；
  `tests/test_analysis_sector_flow.py` 用 `inspect.getsource` 鎖住 selfcheck 不能再內聯一份。
- **大戶缺某檔 Δ 不進分子、仍進分母**（分母是類股規模）；整類股一檔都沒 Δ 時 `y=None`，不是 0
  （0 是「有資料且淨額為零」）。反證做過：把分母改成只算有 Δ 的檔 → `y` 從 0.5 變 1.0、測試紅。
- **法人交易日曆用 `stock_flow_daily` 自己的日期**，不用 `market_daily`——後者當天早上就有列而法人
  16:00 後才公布，會把今天的空列算進 5 日窗口。
- **快取鍵含集保週** `sectorflow:v1:{法人最新日}:{集保週|none}:{days}`，週六 custody_watch 抓到新週自然
  換鍵；**讀取端另外比對 `has_custody`**（同 `_os_futures` 的 `has_remote`：寫入守衛擋不住已寫進去的半套）。
- **顏色不用紅綠**：資金流向不是漲跌，一律 `C.info`；價格漲跌只在 tooltip 的 `chg_pct`。
- **四區排行是 canvas 的鍵盤替代**（同 ui29 chip），每列 `<button aria-pressed>`、`min-height: 28px`；
  點泡泡或列都篩下方交叉選股（`.cross-grp` 帶 `data-sector`、只切 `.hidden` 不重打 API）。
- **降級**：集保不足兩完整週 → 單軸水平長條（`.flow-bars`）＋兩欄排行；法人不足 `2×days` 日 → 無尾巴；
  都寫在說明列。
- **`custody_compare_weeks` 加了 `limit`**（預設 2、行為不變），Y 的上一期需要第 3 個完整週。
- 刻意不做：潮汐的 108 板塊（本站 32 個官方產業別剛好一屏，細分類 530 太碎）、合成分數（不手訂權重）、
  加速度四區（尾巴已表達方向）。

```

- [ ] **Step 2: AGENTS.md 加一節**

在 `## Conventions` 之前插入（CRLF）：

```markdown
## 族群輪動改「法人 × 大戶」資金流向四象限（ui69，2026-09）
主圖「近 N 日類股漲跌表」從 `bfd0a53`（拆 APIRouter）起就壞了——後端把 `sectors` 從陣列改成 dict、前端沒跟，永遠「尚無類股資料」。直接移除 `/api/sectors/rotation`，換成 `GET /api/sectors/flow`：X＝法人近 5 日淨買賣 ÷ 類股市值、Y＝大戶 400張↑ 週增 ÷ 類股市值（除以市值才跨類股可比），泡泡＝類股、尾巴＝上一期→本期；純函式 `analysis.sector_flow`，大戶金額只有一份算式 `analysis.big_holder_amount`（selfcheck 也改呼叫它，`inspect.getsource` 鎖住）。大戶缺某檔 Δ 不進分子仍進分母、整類股沒 Δ 時 `y=None` 不是 0；法人交易日曆用 `stock_flow_daily` 自己的日期（不用 `market_daily`，今天的空列會被算進窗口）；快取鍵含集保週且讀取端比對 `has_custody`；顏色一律 `C.info` 不碰紅綠；四區排行 `<button aria-pressed>` 是 canvas 的鍵盤替代，點泡泡或列篩下方交叉選股；集保不足兩週降級成單軸長條。`custody_compare_weeks` 加 `limit`（預設 2）。

```

- [ ] **Step 3: 行尾檢查**

Run:
```bash
.venv\Scripts\python -c "for p in ('CLAUDE.md','AGENTS.md'): b=open(p,'rb').read(); print(p, 'loneLF', b.count(b'\n')-b.count(b'\r\n'))"
```
Expected: 兩行都 `loneLF 0`

- [ ] **Step 4: 全套測試（不接管線）**

Run: `.venv\Scripts\python -m pytest -q --no-header > pt_full.txt 2>&1; echo EXIT=$?; tail -2 pt_full.txt`
Expected: `EXIT=0`，`N passed`（N ≥ 1038 + 11 + 4 + 7 = 1060）

- [ ] **Step 5: 提交**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: 族群輪動四象限——記下 bfd0a53 弄壞的表格、除以市值、has_custody 雙守衛與不用紅綠的理由" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review（計畫作者已做）

- **Spec 覆蓋**：§1 母體與分類→Task 2 `_is_common_code`＋Task 4 `universe`；§2 全部算式與缺值語意→Task 2；§3 端點、快取鍵、讀取守衛、不連外→Task 4；§4 markup／主圖／尾巴／顏色／tooltip／四區排行／drill-down／說明列／三種降級→Task 5；§5 版面與斷點→Task 5 Step 3；§6 版號與文件→Task 5 Step 1、Task 6；§7 測試→Task 1–4 的測試＋Task 5 Step 7；§8 刻意不做→Task 6 文件。
- **型別一致**：`sector_flow` 的輸出鍵（`sector,x,y,x_prev,y_prev,mcap,n,n_cust,top3,chg_pct`）在 Task 2 定義、Task 4 原樣展開、Task 5 前端讀同名欄位；`custody_compare_weeks(limit=3)` 在 Task 3 定義、Task 4 使用；`flowQuadrant` 與 `FLOW_Q` 的四個 key（`in/big/inst/out`）與 CSS `.flow-q-in` 對應。
- **無佔位**：每一步都有實際程式碼或實際指令與期望輸出。
