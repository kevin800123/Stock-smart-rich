# 族群輪動頁重新設計（ui72）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓「族群輪動」頁 5 秒內回答「最強共振／加速轉強／風險外流」三個問題：新增規則式摘要三卡、全範圍主圖＋核心放大鏡、上期→本期方向向量、象限領先者 Top 3、選取細節列，並把頁首縮成一句判讀＋chips。

**Architecture:** 只改前端（`web/app.js`、`web/styles.css`、`web/index.html`），API 與 X／Y 定義不動。新增純函式 `flowModel(d)` 算出所有判定（放大鏡範圍、正規化刻度、三卡、標籤、Top 3、主圖範圍），摘要卡、兩張 ECharts 散點圖、象限領先者、細節列、頁首全部只讀這一份結果。選取仍是單一 `rotationSectorFilter`，四種點擊（卡、主圖泡泡、放大鏡泡泡、排行列）都走 `toggleRotationFilter`。

**Tech Stack:** vanilla JS（無建置步驟）、ECharts 5.6.1（`web/vendor/echarts.min.js`，含 `lines` 系列）、原生 `popover`、CSS Grid。

Spec：`docs/superpowers/specs/2026-09-23-sector-rotation-redesign-design.md`。

## Global Constraints

- **只改前端**：`web/app.js`、`web/styles.css`、`web/index.html`，加上快取版號與文件。`/api/sectors/flow`、`/api/sectors/picks` 的回應形狀與 X／Y 定義一律不動。
- **放大鏡範圍**：百分位＝排序後線性內插；`window(t)` 每軸取 `[min(q(t),0), max(q(1−t),0)]`（一定含原點）再往兩側各加寬度的 **12%**；從 `t = 0.10` 開始，框內少於 `ceil(0.8 × n)` 就 `t −= 0.01`，直到框住至少 80% 或 `t = 0`；軸寬至少 `0.02`。
- **正規化刻度**：`wx`、`wy`＝放大鏡（含邊界）的 X 寬、Y 寬。`comp = x/wx + y/wy`；有上期時 `dnx = Δx/wx`、`dny = Δy/wy`、`move = dnx + dny`、`mag = hypot(dnx, dny)`。
- **三卡規則**：最強共振＝雙流入中 `comp` 最大；加速轉強＝有上期且 `comp > 0` 且 `move > 0` 中 `move` 最大；風險外流＝有上期且 `comp < 0` 且 `move < 0` 中 `move` 最小。
- **常駐標籤**：三卡類股 → 依 `mag` 由大到小補到 **7** 個（沒有上期時依 `hypot(nx, ny)` 補）→ 加上目前選取（最多 8）。放大鏡只標框內的。
- **象限領先者**：每象限依 `hypot(nx, ny)` 由大到小取前 **3**，並顯示類股數；每列寫明「法人 ±x.xx%」「大戶 ±x.xx%」。
- **主圖範圍**：X、Y 各取「本期＋上期（有上期者）＋原點」的最小到最大，兩側各加 **6%**，線性、不對稱。
- **象限色 token**（新增於 `:root`）：`--flow-in: #5fd6ee`、`--flow-big: #70b8ff`、`--flow-inst: #8b93f8`、`--flow-out: #8494ad`；JS 由 `CSS_VAR` 讀，不寫死。紅／綠只出現在 tooltip 的當日漲跌與下方交叉選股。不新增漸層、3D、發光。
- **數字格式**：卡片、排行、細節列、tooltip 的百分比一律固定兩位小數（`flowPct`），四捨五入後為 0 寫 `0.00%`（不寫 `-0.00%`）。既有 `fmt()` 會把尾端 0 去掉，不可用在這些地方。
- **版面**：≥1181px 兩欄 `7fr 5fr`（左主圖；右＝放大鏡＋象限領先者）；601–1180px 主圖整列、放大鏡與象限領先者並排；≤600px 單欄且順序為 主圖 → 放大鏡 → 細節列 → 象限領先者。主圖高 **580／460／340px**，放大鏡 **250／300／300px**。`.chart` 定義在後面且 `height: 340px` 同權重，這兩個高度必須寫成 `#view-rotation .flow-chart`／`#view-rotation .flow-zoom`。
- **互動**：卡、排行列、清除篩選、指標說明都是 `<button>`；選取用 `aria-pressed`、不可用的卡 `disabled`；觸控目標至少 28px；沿用全站 `:focus-visible`。CSP 是 `script-src 'self'`：**不得寫 inline `on*=`**，事件一律委派在已存在於靜態 HTML 的祖先上。
- **ECharts**：`setOption` 後一定 `resize()`；兩張圖都要加進 `window` resize 清單與 `showView("rotation")`。
- **快取版號** `20260817-ui71` → `20260817-ui72`，四處：`web/index.html`×2、`stocks_power_rich/api/public.py`（2 行 4 處）、`tests/test_api.py`×4。
- 所有 repo 檔案 **CRLF**；改完用 bytes 計數確認 lone LF＝0。**不要用 `sed -i`**。pytest 不接管線。
- Windows 終端機會吃 CJK：含中文的檢查輸出寫 UTF-8 檔再讀。
- 提交訊息結尾 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。**不要 push**。
- 前端沒有自動化測試：每個任務的「測試」是在瀏覽器裡注入 2026-09-23 真實快照（`.superpowers/sdd/fixtures/sector-flow-2026-09-23.json`，git-ignored）後執行斷言腳本。

---

## File Structure

| 檔案 | 這次負責 |
|---|---|
| `web/app.js` | `flowModel` 與格式化／判讀純函式（Task 1）；頁首、摘要卡（Task 2）；兩張圖（Task 3）；象限領先者、細節列、統一選取與鍵盤（Task 4） |
| `web/styles.css` | 象限 token、頁首 chips、popover、摘要卡、兩欄版面與斷點、象限領先者、細節列（Task 2） |
| `web/index.html` | `#view-rotation` 的新骨架（Task 2）；快取版號（Task 5） |
| `stocks_power_rich/api/public.py`、`tests/test_api.py` | 快取版號（Task 5） |
| `CLAUDE.md`、`AGENTS.md` | ui72 規則與量測（Task 5） |

---

### Task 1: `flowModel` 純函式與判讀文字

**Files:**
- Modify: `web/app.js`（在 `const FLOW_Q = [...]` 那一行之後插入；約第 3778 行）

**Interfaces:**
- Produces（後續任務依賴，名稱與形狀不可改）：
  ```js
  flowQuantile(values: number[], p: number) -> number|null
  flowModel(d) -> null | {
    rows: Row[], bySector: {[sector]: Row},
    core: { x: [lo, hi], y: [lo, hi], trim: number, inside: number, n: number },
    wx: number, wy: number,
    main: { x: [lo, hi], y: [lo, hi] },
    cards: { strongest: Row|null, accel: Row|null, outflow: Row|null },
    labels: string[],               // 常駐標籤（不含選取），最多 7
    leaders: { in|big|inst|out: { count: number, top: Row[] } },
    hasPrevAny: boolean,
  }
  // Row = { s（原始 sector 物件）, sector, q（"in"|"big"|"inst"|"out"）, prev: boolean,
  //         nx, ny, comp, dist, inCore, 以及 prev 為 true 時的 dnx, dny, move, mag, qPrev }
  flowArrow(row) -> "" | "→"|"↗"|"↑"|"↖"|"←"|"↙"|"↓"|"↘"
  flowCardNote(kind: "strongest"|"accel"|"outflow", row) -> string
  flowHeadline(model) -> string
  flowSigned2(v) -> string   // "+0.36"／"-0.24"／"0.00"／"—"
  flowPct(v) -> string       // flowSigned2(v) + "%"，null 為 "—"
  FLOW_Q_NAME = { in: "雙流入", big: "大戶增・法人賣", inst: "法人買・大戶減", out: "雙流出" }
  ```
- Consumes: 既有的 `flowQuadrant(s)`（第 3772 行）。

- [ ] **Step 1: 先寫斷言腳本（這就是本任務的測試）**

存成 `.superpowers/sdd/fixtures/flow-model-check.js`（git-ignored，只給瀏覽器用），內容：

```js
(() => {
  const m = flowModel(window.__FX);
  const near = (a, b, tol = 0.001) => Math.abs(a - b) <= tol;
  const names = (arr) => arr.map((r) => r.sector).join(",");
  const outside = m.rows.filter((r) => !r.inCore).map((r) => r.sector).sort().join(",");
  const checks = {
    trim: m.core.trim === 0.08,
    inside: m.core.inside === 28 && m.core.n === 34,
    coreX: near(m.core.x[0], -0.227) && near(m.core.x[1], 0.382),
    coreY: near(m.core.y[0], -0.270) && near(m.core.y[1], 0.168),
    scales: near(m.wx, 0.610) && near(m.wy, 0.438),
    outside: outside === ["光電", "航運", "水泥", "文化創意", "通信網路", "其他"].sort().join(","),
    strongest: m.cards.strongest.sector === "航運" && near(m.cards.strongest.comp, 1.27, 0.01),
    accel: m.cards.accel.sector === "汽車" && near(m.cards.accel.move, 3.05, 0.01),
    outflow: m.cards.outflow.sector === "光電" && near(m.cards.outflow.move, -2.51, 0.01),
    labels: m.labels.join(",") === "航運,汽車,光電,資訊服務,文化創意,水泥,化學",
    leadIn: m.leaders.in.count === 8 && names(m.leaders.in.top) === "航運,電機機械,化學",
    leadBig: m.leaders.big.count === 5 && names(m.leaders.big.top) === "其他,電腦及週邊設備,玻璃陶瓷",
    leadInst: m.leaders.inst.count === 15 && names(m.leaders.inst.top) === "光電,水泥,文化創意",
    leadOut: m.leaders.out.count === 6 && names(m.leaders.out.top) === "通信網路,塑膠,綠能環保",
    mainX: near(m.main.x[0], -0.429) && near(m.main.x[1], 0.619),
    mainY: near(m.main.y[0], -1.383) && near(m.main.y[1], 0.787),
    arrows: flowArrow(m.bySector["汽車"]) === "↑" && flowArrow(m.bySector["光電"]) === "↓",
    noteAccel: flowCardNote("accel", m.cards.accel) === "大戶改善最多：-1.27% → 0.00%",
    noteOut: flowCardNote("outflow", m.cards.outflow) === "大戶惡化最多：+0.67% → -0.67%",
    noteStrong: flowCardNote("strongest", m.cards.strongest) === "由法人買・大戶減轉為雙流入，兩軸合計最強",
    headline: flowHeadline(m) === "航運雙流入最強、汽車轉強最快、光電外流最急。",
    pct: flowPct(0.3) === "+0.30%" && flowPct(-1.267) === "-1.27%" && flowPct(0.001) === "0.00%"
      && flowPct(-0.0004) === "0.00%" && flowPct(null) === "—" && flowSigned2(0.348) === "+0.35",
  };
  // 規則邊界：用假資料逐條驗
  const mk = (list, extra = {}) => ({ has_tail: true, has_custody: true, ...extra,
    sectors: list.map(([sector, x, y, xp, yp]) => ({ sector, x, y, x_prev: xp, y_prev: yp, mcap: 1e12, n: 5, n_cust: 5, top3: [] })) });
  const allWorse = flowModel(mk([["A", 0.1, 0.1, 0.3, 0.3], ["B", -0.1, 0.1, 0.0, 0.2], ["C", -0.2, -0.1, -0.1, 0.0],
    ["D", 0.2, -0.2, 0.3, 0.0], ["E", 0.05, 0.02, 0.1, 0.05]]));
  checks.noAccelWhenAllWorse = allWorse.cards.accel === null;
  const stillNeg = flowModel(mk([["A", -0.3, -0.2, -0.9, -0.8], ["B", 0.1, 0.1, 0.12, 0.11], ["C", -0.05, 0.02, -0.04, 0.03],
    ["D", 0.02, -0.01, 0.03, 0.0], ["E", 0.01, 0.01, 0.01, 0.01]]));
  checks.guardExcludesStillNegative = stillNeg.cards.accel === null;
  const noIn = flowModel(mk([["A", -0.1, 0.1, 0, 0], ["B", 0.1, -0.1, 0, 0], ["C", -0.1, -0.1, 0, 0]]));
  checks.noStrongestWithoutIn = noIn.cards.strongest === null;
  const noTail = flowModel(mk([["A", 0.2, 0.1, 0.1, 0.1], ["B", -0.1, 0.3, 0.0, 0.0], ["C", 0.3, -0.2, 0.1, 0.0]], { has_tail: false }));
  checks.noPrevCards = noTail.cards.accel === null && noTail.cards.outflow === null
    && noTail.hasPrevAny === false && noTail.labels.length === 3;
  const tiny = flowModel(mk([["A", 0.001, 0.001, 0, 0], ["B", 0.001, 0.001, 0, 0]]));
  checks.degenerateSpan = tiny.wx >= 0.02 && tiny.wy >= 0.02 && tiny.core.x[0] <= 0 && tiny.core.x[1] >= 0;
  checks.nullModel = flowModel({ sectors: [] }) === null
    && flowModel({ sectors: [{ sector: "A", x: 0.1, y: null }] }) === null;
  const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
  return JSON.stringify({ failed, passed: Object.keys(checks).length - failed.length, total: Object.keys(checks).length });
})()
```

- [ ] **Step 2: 啟動預覽並注入真實快照，確認斷言先失敗**

1. `mcp__Claude_Browser__preview_start` `{name: "spr"}`（`.claude/launch.json` 已定義，port 8000）。若回 `reused: true`，先 `preview_stop` 再 start，讓新的 JS 生效。
2. 用 Read 讀 `.superpowers/sdd/fixtures/sector-flow-2026-09-23.json`，`javascript_tool` 執行 `window.__FX = <整份 JSON 貼上>; "ok"`。
3. `javascript_tool` 執行 Step 1 的腳本內容。

Expected: `ReferenceError: flowModel is not defined`。

- [ ] **Step 3: 實作**

在 `web/app.js` 的 `const FLOW_Q = [["in", "雙流入"], ...];` 那一行**之後**插入：

```js
// ---------- 族群輪動模型（純函式，不碰 DOM；ui72）----------
// 主圖、放大鏡、摘要卡、象限領先者、標籤、細節列、頁首判讀都只讀 flowModel 的結果，同一個類股
// 在不同區塊不會有不同的判定。規則與 2026-09-23 的實測基準見
// docs/superpowers/specs/2026-09-23-sector-rotation-redesign-design.md §1。
const FLOW_CORE_SHARE = 0.8;    // 放大鏡至少框住的類股比例（「中央約 80%」）
const FLOW_CORE_TRIM0 = 0.10;   // 修剪起點：每軸兩端各修 10%，框不到 80% 就每次放寬 1 點
const FLOW_CORE_PAD = 0.12;     // 放大鏡兩側邊界（寬度的比例）
const FLOW_MAIN_PAD = 0.06;     // 主圖兩側邊界
const FLOW_MIN_SPAN = 0.02;     // 軸的最小寬度（%）：全部同值時不讓座標退化成一點
const FLOW_LABELS_BASE = 7;     // 常駐標籤數（不含目前選取的那一個）
const FLOW_Q_NAME = { in: "雙流入", big: "大戶增・法人賣", inst: "法人買・大戶減", out: "雙流出" };

// 固定兩位小數：既有 fmt() 會去掉尾端 0（+0.30 變 +0.3），並排的數字會對不齊。四捨五入後為 0 寫 0.00，不寫 -0.00。
const flowSigned2 = (v) => {
  if (v == null) return "—";
  const r = Math.round(v * 100) / 100;
  return (r > 0 ? "+" : "") + (r === 0 ? "0.00" : r.toFixed(2));
};
const flowPct = (v) => (v == null ? "—" : flowSigned2(v) + "%");

// 百分位：排序後線性內插（同 numpy 預設）
function flowQuantile(values, p) {
  const b = values.slice().sort((a, c) => a - c);
  if (!b.length) return null;
  const i = (b.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i);
  return b[lo] + (b[hi] - b[lo]) * (i - lo);
}
function flowPadded(lo, hi, pad) {
  if (hi - lo < FLOW_MIN_SPAN) { const c = (lo + hi) / 2; lo = c - FLOW_MIN_SPAN / 2; hi = c + FLOW_MIN_SPAN / 2; }
  const w = hi - lo;
  return [lo - w * pad, hi + w * pad];
}
// 核心放大鏡：兩軸同比例修剪兩端，取框住至少 80% 類股的最小範圍（一定含原點，象限線才看得到）
function flowCoreWindow(rows) {
  const xs = rows.map((r) => r.s.x), ys = rows.map((r) => r.s.y), n = rows.length;
  const need = Math.ceil(FLOW_CORE_SHARE * n - 1e-9);
  let t = FLOW_CORE_TRIM0;
  for (;;) {
    const x = flowPadded(Math.min(flowQuantile(xs, t), 0), Math.max(flowQuantile(xs, 1 - t), 0), FLOW_CORE_PAD);
    const y = flowPadded(Math.min(flowQuantile(ys, t), 0), Math.max(flowQuantile(ys, 1 - t), 0), FLOW_CORE_PAD);
    const inside = rows.filter((r) => r.s.x >= x[0] && r.s.x <= x[1] && r.s.y >= y[0] && r.s.y <= y[1]).length;
    if (inside >= need || t <= 0) return { x, y, trim: t, inside, n };
    t = Math.max(0, Math.round((t - 0.01) * 100) / 100);
  }
}
// 主圖：本期＋上期＋原點的完整範圍（線性、不對稱；舊版對稱 ±最大值，Y 軸空了約兩成）
function flowMainRange(rows, axis) {
  const vals = [0];
  rows.forEach((r) => { vals.push(r.s[axis]); if (r.prev) vals.push(r.s[axis + "_prev"]); });
  return flowPadded(Math.min(...vals), Math.max(...vals), FLOW_MAIN_PAD);
}

function flowModel(d) {
  const base = (d.sectors || []).filter((s) => s.x != null && s.y != null);
  if (!base.length) return null;
  const rows = base.map((s) => ({
    s, sector: s.sector, q: flowQuadrant(s),
    prev: !!d.has_tail && s.x_prev != null && s.y_prev != null,
  }));
  const core = flowCoreWindow(rows);
  // 正規化刻度＝放大鏡的寬度。不用主圖全範圍：主圖 Y 軸會被單一離群值（2026-09-23 是汽車上期的 −1.267）
  // 撐開，用它正規化，Y 的變化會被壓到約三分之一、變成 X 主導。
  const wx = core.x[1] - core.x[0], wy = core.y[1] - core.y[0];
  rows.forEach((r) => {
    r.nx = r.s.x / wx; r.ny = r.s.y / wy;
    r.comp = r.nx + r.ny;
    r.dist = Math.hypot(r.nx, r.ny);
    r.inCore = r.s.x >= core.x[0] && r.s.x <= core.x[1] && r.s.y >= core.y[0] && r.s.y <= core.y[1];
    if (r.prev) {
      r.dnx = (r.s.x - r.s.x_prev) / wx; r.dny = (r.s.y - r.s.y_prev) / wy;
      r.move = r.dnx + r.dny; r.mag = Math.hypot(r.dnx, r.dny);
      r.qPrev = flowQuadrant({ x: r.s.x_prev, y: r.s.y_prev });
    }
  });
  const top = (arr, key) => arr.slice().sort((a, b) => key(b) - key(a));
  const moved = rows.filter((r) => r.prev);
  // 加速轉強／風險外流多一道「本期站到對的一邊」：從很差變成沒那麼差，不叫轉強（使用者 2026-09-23 選擇）。
  // move 也必須同號：全部惡化的日子，不能把惡化最少的叫加速轉強。
  const cards = {
    strongest: top(rows.filter((r) => r.q === "in"), (r) => r.comp)[0] || null,
    accel: top(moved.filter((r) => r.comp > 0 && r.move > 0), (r) => r.move)[0] || null,
    outflow: top(moved.filter((r) => r.comp < 0 && r.move < 0), (r) => -r.move)[0] || null,
  };
  const labels = [];
  const add = (r) => { if (r && labels.length < FLOW_LABELS_BASE && !labels.includes(r.sector)) labels.push(r.sector); };
  [cards.strongest, cards.accel, cards.outflow].forEach(add);
  top(moved, (r) => r.mag).forEach(add);
  top(rows, (r) => r.dist).forEach(add);     // 沒有上期時，以離原點最遠（正規化）的補滿
  const leaders = {};
  ["in", "big", "inst", "out"].forEach((q) => {
    const g = rows.filter((r) => r.q === q);
    leaders[q] = { count: g.length, top: top(g, (r) => r.dist).slice(0, 3) };
  });
  return {
    rows, bySector: Object.fromEntries(rows.map((r) => [r.sector, r])),
    core, wx, wy, main: { x: flowMainRange(rows, "x"), y: flowMainRange(rows, "y") },
    cards, labels, leaders, hasPrevAny: moved.length > 0,
  };
}

// 上期→本期的方向：由正規化位移 (dnx, dny) 的角度取八方位
function flowArrow(r) {
  if (!r || !r.prev || (r.dnx === 0 && r.dny === 0)) return "";
  const k = Math.round(Math.atan2(r.dny, r.dnx) / (Math.PI / 4));
  return ["→", "↗", "↑", "↖", "←", "↙", "↓", "↘"][((k % 8) + 8) % 8];
}
// 摘要卡的一句判讀（規則式）
function flowCardNote(kind, r) {
  if (!r) return "";
  if (kind === "strongest") {
    if (!r.prev) return "法人與大戶同步流入，兩軸合計最強";
    return r.qPrev === "in" ? "連續兩期雙流入，兩軸合計最強" : `由${FLOW_Q_NAME[r.qPrev]}轉為雙流入，兩軸合計最強`;
  }
  const useY = kind === "accel" ? r.dny >= r.dnx : r.dny <= r.dnx;   // 主因＝正規化後變動較大的那一軸
  const k = useY ? "y" : "x";
  return `${useY ? "大戶" : "法人"}${kind === "accel" ? "改善" : "惡化"}最多：`
    + `${flowPct(r.s[k + "_prev"])} → ${flowPct(r.s[k])}`;
}
// 頁首的一句白話判讀
function flowHeadline(m) {
  if (!m) return "";
  const { strongest, accel, outflow } = m.cards, parts = [];
  if (strongest) parts.push(`${strongest.sector}雙流入最強`);
  if (accel) parts.push(`${accel.sector}轉強最快`);
  if (outflow) parts.push(`${outflow.sector}外流最急`);
  return parts.length ? parts.join("、") + "。" : "今天沒有明顯的雙流入或輪動訊號。";
}
```

- [ ] **Step 4: 靜態檢查並重跑斷言**

Run: `node --check web/app.js`
Expected: 無輸出。

瀏覽器：重新整理預覽頁（`javascript_tool` 執行 `location.reload()`），重做 Step 2 的第 2、3 步。
Expected: `{"failed":[],"passed":28,"total":28}`。

- [ ] **Step 5: 反證一條（做完還原）**

暫時把 `accel:` 那行的 `r.comp > 0 && ` 刪掉，重新整理、注入、跑腳本 → `guardExcludesStillNegative` 應出現在 `failed`。改回，再跑一次全綠。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv/Scripts/python -c "b=open('web/app.js','rb').read(); print('loneLF', b.count(b'\n')-b.count(b'\r\n'))"
git add web/app.js
git commit -m "feat(rotation): flowModel 純函式——核心放大鏡範圍、正規化刻度、摘要三卡規則、常駐標籤、象限 Top3" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 頁面骨架、頁首 chips＋指標說明、今日輪動摘要卡、版面 CSS

**Files:**
- Modify: `web/index.html`（`#view-rotation` 開頭到 `rotation-flow` 結束，約第 308–320 行）
- Modify: `web/styles.css`（`:root` 加 token；取代第 899–924 行的族群輪動區塊）
- Modify: `web/app.js`（`let flowChart ...` 宣告、`loadSectorFlow`、`toggleRotationFilter`、新函式、事件委派）

**Interfaces:**
- Consumes（Task 1）：`flowModel`、`flowHeadline`、`flowArrow`、`flowCardNote`、`flowPct`、`FLOW_Q_NAME`、`fmt`、`esc`。
- Produces：全域 `let lastFlowModel`；`renderFlowHeader(d, m)`、`renderFlowSummary(d, m)`、`positionFlowHelp()`；DOM id `flow-headline`、`flow-chips`、`flow-help`、`flow-help-body`、`flow-summary`、`flow-zoom`、`flow-zoom-title`、`flow-detail`、class `flow-zoom-wrap`、`flow-side`、`flow-main`。CSS 變數 `--flow-in/big/inst/out` 與 `.flow-q-in/big/inst/out { --q }`。

- [ ] **Step 1: 替換 HTML 骨架**

`web/index.html` 中，把從 `<section id="view-rotation" class="view picks-view">` 下一行起、到 `rotation-flow` 那個 `</div>` 為止的這段：

```html
        <div class="picks-command">
          <div class="picks-command-copy">
            <div class="picks-title-row"><h2>族群輪動</h2></div>
            <p>X＝法人近 5 日淨買賣、Y＝大戶 400張↑ 週增，皆除以類股市值。右上是錢正流入的族群、左下是正流出的；尾巴指向本期，顯示輪動方向。點泡泡或右側排行可篩下方交叉選股。</p>
          </div>
          <div class="picks-command-foot">
            <span id="rotation-note" class="picks-feedback" role="status" aria-live="polite"></span>
          </div>
        </div>
        <div id="rotation-flow" class="rotation-flow">
          <div id="flow-chart" class="chart flow-chart" role="img" aria-label="族群資金流向四象限：X 軸法人、Y 軸大戶"></div>
          <div id="flow-quadrants" class="flow-quadrants" aria-label="四區排行（鍵盤可操作）"></div>
        </div>
```

換成：

```html
        <div class="picks-command flow-command">
          <div class="picks-command-copy">
            <div class="picks-title-row"><h2>族群輪動</h2></div>
            <p id="flow-headline" class="flow-headline">載入中…</p>
          </div>
          <div id="flow-chips" class="flow-chips" role="status" aria-live="polite"></div>
          <div id="flow-help" class="flow-help" popover><div id="flow-help-body"></div></div>
        </div>
        <div class="flow-summary-head"><h3>今日輪動摘要</h3><span class="flow-summary-note">規則式・非投資建議</span></div>
        <div id="flow-summary" class="flow-summary"></div>
        <div id="rotation-flow" class="rotation-flow">
          <div class="flow-main">
            <div class="flow-chart-head"><span>全範圍</span><span class="flow-legend" aria-hidden="true">○ 上期 → ● 本期</span></div>
            <div id="flow-chart" class="chart flow-chart" role="img" aria-label="族群資金流向四象限（全範圍）：X 軸法人、Y 軸大戶"></div>
          </div>
          <div class="flow-side">
            <div class="flow-zoom-wrap">
              <div class="flow-chart-head"><span id="flow-zoom-title">核心放大鏡</span><span class="flow-legend" aria-hidden="true">○ 上期 → ● 本期</span></div>
              <div id="flow-zoom" class="chart flow-zoom" role="img" aria-label="核心放大鏡"></div>
            </div>
            <div id="flow-quadrants" class="flow-quadrants" aria-label="象限領先者（鍵盤可操作）"></div>
          </div>
          <div id="flow-detail" class="flow-detail" tabindex="-1"></div>
        </div>
```

- [ ] **Step 2: CSS——token 與族群輪動區塊**

(a) `web/styles.css` 的 `:root` 內，`--danger: #ff7a66;` 那一行之後加：

```css
  /* 族群輪動的四個象限（ui72）：同一冷色系、明度由雙流入到雙流出遞減；四色在 --bg-card 上都 ≥ 5.3:1。
     資金流向不是漲跌，不可用紅綠。量測見 docs/superpowers/specs/2026-09-23-sector-rotation-redesign-design.md §11。 */
  --flow-in: #5fd6ee;    /* cyan：雙流入 */
  --flow-big: #70b8ff;   /* 冷藍：大戶增・法人賣（與 --info 同值） */
  --flow-inst: #8b93f8;  /* indigo：法人買・大戶減 */
  --flow-out: #8494ad;   /* slate：雙流出 */
```

(b) 把從 `/* 族群輪動：法人 × 大戶 四象限。資金流向不是漲跌，一律 --info 藍，不碰紅綠。 */` 起、到 `@media (max-width: 600px) { .flow-chart { height: 360px; } .flow-quadrants { grid-template-columns: 1fr; } }` 為止的整段，換成下面這段（`.flow-bars*` 與 `.flow-filter*` 規則原樣保留在裡面）：

```css
/* ===== 族群輪動（ui72）：摘要卡＋全範圍主圖＋核心放大鏡＋象限領先者＋細節列 =====
   資金流向不是漲跌：象限一律用冷色系 token（--flow-*），紅綠只留給行情。 */
.flow-q-in { --q: var(--flow-in); }
.flow-q-big { --q: var(--flow-big); }
.flow-q-inst { --q: var(--flow-inst); }
.flow-q-out { --q: var(--flow-out); }
.flow-command { align-items: center; }
.flow-command .flow-headline { color: var(--text-secondary); font-size: var(--fs-sm); }
.flow-chips { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 6px; max-width: 640px; }
.flow-chip { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 9px; border: 1px solid var(--border-subtle);
  border-radius: 999px; background: var(--bg-sunken); color: var(--muted); font-size: var(--fs-xs); white-space: nowrap; }
.flow-help-btn { min-height: 28px; color: var(--info); font: inherit; font-size: var(--fs-xs); cursor: pointer; }
.flow-help-btn:hover { border-color: var(--info); }
.flow-help[popover] { position: fixed; inset: auto; margin: 0; width: min(440px, calc(100vw - 32px)); max-height: min(70vh, 560px);
  overflow: auto; padding: 12px 14px; color: var(--text); background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--r-lg); box-shadow: var(--shadow-panel); }
.flow-help h4 { margin: 0 0 8px; color: var(--text-primary); font-size: var(--fs-sm); }
.flow-help dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 5px 12px; margin: 0; font-size: var(--fs-xs); line-height: 1.5; }
.flow-help dt { color: var(--label); font-weight: 700; white-space: nowrap; }
.flow-help dd { margin: 0; color: var(--text-secondary); }
.flow-help p { margin: 10px 0 0; }
.flow-summary-head { display: flex; align-items: baseline; gap: 10px; margin: 2px 0 -2px; }
.flow-summary-head h3 { margin: 0; color: var(--text-primary); font-size: var(--fs-sm); }
.flow-summary-note { color: var(--muted); font-size: var(--fs-xs); }
.flow-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.flow-summary-empty { grid-column: 1 / -1; padding: 10px 12px; color: var(--muted); font-size: var(--fs-xs);
  border: 1px dashed var(--border); border-radius: var(--r); }
.flow-card { display: flex; flex-direction: column; gap: 3px; min-width: 0; min-height: 28px; padding: 8px 12px;
  color: var(--text); font: inherit; text-align: left; cursor: pointer; background: var(--card);
  border: 1px solid var(--border); border-left: 3px solid var(--q, var(--border)); border-radius: var(--r); }
.flow-card:hover:not(:disabled) { background: var(--card-hover); }
.flow-card[aria-pressed="true"] { border-color: var(--info); border-left-color: var(--q, var(--info)); background: rgba(112, 184, 255, 0.10); }
.flow-card:disabled { cursor: default; }
.flow-card-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.flow-card-title { color: var(--label); font-size: var(--fs-xs); font-weight: 700; }
.flow-qtag { display: inline-block; padding: 0 7px; border: 1px solid var(--q, var(--border)); border-radius: 999px;
  color: var(--q, var(--muted)); font-size: var(--fs-xs); line-height: 18px; white-space: nowrap; }
.flow-card-name { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.flow-card-name b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs-md); }
.flow-card-arrow { color: var(--q, var(--muted)); font-size: var(--fs-md); line-height: 1; }
.flow-card-vals { display: flex; flex-wrap: wrap; gap: 2px 12px; color: var(--text-secondary); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
.flow-card-vals > span { white-space: nowrap; }
.flow-card-trans { color: var(--muted); }
.flow-card-note, .flow-card-why { color: var(--muted); font-size: var(--fs-xs); line-height: 1.4; }
.rotation-flow { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  grid-template-areas: "main side" "detail detail"; gap: 12px 16px; align-items: start; }
.flow-main { grid-area: main; min-width: 0; }
.flow-side { grid-area: side; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.flow-zoom-wrap { min-width: 0; }
.flow-detail { grid-area: detail; }
.flow-chart-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin: 0 0 4px;
  color: var(--label); font-size: var(--fs-xs); }
.flow-legend { color: var(--muted); white-space: nowrap; }
/* .chart（後面定義、height:340px）同權重會蓋掉這裡——ui69 的 .flow-chart 高度就是這樣變成死規則的，
   所以用 #view-rotation 提高權重。 */
#view-rotation .flow-chart { height: 580px; }
#view-rotation .flow-zoom { height: 250px; }
.flow-quadrants { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
.flow-q { min-width: 0; padding: 8px 10px; background: var(--card); border: 1px solid var(--border);
  border-left: 3px solid var(--q, var(--border)); border-radius: var(--r); }
.flow-q h4 { display: flex; justify-content: space-between; gap: 6px; margin: 0 0 4px; color: var(--q, var(--label));
  font-size: var(--fs-xs); font-weight: 700; }
.flow-q-count { color: var(--muted); font-weight: 400; white-space: nowrap; }
/* 兩行一列（名稱／法人・大戶）。字級 13px、行高 1.35：右欄（放大鏡 250＋Top3 兩列）才會與 580px 主圖大致齊高 */
.flow-row { display: flex; flex-direction: column; align-items: stretch; gap: 1px; width: 100%; min-height: 28px; padding: 3px 6px;
  color: var(--text); font: inherit; font-size: var(--fs-table); line-height: 1.35; text-align: left; cursor: pointer;
  background: transparent; border: 1px solid transparent; border-radius: var(--r); }
.flow-row:hover { background: var(--card-hover); }
.flow-row[aria-pressed="true"] { border-color: var(--info); background: rgba(112, 184, 255, 0.10); }
.flow-row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; }
.flow-row-val { display: flex; flex-wrap: wrap; gap: 0 10px; color: var(--text-secondary); font-size: var(--fs-xs); font-variant-numeric: tabular-nums; }
.flow-row-val > span { white-space: nowrap; }
.flow-detail { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; min-height: 40px; padding: 8px 12px;
  color: var(--text-secondary); font-size: var(--fs-xs); font-variant-numeric: tabular-nums;
  background: var(--card); border: 1px solid var(--border-subtle); border-radius: var(--r); }
.flow-detail:focus { outline: none; }
.flow-detail-name { color: var(--text-primary); font-size: var(--fs-sm); font-weight: 700; }
.flow-detail-top { color: var(--muted); }
.flow-detail-clear { margin-left: auto; min-height: 28px; }
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
@media (max-width: 1180px) {
  .flow-chips { justify-content: flex-start; max-width: none; }
  .rotation-flow { grid-template-columns: minmax(0, 1fr); grid-template-areas: "main" "side" "detail"; }
  .flow-side { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
  #view-rotation .flow-chart { height: 460px; }
  #view-rotation .flow-zoom { height: 300px; }
}
@media (max-width: 600px) {
  .flow-summary { grid-template-columns: minmax(0, 1fr); }
  .rotation-flow { grid-template-areas: "main" "zoom" "detail" "quads"; }
  .flow-side { display: contents; }
  .flow-zoom-wrap { grid-area: zoom; }
  .flow-quadrants { grid-area: quads; }
  #view-rotation .flow-chart { height: 340px; }
  #view-rotation .flow-zoom { height: 300px; }
}
```

- [ ] **Step 3: JS——狀態、頁首、摘要卡、interim 的 load 與 toggle**

(a) 把 `let flowChart = null, lastSectorFlow = null, rotationSectorFilter = null, crossNoteBase = "";` 改成：

```js
let flowChart = null, lastSectorFlow = null, lastFlowModel = null, rotationSectorFilter = null, crossNoteBase = "";
```

(b) 在 Task 1 插入的 `flowHeadline` 函式之後加：

```js
// ---------- 頁首：一句判讀＋compact chips＋指標說明（ui72）----------
// chips 取代原本那段長括號說明；日期／缺口的措辭沿用 ui69 的判斷（上期不連續、集保不相鄰）。
function flowChipsHtml(d) {
  const md = (s) => (s || "").slice(5);
  const dayGap = (a, b) => (Date.parse(b) - Date.parse(a)) / 86400000;       // b − a，日曆天
  const fd = d.flow_dates || [], pd = d.flow_prev_dates || [], chips = [];
  if (fd.length) chips.push(`法人 ${md(fd[0])}～${md(fd[fd.length - 1])}・${fd.length} 日`);
  if (d.has_tail && pd.length) {
    const span = dayGap(pd[0], pd[pd.length - 1]);
    chips.push(`上期 ${md(pd[0])}～${md(pd[pd.length - 1])}` + (span > fd.length * 2 + 4 ? "（不連續）" : ""));
  } else if (!d.has_tail) {
    chips.push(`法人不足 ${fd.length * 2} 日・無上期`);
  }
  if (d.has_custody) chips.push(`集保 ${md(d.custody_weeks[1])}→${md(d.custody_weeks[0])}`);
  else chips.push("集保不足兩週・單軸");
  const pw = d.custody_prev_weeks || [];
  if (pw.length) chips.push(`上期集保 ${md(pw[1])}→${md(pw[0])}`);
  else if (d.custody_prev_skipped === "weeks_not_adjacent" && d.custody_prev_gap) {
    const g = d.custody_prev_gap, cw = d.custody_weeks || [];   // g＝[較新, 較舊]
    const wk = Math.round(dayGap(g[1], g[0]) / 7);
    chips.push(g[0] === cw[0] && g[1] === cw[1] ? `本期集保跨 ${wk} 週・無向量` : `上期集保跨 ${wk} 週・無向量`);
  }
  chips.push(`${(d.sectors || []).length} 類股`);
  const ex = d.excluded || {};
  if (ex.no_price) chips.push(`${ex.no_price} 檔缺收盤`);
  if (ex.sectors_no_mcap) chips.push(`${ex.sectors_no_mcap} 類算不出市值`);
  return chips.map((c) => `<span class="flow-chip">${esc(c)}</span>`).join("")
    + '<button type="button" class="flow-chip flow-help-btn" popovertarget="flow-help">ⓘ 指標說明</button>';
}
function flowHelpHtml(d, m) {
  const n = (d.flow_dates || []).length;
  const core = m ? `今天每軸兩端各修 ${Math.round(m.core.trim * 100)}%，框住 ${m.core.inside}/${m.core.n} 類。` : "";
  const scale = m ? `今天 X 寬 ${fmt(m.wx, 3)}、Y 寬 ${fmt(m.wy, 3)}。` : "";
  return `<h4>指標說明</h4><dl>`
    + `<dt>X 法人</dt><dd>法人近 ${n} 日淨買賣 ÷ 類股市值（%）。</dd>`
    + `<dt>Y 大戶</dt><dd>400 張以上大戶持股週增 ÷ 類股市值（%）。</dd>`
    + `<dt>上期</dt><dd>前一個 ${n} 日窗口與前一個集保週；向量由上期（○）指向本期（●）。</dd>`
    + `<dt>核心放大鏡</dt><dd>兩軸同比例修剪兩端，取框住至少 80% 類股的最小範圍，含原點、外加 12% 邊界，座標皆為線性。${core}框外的類股只在全範圍圖出現。</dd>`
    + `<dt>正規化</dt><dd>以放大鏡的 X 寬、Y 寬當刻度，避免某一軸因離群值主導。${scale}</dd>`
    + `<dt>最強共振</dt><dd>雙流入類股中「X÷X寬＋Y÷Y寬」最大。</dd>`
    + `<dt>加速轉強</dt><dd>相較上期「ΔX÷X寬＋ΔY÷Y寬」最大，且本期綜合值已轉正。</dd>`
    + `<dt>風險外流</dt><dd>同一式最小，且本期綜合值為負。</dd>`
    + `</dl><p class="muted small">規則式摘要，非投資建議。</p>`;
}
function renderFlowHeader(d, m) {
  const head = $("flow-headline"), chips = $("flow-chips"), help = $("flow-help-body");
  if (head) head.textContent = m ? flowHeadline(m)
    : (d.has_custody ? "暫無雙軸資料，以法人單軸顯示。" : "集保不足兩個完整週，暫以法人單軸顯示。");
  if (chips) chips.innerHTML = flowChipsHtml(d);
  if (help) help.innerHTML = flowHelpHtml(d, m);
}
// 原生 popover 預設置中；開啟前後各定位一次（開啟前量不到自己的高度，開啟後再修正）
function positionFlowHelp() {
  const pop = $("flow-help"), btn = document.querySelector('#flow-chips [popovertarget="flow-help"]');
  if (!pop || !btn) return;
  const r = btn.getBoundingClientRect();
  const w = Math.min(440, window.innerWidth - 32), h = pop.offsetHeight || 0;
  pop.style.left = Math.max(16, Math.min(r.right - w, window.innerWidth - 16 - w)) + "px";
  const below = r.bottom + 6;
  pop.style.top = (h && below + h > window.innerHeight - 16 ? Math.max(16, r.top - 6 - h) : below) + "px";
}

// ---------- 今日輪動摘要（規則式，非投資建議；ui72）----------
const FLOW_CARDS = [
  ["strongest", "最強共振", "今天沒有雙流入的類股"],
  ["accel", "加速轉強", "沒有類股本期轉正"],
  ["outflow", "風險外流", "沒有類股本期轉負"],
];
function flowNoPrevReason(d) {
  if (!d.has_tail) return `法人資料不足 ${(d.flow_dates || []).length * 2} 日，尚無上一期`;
  return "上期集保不相鄰，無法比較";
}
function renderFlowSummary(d, m) {
  const el = $("flow-summary"); if (!el) return;
  if (!m) { el.innerHTML = '<div class="flow-summary-empty">集保不足兩個完整週，暫無雙軸摘要</div>'; return; }
  el.innerHTML = FLOW_CARDS.map(([k, title, empty]) => {
    const r = m.cards[k];
    if (!r) {
      const why = k !== "strongest" && !m.hasPrevAny ? flowNoPrevReason(d) : empty;
      return `<button type="button" class="flow-card" disabled aria-pressed="false">`
        + `<span class="flow-card-title">${title}</span><span class="flow-card-why">${esc(why)}</span></button>`;
    }
    const s = r.s, trans = r.prev ? `${FLOW_Q_NAME[r.qPrev]} → ${FLOW_Q_NAME[r.q]}` : FLOW_Q_NAME[r.q];
    return `<button type="button" class="flow-card flow-q-${r.q}" data-sector="${esc(r.sector)}" aria-pressed="${rotationSectorFilter === r.sector}">`
      + `<span class="flow-card-top"><span class="flow-card-title">${title}</span><span class="flow-qtag">${FLOW_Q_NAME[r.q]}</span></span>`
      + `<span class="flow-card-name"><b title="${esc(r.sector)}">${esc(r.sector)}</b><span class="flow-card-arrow" aria-hidden="true">${flowArrow(r)}</span></span>`
      + `<span class="flow-card-vals"><span>法人 ${flowPct(s.x)}</span><span>大戶 ${flowPct(s.y)}</span>`
      + `<span class="flow-card-trans">${esc(trans)}</span></span>`
      + `<span class="flow-card-note">${esc(flowCardNote(k, r))}</span></button>`;
  }).join("");
}
```

(c) 把整支 `async function loadSectorFlow() { ... }` 換成（過渡版：圖與排行仍用舊函式，Task 3／4 會再換）：

```js
async function loadSectorFlow() {
  const el = $("flow-chart");
  if (!el) return;
  const clearFlowBits = () => {
    ["flow-quadrants", "flow-summary", "flow-chips", "flow-help-body"].forEach((id) => { const n = $(id); if (n) n.innerHTML = ""; });
    const h = $("flow-headline"); if (h) h.textContent = "";
  };
  try {
    const d = await getJSON("/api/sectors/flow");
    lastSectorFlow = d;
    if (!(d.sectors || []).length) {
      disposeFlowChart(); lastFlowModel = null; clearFlowBits();
      el.innerHTML = '<div class="muted small" style="padding:12px">尚無法人資料（stock_flow_daily 尚未累積）</div>';
      return;
    }
    lastFlowModel = d.has_custody ? flowModel(d) : null;
    renderFlowHeader(d, lastFlowModel);
    renderFlowSummary(d, lastFlowModel);
    if (d.has_custody) renderSectorFlow(d); else renderFlowBars(d);
    renderFlowQuadrants(d);
  } catch (e) {
    // 失敗也要清狀態：留著上一次的摘要、排行與 lastSectorFlow，按篩選會拿舊資料重畫
    disposeFlowChart(); clearFlowBits();
    el.innerHTML = '<div class="muted small" style="padding:12px">族群輪動載入失敗</div>';
    lastSectorFlow = null; lastFlowModel = null;
  }
}
```

(d) 把整支 `function toggleRotationFilter(sector) { ... }` 換成（過渡版）：

```js
// drill-down：篩下方交叉選股（不重打 API，只切 .hidden）；再點同一個取消。狀態不持久化。
function toggleRotationFilter(sector) {
  rotationSectorFilter = (sector && rotationSectorFilter !== sector) ? sector : null;
  applyRotationFilter();
  if (!lastSectorFlow) return;
  // 重繪會換掉整批按鈕 → 焦點掉回 body，只用鍵盤的人會失去位置（同 ss-picked-note 那段的作法）
  const cur = document.activeElement && document.activeElement.closest
    ? document.activeElement.closest(".flow-row, .flow-card[data-sector]") : null;
  const keep = cur ? `${cur.classList.contains("flow-card") ? ".flow-card" : ".flow-row"}[data-sector="${CSS.escape(cur.dataset.sector)}"]` : null;
  renderFlowSummary(lastSectorFlow, lastFlowModel);
  renderFlowQuadrants(lastSectorFlow);
  if (keep) { const again = $("view-rotation").querySelector(keep); if (again) again.focus(); }
}
```

(e) 在既有的 `if (flowQEl) flowQEl.addEventListener("click", ...)` 那一行之後加：

```js
const flowSummaryEl = $("flow-summary");
if (flowSummaryEl) flowSummaryEl.addEventListener("click", (e) => {
  const b = e.target.closest(".flow-card[data-sector]");
  if (b && !b.disabled) toggleRotationFilter(b.dataset.sector);
});
const flowHelpEl = $("flow-help");
if (flowHelpEl) {
  flowHelpEl.addEventListener("beforetoggle", (e) => { if (e.newState === "open") positionFlowHelp(); });
  flowHelpEl.addEventListener("toggle", (e) => { if (e.newState === "open") positionFlowHelp(); });
  // position:fixed 的面板不會跟著 .content 捲動；捲動時收起，免得和按鈕錯位
  const flowScroller = document.querySelector(".content");
  if (flowScroller) flowScroller.addEventListener("scroll", () => {
    if (flowHelpEl.matches(":popover-open")) flowHelpEl.hidePopover();
  }, { passive: true });
}
```

- [ ] **Step 4: 靜態檢查**

Run: `node --check web/app.js && grep -n "rotation-note" web/app.js web/index.html`
Expected: `node --check` 無輸出；grep 無輸出（舊的 `#rotation-note` 已完全移除）。

- [ ] **Step 5: 瀏覽器驗證（注入真實快照）**

1. `preview_stop` 後 `preview_start {name:"spr"}`（讓新的 HTML／CSS／JS 生效）。
2. 用 Read 讀 fixture，`javascript_tool`：`window.__FX = <整份 JSON>; "ok"`。
3. `javascript_tool`：

```js
if (!window.__origGetJSON) window.__origGetJSON = getJSON;
getJSON = async (u) => (String(u).startsWith("/api/sectors/flow") ? structuredClone(window.__FX) : window.__origGetJSON(u));
showView("rotation"); await loadSectorFlow(); "injected"
```

4. `javascript_tool` 量測並回報：

```js
const cards = [...document.querySelectorAll("#flow-summary .flow-card")];
const chips = [...document.querySelectorAll("#flow-chips .flow-chip")].map((c) => c.textContent.trim());
JSON.stringify({
  headline: $("flow-headline").textContent,
  chips,
  cards: cards.map((c) => ({ sector: c.dataset.sector, pressed: c.getAttribute("aria-pressed"), text: c.textContent.replace(/\s+/g, " ").trim() })),
  cols: getComputedStyle($("flow-summary")).gridTemplateColumns.split(" ").length,
  pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
})
```

Expected：
- `headline` ＝「航運雙流入最強、汽車轉強最快、光電外流最急。」
- `chips` ＝ `["法人 09-17～09-23・5 日","上期 09-10～09-16","集保 09-11→09-18","上期集保 09-04→09-11","34 類股","36 檔缺收盤","ⓘ 指標說明"]`
- 三張卡 `sector` 依序 航運／汽車／光電，`pressed` 都是 `"false"`；航運卡文字含「法人 +0.30%」「大戶 +0.34%」「法人買・大戶減 → 雙流入」「由法人買・大戶減轉為雙流入，兩軸合計最強」；汽車卡含「大戶改善最多：-1.27% → 0.00%」；光電卡含「大戶惡化最多：+0.67% → -0.67%」。
- `cols` ＝ 3（預覽窗寬 ≥ 601px 時）；`pageOverflow` ＝ false。

5. 指標說明：`javascript_tool` 執行 `document.querySelector('#flow-chips .flow-help-btn').click(); const p=$("flow-help"); const r=p.getBoundingClientRect(); JSON.stringify({open:p.matches(":popover-open"), inView: r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight, hasCore: p.textContent.includes("框住 28/34 類")})`
Expected：`{"open":true,"inView":true,"hasCore":true}`。接著 `computer` 按 `Escape`，再查 `$("flow-help").matches(":popover-open")` 為 false。

6. 點卡：`computer` 截圖後點「汽車」那張卡 →
`JSON.stringify({f: rotationSectorFilter, pressed: document.querySelector('.flow-card[data-sector="汽車"]').getAttribute("aria-pressed"), focus: document.activeElement.dataset.sector, cross: [...document.querySelectorAll('#cross .cross-grp')].every((g) => g.classList.contains('hidden') === (g.dataset.sector !== '汽車'))})`
Expected：`{"f":"汽車","pressed":"true","focus":"汽車","cross":true}`。再點一次 → `rotationSectorFilter` 為 null、`aria-pressed` 回到 `"false"`。

7. 降級：`javascript_tool` 執行 `window.__FX2 = structuredClone(__FX); __FX2.has_tail = false; getJSON = async (u) => (String(u).startsWith("/api/sectors/flow") ? structuredClone(window.__FX2) : window.__origGetJSON(u)); await loadSectorFlow(); JSON.stringify([...document.querySelectorAll('#flow-summary .flow-card')].map((c)=>({dis:c.disabled, t:c.textContent.replace(/\s+/g,' ').trim()})))`
Expected：最強共振仍是航運且可點；加速轉強、風險外流 `dis: true` 且文字含「法人資料不足 10 日，尚無上一期」。
再把 `__FX2 = structuredClone(__FX); __FX2.has_custody = false; __FX2.sectors.forEach((s)=>{ s.y=null; s.y_prev=null; });` 重跑 → `#flow-summary` 只剩「集保不足兩個完整週，暫無雙軸摘要」、頁首判讀是「集保不足兩個完整週，暫以法人單軸顯示。」。最後把 `getJSON` 指回 `__FX` 並重新 `loadSectorFlow()`。

8. `read_console_messages {onlyErrors:true}` → 無錯誤。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv/Scripts/python -c "import sys; [print(p, open(p,'rb').read().count(b'\n')-open(p,'rb').read().count(b'\r\n')) for p in ('web/app.js','web/styles.css','web/index.html')]"
git add web/app.js web/styles.css web/index.html
git commit -m "feat(rotation): 頁首一句判讀＋chips＋指標說明 popover、今日輪動摘要三卡、兩欄骨架與象限色 token" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 主圖＋核心放大鏡、方向向量、標籤、選取淡化

**Files:**
- Modify: `web/app.js`（新增 `FLOW_COLOR`、`flowZoomChart`、`flowChartOption` 等；刪除舊 `renderSectorFlow`；改 `disposeFlowChart`、`renderFlowBars`、`loadSectorFlow`、`toggleRotationFilter`、`showView`、`window` resize 清單）

**Interfaces:**
- Consumes（Task 1／2）：`flowModel` 的 `rows/bySector/core/main/labels`、`flowPct`、`flowSigned2`、`FLOW_Q_NAME`、`lastFlowModel`、`financeTooltip`、`initChart`、`withAlpha`、`C`、`HM_FONT`、`chgClass`、`fmt`、`esc`。
- Produces：`let flowZoomChart`；`FLOW_COLOR`；`flowChartOption(d, m, "main"|"zoom")`；`renderFlowCharts(d, m)`；`flowHighlight(sector, on)`；`flowYi(amount) -> string`（億，≥10 取整數、否則一位小數）。series 順序固定為 `[bubbles, prev, vecHead, vecTail, vecSelHead]`，**bubbles 永遠是 seriesIndex 0**（`flowHighlight` 依賴）。

- [ ] **Step 1: 先寫斷言腳本**

存成 `.superpowers/sdd/fixtures/flow-charts-check.js`：

```js
(() => {
  const m = lastFlowModel, om = flowChart.getOption(), oz = flowZoomChart.getOption();
  const near = (a, b, tol = 1e-9) => Math.abs(a - b) <= tol;
  const shown = (o) => o.series[0].data.filter((p) => p.label && p.label.show).map((p) => p.name).sort().join(",");
  const hexes = [C.up, C.down, C.upFill, C.downFill].map((h) => h.toLowerCase());
  const json = (JSON.stringify(om) + JSON.stringify(oz)).toLowerCase();
  const checks = {
    mainRange: near(om.xAxis[0].min, m.main.x[0]) && near(om.xAxis[0].max, m.main.x[1])
      && near(om.yAxis[0].min, m.main.y[0]) && near(om.yAxis[0].max, m.main.y[1]),
    zoomRange: near(oz.xAxis[0].min, m.core.x[0]) && near(oz.xAxis[0].max, m.core.x[1]),
    bubbles: om.series[0].data.length === 34 && oz.series[0].data.length === 34,
    mainLabels: shown(om) === ["航運", "汽車", "光電", "資訊服務", "文化創意", "水泥", "化學"].sort().join(","),
    zoomLabels: shown(oz) === ["汽車", "資訊服務", "化學"].sort().join(","),
    vectors: om.series[2].data.length === 34 && om.series[3].data.length === 34 && om.series[4].data.length === 0
      && om.series[1].data.length === 34,
    coreBox: om.series[0].markArea.data.length === 5 && oz.series[0].markArea.data.length === 4,
    noRedGreen: hexes.every((h) => !json.includes(h)),
    zoomTitle: $("flow-zoom-title").textContent === "核心放大鏡・框住 28/34 類",
    heights: $("flow-chart").style.height === "" && $("flow-chart").clientHeight > 300,
  };
  toggleRotationFilter("光電");
  const s1 = flowChart.getOption();
  const idx = m.rows.findIndex((r) => r.sector === "光電");
  const other = m.rows.findIndex((r) => r.sector === "半導體");
  checks.selected = s1.series[0].data[idx].itemStyle.borderWidth === 2 && s1.series[4].data.length === 1
    && s1.series[2].data.length === 33 && s1.series[4].data[0].lineStyle.width === 2.5
    && s1.series[0].data[other].itemStyle.color.includes("0.12");
  // 半導體不在常駐標籤裡：選它之後標籤要出現（光電本來就常駐，拿它測等於恆真）
  toggleRotationFilter("半導體");
  checks.selectedLabel = flowChart.getOption().series[0].data[other].label.show === true;
  toggleRotationFilter("半導體");
  checks.cleared = rotationSectorFilter === null && flowChart.getOption().series[4].data.length === 0;
  const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
  return JSON.stringify({ failed, passed: Object.keys(checks).length - failed.length, total: Object.keys(checks).length });
})()
```

（`withAlpha` 回傳 `rgba(r, g, b, a)`，所以淡化的泡泡顏色字串一定含 `0.12`。）

- [ ] **Step 2: 確認斷言先失敗**

重新整理預覽、注入 fixture（同 Task 2 Step 5 的 2–3），執行上面腳本。
Expected：`TypeError`（`flowZoomChart` 為 null 或 `getOption` 未定義）。

- [ ] **Step 3: 實作**

(a) 在 `renderFlowSummary` 函式之後加：

```js
// ---------- 主圖（全範圍）＋核心放大鏡（ui72）----------
// 兩張圖同一個 option 產生器，只差座標範圍。象限色讀 CSS token，不在 JS 寫死。
const FLOW_COLOR = {
  in: CSS_VAR("--flow-in", "#5fd6ee"), big: CSS_VAR("--flow-big", "#70b8ff"),
  inst: CSS_VAR("--flow-inst", "#8b93f8"), out: CSS_VAR("--flow-out", "#8494ad"),
};
let flowZoomChart = null;
const flowYi = (amt) => { const v = (amt || 0) / 1e8; return fmt(v, Math.abs(v) >= 10 ? 0 : 1); };

function flowQuadrantAreas(rx, ry) {
  const a = (q, x0, y0, x1, y1, alpha) => [{ xAxis: x0, yAxis: y0, itemStyle: { color: withAlpha(FLOW_COLOR[q], alpha) } }, { xAxis: x1, yAxis: y1 }];
  return [a("in", 0, 0, rx[1], ry[1], 0.07), a("big", rx[0], 0, 0, ry[1], 0.05),
          a("inst", 0, ry[0], rx[1], 0, 0.05), a("out", rx[0], ry[0], 0, 0, 0.04)];
}
function flowCoreBox(core) {
  return [{ xAxis: core.x[0], yAxis: core.y[0],
            itemStyle: { color: "transparent", borderColor: C.muted, borderWidth: 1, borderType: "dashed" },
            label: { show: true, position: "insideTopLeft", formatter: "放大鏡", color: C.muted, fontSize: 10 } },
          { xAxis: core.x[1], yAxis: core.y[1] }];
}
function flowTooltip(d, m, p) {
  const r = p.data && p.data.sector ? m.bySector[p.data.sector] : null;
  if (!r) return "";
  const s = r.s, md = (x) => (x || "").slice(5), fd = d.flow_dates || [];
  // 當日漲跌是行情 → 這裡是整頁唯一用紅綠的地方
  const chg = s.chg_pct == null ? "—"
    : `<span class="${chgClass(s.chg_pct)}">${s.chg_pct > 0 ? "▲" : s.chg_pct < 0 ? "▼" : ""}${fmt(Math.abs(s.chg_pct), 2)}%</span>`;
  const pv = (k) => (r.prev ? `（上期 ${flowPct(s[k + "_prev"])}，Δ ${flowSigned2(s[k] - s[k + "_prev"])}）` : "");
  const top = (s.top3 || []).map((t) => `${esc(t.code)} ${esc(t.name)} ${flowYi(t.amount)} 億`).join("<br>");
  return `<b>${esc(s.sector)}</b>　${FLOW_Q_NAME[r.q]}　當日 ${chg}<br>`
    + `法人 ${fd.length} 日 ${flowPct(s.x)}${pv("x")}（${md(fd[0])}～${md(fd[fd.length - 1])}）<br>`
    + `大戶週增 ${flowPct(s.y)}${pv("y")}（樣本 ${s.n_cust}/${s.n} 檔）<br>`
    + `市值 ${fmt(s.mcap / 1e8, 0)} 億` + (top ? `<br><span class="muted">法人買最多：</span><br>${top}` : "");
}
function flowChartOption(d, m, which) {
  const zoom = which === "zoom";
  const rx = zoom ? m.core.x : m.main.x, ry = zoom ? m.core.y : m.main.y;
  const sel = rotationSectorFilter && m.bySector[rotationSectorFilter] ? rotationSectorFilter : null;
  const mcMax = Math.max(1, ...m.rows.map((r) => r.s.mcap || 0));
  const size = (mc) => Math.max(10, Math.min(56, 10 + 46 * Math.sqrt((mc || 0) / mcMax)));   // √市值
  const faded = (r) => !!sel && r.sector !== sel;
  const labelOn = new Set(m.labels); if (sel) labelOn.add(sel);
  const n = (d.flow_dates || []).length;
  const bubbles = {
    id: "bubbles", type: "scatter", z: 5, clip: true, animation: false,
    data: m.rows.map((r) => {
      const col = FLOW_COLOR[r.q], isSel = r.sector === sel;
      return {
        name: r.sector, value: [r.s.x, r.s.y], sector: r.sector, symbolSize: size(r.s.mcap),
        itemStyle: { color: withAlpha(col, faded(r) ? 0.12 : isSel ? 0.85 : 0.6),
                     borderColor: withAlpha(col, faded(r) ? 0.3 : 1), borderWidth: isSel ? 2 : 1 },
        // 常駐標籤最多 8 個（三卡＋位移最大者＋選取）；放大鏡只標框內的
        label: { show: labelOn.has(r.sector) && (!zoom || r.inCore) },
      };
    }),
    label: { show: false, position: "right", distance: 4, formatter: (p) => p.name, color: C.text, fontSize: 11,
             fontFamily: HM_FONT, backgroundColor: withAlpha(C.panel, 0.85), padding: [2, 5], borderRadius: 3 },
    labelLayout: { moveOverlap: "shiftY" },
    emphasis: { scale: 1.12, label: { show: true } },
    markLine: { silent: true, symbol: "none", animation: false, label: { show: false },
                lineStyle: { color: C.borderStrong, type: "solid", width: 1 }, data: [{ xAxis: 0 }, { yAxis: 0 }] },
    markArea: { silent: true, animation: false, data: flowQuadrantAreas(rx, ry).concat(zoom ? [] : [flowCoreBox(m.core)]) },
  };
  // 方向向量：上期（空心○）→ 本期（泡泡●）。拆成「上期→中點（畫箭頭）」與「中點→本期」兩段，箭頭不會被泡泡蓋住。
  const prevRows = m.rows.filter((r) => r.prev);
  const seg = (r, half) => {
    const a = [r.s.x_prev, r.s.y_prev], b = [r.s.x, r.s.y], mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const isSel = r.sector === sel;
    return { coords: half === "head" ? [a, mid] : [mid, b],
             lineStyle: { color: withAlpha(FLOW_COLOR[r.q], isSel ? 1 : faded(r) ? 0.12 : sel ? 0.7 : 0.45), width: isSel ? 2.5 : 1 } };
  };
  const lines = (id, data, arrowSize) => ({ id, type: "lines", coordinateSystem: "cartesian2d", polyline: false,
    z: 3, silent: true, clip: true, animation: false,
    symbol: arrowSize ? ["none", "arrow"] : "none", symbolSize: arrowSize || 0, data });
  const selRow = sel ? prevRows.find((r) => r.sector === sel) : null;
  const prevDots = { id: "prev", type: "scatter", z: 4, silent: true, clip: true, animation: false, symbolSize: 7,
    data: prevRows.map((r) => ({ value: [r.s.x_prev, r.s.y_prev],
      itemStyle: { color: "transparent", borderColor: withAlpha(FLOW_COLOR[r.q], faded(r) ? 0.25 : 0.95), borderWidth: 1.5 } })) };
  const g = zoom ? { left: 48, right: 12, top: 18, bottom: 34 } : { left: 62, right: 18, top: 22, bottom: 46 };
  const qText = (text, pos) => ({ type: "text", silent: true, ...pos, style: { text, fill: C.muted, fontSize: 11, fontFamily: HM_FONT } });
  return {
    animation: false,
    tooltip: financeTooltip({ trigger: "item", formatter: (p) => flowTooltip(d, m, p) }),
    grid: g,
    xAxis: { type: "value", min: rx[0], max: rx[1], splitNumber: zoom ? 4 : 6, splitLine: { show: false },
      name: zoom ? "法人（%）" : `法人近 ${n} 日淨買賣 ÷ 市值（%）`, nameLocation: "middle", nameGap: zoom ? 22 : 30,
      nameTextStyle: { color: C.label, fontSize: 11 }, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => fmt(v, 2) } },
    yAxis: { type: "value", min: ry[0], max: ry[1], splitNumber: zoom ? 4 : 6, splitLine: { show: false },
      name: zoom ? "大戶（%）" : "大戶 400張↑ 週增 ÷ 市值（%）", nameLocation: "middle", nameGap: zoom ? 34 : 46,
      nameTextStyle: { color: C.label, fontSize: 11 }, axisLabel: { color: C.muted, fontSize: 11, formatter: (v) => fmt(v, 2) } },
    graphic: [
      qText("大戶增・法人賣", { left: g.left + 6, top: g.top + 4 }),
      qText("雙流入", { right: g.right + 6, top: g.top + 4 }),
      qText("雙流出", { left: g.left + 6, bottom: g.bottom + 4 }),
      qText("法人買・大戶減", { right: g.right + 6, bottom: g.bottom + 4 }),
    ],
    series: [bubbles, prevDots,
      lines("vecHead", prevRows.filter((r) => r.sector !== sel).map((r) => seg(r, "head")), 6),
      lines("vecTail", prevRows.map((r) => seg(r, "tail")), 0),
      lines("vecSelHead", selRow ? [seg(selRow, "head")] : [], 10)],
  };
}
function bindFlowChart(ch) {
  ch.on("click", (p) => { if (p.data && p.data.sector) toggleRotationFilter(p.data.sector); });
}
function renderFlowCharts(d, m) {
  const mainEl = $("flow-chart"), zoomEl = $("flow-zoom"), wrap = document.querySelector(".flow-zoom-wrap");
  if (wrap) wrap.classList.remove("hidden");
  mainEl.style.height = "";        // 單軸降級會寫 inline auto；雙軸時把高度交還給 CSS 斷點
  if (!flowChart) { mainEl.innerHTML = ""; flowChart = initChart(mainEl); bindFlowChart(flowChart); }
  if (!flowZoomChart) { zoomEl.innerHTML = ""; flowZoomChart = initChart(zoomEl); bindFlowChart(flowZoomChart); }
  // 先有容器尺寸 → setOption → resize（echarts.init 會凍住它看到的尺寸）
  flowChart.setOption(flowChartOption(d, m, "main"), true);
  flowZoomChart.setOption(flowChartOption(d, m, "zoom"), true);
  flowChart.resize(); flowZoomChart.resize();
  const t = $("flow-zoom-title");
  if (t) t.textContent = `核心放大鏡・框住 ${m.core.inside}/${m.core.n} 類`;
  zoomEl.setAttribute("aria-label", `核心放大鏡：法人 ${fmt(m.core.x[0], 2)}～${fmt(m.core.x[1], 2)}%、`
    + `大戶 ${fmt(m.core.y[0], 2)}～${fmt(m.core.y[1], 2)}%，框住 ${m.core.inside}/${m.core.n} 類`);
}
// 鍵盤 focus 到卡或排行列時，讓兩張圖的對應泡泡高亮（標籤跟著出現）＋主圖顯示 tooltip。
// 這是鍵盤使用者唯一能「指到」canvas 泡泡的方式。bubbles 永遠是 seriesIndex 0。
function flowHighlight(sector, on) {
  if (!lastFlowModel) return;
  const idx = lastFlowModel.rows.findIndex((r) => r.sector === sector);
  if (idx < 0) return;
  [flowChart, flowZoomChart].forEach((ch) => {
    if (ch) ch.dispatchAction({ type: on ? "highlight" : "downplay", seriesIndex: 0, dataIndex: idx });
  });
  if (flowChart) flowChart.dispatchAction(on ? { type: "showTip", seriesIndex: 0, dataIndex: idx } : { type: "hideTip" });
}
```

(b) 刪除整支舊的 `function renderSectorFlow(d) { ... }`（從 `function renderSectorFlow(d) {` 到它的結尾 `}`，含其中的 `flowChart.on("click", ...)`）。

(c) `disposeFlowChart` 換成：

```js
function disposeFlowChart() {
  if (flowChart) { flowChart.dispose(); flowChart = null; }
  if (flowZoomChart) { flowZoomChart.dispose(); flowZoomChart = null; }
}
```

(d) `renderFlowBars` 函式開頭的 `disposeFlowChart();` 之後加一行：

```js
  const zw = document.querySelector(".flow-zoom-wrap"); if (zw) zw.classList.add("hidden");   // 單軸沒有放大鏡
```

(e) `loadSectorFlow` 裡的 `if (d.has_custody) renderSectorFlow(d); else renderFlowBars(d);` 換成：

```js
    if (lastFlowModel) renderFlowCharts(d, lastFlowModel); else renderFlowBars(d);
```

(f) `toggleRotationFilter` 裡的 `renderFlowSummary(lastSectorFlow, lastFlowModel);` 之後加：

```js
  if (lastFlowModel) renderFlowCharts(lastSectorFlow, lastFlowModel);
```

(g) `showView` 裡的 `if (name === "rotation") { if (flowChart) flowChart.resize(); loadSectorFlow(); loadCross(); }` 換成：

```js
  if (name === "rotation") { if (flowChart) flowChart.resize(); if (flowZoomChart) flowZoomChart.resize(); loadSectorFlow(); loadCross(); }
```

(h) `window.addEventListener("resize", ...)` 清單裡的 `instBreadthChart, instAlphaChart, flowChart]` 換成 `instBreadthChart, instAlphaChart, flowChart, flowZoomChart]`。

- [ ] **Step 4: 靜態檢查並重跑斷言**

Run: `node --check web/app.js && grep -n "renderSectorFlow" web/app.js`
Expected：`node --check` 無輸出；grep 無輸出。

瀏覽器：`preview_stop`→`preview_start`、注入 fixture、執行 Step 1 腳本。
Expected：`{"failed":[],"passed":13,"total":13}`。

- [ ] **Step 5: 互動與視覺驗證**

1. 泡泡點擊：`javascript_tool` 執行
   `const s = lastFlowModel.bySector["航運"].s; const [px, py] = flowChart.convertToPixel({ seriesIndex: 0 }, [s.x, s.y]); const r = $("flow-chart").getBoundingClientRect(); JSON.stringify({ x: r.left + px, y: r.top + py })`
   取得 viewport 座標，`computer` 先截圖確認座標框，再 `left_click` 該點 → `rotationSectorFilter === "航運"`。放大鏡同法點「汽車」（`flowZoomChart.convertToPixel`、`$("flow-zoom")`）→ `rotationSectorFilter === "汽車"`。最後 `toggleRotationFilter(null)`。
2. tooltip 不被裁切：對「文化創意」「光電」「通信網路」「航運」四個角落的類股逐一執行 `flowChart.dispatchAction({type:"showTip", seriesIndex:0, dataIndex: lastFlowModel.rows.findIndex(r=>r.sector==="文化創意")})`，再量 `$("flow-chart").querySelector('div[style*="z-index"]')`（ECharts tooltip DOM）的 `getBoundingClientRect()` 必須完全落在 `$("flow-chart").getBoundingClientRect()` 內。
3. resize：`resize_window` 1560×900 → 量兩張圖 `canvas` 寬度＝容器寬度（±2px）；再 `resize_window` 1024×800 → 同樣相等，且此時 `$("flow-chart").clientHeight` ≈ 460、`$("flow-zoom").clientHeight` ≈ 300。最後 `resize_window {preset:"desktop"}`。
4. 截圖一張 1560px 的主圖＋放大鏡，確認：主圖看得到 6 個離群類股（光電、航運、水泥、文化創意、通信網路、其他）與虛線放大鏡框；放大鏡內 28 類分得開；標籤有深色底框、沒有互相壓住。
5. 單軸降級：`__FX2 = structuredClone(__FX); __FX2.has_custody = false; __FX2.sectors.forEach((s)=>{s.y=null;s.y_prev=null;});` 注入並 `loadSectorFlow()` → `.flow-zoom-wrap` 有 `hidden`、`flowZoomChart === null`、`#flow-chart .flow-bars` 存在。還原成 `__FX` 再載入一次 → 兩張圖回來、`.flow-zoom-wrap` 沒有 `hidden`。
6. `read_console_messages {onlyErrors:true}` → 無錯誤。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv/Scripts/python -c "b=open('web/app.js','rb').read(); print('loneLF', b.count(b'\n')-b.count(b'\r\n'))"
git add web/app.js
git commit -m "feat(rotation): 全範圍主圖＋核心放大鏡、上期○→本期●方向向量、常駐標籤 7＋選取、選取時其餘淡化" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 象限領先者、選取細節列、統一選取與鍵盤

**Files:**
- Modify: `web/app.js`（新增 `renderFlowLeaders`／`flowLeaderRow`／`flowLeaderBox`／`renderFlowDetail`／`renderFlowAll`／`clearFlowView`；刪除 `renderFlowQuadrants`／`flowColumn`；最終版 `loadSectorFlow`／`toggleRotationFilter`；事件委派改到 `#view-rotation`）

**Interfaces:**
- Consumes（Task 1–3）：`flowModel` 的 `leaders/bySector`、`flowPct`、`flowSigned2`、`flowYi`、`FLOW_Q_NAME`、`renderFlowHeader`、`renderFlowSummary`、`renderFlowCharts`、`renderFlowBars`、`flowHighlight`、`disposeFlowChart`、`applyRotationFilter`。
- Produces：`renderFlowAll(d, m)`（選取改變時重畫卡、圖、排行、細節列；**不**重畫頁首，免得 `aria-live` 的 chips 每點一次就被螢幕報讀器重念）；`clearFlowView(msg)`。

- [ ] **Step 1: 先寫斷言腳本**

存成 `.superpowers/sdd/fixtures/flow-leaders-check.js`：

```js
(() => {
  const boxes = [...document.querySelectorAll("#flow-quadrants .flow-q")];
  const box = (q) => document.querySelector(`#flow-quadrants .flow-q-${q}`);
  const rows = (q) => [...box(q).querySelectorAll(".flow-row")].map((b) => b.dataset.sector).join(",");
  const checks = {
    fourBoxes: boxes.length === 4,
    counts: ["in:8", "big:5", "inst:15", "out:6"].every((c) => { const [q, n] = c.split(":"); return box(q).querySelector(".flow-q-count").textContent === `${n} 類`; }),
    topIn: rows("in") === "航運,電機機械,化學",
    topBig: rows("big") === "其他,電腦及週邊設備,玻璃陶瓷",
    topInst: rows("inst") === "光電,水泥,文化創意",
    topOut: rows("out") === "通信網路,塑膠,綠能環保",
    labelsExplicit: [...document.querySelectorAll("#flow-quadrants .flow-row-val")].every((v) => /法人 [+-]?\d+\.\d\d%/.test(v.textContent) && /大戶 [+-]?\d+\.\d\d%/.test(v.textContent)),
    titles: [...document.querySelectorAll("#flow-quadrants .flow-row-name")].every((n) => n.title === n.textContent),
    hint: $("flow-detail").textContent.includes("點摘要卡、泡泡或右側排行"),
  };
  toggleRotationFilter("電機機械");
  const t = $("flow-detail").textContent.replace(/\s+/g, " ");
  checks.detail = t.includes("電機機械") && t.includes("大戶增・法人賣 → 雙流入") && t.includes("法人 +0.08%（Δ +0.35）")
    && t.includes("大戶 +0.14%（Δ -0.23）") && t.includes("1560 中砂 25 億") && t.includes("清除篩選");
  checks.syncPressed = document.querySelector('#flow-quadrants .flow-row[data-sector="電機機械"]').getAttribute("aria-pressed") === "true";
  toggleRotationFilter("航運");
  checks.cardAndRowTogether = document.querySelector('.flow-card[data-sector="航運"]').getAttribute("aria-pressed") === "true"
    && document.querySelector('#flow-quadrants .flow-row[data-sector="航運"]').getAttribute("aria-pressed") === "true";
  document.querySelector(".flow-detail-clear").click();
  checks.clearButton = rotationSectorFilter === null && $("flow-detail").textContent.includes("點摘要卡");
  const failed = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
  return JSON.stringify({ failed, passed: Object.keys(checks).length - failed.length, total: Object.keys(checks).length });
})()
```

- [ ] **Step 2: 確認斷言先失敗**

重新整理、注入 fixture、執行腳本。
Expected：`fourBoxes`、`counts`、`titles`、`hint`、`detail`、`clearButton` 等出現在 `failed`（舊的四份完整清單仍在、細節列是空的）或 `TypeError`（找不到 `.flow-q-count`／`.flow-detail-clear`）。

- [ ] **Step 3: 實作**

(a) 刪除整支 `function renderFlowQuadrants(d) { ... }` 與 `function flowColumn(k, title, rows, withY) { ... }`，原位換成：

```js
// ---------- 象限領先者：2×2、每格類股數＋Top 3（ui72）----------
// 每列寫明「法人」「大戶」（不再用「+0.36／+0.34」）；名稱過長省略號、title 帶全名。
function flowLeaderRow(sector, x, y) {
  return `<button type="button" class="flow-row" data-sector="${esc(sector)}" aria-pressed="${rotationSectorFilter === sector}">`
    + `<span class="flow-row-name" title="${esc(sector)}">${esc(sector)}</span>`
    + `<span class="flow-row-val"><span>法人 ${flowPct(x)}</span>${y == null ? "" : `<span>大戶 ${flowPct(y)}</span>`}</span></button>`;
}
function flowLeaderBox(q, title, count, rowsHtml) {
  return `<div class="flow-q flow-q-${q}"><h4>${title}<span class="flow-q-count">${count} 類</span></h4>`
    + (rowsHtml.join("") || '<div class="muted small">—</div>') + "</div>";
}
function renderFlowLeaders(d, m) {
  const el = $("flow-quadrants"); if (!el) return;
  if (!m) {   // 單軸降級：法人買超／賣超各 Top 3
    const secs = (d.sectors || []).filter((s) => s.x != null);
    const buy = secs.filter((s) => s.x > 0).sort((a, b) => b.x - a.x);
    const sell = secs.filter((s) => s.x <= 0).sort((a, b) => a.x - b.x);
    el.innerHTML = flowLeaderBox("in", "法人買超", buy.length, buy.slice(0, 3).map((s) => flowLeaderRow(s.sector, s.x, null)))
      + flowLeaderBox("out", "法人賣超", sell.length, sell.slice(0, 3).map((s) => flowLeaderRow(s.sector, s.x, null)));
    return;
  }
  el.innerHTML = ["in", "big", "inst", "out"].map((q) => flowLeaderBox(q, FLOW_Q_NAME[q], m.leaders[q].count,
    m.leaders[q].top.map((r) => flowLeaderRow(r.sector, r.s.x, r.s.y)))).join("");
}

// ---------- 選取細節列（ui72）----------
// 主要貢獻個股就是 tooltip 既有的 top3（法人買最多），不新增 API。Δ 不著紅綠（資金流向不是漲跌）。
function renderFlowDetail(d, m) {
  const el = $("flow-detail"); if (!el) return;
  const sel = rotationSectorFilter;
  if (!sel) { el.innerHTML = '<span class="muted">點摘要卡、泡泡或右側排行，查看類股細節並篩選下方交叉選股</span>'; return; }
  const r = m ? m.bySector[sel] : null;
  const s = r ? r.s : (d.sectors || []).find((x) => x.sector === sel);
  const clear = '<button type="button" class="tf flow-detail-clear">清除篩選</button>';
  if (!s) { el.innerHTML = `<span class="flow-detail-name">${esc(sel)}</span><span class="muted">不在目前的族群資料中</span>${clear}`; return; }
  const trans = r ? (r.prev ? `${FLOW_Q_NAME[r.qPrev]} → ${FLOW_Q_NAME[r.q]}` : FLOW_Q_NAME[r.q]) : "";
  const dl = (k) => (r && r.prev ? `（Δ ${flowSigned2(s[k] - s[k + "_prev"])}）` : "");
  const top = (s.top3 || []).map((t) => `${esc(t.code)} ${esc(t.name)} ${flowYi(t.amount)} 億`).join("、");
  el.innerHTML = `<span class="flow-detail-name">${esc(s.sector)}</span>`
    + (trans ? `<span class="flow-qtag flow-q-${r.q}">${esc(trans)}</span>` : "")
    + `<span>法人 ${flowPct(s.x)}${dl("x")}</span>`
    + (s.y != null ? `<span>大戶 ${flowPct(s.y)}${dl("y")}</span>` : "")
    + (top ? `<span class="flow-detail-top">法人買最多：${top}</span>` : "")
    + clear;
}

// 選取改變或載入完成時重畫（不含頁首：chips 是 aria-live，每點一次就重念會很吵）
function renderFlowAll(d, m) {
  renderFlowSummary(d, m);
  if (m) renderFlowCharts(d, m); else renderFlowBars(d);
  renderFlowLeaders(d, m);
  renderFlowDetail(d, m);
}
function clearFlowView(msg) {
  disposeFlowChart();
  lastSectorFlow = null; lastFlowModel = null;
  const el = $("flow-chart");
  el.style.height = "auto";
  el.innerHTML = `<div class="muted small" style="padding:12px">${esc(msg)}</div>`;
  ["flow-quadrants", "flow-summary", "flow-detail", "flow-chips", "flow-help-body"].forEach((id) => { const n = $(id); if (n) n.innerHTML = ""; });
  const h = $("flow-headline"); if (h) h.textContent = "";
  const zw = document.querySelector(".flow-zoom-wrap"); if (zw) zw.classList.add("hidden");
}
```

(b) 整支 `async function loadSectorFlow() { ... }` 換成最終版：

```js
async function loadSectorFlow() {
  if (!$("flow-chart")) return;
  try {
    const d = await getJSON("/api/sectors/flow");
    if (!(d.sectors || []).length) { clearFlowView("尚無法人資料（stock_flow_daily 尚未累積）"); return; }
    lastSectorFlow = d;
    lastFlowModel = d.has_custody ? flowModel(d) : null;
    renderFlowHeader(d, lastFlowModel);
    renderFlowAll(d, lastFlowModel);
  } catch (e) {
    // 失敗也要清狀態：留著上一次的摘要、排行與 lastSectorFlow，按篩選會拿舊資料重畫
    clearFlowView("族群輪動載入失敗");
  }
}
```

(c) 整支 `function toggleRotationFilter(sector) { ... }` 換成最終版：

```js
// drill-down：篩下方交叉選股（不重打 API，只切 .hidden）；再點同一個取消。狀態不持久化。
// 摘要卡、主圖泡泡、放大鏡泡泡、象限領先者列、細節列的「清除篩選」都走這裡，四處選取狀態永遠一致。
function toggleRotationFilter(sector) {
  rotationSectorFilter = (sector && rotationSectorFilter !== sector) ? sector : null;
  applyRotationFilter();
  if (!lastSectorFlow) return;
  // 重繪會換掉整批按鈕 → 焦點掉回 body，只用鍵盤的人會失去位置（同 ss-picked-note 那段的作法）
  const act = document.activeElement;
  const cur = act && act.closest ? act.closest(".flow-card[data-sector], .flow-row[data-sector], .flow-detail-clear") : null;
  const key = !cur ? null : cur.classList.contains("flow-detail-clear") ? "clear"
    : `${cur.classList.contains("flow-card") ? ".flow-card" : ".flow-row"}[data-sector="${CSS.escape(cur.dataset.sector)}"]`;
  renderFlowAll(lastSectorFlow, lastFlowModel);
  if (key === "clear") { const dt = $("flow-detail"); if (dt) dt.focus(); }
  else if (key) { const again = $("view-rotation").querySelector(key); if (again) again.focus(); }
}
```

(d) 事件委派：刪除這兩段——

```js
const flowQEl = $("flow-quadrants");
if (flowQEl) flowQEl.addEventListener("click", (e) => { const b = e.target.closest(".flow-row"); if (b) toggleRotationFilter(b.dataset.sector); });
```

與 Task 2 加的

```js
const flowSummaryEl = $("flow-summary");
if (flowSummaryEl) flowSummaryEl.addEventListener("click", (e) => {
  const b = e.target.closest(".flow-card[data-sector]");
  if (b && !b.disabled) toggleRotationFilter(b.dataset.sector);
});
```

在原位加（`flowHelpEl` 那段保留）：

```js
// 族群輪動的按鈕都是動態產生的 → 委派在靜態 HTML 就有的 #view-rotation 上（CSP 擋 inline on*=）
const rotationEl = $("view-rotation");
if (rotationEl) {
  rotationEl.addEventListener("click", (e) => {
    if (e.target.closest(".flow-detail-clear")) { toggleRotationFilter(null); return; }
    const b = e.target.closest(".flow-card[data-sector], .flow-row[data-sector]");
    if (b && !b.disabled) toggleRotationFilter(b.dataset.sector);
  });
  const flowFocus = (on) => (e) => {
    const b = e.target.closest && e.target.closest(".flow-card[data-sector], .flow-row[data-sector]");
    if (b) flowHighlight(b.dataset.sector, on);
  };
  rotationEl.addEventListener("focusin", flowFocus(true));
  rotationEl.addEventListener("focusout", flowFocus(false));
}
```

- [ ] **Step 4: 靜態檢查並重跑斷言**

Run: `node --check web/app.js && grep -n "renderFlowQuadrants\|flowColumn\|flowQEl\|flowSummaryEl" web/app.js`
Expected：`node --check` 無輸出；grep 無輸出。

瀏覽器：`preview_stop`→`preview_start`、注入 fixture、執行 Step 1 腳本。
Expected：`{"failed":[],"passed":13,"total":13}`。

- [ ] **Step 5: 鍵盤與一致性驗證**

1. 只用鍵盤：`computer` 點一下頁首標題（把焦點放進頁面），按 `Tab` 直到焦點落在第一張摘要卡（`document.activeElement.classList.contains("flow-card")`）。此時主圖 tooltip DOM 可見（`flowChart` 容器內 tooltip 的 `style.display !== "none"`）。按 `Enter` → `rotationSectorFilter === "航運"` 且 `document.activeElement.dataset.sector === "航運"`。繼續 `Tab` 到象限領先者任一列、`Enter` → 選取換成該列的類股、焦點仍在該列。`Tab` 到「清除篩選」、`Enter` → 選取為 null、`document.activeElement.id === "flow-detail"`。
2. 下方交叉選股一致：分別用「點卡」「點主圖泡泡」（Task 3 Step 5 的座標法）「點排行列」選同一個類股，三次都檢查 `[...document.querySelectorAll('#cross .cross-grp')].every((g) => g.classList.contains('hidden') === (g.dataset.sector !== rotationSectorFilter))` 為 true。
3. 交叉選股右上角的「顯示全部」（`#cross-clear`）→ 選取清空、細節列回到提示、卡與列的 `aria-pressed` 全為 false。
4. 載入失敗：`getJSON = async (u) => { if (String(u).startsWith("/api/sectors/flow")) throw new Error("x"); return window.__origGetJSON(u); }; await loadSectorFlow();` → `#flow-chart` 顯示「族群輪動載入失敗」、`#flow-summary`／`#flow-quadrants`／`#flow-detail`／`#flow-chips` 都是空的、`.flow-zoom-wrap` 有 `hidden`。還原 `getJSON` 指回 fixture、重新 `loadSectorFlow()`。
5. `read_console_messages {onlyErrors:true}` → 無錯誤。

- [ ] **Step 6: CRLF 檢查並提交**

```bash
.venv/Scripts/python -c "b=open('web/app.js','rb').read(); print('loneLF', b.count(b'\n')-b.count(b'\r\n'))"
git add web/app.js
git commit -m "feat(rotation): 象限領先者 Top3（法人／大戶明示）、選取細節列、四種點擊同一個選取、鍵盤 focus 高亮泡泡" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 快取版號、文件、全寬度驗證

**Files:**
- Modify: `web/index.html`、`stocks_power_rich/api/public.py`、`tests/test_api.py`（版號）
- Modify: `CLAUDE.md`、`AGENTS.md`

**Interfaces:**
- Consumes：Task 1–4 的全部成果。

- [ ] **Step 1: 版號 ui71 → ui72（四處）**

```bash
.venv/Scripts/python -c "
OLD,NEW='20260817-ui71','20260817-ui72'
for p in ('web/index.html','stocks_power_rich/api/public.py','tests/test_api.py'):
    s=open(p,'rb').read().decode('utf-8'); n=s.count(OLD); s=s.replace(OLD,NEW)
    assert s.count('\n')==s.count('\r\n'), p
    open(p,'wb').write(s.encode('utf-8')); print(p, n)
"
```

Expected：`web/index.html 2`、`stocks_power_rich/api/public.py 4`、`tests/test_api.py 4`。

Run: `.venv/Scripts/python -m pytest tests/test_api.py -q --no-header -k "public or frontend or overview" > pt_t5.txt 2>&1; echo EXIT=$?`，讀 `pt_t5.txt` 最後一行。
Expected：`EXIT=0`。刪掉 `pt_t5.txt`。

- [ ] **Step 2: CLAUDE.md**

在「### 族群輪動改「法人 × 大戶」資金流向四象限（ui69，2026-09）」那一節的最後一個條目之後，加一節：

```markdown
### 族群輪動重新設計（ui72，2026-09）

目標是讓使用者 5 秒內回答「最強共振／加速轉強／風險外流」。只改前端，API 與 X／Y 定義不動。

- **一份模型、所有區塊共用**：`flowModel(d)`（純函式）算出放大鏡範圍、正規化刻度、三卡、常駐標籤、象限 Top 3、主圖範圍；
  摘要卡、兩張圖、象限領先者、細節列、頁首判讀都只讀它，同一類股不會在不同區塊有不同判定。
- **核心放大鏡＝兩軸同比例修剪兩端、取框住至少 80% 類股的最小範圍**（從各修 10% 起每次放寬 1 點，含原點、加 12% 邊界）。
  2026-09-23 實測：修 8%、框住 28/34；固定修 10% 只框 74%、固定修 5% 框 91%。主圖改成「本期＋上期＋原點」的實際範圍
  （舊版對稱 ±最大值，Y 軸空約兩成），並用虛線框標出放大鏡範圍。座標全部線性。
- **正規化刻度用放大鏡寬度，不用主圖全範圍**：主圖 Y 軸被汽車上期 −1.267 這一個點撐到 ±1.4，拿它正規化，
  Y 的變化被壓到約三分之一、X 變成主導（當天「加速轉強」會變文化創意）——正好違反「避免單一軸主導」。
- **加速轉強／風險外流多一道「本期站到對的一邊」**（使用者 2026-09-23 選擇）：`comp > 0`／`< 0`，且 `move` 同號，
  從很差變成沒那麼差不叫轉強；全部惡化的日子加速轉強卡顯示「沒有類股本期轉正」。
- **常駐標籤最多 8 個**（三卡＋位移最大者補到 7＋選取），其他在 hover／鍵盤 focus／選取時才出現；放大鏡只標框內的。
- **向量**：上期空心○ → 本期●，拆兩段、箭頭畫在中點（不被泡泡蓋住）；選取時 2.5px、其餘降到 0.12 透明。
- **象限色是新 token**（`--flow-in/big/inst/out`：cyan／冷藍／indigo／slate，明度遞減，在卡片底 5.3–9.6:1）；
  紅綠只在 tooltip 的當日漲跌與交叉選股。
- **數字固定兩位小數用 `flowPct`**：既有 `fmt()` 只設 `maximumFractionDigits`，`+0.30` 會變 `+0.3`，並排對不齊。
- **高度要用 `#view-rotation .flow-chart`**：`.chart { height: 340px }` 定義在後面、同權重，會把 `.flow-chart` 蓋掉
  （ui69 那條就是這樣變成死規則，當時靠 inline style 撐著）。
- **重畫不含頁首**：chips 列是 `aria-live`，選取時只重畫卡／圖／排行／細節列，免得每點一次就被重念。
- **鍵盤**：focus 到卡或排行列時對兩張圖 `highlight`＋主圖 `showTip`（bubbles 固定 seriesIndex 0），這是鍵盤使用者
  唯一能指到 canvas 泡泡的方式。
- 前端沒有自動化測試；驗證是把 2026-09-23 的真實快照注入 `getJSON` 後跑斷言（放大鏡 28/34、三卡航運／汽車／光電、
  標籤 7 個、四象限 Top 3），再在各寬度量溢出。
```

- [ ] **Step 3: AGENTS.md**

在族群輪動（ui69）那一段之後加一段：

```markdown
## 族群輪動重新設計（ui72，2026-09）
只改前端。`flowModel(d)` 是唯一的判定來源（放大鏡範圍、正規化刻度、三卡、常駐標籤、象限 Top 3、主圖範圍），所有區塊只讀它。放大鏡＝兩軸同比例修剪兩端、框住至少 80% 的最小範圍（含原點、加 12%）；正規化用放大鏡寬度（主圖全範圍會被單一離群值撐開、讓 X 主導）；加速轉強／風險外流要求本期 `comp` 同號且 `move` 同號。常駐標籤最多 8 個；向量上期○→本期●、箭頭在中點；象限色 token `--flow-in/big/inst/out`，紅綠只在 tooltip 當日漲跌。百分比用 `flowPct` 固定兩位（`fmt` 會吃掉尾端 0）；高度要寫 `#view-rotation .flow-chart`（`.chart` 同權重在後面）；選取重畫不含 aria-live 的頁首 chips。
```

- [ ] **Step 4: 全寬度驗證（注入真實快照）**

`preview_stop`→`preview_start`、注入 fixture。對 **1560、1280、1181、1180、601、600、375** 每個寬度（`resize_window`，375 用 `preset:"mobile"`）執行並記錄：

```js
const el = (s) => [...document.querySelectorAll(s)];
const over = (e) => e.scrollWidth > e.clientWidth + 1;
JSON.stringify({
  vw: innerWidth,
  pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  summaryCols: getComputedStyle($("flow-summary")).gridTemplateColumns.split(" ").length,
  flowCols: getComputedStyle($("rotation-flow")).gridTemplateColumns.split(" ").length,
  chartH: $("flow-chart").clientHeight, zoomH: $("flow-zoom").clientHeight,
  canvasOk: [flowChart, flowZoomChart].every((c) => Math.abs(c.getWidth() - c.getDom().clientWidth) <= 2),
  textOverflow: el("#view-rotation .flow-card-vals, #view-rotation .flow-card-note, #view-rotation .flow-row-val, #view-rotation .flow-detail > span, #view-rotation .flow-chip").filter(over).length,
  truncatedWithoutTitle: el("#view-rotation .flow-row-name, #view-rotation .flow-card-name b").filter((e) => over(e) && !e.title).length,
  // .flow-side 在 ≤600px 是 display:contents（沒有自己的框），所以直接量四個區塊
  order: ["flow-main", "flow-zoom-wrap", "flow-detail", "flow-quadrants"]
    .map((c) => [c, Math.round(document.querySelector("#rotation-flow ." + c).getBoundingClientRect().top)]),
  sideVsMain: Math.round(document.querySelector("#rotation-flow .flow-side").getBoundingClientRect().height
    - document.querySelector("#rotation-flow .flow-main").getBoundingClientRect().height),
})
```

Expected：
- 每個寬度 `pageOverflow: false`、`textOverflow: 0`、`truncatedWithoutTitle: 0`、`canvasOk: true`。
- 1560／1280／1181：`summaryCols 3`、`flowCols 2`、`chartH 580`、`zoomH 250`，`sideVsMain` 的絕對值 ≤ 40（右欄與主圖大致齊高；超過就調 `.flow-row` 行高或主圖高度，量到的數字寫進報告與 CLAUDE.md）。
- 1180／601：`summaryCols 3`、`flowCols 1`、`chartH 460`、`zoomH 300`（放大鏡與象限領先者同一列並排）。
- 600／375：`summaryCols 1`、`chartH 340`、`zoomH 300`，`order` 的 top 由小到大依序是 `flow-main` → `flow-zoom-wrap` → `flow-detail` → `flow-quadrants`。
- 任一寬度不符就修 CSS 再量，量到的數字寫進報告。最後 `resize_window {preset:"desktop"}`。

其餘逐項：
1. 1560px 截圖一張（主圖＋放大鏡＋摘要卡），確認中央群聚與 6 個離群類股同時可辨識。
2. 1560px 與 375px 各跑一次 Task 1、Task 3、Task 4 的斷言腳本，全綠。
3. `read_console_messages {onlyErrors:true}` → 無錯誤。

- [ ] **Step 5: CRLF 檢查並提交**

```bash
.venv/Scripts/python -c "[print(p, (lambda b: b.count(b'\n')-b.count(b'\r\n'))(open(p,'rb').read())) for p in ('web/index.html','stocks_power_rich/api/public.py','tests/test_api.py','CLAUDE.md','AGENTS.md','web/styles.css','web/app.js')]"
git add web/index.html stocks_power_rich/api/public.py tests/test_api.py CLAUDE.md AGENTS.md web/styles.css web/app.js
git commit -m "feat(rotation): 族群輪動重新設計（ui72）——版號、文件、全寬度驗證修正" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review（計畫作者已做）

- **Spec 覆蓋**：§1 模型→Task 1；§2 版面→Task 2（CSS）＋Task 5（量測）；§3 頁首→Task 2；§4 摘要卡→Task 2；§5 兩張圖→Task 3；§6 向量→Task 3；§7 標籤→Task 1（選哪些）＋Task 3（顯示）；§8 選取與鍵盤→Task 2（卡）＋Task 3（泡泡）＋Task 4（統一、focus）；§9 象限領先者→Task 4；§10 細節列→Task 4；§11 配色→Task 2（token）＋Task 3（圖）；§12 無障礙→Task 2／4；§13 降級→Task 2（卡）＋Task 3（單軸圖）＋Task 4（排行、失敗）；§14 檔案→各 Task；§15 驗證→每個 Task 的斷言＋Task 5；§16 刻意不做→不在任何 Task 裡。
- **補充規格的地方（Spec 沒寫、這裡定下來）**：沒有上期時常駐標籤改用 `hypot(nx, ny)` 補滿；百分比固定兩位小數（`flowPct`）；選取狀態沿用本頁既有的 `--info` 框（不另開顏色）；popover 在 `.content` 捲動時自動收起。
- **型別一致**：`flowModel` 回傳欄位（`rows/bySector/core/main/cards/labels/leaders/hasPrevAny/wx/wy`）在 Task 2–4 使用同名；series 順序 `[bubbles, prev, vecHead, vecTail, vecSelHead]` 在 Task 3 的斷言與 `flowHighlight` 一致；`renderFlowAll` 只在 Task 4 定義、之後才被 `toggleRotationFilter`／`loadSectorFlow` 呼叫。
- **無佔位**：每一步都有實際程式碼、實際指令與期望值。
