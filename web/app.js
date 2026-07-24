"use strict";

const $ = (id) => document.getElementById(id);
// HTML 跳脫：所有嵌入 innerHTML 的外部/CSV 字串（股名、產業、檔名、錯誤訊息）都要經過，
// 防止惡意 CSV 的股名如 <img onerror> 被當標記執行（儲存型 XSS）。兼顧屬性情境（含 " '）。
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const chgClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const chgText = (v) => (v === null || v === undefined ? "" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v)));

// 公開模式（/public/overview 注入 data-public）：與站內共用同一份前端，只換資料來源與可見範圍。
// API 前綴集中在 apiUrl() 轉換，各呼叫點照舊寫 "/api/..."，一處改完全部生效。
const PUBLIC = document.body.dataset.public === "1";
const apiUrl = (p) => (PUBLIC ? p.replace(/^\/api\//, "/public/api/") : p);

async function getJSON(url) {
  const r = await fetch(apiUrl(url));
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

// ========== 圖表共用 ==========
let idxChart, stockChart;
let idxSymbol = "taiex", idxInterval = "1d", overviewWaves = false;
let stockCode = "", stockInterval = "1d", stockWaves = false;
let wavePct = 0.05;
let lastIndexData = null, lastStockData = null;
let chipChart = null, chipMetric = "inst", lastHistory = [];
// 判讀句需要「指數方向」(dashboard) ＋「漲跌家數」(breadth) 兩支 API 的值，但兩者分開載入。
// 兩處呼叫點都是 loadDashboard 先 await、loadBreadth 後跑，故此處存下最新一列即可。
let lastLatest = null, lastBands = {};
let txVolChart = null;
let stockChipsChart = null, stockCustodyChart = null;
// heatmapTop 預設 5：實測 1267px 寬下，5 檔比 6 檔「顯示更多可讀標籤」(110 vs 108) 且字更大、
// 留白更少——格數少 → 格子大 → 過得了 11px 中文可讀下限的格子反而變多。
let sectorChart = null, heatmapMarket = "tse", heatmapTop = 5, lastHeatmapData = null;
let cupChart = null, cupMatches = [], cupLoaded = false;
let rankWho = "foreign", rankUnit = "shares";
const MA_DEFS = [
  { n: 5, color: "#5b8ff9" }, { n: 20, color: "#5ad8a6" },
  { n: 60, color: "#f6bd16" }, { n: 120, color: "#e8684a" },
];

// ECharts 只吃色字串，無法用 var(--x)，所以圖表色一律由此處從 CSS token 讀出——
// 讓 :root 成為單一真相來源，改 token 時圖表不會悄悄失同步。
const CSS_VAR = (name, fallback) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const C = {
  up: CSS_VAR("--up", "#f56069"),
  down: CSS_VAR("--down", "#25b37d"),
  upFill: CSS_VAR("--up-fill", "#c62b38"),
  downFill: CSS_VAR("--down-fill", "#127a53"),
  accent: CSS_VAR("--accent", "#f0a500"),
  info: CSS_VAR("--info", "#6cb6ff"),
  text: CSS_VAR("--text", "#e6e6e6"),
  muted: CSS_VAR("--muted", "#8a94a3"),
  label: CSS_VAR("--label", "#cfd6df"),
  border: CSS_VAR("--border", "#2e3845"),
  bg: CSS_VAR("--bg", "#0f1419"),
};
// 圖表系列固定色（三大法人等跨圖共用的角色色，非市場方向色）：
// 外資琥珀刻意與 --accent 區隔（accent 專屬「目前選取」），投信沿用 info 藍。
const SER = { foreign: "#e0a23c", trust: C.info, dealer: "#a07cff" };
// 圖表字型：熱力圖的量測(canvas)與繪製(ECharts)必須用同一組，否則量得下卻被截；
// K線等其他圖表也套同一組讓全站字型一致。須與 styles.css 的 body 堆疊同步（數字→Num、中文→粉圓）。
const HM_FONT = '"Num", "Huninn", "Microsoft JhengHei", "PingFang TC", sans-serif';
const PIN_YELLOW = "#ffd23f";   // 杯柄圖釘醒目黃（獨立於 token，僅此一用）

function ma(values, n) {
  const out = [];
  for (let i = 0; i < values.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    let s = 0;
    for (let j = 0; j < n; j++) s += values[i - j];
    out.push(+(s / n).toFixed(2));
  }
  return out;
}

function candlestickOption(data, startPct, showW, pct) {
  const closes = data.candles.map((c) => c[1]);
  const maSeries = MA_DEFS.map((m) => ({ name: "MA" + m.n, type: "line", data: ma(closes, m.n), smooth: true, showSymbol: false, lineStyle: { width: 1, color: m.color }, itemStyle: { color: m.color } }));
  const candle = { name: "K線", type: "candlestick", data: data.candles, itemStyle: { color: C.up, color0: C.down, borderColor: C.up, borderColor0: C.down } };
  // 現價線：最後收盤的水平虛線（顏色跟最後一根方向），掃一眼就知道現價相對歷史的位置
  const last = data.candles[data.candles.length - 1];
  if (last) {
    const lastCol = last[1] >= last[0] ? C.up : C.down;   // candles = [open, close, low, high]
    candle.markLine = {
      symbol: "none", silent: true, animation: false,
      lineStyle: { type: "dashed", color: lastCol, width: 1, opacity: 0.75 },
      label: { show: true, position: "insideEndTop", color: lastCol, fontSize: 11, fontWeight: 700, formatter: () => fmt(last[1], 2) },
      data: [{ yAxis: last[1] }],
    };
  }
  // 量能柱依當根 K 棒方向著紅/綠（半透明，不搶主圖）；tooltip 讀 value 不受影響
  const volumes = data.candles.map((c, i) => ({
    value: data.volumes[i], itemStyle: { color: c[1] >= c[0] ? C.up : C.down },
  }));
  if (showW) {
    const pctKey = Math.round(pct * 100).toString();
    const waves = (data.waves && data.waves[pctKey]) || [];
    if (waves.length) candle.markPoint = {
      symbol: "circle", symbolSize: 20,
      label: { color: "#1a1a1a", fontWeight: 700, fontSize: 12, formatter: (p) => p.data.value },
      data: waves.map((w) => ({ value: w.label, coord: [data.dates[w.index], data.candles[w.index][3]], itemStyle: { color: /[ABC]/.test(w.label) ? C.info : C.accent } })),
    };
  }
  return {
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross" },
      formatter: (ps) => {
        if (!ps || !ps.length) return "";
        let html = ps[0].axisValue;
        ps.forEach((p) => {
          const m = p.marker || "";
          if (p.seriesType === "candlestick") {
            const d = p.data, n = d.length;
            const o = d[n - 4], c = d[n - 3], l = d[n - 2], h = d[n - 1];  // [(idx,)open,close,low,high]
            html += `<br/>${m}K線　開 ${fmt(o, 2)}　收 ${fmt(c, 2)}　低 ${fmt(l, 2)}　高 ${fmt(h, 2)}`;
          } else if (p.seriesName === "量") {
            if (p.value != null) html += `<br/>${m}量 ${fmt(p.value, 0)}`;
          } else if (p.value != null) {
            html += `<br/>${m}${p.seriesName} ${fmt(p.value, 2)}`;
          }
        });
        return html;
      },
    },
    textStyle: { fontFamily: HM_FONT },
    legend: { top: 0, data: ["K線", ...MA_DEFS.map((m) => "MA" + m.n)], textStyle: { color: C.label } },
    grid: [{ left: 60, right: 20, top: 30, height: "60%" }, { left: 60, right: 20, top: "76%", height: "15%" }],
    xAxis: [{ type: "category", data: data.dates, axisLabel: { color: C.muted } }, { type: "category", data: data.dates, gridIndex: 1, axisLabel: { show: false } }],
    yAxis: [{ scale: true, axisLabel: { color: C.muted }, splitLine: { lineStyle: { color: C.border } } }, { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: startPct }, { type: "slider", xAxisIndex: [0, 1], start: startPct, bottom: 0, height: 16 }],
    series: [candle, ...maSeries, { name: "量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, itemStyle: { opacity: 0.55 }, data: volumes }],
  };
}

// ========== 視圖切換 ==========
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  if (name === "overview") { idxChart && idxChart.resize(); chipChart && chipChart.resize(); sectorChart && sectorChart.resize(); txVolChart && txVolChart.resize(); }
  if (name === "stock") { stockChart && stockChart.resize(); stockChipsChart && stockChipsChart.resize(); stockCustodyChart && stockCustodyChart.resize(); }
  if (name === "rotation") { loadRotation(); loadCross(); }
  // 兩個監控頁各自輪詢：進入才啟動、切走即停——控制請求量
  if (name === "osfut") { loadOsFutures("live"); startOsfutPolling(); } else stopOsfutPolling();
  if (name === "hiprice") { loadRankPrice(); startRankPolling(); } else stopRankPolling();
  if (name === "cup") { if (!cupLoaded) loadCupHandle(); else cupChart && cupChart.resize(); }
  if (name === "weekly") loadCsvSummary(false);  // 讀快取即回；匯入後才會重新生成
  if (name === "watch") loadWatchlist();
  if (name === "trades") loadTrades();
  if (name === "traders") loadTraders();
  if (name === "settings") loadSettings();
}

// ========== 交易帳本（#6）：實單/模擬單記錄與績效統計 ==========
async function loadTrades() {
  if (!$("tr-open")) return;
  if (!$("tr-date").value) {  // 進場日預設今天（本地時區，避免 toISOString 的 UTC 偏移）
    const d = new Date();
    $("tr-date").value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  try { renderTrades(await getJSON("/api/trades")); }
  catch (e) { $("tr-open").innerHTML = '<span class="muted small">載入失敗</span>'; }
}
function renderTrades(d) {
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const pct = (v) => v == null ? "—" : (v > 0 ? "+" : "") + fmt(v, 2) + "%";
  const money = (v) => v == null ? "—" : (v > 0 ? "+" : "") + fmt(v, 0);
  const R = 'style="text-align:right"';
  const s = d.stats || {};
  $("tr-stats").innerHTML = [
    ["已平倉筆數", s.closed_n], ["勝率", s.win_rate == null ? "—" : fmt(s.win_rate, 1) + "%"],
    ["平均賺", pct(s.avg_win)], ["平均賠", pct(s.avg_loss)],
    ["賺賠比", s.payoff == null ? "—" : fmt(s.payoff, 2)], ["期望值/筆", pct(s.expectancy)],
    ["已實現損益", money(s.realized_pnl)], ["未實現損益", money(s.open_pnl)],
    ["平均勝過大盤", pct(s.avg_alpha)],
  ].map(([k, v]) => `<div class="stat"><div class="stat-k">${k}</div><div class="stat-v">${v == null ? "—" : v}</div></div>`).join("");
  const ts = d.trades || [], op = ts.filter(t => t.status === "open"), cl = ts.filter(t => t.status === "closed");
  $("tr-open").innerHTML = op.length
    ? `<table><tr><th>股票</th><th>進場日</th><th ${R}>進場價</th><th ${R}>股數</th><th ${R}>現價</th><th ${R}>未實現%</th><th ${R}>未實現損益</th><th ${R}>同期大盤</th><th></th></tr>`
      + op.map(t => `<tr title="${esc(t.note || "")}"><td>${stockLink(t.code, t.name)}</td><td>${esc(t.entry_date)}</td>`
        + `<td ${R}>${fmt(t.entry_price, 2)}</td><td ${R}>${fmt(t.shares, 0)}</td><td ${R}>${t.mark == null ? "—" : fmt(t.mark, 2)}</td>`
        + `<td ${R} class="${cls(t.net_pct)}">${pct(t.net_pct)}</td><td ${R} class="${cls(t.pnl)}">${money(t.pnl)}</td>`
        + `<td ${R}>${pct(t.mkt_pct)}</td><td><button class="file-label tr-close" data-id="${t.id}">平倉</button> <button class="file-label tr-del" data-id="${t.id}">刪</button></td></tr>`).join("") + "</table>"
    : '<span class="muted small">無未平倉部位（上方「＋記一筆」開始記錄）</span>';
  $("tr-closed").innerHTML = cl.length
    ? `<table><tr><th>股票</th><th>持有期間</th><th ${R}>進→出</th><th ${R}>股數</th><th ${R}>淨報酬</th><th ${R}>損益</th><th ${R}>同期大盤</th><th ${R}>勝過大盤</th><th></th></tr>`
      + cl.map(t => `<tr title="${esc(t.note || "")}"><td>${stockLink(t.code, t.name)}</td><td>${esc(t.entry_date)} → ${esc(t.exit_date)}</td>`
        + `<td ${R}>${fmt(t.entry_price, 2)} → ${fmt(t.exit_price, 2)}</td><td ${R}>${fmt(t.shares, 0)}</td>`
        + `<td ${R} class="${cls(t.net_pct)}">${pct(t.net_pct)}</td><td ${R} class="${cls(t.pnl)}">${money(t.pnl)}</td>`
        + `<td ${R}>${pct(t.mkt_pct)}</td><td ${R} class="${cls(t.alpha)}">${pct(t.alpha)}</td>`
        + `<td><button class="file-label tr-del" data-id="${t.id}">刪</button></td></tr>`).join("") + "</table>"
    : '<span class="muted small">尚無已平倉交易</span>';
}
async function trTableClick(e) {
  const cbtn = e.target.closest(".tr-close"), dbtn = e.target.closest(".tr-del");
  if (cbtn) {
    const p = parseFloat(prompt("出場價？") || ""); if (!p || p <= 0) return;
    const ds = (prompt("出場日（YYYY-MM-DD，留空＝今天）") || "").trim();
    const body = ds ? { exit_price: p, exit_date: ds } : { exit_price: p };
    const r = await fetch(`/api/trades/${cbtn.dataset.id}/close`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((x) => x.json());
    if (r.ok) renderTrades(r); else alert(r.error || "平倉失敗");
  } else if (dbtn) {
    if (!confirm("確定刪除這筆交易？（不可復原）")) return;
    const r = await fetch(`/api/trades/${dbtn.dataset.id}`, { method: "DELETE" }).then((x) => x.json());
    if (r.ok) renderTrades(r); else alert(r.error || "刪除失敗");
  }
}

// ========== 操盤手（多操盤手，通用區塊渲染） ==========
let traderList = [], currentTrader = null;
async function loadTraders() {
  if (!$("trader-tabs")) return;
  try {
    const d = await getJSON("/api/traders");
    traderList = d.traders || [];
    $("trader-tabs").innerHTML = traderList.map((t) =>
      `<button class="tf trader-tab" data-tid="${esc(t.id)}">${esc(t.emoji || "")} ${esc(t.name)}</button>`).join("");
    $("trader-tabs").querySelectorAll(".trader-tab").forEach((b) =>
      b.addEventListener("click", () => selectTrader(b.dataset.tid)));
    const first = (traderList.find((t) => t.id === currentTrader) || traderList[0] || {}).id;
    if (first) selectTrader(first);
    else $("trader-sections").innerHTML = '<div class="muted small">尚無操盤手</div>';
  } catch (e) {
    $("trader-sections").innerHTML = `<span class="muted small">載入失敗: ${esc(e.message)}</span>`;
  }
}
async function selectTrader(tid) {
  currentTrader = tid;
  $("trader-tabs").querySelectorAll(".trader-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tid === tid));
  $("trader-sections").innerHTML = '<div class="muted small">載入中…</div>';
  try {
    renderTrader(await getJSON(`/api/traders/${encodeURIComponent(tid)}`));
  } catch (e) {
    $("trader-sections").innerHTML = `<span class="muted small">載入失敗: ${esc(e.message)}</span>`;
  }
}
function renderTrader(d) {
  $("trader-date").textContent = d.date ? `資料日期 ${d.date}` : "";
  $("trader-tagline").textContent = d.tagline || "";
  $("trader-sections").innerHTML = (d.sections || []).map(renderTraderSection).join("");
  $("trader-disclaimer").innerHTML = d.disclaimer ? `⚠️ ${esc(d.disclaimer)}` : "";
}
const TRADER_MARK = { bull: ["▲ 偏多", "var(--up)"], bear: ["▼ 偏空", "var(--down)"],
  warn: ["⚠ 留意", "#e0a23c"], neutral: ["● 中性", "#8a94a3"], na: ["— 無資料", "#666"] };
function traderCell(row, col) {
  const v = row[col.key];
  if (col.kind === "stock") return stockLink(row.code, row.name);
  if (col.kind === "lan") return lanCell(v);
  if (col.kind === "num") return fmt(v);
  if (col.kind === "num1") return fmt(v, 1);
  if (col.kind === "num2") return fmt(v, 2);
  return esc(v == null ? "—" : String(v));
}
function renderTraderSection(s) {
  const head = s.title
    ? `<div class="ai-head"><span>${esc(s.title)}</span>${s.note ? `<span class="muted small">${esc(s.note)}</span>` : ""}</div>`
    : "";
  if (s.type === "checklist") {
    const body = (s.items || []).map((it) => {
      const [label, color] = TRADER_MARK[it.status] || TRADER_MARK.na;
      return `<tr><td>${esc(it.name)}</td><td style="color:${color};white-space:nowrap"><b>${label}</b></td>` +
             `<td>${esc(it.value == null ? "—" : String(it.value))}</td><td class="muted">${esc(it.note || "")}</td></tr>`;
    }).join("");
    return head + `<div class="table-wrap"><table><thead><tr><th>檢核項</th><th>判定</th><th>數值</th><th>說明</th></tr></thead><tbody>${body}</tbody></table></div>`;
  }
  if (s.type === "table") {
    if (!s.rows || !s.rows.length) return head + `<div class="muted small">${esc(s.empty || "無資料")}</div>`;
    const th = (s.columns || []).map((c) => `<th>${esc(c.label)}</th>`).join("");
    const tr = s.rows.map((r) => `<tr>${(s.columns || []).map((c) => `<td>${traderCell(r, c)}</td>`).join("")}</tr>`).join("");
    return head + `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table></div>`;
  }
  if (s.type === "routine") {
    const groups = (s.groups || []).map((g) =>
      `<b>${esc(g.label)}</b><br/>${(g.items || []).map((i) => `・${esc(i)}`).join("<br/>")}`).join("<br/>");
    return head + `<div class="wave-help" style="display:block">${groups}</div>`;
  }
  if (s.type === "note") return head + `<div class="wave-help" style="display:block">${esc(s.text || "")}</div>`;
  return "";
}

// 「訊號追蹤」頁已收掉（signal_ledger 背景記錄照跑，見 CLAUDE.md ledger.py 條目）

async function loadSettings() {
  try {
    const s = await getJSON("/api/settings");
    $("set-schedule").value = s.schedule_time || "";
    $("set-datadir").value = s.data_dir || "";
    $("set-sched-status").textContent = s.scheduler_running ? "（排程執行中）" : "（排程未啟用；用 啟動.bat 會自動啟用）";
    const g = $("set-gemini");
    g.textContent = s.gemini_configured ? "已設定 ✓" : "未設定";
    g.className = "set-badge " + (s.gemini_configured ? "ok" : "no");
    const ln = $("set-line");
    // webhook 已設定＝可直接傳訊息給 OA 查詢（reply 不計入每月免費額度）
    ln.textContent = s.line_configured
      ? `已設定 ✓（速報 ${s.line_push_time}・完整版 ${s.schedule_time}・週報 週六 ${s.weekly_push_time}）`
        + (s.line_webhook_configured ? "・查詢 webhook ✓" : "・查詢 webhook 未設定")
      : "未設定";
    ln.className = "set-badge " + (s.line_configured ? "ok" : "no");
    $("set-picks-only").checked = !!s.intraday_picks_only;
    $("set-loss-tol").value = s.loss_tolerance || "";
    $("set-stats").innerHTML = [
      ["快照天數", s.snapshots], ["台指期歷史天數", s.tx_history_days], ["最新大盤日期", s.last_market_date || "—"],
    ].map(([k, v]) => `<div class="stat"><div class="stat-k">${k}</div><div class="stat-v">${v}</div></div>`).join("");
  } catch (e) { /* 忽略 */ }
}

async function saveSettings() {
  $("set-saved").textContent = "儲存中…";
  try {
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schedule_time: $("set-schedule").value, data_dir: $("set-datadir").value, intraday_picks_only: $("set-picks-only").checked, loss_tolerance: parseInt($("set-loss-tol").value, 10) || 0 }) });
    $("set-saved").textContent = "已儲存 ✓"; setTimeout(() => { $("set-saved").textContent = ""; }, 2000);
    loadSettings();
  } catch (e) { $("set-saved").textContent = "儲存失敗：" + e.message; }
}

// ========== 總覽：指標卡 ==========
const pctOf = (val, chg) => (val != null && chg != null && (val - chg) ? chg / (val - chg) * 100 : null);
// 由「現值 + 漲跌%」回推漲跌點數（國際指數/VIX 只回傳%，據此換算點數顯示）
const chgPts = (val, pct) => (val != null && pct != null && (100 + pct) !== 0) ? val * pct / (100 + pct) : null;
function pctTag(pct) { return pct == null ? "" : ` (${pct > 0 ? "+" : ""}${fmt(pct, 2)}%)`; }
// 日對日漲跌與百分比（同號才給 %，避免比率/部位翻號時百分比失真）
function dod(cur, prev) {
  if (cur == null || prev == null) return { chg: null, pct: null };
  const chg = cur - prev;
  const pct = (prev !== 0 && Math.sign(prev) === Math.sign(cur)) ? chg / Math.abs(prev) * 100 : null;
  return { chg, pct };
}
// 位階條：一條細軌 + 目前值所在的百分位刻度。tooltip 誠實標示樣本數，因為各欄位的
// 有效天數差很多（融資維持率遠少於散戶多空比），不標的話會讓人以為都是同一個基準。
function railHtml(rk) {
  if (!rk) return "";
  return `<div class="card-rail" title="近 ${rk.n} 日位階 ${rk.p}%（${rk.n} 筆有效資料）">`
    + `<i style="left:${rk.p}%"></i></div>`;
}
// 卡片外殼：把 alert（琥珀外框）與位階條收在一處，五個卡片建構式共用。
function cardWrap(inner, title, rk, alert) {
  const attr = title ? ` title="${esc(title)}"` : "";
  return `<div class="card${alert ? " alert" : ""}"${attr}>${inner}${railHtml(rk)}</div>`;
}
// 修飾語（淨多/淨空/散戶偏多）獨立成一行小字。原本它跟數字同為 26px 擠在 .card-val 裡，
// 175px 的卡片放不下就把單位「口」擠到第二行；而且真正的讀數是數字，修飾語只是標籤，
// 兩者本就不該同一級。
function qual(text) { return text ? `<div class="card-qual">${text}</div>` : ""; }
// 補充資訊（金額、相對兩平…）。放在漲跌之後，不與主數值爭位置。
function note(text) { return text ? `<div class="card-note">${text}</div>` : ""; }
// 帶修飾語的數值卡（多空比）：標籤一行、數字一行，不讓兩者擠在同一級
function qualCard(label, q, value, chg, pct, rk = null, alert = false) {
  const sub = chg == null ? ""
    : `<div class="card-chg ${chgClass(chg)}">${chgText(chg)}${pctTag(pct)}</div>`;
  return cardWrap(`<div class="card-label">${label}</div>${qual(q)}`
    + `<div class="card-val">${value}</div>${sub}`, "", rk, alert);
}
// label, value, chg(可空), pct(可空), unit；rk=位階物件（可空），alert=是否標為異常讀數
function card(label, value, chg, pct, unit = "", title = "", rk = null, alert = false, extra = "") {
  let sub = "";
  if (chg !== undefined && chg !== null) sub = `<div class="card-chg ${chgClass(chg)}">${chgText(chg)}${pctTag(pct)}</div>`;
  else if (pct !== undefined && pct !== null) sub = `<div class="card-chg ${chgClass(pct)}">${pct > 0 ? "▲" : pct < 0 ? "▼" : ""}${fmt(Math.abs(pct), 2)}%</div>`;
  return cardWrap(`<div class="card-label">${label}</div><div class="card-val">${value}${unit}</div>${sub}${note(extra)}`, title, rk, alert);
}
// 未平倉口數卡：依淨多/淨空上色（紅多綠空），附「較昨日」增減口數與百分比
function oiCard(label, v, prev, rk = null, alert = false) {
  if (v === null || v === undefined) return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const q = v > 0 ? "淨多" : v < 0 ? "淨空" : "";
  const head = `${fmt(Math.abs(v), 0)}<span class="card-unit">口</span>`;
  const { chg, pct } = dod(v, prev);
  const sub = chg == null ? "" : `<div class="card-chg ${chgClass(chg)}">較昨 ${chg > 0 ? "+" : ""}${fmt(chg, 0)} 口${pctTag(pct)}</div>`;
  return cardWrap(`<div class="card-label">${label}</div>${qual(q)}<div class="card-val ${cls}">${head}</div>${sub}`, "", rk, alert);
}
// 買賣超/淨額卡：當日淨流量，數值依正負上色，附「較昨日」增減金額
// （淨流量基數會翻號、趨近 0，算百分比會失真，故只給金額增減、不給 %）
function flowCard(label, v, prev, unit = "", rk = null, alert = false) {
  if (v === null || v === undefined) return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  const chg = prev == null ? null : v - prev;
  const sub = chg == null ? "" : `<div class="card-chg ${chgClass(chg)}">較昨 ${chg > 0 ? "+" : ""}${fmt(chg)}${unit}</div>`;
  return cardWrap(`<div class="card-label">${label}</div><div class="card-val ${chgClass(v)}">${fmt(v)}${unit}</div>${sub}`, "", rk, alert);
}
// 餘額卡（融資/融券）：當日尚未公布（晚間才出）時，退而顯示最近一筆有資料的交易日，並標註日期
// amtKey＝該餘額對應的金額欄；est=true 代表那是我們用現價估的，不是官方數字（融券沒有官方金額）
function balanceCard(label, srcRow, curDate, balKey, chgKey, hist = [], amtKey = "", est = false) {
  if (!srcRow || srcRow[balKey] === null || srcRow[balKey] === undefined) {
    return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  }
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const rk = pctile(hist, balKey, srcRow[balKey]);
  const amt = amtKey ? srcRow[amtKey] : null;
  const extra = amt == null ? ""
    : `${est ? "市值" : "金額"} ${fmt(amt, 1)} 億${est ? "（估）" : ""}`;
  return card(lbl, fmt(srcRow[balKey], 0), srcRow[chgKey], pctOf(srcRow[balKey], srcRow[chgKey]),
    "", "", rk, isAlert(balKey, srcRow[balKey], rk), extra);
}
// 融資維持率卡：DB 未存官方逐日漲跌（不像融資/融券有 margin_chg/short_chg 現成值），
// 故從 hist 找 srcRow 當日之前最近一筆有值的交易日自行算較昨——比較基準是 srcRow 自己的日期，
// 而非「今天」，避免資料延遲時把「vs 6 天前」誤標成「較昨」
// 兩個市場各一張。融資成數不同（上市 60%／上櫃 50%）→ 損益兩平線 166.7% vs 200%，
// 所以原始數字看起來接近時意義可能相反（今日：上市 180.1% 獲利、上櫃 166.8% 套牢）。
// 副標放「相對兩平 ±X%」才是兩張卡之間唯一可比的量；兩平線與追繳線由後端 bands 供給。
function maintTip(even, call, mv, sv, amt, est) {
  return `整戶擔保維持率＝(融資市值＋融券擔保品＋融券保證金)÷(融資金額＋融券市值)。`
    + `\n損益兩平 ${even}%（剛融資買進、價格未動的水準，由融資成數推得）；`
    + `低於 ${call}% 會被追繳、限期未補即斷頭。`
    + (mv != null ? `\n本日 融資市值 ${fmt(mv, 0)} 億 ÷ 融資金額 ${fmt(amt, 0)} 億` : "")
    + `\n註：成數為一般股票標準值，警示股／處置股更低，故兩平線為近似；`
    + `各家計算口徑不同（是否含 ETF 等），與外部數字不可直接對照。`;
}
function marginMaintCard(hist, srcRow, curDate, opts) {
  const { label, col, mvCol, amtCol } = opts;
  const band = (lastBands[col] || {});
  const even = band.breakeven, call = band.call;
  if (!srcRow || srcRow[col] === null || srcRow[col] === undefined) {
    return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  }
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const idx = hist.findIndex((r) => r && r.date === srcRow.date);
  const priorRow = idx > 0
    ? [...hist.slice(0, idx)].reverse().find((r) => r && r[col] != null)
    : null;
  const chg = priorRow ? srcRow[col] - priorRow[col] : null;
  const v = srcRow[col];
  const rk = pctile(hist, col, v);
  const rel = even ? (v - even) / even * 100 : null;
  const extra = rel == null ? ""
    : `相對兩平 ${rel > 0 ? "+" : ""}${fmt(rel, 1)}%（${rel >= 0 ? "獲利" : "套牢"}）`;
  const tip = maintTip(even, call, srcRow[mvCol], srcRow[opts.svCol], srcRow[amtCol]);
  // 逼近追繳線或深度套牢＝值得看一眼（與 ss_trader 同向：低維持率是反指標，不是利空）
  const alert = call != null && (v < call * 1.08 || (rel != null && rel <= -20));
  return card(lbl, fmt(v, 1) + "%", chg, pctOf(v, chg), "", tip, rk, alert, extra);
}
// 市場內部儀表：指數（方向）＋三大法人（資金）。中間的漲跌家數由 loadBreadth 填 #breadth，
// 三者並列才看得出「指數持平但下跌家數遠多於上漲」這種內部背離。
function renderMarketStrip(m, total3) {
  const idx = $("ms-index"), inst = $("ms-inst");
  if (!idx || !inst) return;
  const pct = pctOf(m.taiex, m.taiex_chg);
  idx.innerHTML = `<span class="ms-label">加權指數</span>`
    + `<span class="ms-val ${chgClass(m.taiex_chg)}">${fmt(m.taiex)}</span>`
    + `<span class="ms-chg ${chgClass(m.taiex_chg)}">${chgText(m.taiex_chg)}${pctTag(pct)}</span>`;
  // 只放「合計」：外資/投信/自營的明細與較昨變化在下方卡片，不在此重複
  inst.innerHTML = `<div class="ms-i total"><div class="ms-i-k">三大法人合計</div>`
    + `<div class="ms-i-v ${chgClass(total3)}">${total3 == null ? "—" : fmt(total3) + " 億"}</div></div>`;
}

// 一句判讀：把「指數方向」與「內部廣度」的關係講出來，而不是把兩組數字並排讓人自己心算。
// 背離（指數與家數不同向）才是這一列存在的理由，也只有背離會套琥珀外框。
// 刻意只陳述觀察不給動作——「內部偏弱」可以，「偏空」不行，全站免責基調一致。
const VERDICT_GAP = 0.1;   // 家數差需達總家數 10% 才算背離，否則平盤日天天都在響
let lastBreadth = null;
function renderVerdict() {
  const el = $("ms-verdict");
  if (!el) return;
  const b = lastBreadth, chg = lastLatest && lastLatest.taiex_chg;
  if (!b || chg == null) { el.textContent = ""; el.className = "ms-verdict"; return; }
  const up = b.up || 0, flat = b.flat || 0, down = b.down || 0;
  const tot = up + flat + down;
  if (!tot) { el.textContent = ""; el.className = "ms-verdict"; return; }
  const gap = up - down;
  const wide = Math.abs(gap) >= VERDICT_GAP * tot;
  const idxUp = chg > 0, idxDown = chg < 0;
  let text, diverge = false;
  if (idxUp && gap < 0 && wide) {
    text = `指數收紅，但下跌家數多 ${fmt(-gap, 0)} 家 — 內部偏弱`; diverge = true;
  } else if (idxDown && gap > 0 && wide) {
    text = `指數收黑，但上漲家數多 ${fmt(gap, 0)} 家 — 內部偏強`; diverge = true;
  } else if (idxUp && gap > 0) {
    text = "指數與家數同步走強";
  } else if (idxDown && gap < 0) {
    text = "指數與家數同步走弱";
  } else {
    text = wide
      ? `指數持平，家數${gap > 0 ? "偏多" : "偏空"} ${fmt(Math.abs(gap), 0)} 家`
      : "指數與家數皆無明顯方向";
  }
  el.textContent = text;
  el.className = "ms-verdict" + (diverge ? " alert" : "");
}

// 某欄位在近 N 日的百分位。位階條只在樣本夠時才畫得有意義——n<15 的欄位（如目前
// 只有 7 筆有值的融資維持率）畫出來是雜訊，寧可不畫，改由固定門檻判定。
const RAIL_MIN_N = 15;
function pctile(hist, key, v) {
  if (v == null) return null;
  const a = hist.map((r) => r && r[key]).filter((x) => x != null);
  if (a.length < RAIL_MIN_N) return null;
  return { p: Math.round(a.filter((x) => x < v).length / a.length * 100), n: a.length };
}
// 異常＝跨過 ss_trader 的固定門檻，或位階落在頭尾 10%。兩者都套同一個琥珀外框。
function isAlert(key, v, rank) {
  const band = lastBands[key];
  if (band && v != null && (v <= band.low || v >= band.high)) return true;
  return !!rank && (rank.p >= 90 || rank.p <= 10);
}

function renderCards(m, prev = {}, hist = []) {
  if (!m || !m.date) { $("cards-tw").innerHTML = '<div class="muted">尚無大盤資料。</div>'; $("cards-fut").innerHTML = ""; $("cards-intl").innerHTML = ""; $("data-date").textContent = ""; return; }
  $("data-date").textContent = "資料日期：" + m.date;
  // retail_ls_mtx/tmf 是比率（如 0.139），介面一律以百分比呈現（13.9%），避免讀成「0.139 倍」
  const pct100 = (v) => (v == null ? null : v * 100);
  // 多空比同樣把「散戶偏多/偏空」降成小字標籤，26px 只留給數字（見 qual 的說明）
  const lsQual = (v) => (v == null ? "" : v > 0 ? "散戶偏多" : v < 0 ? "散戶偏空" : "");
  const ls = (v) => (v === null || v === undefined ? "—" : fmt(pct100(v), 1) + "%");
  const sum3 = (r) => [r.inst_foreign, r.inst_trust, r.inst_dealer].every((x) => x != null)
    ? r.inst_foreign + r.inst_trust + r.inst_dealer : null;
  const lsm = dod(pct100(m.retail_ls_mtx), pct100(prev.retail_ls_mtx));
  const lst = dod(pct100(m.retail_ls_tmf), pct100(prev.retail_ls_tmf));
  // 融資/融券：當日有就用當日，否則退到最近一筆有資料的交易日（晚間才公布的容錯）
  const marginRow = [...hist].reverse().find((r) => r && r.margin_balance != null) || m;
  // 上櫃走櫃買自己的發布時程，與上市未必同時到齊，故各自找最近一筆有值的列
  const otcRow = [...hist].reverse().find((r) => r && r.otc_margin_maintenance != null) || m;
  renderMarketStrip(m, sum3(m));   // 加權指數與三大法人合計移到頂端儀表，下方卡片不再重複
  // 位階：把「這個數字在近期分佈的哪裡」補上。同一欄位算一次，rk 與 alert 共用。
  const rank = (key, v) => pctile(hist, key, v === undefined ? m[key] : v);
  $("cards-tw").innerHTML = [
    flowCard("外資買賣超", m.inst_foreign, prev.inst_foreign, " 億", rank("inst_foreign"), isAlert("inst_foreign", m.inst_foreign, rank("inst_foreign"))),
    flowCard("投信買賣超", m.inst_trust, prev.inst_trust, " 億", rank("inst_trust"), isAlert("inst_trust", m.inst_trust, rank("inst_trust"))),
    flowCard("自營買賣超", m.inst_dealer, prev.inst_dealer, " 億", rank("inst_dealer"), isAlert("inst_dealer", m.inst_dealer, rank("inst_dealer"))),
    // 融資金額是官方數字；融券金額官方不發布，只能以現價估算，故標「估」
    balanceCard("融資餘額(張)", marginRow, m.date, "margin_balance", "margin_chg", hist, "margin_value"),
    balanceCard("融券餘額(張)", marginRow, m.date, "short_balance", "short_chg", hist, "short_mv", true),
    marginMaintCard(hist, marginRow, m.date, { label: "融資維持率（上市）", col: "margin_maintenance",
      mvCol: "margin_mv", svCol: "short_mv", amtCol: "margin_value" }),
    marginMaintCard(hist, otcRow, m.date, { label: "融資維持率（上櫃）", col: "otc_margin_maintenance",
      mvCol: "otc_margin_mv", svCol: "otc_short_mv", amtCol: "otc_margin_value" }),
  ].join("");
  $("cards-fut").innerHTML = [
    card("台指期", fmt(m.tx_price), m.tx_chg, pctOf(m.tx_price, m.tx_chg)),
    oiCard("外資台指淨未平倉", m.tx_foreign_oi, prev.tx_foreign_oi, rank("tx_foreign_oi"), isAlert("tx_foreign_oi", m.tx_foreign_oi, rank("tx_foreign_oi"))),
    oiCard("散戶小台淨未平倉", m.retail_oi_mtx, prev.retail_oi_mtx, rank("retail_oi_mtx"), isAlert("retail_oi_mtx", m.retail_oi_mtx, rank("retail_oi_mtx"))),
    qualCard("小台散戶多空比", lsQual(m.retail_ls_mtx), ls(m.retail_ls_mtx), lsm.chg, lsm.pct, rank("retail_ls_mtx"), isAlert("retail_ls_mtx", m.retail_ls_mtx, rank("retail_ls_mtx"))),
    qualCard("微台散戶多空比", lsQual(m.retail_ls_tmf), ls(m.retail_ls_tmf), lst.chg, lst.pct, rank("retail_ls_tmf"), isAlert("retail_ls_tmf", m.retail_ls_tmf, rank("retail_ls_tmf"))),
    card("VIX 恐慌指數", fmt(m.vix), chgPts(m.vix, m.vix_chg), m.vix_chg, "", "", rank("vix"), isAlert("vix", m.vix, rank("vix"))),
  ].join("");
  // 國際市場刻意不給位階條：價格的 45 日位階訊號薄弱，畫了只是裝飾，
  // 反而稀釋位階條在籌碼欄位上的意義。
  $("cards-intl").innerHTML = [
    card("費城半導體", fmt(m.sox), chgPts(m.sox, m.sox_chg), m.sox_chg),
    card("日經225", fmt(m.n225), chgPts(m.n225, m.n225_chg), m.n225_chg),
    card("韓股KOSPI", fmt(m.kospi), chgPts(m.kospi, m.kospi_chg), m.kospi_chg),
    card("黃金", fmt(m.gold), chgPts(m.gold, m.gold_chg), m.gold_chg),
    card("美元兌日圓", fmt(m.jpy, 2), null, m.jpy_chg),
    card("比特幣", fmt(m.btc), chgPts(m.btc, m.btc_chg), m.btc_chg),
  ].join("");
}

function renderStale(d) {
  const el = $("stale-note");
  if (!el) return;
  if (d && d.data_stale && d.latest && d.latest.date) {
    el.textContent = "⚠️ 官方盤後資料尚未釋出，目前顯示最近結算日 " + d.latest.date
      + "（今日 " + (d.today || "") + " 的盤後籌碼/期貨數據，官方 openapi 通常隔日早上才補上，屆時按「一鍵更新」或等晚間排程即會更新）";
    el.classList.remove("hidden");
  } else {
    el.classList.add("hidden");
  }
}

async function loadDashboard() {
  const d = await getJSON("/api/dashboard");
  const hist = d.history || [];
  const prev = hist.length >= 2 ? hist[hist.length - 2] : {};
  // lastBands 供 renderCards 判定「異常讀數」，必須在它之前設好
  lastHistory = hist; lastLatest = d.latest || null; lastBands = d.bands || {};
  renderCards(d.latest, prev, hist);
  renderVerdict();               // 家數尚未載入時會自行留白，loadBreadth 完再補畫一次
  loadChipTrend();
  renderStale(d);
  if (d.latest && d.latest.updated_at) $("last-updated").textContent = "更新：" + d.latest.updated_at.replace("T", " ").slice(0, 19);
  return d;
}

// 依漲跌幅在「中性深色 → 紅(漲)／綠(跌)」間插值；漲跌越大顏色越飽和。
function _hex(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [ar, ag, ab] = p(a), [br, bg, bb] = p(b);
  const c = (x, y) => Math.round(x + (y - x) * t).toString(16).padStart(2, "0");
  return "#" + c(ar, br) + c(ag, bg) + c(ab, bb);
}
// 這些色塊上面疊白字（權值股卡、熱力圖），所以插值終點用 --up-fill/--down-fill 而非
// --up/--down——後者為了小字對比調亮過，拿來當底會把白字壓到 3:1 以下。
function sectorColor(chg) {
  if (chg == null) return "#2b3038";
  const t = 0.35 + 0.65 * Math.min(Math.abs(chg) / 3, 1); // 小漲跌也看得出方向
  return _hex("#2b3038", chg >= 0 ? C.upFill : C.downFill, t);
}

// 台股漲跌家數：紅漲綠跌的市場氣氛長條 + 漲停/跌停家數
async function loadBreadth() {
  const el = $("breadth"); if (!el) return;
  const note = $("breadth-note");
  try {
    const d = await getJSON("/api/breadth");
    if (d.up == null && d.down == null) { el.innerHTML = ""; if (note) note.textContent = ""; lastBreadth = null; renderVerdict(); return; }
    lastBreadth = d; renderVerdict();
    const up = d.up || 0, flat = d.flat || 0, down = d.down || 0, tot = up + flat + down || 1;
    const w = (n) => (n / tot * 100).toFixed(1) + "%";
    if (note) note.textContent = `（${d.date}）`;
    const ul = d.up_limit ? `<span class="bd-lim up">漲停 ${d.up_limit}</span>` : "";
    const dl = d.down_limit ? `<span class="bd-lim down">跌停 ${d.down_limit}</span>` : "";
    el.innerHTML = `
      <div class="breadth-nums">
        <span class="up">▲ 上漲 ${fmt(up, 0)}</span>${ul}
        <span class="flat">－ 平盤 ${fmt(flat, 0)}</span>
        <span class="down">▼ 下跌 ${fmt(down, 0)}</span>${dl}
      </div>
      <div class="breadth-bar">
        <div class="seg up" style="width:${w(up)}" title="上漲 ${fmt(up, 0)}"></div>
        <div class="seg flat" style="width:${w(flat)}" title="平盤 ${fmt(flat, 0)}"></div>
        <div class="seg down" style="width:${w(down)}" title="下跌 ${fmt(down, 0)}"></div>
      </div>`;
  } catch (e) { el.innerHTML = ""; lastBreadth = null; renderVerdict(); }
}

// 亞當杯柄型態選股：清單 + K 線疊「趨勢線(左緣→右緣)＋壓力線(右緣水平)」
function cupChartOption(d, m) {
  const closes = d.candles.map((c) => c[1]);
  const maSeries = MA_DEFS.map((x) => ({ name: "MA" + x.n, type: "line", data: ma(closes, x.n),
    smooth: true, showSymbol: false, lineStyle: { width: 1, color: x.color } }));
  const lastDate = d.dates[d.dates.length - 1];
  const candle = {
    name: "K線", type: "candlestick", data: d.candles,
    itemStyle: { color: C.up, color0: C.down, borderColor: C.up, borderColor0: C.down },
    markLine: {
      symbol: ["none", "none"],
      // 標籤放線段中段（非末端）：末端貼右緣會被 grid 裁切破版；中段有留白、也不會
      // 跟右緣 pin 疊在一起（右緣點＝趨勢線終點＝壓力線起點，三者同座標）
      label: { show: true, position: "middle", color: "#fff", fontSize: 11,
               backgroundColor: "rgba(0,0,0,0.55)", padding: [2, 4], borderRadius: 3 },
      data: [
        // 趨勢線不標字（左右緣已有 pin），避免與右緣 pin 疊字
        [{ coord: [m.left_date, m.left_price], lineStyle: { color: C.accent, width: 2 }, label: { show: false } },
         { coord: [m.right_date, m.right_price] }],
        [{ name: `壓力 ${fmt(m.resistance, 2)}`, coord: [m.right_date, m.resistance],
          lineStyle: { color: C.info, width: 2, type: "dashed" } },
         { coord: [lastDate, m.resistance] }],
      ],
    },
    markPoint: {
      // 圖釘原本跟趨勢線同橘色、融進線裡不明顯：改亮黃＋白色描邊讓圖釘從線上「跳出來」，
      // 並用 symbolOffset 把圖釘往上提，避開與趨勢線／K棒交叉處的視覺重疊。
      symbol: "pin", symbolSize: 40, symbolOffset: [0, -10],
      itemStyle: { color: PIN_YELLOW, borderColor: "#fff", borderWidth: 1.5,
                   shadowColor: "rgba(0,0,0,0.5)", shadowBlur: 4 },
      label: { color: "#1a1a1a", fontSize: 12, fontWeight: 700, formatter: (p) => p.data.value },
      data: [{ value: "左緣", coord: [m.left_date, m.left_price] },
             { value: "右緣", coord: [m.right_date, m.right_price] }],
    },
  };
  if (m.stop_loss != null && m.stop_loss > 0)  // 停損線＝突破價−2×ATR14（部位管理，見下方說明）
    candle.markLine.data.push(
      [{ name: `停損 ${fmt(m.stop_loss, 2)}`, coord: [m.right_date, m.stop_loss],
        lineStyle: { color: C.up, width: 2, type: "dashed" } },
       { coord: [lastDate, m.stop_loss] }]);
  return {
    textStyle: { fontFamily: HM_FONT },
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { top: 0, data: ["K線", ...MA_DEFS.map((x) => "MA" + x.n)], textStyle: { color: C.label } },
    grid: { left: 55, right: 30, top: 30, bottom: 50 },
    xAxis: { type: "category", data: d.dates, axisLabel: { color: C.muted } },
    yAxis: { scale: true, axisLabel: { color: C.muted }, splitLine: { lineStyle: { color: C.border } } },
    dataZoom: [{ type: "inside", start: 35 }, { type: "slider", start: 35, bottom: 0, height: 16 }],
    series: [candle, ...maSeries],
  };
}
async function drawCupChart(m) {
  const el = $("cup-chart"); if (!el) return;
  if (!cupChart || cupChart.getDom() !== el) cupChart = echarts.init(el);
  cupChart.showLoading();
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(m.code)}/ohlc?bars=400`);
    cupChart.hideLoading();
    if (!d.candles || !d.candles.length) { cupChart.clear(); return; }
    cupChart.setOption(cupChartOption(d, m), true);
  } catch (e) { cupChart.hideLoading(); }
}
let cupData = null, cupPicksOnly = false, cupMinR = 70;
async function loadCupHandle() {
  const list = $("cup-list"); if (!list) return;
  list.innerHTML = '<span class="muted small">篩選中…</span>';
  try {
    cupData = await getJSON(`/api/patterns/cup-handle?min_r=${cupMinR}`);
    cupLoaded = true;
    renderCupChips();
  } catch (e) { list.innerHTML = '<span class="muted small">載入失敗</span>'; }
}
function renderCupChips() {
  const list = $("cup-list"), note = $("cup-note"); if (!list || !cupData) return;
  const d = cupData;
  if (d.note) { list.innerHTML = `<span class="muted small">${esc(d.note)}</span>`; if (note) note.textContent = ""; return; }
  const all = d.stocks || [];
  cupMatches = cupPicksOnly ? all.filter((m) => m.in_picks) : all;
  if (note) note.textContent = `（${d.date}　%R≥${d.min_r ?? cupMinR}　符合 ${d.count} 檔`
    + (d.has_picks ? `／同時符合籌碼基本 ${d.picks_count} 檔` : "") + `／掃描 ${d.bars} 根）`;
  if (cupPicksOnly && !d.has_picks) { list.innerHTML = '<span class="muted small">尚未載入當日 CSV，無「籌碼/基本選股」可交集（請先到該分頁上傳）</span>'; if (cupChart) cupChart.clear(); renderCupRisk(null); return; }
  if (!cupMatches.length) { list.innerHTML = `<span class="muted small">${cupPicksOnly ? "無同時符合兩者的個股" : "今日無符合杯柄型態的個股"}</span>`; if (cupChart) cupChart.clear(); renderCupRisk(null); return; }
  list.innerHTML = cupMatches.map((m, i) => {
    const tip = `杯深 ${fmt(m.cup_depth_pct, 1)}%・距壓力 ${fmt(m.dist_pct, 1)}%`;
    return `<a href="#" class="cup-chip${i === 0 ? " active" : ""}${m.in_picks ? " pick" : ""}" data-i="${i}" title="${tip}">${esc(m.code)} ${esc(m.name || "")}<span class="cup-r">%R ${fmt(m.percent_r, 0)}</span></a>`;
  }).join("");
  drawCupChart(cupMatches[0]);
  renderCupRisk(cupMatches[0]);
}

// 部位管理（#5）：選中個股的停損/建議部位資訊列。
// 建議股數＝可容忍虧損 ÷ 每股風險（突破價−停損價＝2×ATR14）——高價股自動買少、
// 低價股自動買多，單筆最壞虧損固定；公式與限制見頁面下方「停損與建議部位」說明。
function renderCupRisk(m) {
  const el = $("cup-risk"); if (!el) return;
  if (!m || m.stop_loss == null) { el.innerHTML = ""; return; }
  const risk = m.resistance - m.stop_loss;
  let txt = `🛡️ ${esc(m.name || m.code)} 建議停損 <b>${fmt(m.stop_loss, 2)}</b>`
    + `（突破價 ${fmt(m.resistance, 2)} − 2×ATR ${fmt(m.atr, 2)}）`;
  const tol = cupData && cupData.loss_tolerance;
  if (tol && risk > 0) {
    const sh = Math.floor(tol / risk);
    const lots = Math.floor(sh / 1000), odd = sh % 1000;
    const pos = lots ? `${lots} 張${odd ? ` + ${odd} 股` : ""}` : `${odd} 股`;
    txt += `　💰 建議部位 <b>${pos}</b>（可容忍虧損 ${fmt(tol, 0)} 元 ÷ 每股風險 ${fmt(risk, 2)} 元）`;
  } else {
    txt += `　<span class="muted">到「設定」填「單筆可容忍虧損」即自動算建議部位</span>`;
  }
  el.innerHTML = txt;
}

// 杯柄訊號回測報告：突破率、各持有期勝率/平均報酬、近期交易明細
let cupBtLoaded = false;
async function loadCupBacktest() {
  const box = $("cup-bt"); if (!box) return;
  box.innerHTML = '<span class="muted small">回測計算中…（掃全市場歷史，首次約 30–90 秒）</span>';
  try {
    const d = await getJSON("/api/patterns/cup-handle/backtest");
    cupBtLoaded = true;
    if (d.note) { box.innerHTML = `<span class="muted small">${esc(d.note)}</span>`; return; }
    const h = d.horizons || {};
    const stat = (k, v) => `<div class="stat"><div class="stat-k">${k}</div><div class="stat-v">${v}</div></div>`;
    const hz = (n) => h[n] ? `${fmt(h[n].win_rate, 1)}%勝／均${h[n].avg >= 0 ? "+" : ""}${fmt(h[n].avg, 2)}%` : "—";
    const rows = (d.trades || []).slice(0, 15).map((t) =>
      `<tr><td>${stockLink(t.code, t.name)}</td><td>${t.entry_date}</td><td style="text-align:right">${fmt(t.entry, 2)}</td>` +
      ["ret5", "ret10", "ret20"].map((k) => { const v = t[k]; return `<td style="text-align:right" class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v == null ? "—" : (v > 0 ? "+" : "") + fmt(v, 1) + "%"}</td>`; }).join("") + "</tr>").join("");
    box.innerHTML =
      `<div class="stats-grid" style="max-width:none">` +
      stat("訊號次數", d.signals) + stat("突破進場", `${d.trades_n}（${fmt(d.breakout_rate, 1)}%）`) +
      stat("持有5日", hz("5")) + stat("持有10日", hz("10")) + stat("持有20日", hz("20")) + `</div>` +
      `<div class="muted small" style="margin:6px 0">假設突破日收盤進場；未含手續費/稅/滑價（來回約0.6%）；樣本不含已下市股（存活者偏差）；歷史不代表未來。資料 ${d.bars} 天（${d.date} 止）。</div>` +
      (rows ? `<div class="table-wrap" style="max-height:38vh"><table><tr><th>股票</th><th>進場日</th><th style="text-align:right">進場價</th><th style="text-align:right">+5日</th><th style="text-align:right">+10日</th><th style="text-align:right">+20日</th></tr>${rows}</table></div>` : "");
  } catch (e) { box.innerHTML = '<span class="muted small">回測載入失敗：' + esc(e.message) + "</span>"; }
}

// 海期監控：五大分類色階卡片（名稱/價格上排、漲跌%/點數下排）
function osDecimals(v) { const a = Math.abs(v); return a >= 1000 ? 0 : a >= 10 ? 2 : 4; }
async function loadOsFutures(mode) {
  const el = $("osfut"); if (!el) return;
  if (!el.innerHTML) el.innerHTML = '<div class="muted small">載入報價中…（首次抓取約 5–10 秒）</div>';
  try {
    // "live"＝盤中 meta 報價（後端 90 秒 TTL）；true＝強制重抓日線（排程用，前端已不用）
    const q = mode === "live" ? "?live=1" : mode ? "?refresh=1" : "";
    const d = await getJSON("/api/os-futures" + q);
    const t = $("osfut-time");
    if (t && d.updated_at) t.textContent = "更新：" + d.updated_at.slice(0, 16).replace("T", " ");
    if (!d.categories || !d.categories.every) { el.innerHTML = '<div class="muted small">暫無報價，稍後按更新重試</div>'; return; }
    el.innerHTML = d.categories.filter((g) => g.items.length).map((g) => {
      const cards = g.items.map((it) => {
        const dp = osDecimals(it.value);
        const ps = it.chg_pct == null ? "" : (it.chg_pct >= 0 ? "+" : "") + fmt(it.chg_pct, 2) + "%";
        const cs = it.chg == null ? "" : (it.chg >= 0 ? "+" : "") + fmt(it.chg, dp);
        const tm = it.time ? `<span class="of-time">${esc(it.time)}</span>` : "";
        return `<div class="mv-card" style="background:${sectorColor(it.chg_pct)}">
          <div class="of-top"><span class="of-name">${esc(it.name)}</span><span class="of-price">${fmt(it.value, dp)}</span></div>
          <div class="of-bot"><span>${ps}</span><span>${cs}${tm}</span></div>
        </div>`;
      }).join("");
      return `<div class="card-group"><div class="group-title">${esc(g.category)}</div><div class="mv-grid">${cards}</div></div>`;
    }).join("");
    if (!el.innerHTML) el.innerHTML = '<div class="muted small">暫無報價，稍後按更新重試</div>';
  } catch (e) { el.innerHTML = '<div class="muted small">載入失敗：' + esc(e.message) + "</div>"; }
}

// ===== 台股高價股即時排行（MIS）＝高價股監控頁 =====
let rankMarket = "all", osfutTimer = null, rankTimer = null;

// 成交額一律以「億」呈現：個股單日動輒數十億，元或萬都得數零
function yi(v, dp) { return v == null ? "—" : fmt(v / 1e8, dp == null ? 1 : dp); }

async function loadRankPrice() {
  const el = $("rankprice"); if (!el) return;
  try {
    const d = await getJSON(`/api/rank/price?market=${rankMarket}&n=30`);
    const note = $("rankprice-note");
    if (note) {
      const anyLive = (d.items || []).some((i) => i.time);
      const base = d.prev_date ? `　量額比較基準 ${d.prev_date}` : "";
      note.textContent = (anyLive ? `（證交所即時，${d.fetched_at ? d.fetched_at.slice(11, 16) : ""} 更新` : "（收盤價") + base + "）";
    }
    if (!d.items || !d.items.length) { el.innerHTML = '<div class="muted">尚無資料（需先跑過 OHLC 回補）</div>'; return; }
    const head = "<tr><th>#</th><th>股票</th><th class=\"num\">成交價</th><th class=\"num\">漲跌</th>" +
      "<th class=\"num\">漲跌%</th><th class=\"num\">成交量(張)</th><th class=\"num\">成交額(億)</th>" +
      "<th class=\"num\">成交額增減</th><th class=\"num\">時間</th></tr>";
    const body = d.items.map((it, i) => {
      const cls = it.chg > 0 ? "up" : it.chg < 0 ? "down" : "flat";
      // 盤中官方成交金額尚未發布 → 後端用 量×現價 估算，標「~」並在 tooltip 說明
      const amt = it.amount == null ? "—"
        : yi(it.amount) + (it.amount_est ? '<span class="muted" title="盤中估算：成交量×現價（官方成交金額收盤後才發布）">~</span>' : "");
      const acls = it.amount_chg > 0 ? "up" : it.amount_chg < 0 ? "down" : "flat";
      const ach = it.amount_chg == null ? "—"
        : `${it.amount_chg > 0 ? "+" : ""}${yi(it.amount_chg)} 億` +
          (it.amount_chg_pct == null ? "" : `（${it.amount_chg_pct > 0 ? "+" : ""}${fmt(it.amount_chg_pct, 1)}%）`);
      return `<tr><td>${i + 1}</td><td>${stockLink(it.code, it.name)}</td>` +
        `<td class="num">${fmt(it.price, 2)}</td>` +
        `<td class="num ${cls}">${it.chg == null ? "—" : (it.chg > 0 ? "+" : "") + fmt(it.chg, 2)}</td>` +
        `<td class="num ${cls}">${it.chg_pct == null ? "—" : (it.chg_pct > 0 ? "+" : "") + fmt(it.chg_pct, 2) + "%"}</td>` +
        `<td class="num">${it.vol == null ? "—" : fmt(it.vol, 0)}</td>` +
        `<td class="num">${amt}</td>` +
        `<td class="num ${acls}">${ach}</td>` +
        `<td class="num">${it.time ? esc(it.time) : "收盤"}</td></tr>`;
    }).join("");
    el.innerHTML = `<table>${head}${body}</table>`;
  } catch (e) { el.innerHTML = '<div class="muted">載入失敗：' + esc(e.message) + "</div>"; }
}
function startRankPolling() {                   // 台股每 10 秒（MIS 便宜，後端 TTL 8 秒）
  if (rankTimer) return;                        // 防重複註冊
  rankTimer = setInterval(loadRankPrice, 10000);
}
function stopRankPolling() {
  if (rankTimer) { clearInterval(rankTimer); rankTimer = null; }
}
function startOsfutPolling() {                  // 海期每 120 秒（Yahoo 較貴）
  if (osfutTimer) return;
  osfutTimer = setInterval(() => loadOsFutures("live"), 120000);
}
function stopOsfutPolling() {
  if (osfutTimer) { clearInterval(osfutTimer); osfutTimer = null; }
}

// 權值股貢獻大盤點數：色階卡片（依漲跌幅上色），主秀「貢獻幾點」
async function loadMovers() {
  const el = $("movers"); if (!el) return;
  const note = $("movers-note");
  try {
    const d = await getJSON("/api/index-movers?top=18");
    if (!d.movers || !d.movers.length) { el.innerHTML = ""; if (note) note.textContent = ""; return; }
    if (note) note.textContent = `（${d.date}　大盤 ${fmt(d.index, 0)} ${d.index_chg >= 0 ? "+" : ""}${fmt(d.index_chg, 2)} 點）`;
    el.innerHTML = d.movers.map((m) => {
      const cs = m.contribution >= 0 ? "+" : "";
      const ps = m.chg_pct >= 0 ? "+" : "";
      return `<div class="mv-card" style="background:${sectorColor(m.chg_pct)}" title="權重 ${fmt(m.weight, 2)}%">
        <div class="mv-name">${esc(m.name || m.code)}</div>
        <div class="mv-contrib">${cs}${fmt(m.contribution, 1)}<span class="mv-unit">點</span></div>
        <div class="mv-sub">${fmt(m.close, 1)}　${ps}${fmt(m.chg_pct, 2)}%</div>
      </div>`;
    }).join("");
  } catch (e) { el.innerHTML = ""; }
}

const HM_MIN_FS = 11;    // 中文可讀下限：低於此級數的方塊字是墨團，寧可留白也不硬塞
const HM_MAX_FS = 36;
const HM_INSET = 12;     // ECharts 標籤裁切寬度 ≈ 格寬固定內縮約 10px（非比例）

// 量測文字在 HM_FONT 下的實際寬度（每 1px 字級佔幾 px 寬）。用 canvas 實測取代字數估算——
// 中文全形、數字半形、「+」「%」各不相同，估算必然失準（曾把台積電漲跌截成「+1....」）。
let _hmCtx = null;
function textWidthPerPx(text) {
  if (!_hmCtx) _hmCtx = document.createElement("canvas").getContext("2d");
  _hmCtx.font = `700 100px ${HM_FONT}`;
  return _hmCtx.measureText(text).width / 100;
}

// 依 treemap 排版後每格的真實寬高，把字級調到剛好放得下（大格大字、小格小字）。
// 排版後才知道格子確切尺寸（面積相同、寬高比可能差很多），故此步在 setOption 之後執行。
function fitHeatmapFonts(data) {
  if (!sectorChart) return;
  const root = sectorChart.getModel().getSeriesByIndex(0).getData().tree.root;
  const labelByCode = {};
  const bandBySector = {};
  root.eachNode((n) => {
    // 產業標籤帶：窄的產業放不下「名稱　漲跌%」，退成只放名稱，再不行才縮字級
    if (n.children && n.children.length) {
      const lay = n.getLayout();
      if (!lay || !n.name) return;
      const avg = n.getModel().get("avg");
      const full = `${n.name}　${avg >= 0 ? "+" : ""}${fmt(avg, 1)}%`;
      const availW = lay.width - HM_INSET;
      const fitFull = availW / textWidthPerPx(full);
      const fitName = availW / textWidthPerPx(n.name);
      bandBySector[n.name] = fitFull >= 14
        ? { show: true, height: 22, fontSize: 14, formatter: full }
        : (fitName >= 12
            ? { show: true, height: 22, fontSize: Math.min(14, Math.floor(fitName)), formatter: n.name }
            : { show: true, height: 18, fontSize: 12, formatter: n.name });
      return;
    }
    let code, chg;
    try { code = n.getModel().get("code"); chg = n.getModel().get("chg"); } catch (e) { return; }
    if (!code) return;
    const lay = n.getLayout();
    if (!lay) return;
    const w = lay.width, h = lay.height, name = n.name || "";
    const pctStr = (chg >= 0 ? "+" : "") + fmt(chg, 1) + "%";
    const availW = w - HM_INSET;
    const fit = (t) => availW / textWidthPerPx(t);   // 這串文字剛好放得下的字級
    // 先試兩行（名稱＋漲跌）：寬度要兩行都放得下，高度要容兩行
    const fs2 = Math.floor(Math.min(HM_MAX_FS, fit(name), fit(pctStr), (h - 8) / 2.3));
    if (fs2 >= HM_MIN_FS) {
      labelByCode[code] = { show: true, fontSize: fs2, lineHeight: Math.round(fs2 * 1.08),
                            formatter: name + "\n" + pctStr };
      return;
    }
    // 放不下兩行 → 只顯示名稱一行（比整格空白好）；連名稱都放不下才隱藏
    const fs1 = Math.floor(Math.min(HM_MAX_FS, fit(name), (h - 4) / 1.2));
    labelByCode[code] = fs1 >= HM_MIN_FS
      ? { show: true, fontSize: fs1, lineHeight: Math.round(fs1 * 1.05), formatter: name }
      : { show: false };
  });
  const data2 = data.map((g) => ({
    ...g,
    upperLabel: bandBySector[g.name],
    children: g.children.map((c) => ({ ...c, label: labelByCode[c.code] || { show: false } })),
  }));
  sectorChart.setOption({ series: [{ data: data2 }] });  // 值不變→排版一致，僅套用調好的字級
}

async function loadSectors() {
  const el = $("sectors");
  if (!el) return;
  const note = $("sectors-note");
  try {
    const d = await getJSON(`/api/heatmap?market=${heatmapMarket}`);
    const groups = (d.groups || []).filter((g) => g.stocks && g.stocks.length);
    if (!groups.length) {
      el.classList.add("sectors");
      const hint = heatmapMarket === "otc" ? "尚無上櫃資料（櫃買報價待回補）" : "尚無個股資料";
      el.innerHTML = `<div class="muted small">${hint}</div>`; if (note) note.textContent = ""; return;
    }
    // 每個產業只留市值前 N 大（後端已依市值排序）。格數越少＝格子越大＝名稱字級越大，
    // 這是熱力圖可讀性最有效的槓桿；要密度可用「每類股檔數」切換。
    const TOP_PER_SECTOR = heatmapTop;
    const shownGroups = groups.map((g) => ({ ...g, stocks: g.stocks.slice(0, TOP_PER_SECTOR) }));
    const shownStocks = shownGroups.flatMap((g) => g.stocks);
    const up = shownStocks.filter((s) => s.chg_pct > 0).length;
    const down = shownStocks.filter((s) => s.chg_pct < 0).length;
    if (note) note.textContent = `（${d.date}　各產業市值前 ${TOP_PER_SECTOR} 大　面積＝市值、顏色＝漲跌幅，點格看 K 線）`;
    // 兩層 treemap：產業為群組（顯示產業名 + 平均漲跌），個股為色塊
    // 面積用市值平方根：台積電市值佔全市場逾四成，直接用市值會獨大到吃掉整張圖，
    // 平方根壓縮動態範圍後仍保留「大小＝市值高低」的順序，畫面才讀得清（tooltip 仍給真實市值）
    const area = (mc) => Math.sqrt(mc);
    el.classList.remove("sectors");
    el.style.height = "720px";   // 夠高，讓被擠到下方的小型類股格子也放得下名稱
    const data = shownGroups.map((g) => {
      const w = g.stocks.reduce((a, s) => a + s.mcap, 0) || 1;
      const avg = g.stocks.reduce((a, s) => a + (s.chg_pct || 0) * s.mcap, 0) / w;  // 市值加權平均漲跌
      return {
        name: g.sector, value: g.stocks.reduce((a, s) => a + area(s.mcap), 0), avg,
        mcap: g.stocks.reduce((a, s) => a + s.mcap, 0),
        children: g.stocks.map((s) => ({
          name: s.name, value: area(s.mcap), mcap: s.mcap, chg: s.chg_pct, code: s.code, sector: g.sector,
          itemStyle: { color: sectorColor(s.chg_pct) },
        })),
      };
    });
    if (!sectorChart || sectorChart.getDom() !== el) sectorChart = echarts.init(el);
    sectorChart.setOption({
      tooltip: {
        formatter: (p) => {
          if (p.data.code == null) {  // 產業群組
            const sign = p.data.avg >= 0 ? "+" : "";
            return `${esc(p.name)}<br/>市值加權漲跌 <b>${sign}${fmt(p.data.avg, 2)}%</b><br/>總市值 ${fmt(p.data.mcap, 0)} 億`;
          }
          const sign = p.data.chg >= 0 ? "+" : "";
          return `${esc(p.data.code)} ${esc(p.name)}<br/>漲跌 <b>${sign}${fmt(p.data.chg, 2)}%</b>`
            + `<br/>市值 ${fmt(p.data.mcap, 0)} 億　${esc(p.data.sector)}<br/><span style="color:#8a94a3">點擊看 K 線</span>`;
        },
      },
      series: [{
        type: "treemap", roam: false, nodeClick: false, animationDuration: 300,
        breadcrumb: { show: false },
        left: 1, right: 1, top: 1, bottom: 1,
        squareRatio: 1,           // 盡量讓格子接近正方形（而非瘦長條），名稱才放得下
        visibleMin: 8,            // 面積過小的個股併入群組留白，避免碎到看不清
        childrenVisibleMin: 60,
        // 父節點（產業）頂部標籤帶：須設在 series 層級才會套用到非葉節點
        upperLabel: {
          show: true, height: 22, color: C.label, fontSize: 14, fontWeight: 700, fontFamily: HM_FONT,
          formatter: (p) => `${esc(p.name)}　${p.data.avg >= 0 ? "+" : ""}${fmt(p.data.avg, 1)}%`,
        },
        levels: [
          {  // 產業群組：深色邊框，父層 itemStyle 是標籤帶底色
            itemStyle: { borderColor: "#0b0e13", borderWidth: 3, gapWidth: 3, color: "#171c24" },
          },
          {  // 個股色塊
            itemStyle: { borderColor: "#0f1419", borderWidth: 1, gapWidth: 1 },
          },
        ],
        // 字級由各節點自帶（label.fontSize，隨面積縮放）；此處只定共用樣式與文字。
        // fontFamily 必須明示：ECharts 預設用 sans-serif，會與 fitHeatmapFonts 的量測字型不一致，
        // 導致「量得下、畫出來卻被截」（曾把台達電漲跌截成「+0....」）。
        label: {
          show: true, overflow: "truncate", color: "#fff", fontWeight: 700, fontFamily: HM_FONT,
          textShadowColor: "rgba(0,0,0,0.55)", textShadowBlur: 3, textShadowOffsetY: 1,  // 白字浮起、更清楚
          formatter: (p) => {
            if (p.data.code == null) return "";  // 產業群組用 upperLabel，不在中間標字
            const sign = p.data.chg >= 0 ? "+" : "";
            return `${esc(p.name)}\n${sign}${fmt(p.data.chg, 1)}%`;
          },
        },
        data,
      }],
    }, true);
    sectorChart.off("click");
    sectorChart.on("click", (p) => {
      if (p && p.data && p.data.code) { showView("stock"); $("stock-input").value = p.data.code; loadStock(p.data.code, p.data.name); }
    });
    sectorChart.resize();
    // 第二遍：依實際格子尺寸把字級調到剛好放得下。ECharts 排版在 setOption/resize 後同步就緒，
    // 直接同步呼叫（勿用 rAF——背景分頁不觸發，會停在未調字級的狀態）
    lastHeatmapData = data;
    fitHeatmapFonts(data);
  } catch (e) { el.classList.add("sectors"); el.innerHTML = '<div class="muted small">熱力圖載入失敗</div>'; }
}

// ========== 期權情緒・大額交易人 ==========
async function loadOptionsSentiment() {
  const el = $("cards-opt");
  if (!el) return;
  try {
    const d = await getJSON("/api/options-sentiment");
    const p = d.pcr || {}, l = d.large || {};
    const note = $("opt-note"); if (note) note.textContent = (p.date || l.date) ? `（${p.date || l.date}）` : "";
    el.innerHTML = [
      card("P/C 未平倉比", fmt(p.pc_oi_ratio)),
      card("P/C 成交量比", fmt(p.pc_vol_ratio)),
      oiCard("前10大特定法人淨未平倉", l.top10_specific_net),
      oiCard("前5大特定法人淨未平倉", l.top5_specific_net),
    ].join("");
  } catch (e) { el.innerHTML = '<div class="muted small">載入失敗</div>'; }
}

// ========== 法人買賣超排行 ==========
async function loadInstRanking() {
  const buyEl = $("rank-buy"), sellEl = $("rank-sell");
  if (!buyEl) return;
  try {
    const d = await getJSON(`/api/inst-ranking?who=${rankWho}&unit=${rankUnit}&top=15`);
    const isVal = d.unit === "value";
    const note = $("rank-note"); if (note) note.textContent = d.date ? `（${d.date}，單位：${isVal ? "億元" : "張"}）` : "";
    const row = (x) => `<div class="rank-row">${stockLink(x.code, x.name)}<span class="${x.net > 0 ? "up" : x.net < 0 ? "down" : ""}">${x.net > 0 ? "+" : ""}${fmt(x.net, isVal ? 2 : 0)}${isVal ? " 億" : ""}</span></div>`;
    buyEl.innerHTML = d.buy && d.buy.length ? d.buy.map(row).join("") : '<div class="muted small">—</div>';
    sellEl.innerHTML = d.sell && d.sell.length ? d.sell.map(row).join("") : '<div class="muted small">—</div>';
  } catch (e) { buyEl.innerHTML = '<div class="muted small">載入失敗</div>'; }
}

// ========== 自選股 + 進出榜追蹤 ==========
async function loadWatchlist() {
  const el = $("watch-table");
  if (!el) return;
  try {
    const d = await getJSON("/api/watchlist");
    if (!d.stocks || !d.stocks.length) { el.innerHTML = '<div class="muted small">尚無自選股，輸入股號加入。</div>'; return; }
    const num = (v, d = 2) => `<td style="text-align:right">${v == null ? "—" : fmt(v, d)}</td>`;
    const rows = d.stocks.map((s) => {
      const onb = s.in_latest ? '<span class="status new">在榜</span>' : '<span class="status out">未在榜</span>';
      const ret = s.ret_pct == null ? "—" : `<span class="${s.ret_pct > 0 ? "up" : s.ret_pct < 0 ? "down" : ""}">${s.ret_pct > 0 ? "+" : ""}${fmt(s.ret_pct, 2)}%</span>`;
      const ch = s.chip || {};
      return `<tr><td>${stockLink(s.code, s.name)}</td><td>${onb}</td><td style="text-align:right">${s.times}</td><td>${s.entry_date || "—"}</td><td style="text-align:right">${ret}</td>` +
        num(ch.close) + num(ch.lan_value, 1) + num(ch.lpe, 1) + num(ch.est_profit) + num(ch.rev_yoy, 1) + num(ch.holder_drop_ratio) + num(ch.big_holder_ratio) +
        `<td><a href="#" class="watch-del err-text" data-code="${s.code}">移除</a></td></tr>`;
    }).join("");
    const rh = (t) => `<th style="text-align:right">${t}</th>`;
    el.innerHTML = `<table><tr><th>股票</th><th>今日選股榜</th>${rh("在榜次數")}<th>進榜日</th>${rh("自進榜報酬")}${rh("收盤")}${rh("蘭值")}${rh("本益比")}${rh("推估EPS")}${rh("營收年增%")}${rh("人數降比")}${rh("大戶增比")}<th></th></tr>${rows}</table>`;
  } catch (e) { el.innerHTML = '<div class="muted small">載入失敗</div>'; }
}
async function addWatch() {
  const v = ($("watch-input").value || "").trim();
  if (!v) return;
  $("watch-input").value = "";
  try { await fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: v }) }); } catch (e) { /* ignore */ }
  loadWatchlist();
}

// ========== 族群輪動 + 交叉選股 ==========
async function loadRotation() {
  const el = $("rotation");
  if (!el) return;
  try {
    const d = await getJSON("/api/sectors/rotation?days=5");
    if (!d.sectors || !d.sectors.length) { el.innerHTML = '<div class="muted small">尚無類股資料</div>'; return; }
    const note = $("rotation-note");
    if (note && d.dates.length) note.textContent = `（${d.dates[0].slice(5)} ～ ${d.dates[d.dates.length - 1].slice(5)}）`;
    const cell = (v) => v == null ? '<td class="muted" style="text-align:right">—</td>'
      : `<td class="${chgClass(v)}" style="text-align:right">${v > 0 ? "+" : ""}${fmt(v, 2)}</td>`;
    const head = "<tr><th>類股</th>" + d.dates.map((dt) => `<th style="text-align:right">${dt.slice(5)}</th>`).join("") + '<th style="text-align:right">累計</th></tr>';
    const body = d.sectors.map((s) => `<tr><td>${esc(s.name)}</td>${s.series.map(cell).join("")}<td class="${chgClass(s.sum)}" style="text-align:right;font-weight:700">${s.sum > 0 ? "+" : ""}${fmt(s.sum, 2)}</td></tr>`).join("");
    el.innerHTML = `<table>${head}${body}</table>`;
  } catch (e) { el.innerHTML = '<div class="muted small">輪動載入失敗</div>'; }
}
async function loadCross() {
  const el = $("cross");
  if (!el) return;
  const note = $("cross-note");
  try {
    const d = await getJSON("/api/sectors/picks");
    if (!d.groups || !d.groups.length) { el.innerHTML = '<div class="muted small">尚無選股或族群資料（請先到「籌碼/基本選股」載入當日 CSV）。</div>'; if (note) note.textContent = ""; return; }
    if (note) note.textContent = `（選股日 ${d.date || ""}，共 ${d.groups.length} 族群）`;
    el.innerHTML = d.groups.map((g) => {
      const cls = chgClass(g.chg_pct);
      const arrow = g.chg_pct > 0 ? "▲" : g.chg_pct < 0 ? "▼" : "";
      const pct = g.chg_pct == null ? '<span class="muted">—</span>' : `<span class="${cls}">${arrow}${fmt(Math.abs(g.chg_pct), 2)}%</span>`;
      const stocks = g.stocks.map((s) => stockLink(s.code, s.name)).join("　");
      return `<div class="cross-grp ${cls}"><div class="cross-h"><b>${esc(g.sector)}</b>　${pct}　<span class="muted">· ${g.count} 檔</span></div><div class="cross-stocks">${stocks}</div></div>`;
    }).join("");
  } catch (e) { el.innerHTML = '<div class="muted small">交叉選股載入失敗</div>'; }
}

// ========== 籌碼趨勢圖（用 dashboard 的近 60 日 history，純前端） ==========
function chipTrendOption(hist, metric) {
  const dates = hist.map((r) => (r.date ? r.date.slice(5) : ""));
  // 顯示實際資料點、斷點自動連線(不留假空白)、輕微平滑(不腦補誇大轉折)
  const LP = { type: "line", smooth: 0.15, showSymbol: true, symbolSize: 5, connectNulls: true };
  const axisTaiex = { type: "value", scale: true, position: "right", axisLabel: { color: "#777", fontSize: 11 }, splitLine: { show: false } };
  const taiexLine = { ...LP, name: "加權", yAxisIndex: 1, symbolSize: 0, data: hist.map((r) => r.taiex), lineStyle: { width: 1, color: "#8a94a3", type: "dashed" }, itemStyle: { color: "#8a94a3" } };
  const base = {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { textStyle: { color: "#ccc" }, top: 0 },
    grid: { left: 64, right: 60, top: 30, bottom: 26 },
    xAxis: { type: "category", data: dates, boundaryGap: metric === "inst", axisLabel: { color: "#999" } },
  };
  const zeroMark = { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: "#555", type: "dashed" } };
  if (metric === "inst") {
    const bar = (name, key, color) => ({ name, type: "bar", data: hist.map((r) => r[key]), itemStyle: { color } });
    return { ...base, yAxis: [{ type: "value", name: "億", axisLabel: { color: "#999" } }, axisTaiex],
      series: [bar("外資", "inst_foreign", SER.foreign), bar("投信", "inst_trust", SER.trust), bar("自營", "inst_dealer", SER.dealer), taiexLine] };
  }
  if (metric === "foreign_oi") {
    return { ...base, yAxis: [{ type: "value", name: "口", axisLabel: { color: "#999" } }, axisTaiex],
      series: [{ ...LP, name: "外資台指淨未平倉", data: hist.map((r) => r.tx_foreign_oi), areaStyle: { opacity: 0.08 }, lineStyle: { color: SER.foreign }, itemStyle: { color: SER.foreign }, markLine: zeroMark }, taiexLine] };
  }
  if (metric === "retail_ls") {
    // retail_ls_mtx/tmf 是比率（如 0.139）→ ×100 以百分比呈現，與卡片一致
    const pct100 = (v) => (v == null ? null : v * 100);
    return { ...base, yAxis: [{ type: "value", name: "%", axisLabel: { color: "#999" } }, axisTaiex],
      series: [
        { ...LP, name: "小台散戶多空比", data: hist.map((r) => pct100(r.retail_ls_mtx)), lineStyle: { color: SER.foreign }, itemStyle: { color: SER.foreign }, markLine: zeroMark },
        { ...LP, name: "微台散戶多空比", data: hist.map((r) => pct100(r.retail_ls_tmf)), lineStyle: { color: SER.trust }, itemStyle: { color: SER.trust } },
        taiexLine] };
  }
  // margin：融資（左軸）+ 融券（右軸，量級差很多）
  return { ...base, yAxis: [
      { type: "value", name: "融資(張)", axisLabel: { color: "#999" } },
      { type: "value", name: "融券(張)", position: "right", axisLabel: { color: "#999" }, splitLine: { show: false } }],
    series: [
      { ...LP, name: "融資餘額", data: hist.map((r) => r.margin_balance), lineStyle: { color: C.up }, itemStyle: { color: C.up } },
      { ...LP, name: "融券餘額", yAxisIndex: 1, data: hist.map((r) => r.short_balance), lineStyle: { color: C.down }, itemStyle: { color: C.down } }] };
}
// echarts.init 會把「當下」的容器尺寸記下來，之後不會自己重量。初次載入時若這行早於
// 版面完成，寬度就被記成 0，畫布維持 0px 直到有人縮視窗或切分頁才復原（間歇性、
// 重整幾次才遇得到一次）。熱力圖沒這問題正是因為它在 render 後補了 resize()，
// 這裡比照辦理——setOption 後量一次，讓結果不依賴 init 的時機。
function loadChipTrend() {
  if (!$("chipchart")) return;
  if (!chipChart) chipChart = echarts.init($("chipchart"));
  if (!lastHistory.length) { chipChart.clear(); return; }
  chipChart.setOption(chipTrendOption(lastHistory, chipMetric), true);
  chipChart.resize();
}

async function loadIndexChart() {
  if (!idxChart) idxChart = echarts.init($("idxchart"));
  idxChart.showLoading();
  try {
    const d = await getJSON(`/api/index/kline?symbol=${idxSymbol}&interval=${idxInterval}`);
    idxChart.hideLoading();
    if (!d.candles || !d.candles.length) { idxChart.clear(); $("idx-note").textContent = "尚無資料"; return; }
    $("idx-note").textContent = d.proxy ? "（台指期歷史抓取失敗，暫以加權指數近似）" : (idxSymbol === "tx" ? "（台指期：期交所近月歷史日K）" : "");
    lastIndexData = d;
    idxChart.setOption(candlestickOption(d, d.candles.length > 120 ? 70 : 0, overviewWaves, wavePct), true);
    idxChart.resize();      // 同 loadChipTrend：不依賴 init 當下的容器尺寸
  } catch (e) { idxChart.hideLoading(); $("idx-note").textContent = "載入失敗：" + e.message; }
  const panel = $("tx-vol-panel");
  if (panel) {
    if (idxSymbol === "tx") { panel.classList.remove("hidden"); loadTxVolumeChart(); }
    else panel.classList.add("hidden");
  }
}

function txVolumeOption(d) {
  const dates = d.dates.map((x) => x.slice(5));
  return {
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#ccc" }, top: 0 },
    grid: { left: 56, right: 56, top: 30, bottom: 26 },
    xAxis: { type: "category", data: dates, axisLabel: { color: "#999" } },
    yAxis: [
      { type: "value", name: "口", axisLabel: { color: "#999" } },
      { type: "value", name: "夜/日比", position: "right", axisLabel: { color: "#999" }, splitLine: { show: false } },
    ],
    series: [
      { name: "日盤量", type: "bar", data: d.day_volume, itemStyle: { color: SER.trust } },
      { name: "夜盤量", type: "bar", data: d.night_volume, itemStyle: { color: SER.foreign } },
      { name: "夜/日比", type: "line", yAxisIndex: 1, data: d.ratio, symbolSize: 4,
        lineStyle: { color: C.up }, itemStyle: { color: C.up } },
    ],
  };
}
async function loadTxVolumeChart() {
  const el = $("tx-vol-chart");
  if (!el) return;
  if (!txVolChart) txVolChart = echarts.init(el);
  try {
    const d = await getJSON("/api/tx/volume-sessions?days=60");
    if (!d.dates || !d.dates.length) { txVolChart.clear(); return; }
    txVolChart.setOption(txVolumeOption(d), true);
    txVolChart.resize();    // 同上；此圖藏在 .hidden 面板裡，init 時尺寸為 0 更是常態
  } catch (e) { txVolChart.clear(); }
}

async function loadMarketSummary(refresh) {
  const box = $("market-summary"); box.textContent = "AI 生成中…";
  try { const s = await getJSON("/api/market/summary" + (refresh ? "?refresh=1" : "")); box.textContent = s.text || ""; box.classList.toggle("disabled", !s.enabled); }
  catch (e) { box.textContent = "AI 摘要失敗：" + e.message; }
}
async function loadCsvSummary(refresh) {
  const box = $("csv-summary"); box.textContent = "AI 生成中…";
  try {
    const s = await getJSON("/api/analysis/summary" + (refresh ? "?refresh=1" : ""));
    box.textContent = s.text || "";
    box.classList.toggle("disabled", !s.enabled);
    const dt = $("csv-summary-date");
    if (dt) dt.textContent = s.snap_date ? `資料日期 ${s.snap_date}` : "";  // 舊快取無此欄則留空
  } catch (e) { box.textContent = "AI 分析失敗：" + e.message; }
}

// ========== 自動更新（無按鍵；開頁時若資料非當日即自動抓一次） ==========
let autoUpdating = false;
async function autoUpdate() {
  if (autoUpdating) return;
  autoUpdating = true;
  const bar = $("update-status"); bar.classList.remove("hidden"); bar.className = "status-bar";
  bar.textContent = "🔄 自動更新中…（抓取 TWSE / TAIFEX / 國際指數，約 20–30 秒）";
  $("last-updated").textContent = "🔄 自動更新中…";
  try {
    const res = await (await fetch("/api/update/run", { method: "POST" })).json();
    const fail = (res.failed || []).map((f) => f.name).join("、");
    bar.innerHTML = fail ? `已自動更新（部分來源未取得：${fail}）` : "✅ 已自動更新";
    bar.className = "status-bar " + (fail ? "warn" : "ok");
    await loadDashboard(); await loadIndexChart(); loadBreadth(); loadMovers(); loadSectors(); loadMarketSummary(false);
    setTimeout(() => bar.classList.add("hidden"), 5000);
  } catch (e) {
    bar.textContent = "自動更新失敗：" + e.message; bar.className = "status-bar err";
    await loadDashboard();
  } finally { autoUpdating = false; }
}

// ========== 選股清單 ==========
function stockLink(code, name) { const c = esc(code), n = esc(name || ""); return `<a href="#" class="stock" data-code="${c}" data-name="${n}">${c} ${n}</a>`; }
function lanCell(v) { if (v === null || v === undefined) return "—"; return v > 60 ? `<b style="color:var(--up)">${fmt(v, 1)}</b>` : fmt(v, 1); }

const sortState = {};
function renderSortable(elId, columns, rows, emptyMsg, onRowClick) {
  if (!rows || !rows.length) { $(elId).innerHTML = `<div class="muted">${emptyMsg || "無資料"}</div>`; return; }
  const st = sortState[elId] || {};
  const data = rows.slice();
  if (st.key) {
    const col = columns.find((c) => c.key === st.key) || {};
    data.sort((a, b) => {
      let va = a[st.key], vb = b[st.key];
      if (col.numeric) { va = va == null ? -Infinity : Number(va); vb = vb == null ? -Infinity : Number(vb); return st.asc ? va - vb : vb - va; }
      va = va == null ? "" : String(va); vb = vb == null ? "" : String(vb); return st.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }
  const head = "<tr>" + columns.map((c) => `<th class="sortable" data-sort="${c.key}">${c.label}${st.key === c.key ? (st.asc ? " ▲" : " ▼") : ""}</th>`).join("") + "</tr>";
  const body = data.map((r, i) => `<tr data-i="${i}"${onRowClick ? ' class="clickrow"' : ""}>` + columns.map((c) => `<td>${c.render ? c.render(r) : fmt(r[c.key], c.dp === undefined ? 2 : c.dp)}</td>`).join("") + "</tr>").join("");
  $(elId).innerHTML = `<table>${head}${body}</table>`;
  $(elId).querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
    const key = th.dataset.sort, cur = sortState[elId] || {};
    sortState[elId] = { key, asc: cur.key === key ? !cur.asc : false };
    renderSortable(elId, columns, rows, emptyMsg, onRowClick);
  }));
  if (onRowClick) $(elId).querySelectorAll("tr[data-i]").forEach((tr) => tr.addEventListener("click", () => onRowClick(data[Number(tr.dataset.i)])));
}

const PICK_COLS = [
  { key: "code", label: "股票", render: (r) => stockLink(r.code, r.name) },
  { key: "lan_value", label: "蘭值", numeric: true, render: (r) => lanCell(r.lan_value) },
  { key: "lan_score", label: "蘭質", numeric: true, dp: 1 },
  { key: "lpe", label: "本益比", numeric: true },
  { key: "est_profit", label: "推估EPS", numeric: true },
  { key: "rev_yoy", label: "營收年增%", numeric: true, dp: 1 },
  { key: "accum_inc", label: "營收累增", numeric: true, dp: 1 },
  { key: "holder_drop_ratio", label: "人數降比", numeric: true },
  { key: "big_holder_ratio", label: "大戶增比", numeric: true },
];
const SUBIND_COLS = [
  { key: "sub_industry", label: "細產業", render: (r) => r.sub_industry },
  { key: "count", label: "檔數", numeric: true, dp: 0 },
];

let currentPicks = [], subFilter = null, lanMin = null, lpeMin = null;
// 蘭質/本益比門檻（前端即時篩選，收斂檔數）：null＝該欄不設限；缺值一律不通過
function passesValueFilter(p) {
  if (lanMin != null && !(p.lan_score != null && p.lan_score >= lanMin)) return false;
  if (lpeMin != null && !(p.lpe != null && p.lpe >= lpeMin)) return false;
  return true;
}
// 細產業統計改由前端從「已套蘭質/本益比篩選」的清單重算，讓聯動數字與表格一致（不含 subFilter，
// 才能顯示所有細產業供切換）；無篩選時等同 server 送的 d.subindustry
function computeSubindustry(picks) {
  const m = new Map();
  picks.forEach((p) => { if (p.sub_industry) m.set(p.sub_industry, (m.get(p.sub_industry) || 0) + 1); });
  return [...m].map(([sub_industry, count]) => ({ sub_industry, count }));
}
function renderSubFilterChip(valueFiltered) {
  const el = $("sub-filter");
  if (subFilter) {
    el.innerHTML = `篩選：<b>${esc(subFilter)}</b>（${valueFiltered.filter((p) => p.sub_industry === subFilter).length} 檔） <a href="#" id="clear-sub">✕ 全部</a>`;
    const clr = $("clear-sub"); if (clr) clr.addEventListener("click", (e) => { e.preventDefault(); subFilter = null; renderDailyView(); });
  } else { el.innerHTML = `共 ${valueFiltered.length} 檔`; }
}
function renderDaily(picks) { if (!sortState.daily) sortState.daily = { key: "lan_value", asc: false }; renderSortable("daily", PICK_COLS, picks, "無符合條件的個股"); }
function renderDailyView() {
  const valueFiltered = currentPicks.filter(passesValueFilter);
  renderSubFilterChip(valueFiltered);
  renderIndustry(computeSubindustry(valueFiltered));
  renderDaily(subFilter ? valueFiltered.filter((p) => p.sub_industry === subFilter) : valueFiltered);
}
function renderIndustry(subind) {
  if (!sortState.industry) sortState.industry = { key: "count", asc: false };
  renderSortable("industry", SUBIND_COLS, subind, "無資料", (r) => { subFilter = r.sub_industry; renderDailyView(); });
}
function resetPickFilters() {
  lanMin = lpeMin = null;
  const fsc = $("f-lan-score"), fpe = $("f-lpe");
  if (fsc) fsc.value = ""; if (fpe) fpe.value = "";
}
async function loadDaily(date) {
  try {
    const d = await getJSON("/api/analysis/daily" + (date ? `?date=${encodeURIComponent(date)}` : ""));
    currentPicks = d.picks || []; subFilter = null; resetPickFilters();
    renderDailyView();
    if (d.snap_date) $("date-select").value = d.snap_date;
  } catch (e) { /* 忽略 */ }
}
async function loadDates() {
  try {
    const dates = (await getJSON("/api/snapshots")).dates || [];
    $("date-select").innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
    await loadDaily(dates[dates.length - 1]);
  } catch (e) { /* 忽略 */ }
}

// ========== 跨週 ==========
function statusBadge(s) { const map = { "新進榜": "new", "加速": "acc", "持平": "flat2", "退榜": "out" }; return `<span class="status ${map[s] || ""}">${s}</span>`; }
function renderWeekly(data) {
  if (data.note) { $("weekly").innerHTML = `<div class="muted">${data.note}</div>`; return; }
  $("weekly-dates").textContent = data.this_date ? `（${data.last_date} → ${data.this_date}）` : "";
  const rows = (data.stocks || []).filter((r) => r.status !== "持平");
  if (!rows.length) { $("weekly").innerHTML = '<div class="muted">本週與上週無新進榜／加速／退榜（或兩份資料相同）。</div>'; return; }
  rows.sort((a, b) => (b.custody_delta || -999) - (a.custody_delta || -999));
  const head = "<tr><th>股票</th><th>狀態</th><th>集保Δ</th><th>大戶增比</th><th>產業</th></tr>";
  const body = rows.map((r) => `<tr><td>${stockLink(r.code, r.name)}</td><td>${statusBadge(r.status)}</td><td>${fmt(r.custody_delta, 2)}</td><td>${fmt(r.big_holder_ratio, 2)}</td><td>${esc(r.industry || "")}</td></tr>`).join("");
  $("weekly").innerHTML = `<table>${head}${body}</table>`;
}
async function loadWeekly() { try { renderWeekly(await getJSON("/api/analysis/weekly")); } catch (e) { /* 忽略 */ } }

// ========== 個股查詢 ==========
function renderProfile(p) {
  const el = $("stock-profile");
  if (!p || !p.chip) { el.innerHTML = '<div class="muted">查無此個股的籌碼資料（請先上傳含該股的 CSV）。</div>'; return; }
  const c = p.chip, v = p.valuation || {};
  const groups = [
    ["籌碼面", [["大戶增比", fmt(c.big_holder_ratio)], ["人數降比", fmt(c.holder_drop_ratio)], ["集保大戶", fmt(c.custody)], ["投信3日", fmt(c.trust_3d)], ["外資3日", fmt(c.foreign_3d)]]],
    ["技術面", [["W55", Number(c.w55) >= 1 ? "翻多 ✓" : "—"]]],
    ["基本/財務", [["營收年增%", fmt(c.rev_yoy, 1)], ["推估EPS(下季)", fmt(c.est_profit)], ["蘭質(財評/15)", fmt(c.lan_score)], ["本益比(LPE)", fmt(c.lpe)], ["蘭值", lanCell(c.lan_value)], ["市值(億)", fmt(c.market_cap, 0)], ["股本(億)", fmt(c.capital)]]],
  ];
  const html = groups.map(([t, items]) => `<div class="pf-group"><span class="pf-title">${t}</span>${items.map(([k, val]) => `<span class="pf-item"><b>${k}</b> ${val}</span>`).join("")}</div>`).join("");
  // TWSE估值只涵蓋上市股，上櫃股三欄永遠是「—」——全空時收起整組，換一行說明而非留破折號佔位
  const hasTwse = [v.pe, v.yield, v.pb].some((x) => x != null);
  const twseHtml = hasTwse
    ? `<div class="pf-group"><span class="pf-title">TWSE估值</span><span class="pf-item"><b>本益比(TWSE)</b> ${fmt(v.pe)}</span><span class="pf-item"><b>殖利率%</b> ${fmt(v.yield)}</span><span class="pf-item"><b>淨值比</b> ${fmt(v.pb)}</span></div>`
    : `<span class="muted small">TWSE估值僅上市股提供</span>`;
  el.innerHTML = html + twseHtml;
}
// 首尾整段無資料的日期修掉（法人冷門股常見）：只留「有資料」的區段，避免版面被空白軸吃掉，
// 讓「沒資料」和「有資料但沒買賣超（0/null 混雜於中段）」不會長得一樣空
function trimEdges(dates, series) {
  const has = (i) => series.some((arr) => arr[i] != null);
  let s = 0, e = dates.length - 1;
  while (s <= e && !has(s)) s++;
  while (e >= s && !has(e)) e--;
  const idx = dates.slice(s, e + 1).map((_, k) => k + s);
  return { dates: idx.map((i) => dates[i]), series: series.map((arr) => idx.map((i) => arr[i])), total: dates.length, kept: idx.length };
}
async function loadStockChips(code) {
  const wrap = $("stock-chips-wrap");
  if (!wrap) return;
  wrap.classList.remove("hidden");
  if (!stockChipsChart) stockChipsChart = echarts.init($("stock-chips"));
  stockChipsChart.showLoading();
  const note = $("stock-chips-note");
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/chips?days=60`);
    stockChipsChart.hideLoading();
    if (!d.total || !d.total.some((v) => v != null)) { stockChipsChart.clear(); if (note) note.textContent = "（查無此股三大法人資料）"; return; }
    const last = [...d.total].reverse().find((v) => v != null);
    const mk = d.market === "tpex" ? "上櫃" : "上市";
    const { dates, series, total, kept } = trimEdges(d.dates, [d.foreign, d.trust, d.dealer]);
    const span = kept < total ? `　共 ${kept}/${total} 日有資料` : "";
    if (note) note.textContent = `（${mk}・最新合計 ${last > 0 ? "+" : ""}${fmt(last, 0)} 張${span}）`;
    const bar = (name, arr, color) => ({ name, type: "bar", stack: "三大法人", data: arr, itemStyle: { color } });
    stockChipsChart.setOption({
      textStyle: { fontFamily: HM_FONT },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { textStyle: { color: C.label }, top: 0 },
      grid: { left: 58, right: 16, top: 26, bottom: 24 },
      xAxis: { type: "category", data: dates.map((x) => x.slice(5)), axisLabel: { color: C.muted } },
      yAxis: { type: "value", name: "張", axisLabel: { color: C.muted }, splitLine: { lineStyle: { color: C.border } } },
      series: [bar("外資", series[0], SER.foreign), bar("投信", series[1], SER.trust), bar("自營", series[2], SER.dealer)],
    }, true);
  } catch (e) { stockChipsChart.hideLoading(); if (note) note.textContent = "（載入失敗）"; }
}

async function loadStockCustody(code) {
  const wrap = $("stock-custody-wrap");
  if (!wrap) return;
  wrap.classList.remove("hidden");
  if (!stockCustodyChart) stockCustodyChart = echarts.init($("stock-custody"));
  stockCustodyChart.showLoading();
  const note = $("stock-custody-note");
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/custody`);
    stockCustodyChart.hideLoading();
    if (!d.trend || !d.trend.length) { stockCustodyChart.clear(); if (note) note.textContent = "（查無集保資料；上市櫃個股適用）"; return; }
    const cur = d.current;
    if (note) note.textContent = cur ? `（${d.week}　千張大戶 ${fmt(cur.big1000_pct, 2)}%・400張↑ ${fmt(cur.big400_pct, 2)}%・千張大戶 ${fmt(cur.big_holders, 0)} 人；趨勢逐週累積）` : "";
    const wk = d.trend.map((t) => (t.week ? t.week.slice(5) : ""));
    // 逐點圓圈在 51 週的密度下蓋過線形，改收掉；改在線尾標最新值（各自線色），
    // 一眼看現在水位不必回頭讀上面 note 那行小字
    const line = (name, key, color) => ({
      name, type: "line", smooth: 0.2, showSymbol: false, data: d.trend.map((t) => t[key]),
      lineStyle: { color }, itemStyle: { color },
      endLabel: { show: true, formatter: (p) => fmt(p.value, 1) + "%", color, fontWeight: 700, distance: 6 },
    });
    stockCustodyChart.setOption({
      textStyle: { fontFamily: HM_FONT },
      tooltip: { trigger: "axis" }, legend: { textStyle: { color: C.label }, top: 0 },
      grid: { left: 48, right: 44, top: 26, bottom: 24 },
      xAxis: { type: "category", data: wk, boundaryGap: false, axisLabel: { color: C.muted } },
      // 大戶比常年落在 80~90%，若軸從 0 起會壓成貼頂扁線看不出週變化 → scale 放大到資料區間＋留白
      yAxis: { type: "value", name: "%", scale: true,
               min: (v) => Math.floor(v.min - 0.5), max: (v) => Math.ceil(v.max + 0.5),
               axisLabel: { color: C.muted }, splitLine: { lineStyle: { color: C.border } } },
      series: [line("千張大戶%", "big1000_pct", SER.foreign), line("400張↑大戶%", "big400_pct", SER.trust)],
    }, true);
  } catch (e) { stockCustodyChart.hideLoading(); if (note) note.textContent = "（載入失敗）"; }
}

async function loadStock(code, name) {
  code = (code || "").trim().toUpperCase();
  if (!code) return;
  if (!/\./.test(code)) code += ".TW";
  stockCode = code;
  if (!stockChart) stockChart = echarts.init($("stock-chart"));
  $("stock-note").textContent = "載入中…";
  try { renderProfile(await getJSON(`/api/stock/${encodeURIComponent(code)}/profile`)); } catch (e) { $("stock-profile").innerHTML = ""; }
  loadStockChips(code);
  loadStockCustody(code);
  stockChart.showLoading();
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/kline?interval=${stockInterval}`);
    stockChart.hideLoading();
    if (!d.candles || !d.candles.length) { stockChart.clear(); lastStockData = null; $("stock-note").textContent = `${code} 無 K 線資料`; return; }
    lastStockData = d;
    $("stock-note").textContent = `${d.code || code} ${name || ""}`;
    stockChart.resize();
    stockChart.setOption(candlestickOption(d, d.candles.length > 120 ? 60 : 0, stockWaves, wavePct), true);
  } catch (e) { stockChart.hideLoading(); $("stock-note").textContent = "載入失敗：" + e.message; }
}

// ========== 上傳 / 匯入 ==========
async function applyImportResult(res) {
  const info = $("upload-info");
  if (res.error && !res.count) { info.innerHTML = `<span class="err-text">⚠ ${esc(res.error)}</span>`; return; }
  if (!res.count) { info.innerHTML = `<span class="err-text">⚠ 讀到 0 檔（${res.snap_date}）。請確認是籌碼匯出檔。</span>`; return; }
  info.textContent = `已匯入 ${res.file ? res.file + "：" : ""}${res.snap_date}，共 ${res.count} 檔`;
  await loadDates(); await loadWeekly();
  loadCsvSummary(false); // 匯入清了該日快取 → 這次載入會自動重新生成
}
async function uploadCsv(file) {
  $("upload-info").textContent = "解析中…";
  const fd = new FormData(); fd.append("file", file);
  try { await applyImportResult(await (await fetch("/api/csv/upload", { method: "POST", body: fd })).json()); }
  catch (e) { $("upload-info").textContent = "上傳失敗：" + e.message; }
}
async function importLatest() {
  $("upload-info").textContent = "讀取資料夾最新檔…";
  try { await applyImportResult(await (await fetch("/api/csv/import-latest", { method: "POST" })).json()); }
  catch (e) { $("upload-info").textContent = "讀取失敗：" + e.message; }
}

// ========== 事件 ==========
document.querySelectorAll(".nav").forEach((n) => n.addEventListener("click", () => showView(n.dataset.view)));
$("csv").addEventListener("change", (e) => { if (e.target.files[0]) uploadCsv(e.target.files[0]); });
$("btn-latest").addEventListener("click", importLatest);
$("date-select").addEventListener("change", (e) => loadDaily(e.target.value));
const _numOrNull = (v) => { const s = String(v).trim(); if (!s) return null; const n = parseFloat(s); return isFinite(n) ? n : null; };
$("f-lan-score").addEventListener("input", (e) => { lanMin = _numOrNull(e.target.value); renderDailyView(); });
$("f-lpe").addEventListener("input", (e) => { lpeMin = _numOrNull(e.target.value); renderDailyView(); });
$("clear-picks-filter").addEventListener("click", (e) => { e.preventDefault(); resetPickFilters(); renderDailyView(); });
$("btn-export").addEventListener("click", () => {
  const url = `/api/analysis/export?date=${encodeURIComponent($("date-select").value || "")}` + (subFilter ? `&sub=${encodeURIComponent(subFilter)}` : "");
  window.location.href = url;
});
$("btn-save-settings").addEventListener("click", saveSettings);
$("btn-osfut-refresh").addEventListener("click", () => loadOsFutures("live"));
$("btn-rank-refresh").addEventListener("click", loadRankPrice);
document.querySelectorAll(".rkp-tab").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.market === rankMarket) return;
  rankMarket = b.dataset.market;
  document.querySelectorAll(".rkp-tab").forEach((x) => x.classList.toggle("active", x === b));
  loadRankPrice();
}));
document.querySelectorAll(".hm-tab").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.market === heatmapMarket) return;
  heatmapMarket = b.dataset.market;
  document.querySelectorAll(".hm-tab").forEach((x) => x.classList.toggle("active", x === b));
  loadSectors();
}));
document.querySelectorAll(".hm-top").forEach((b) => b.addEventListener("click", () => {
  const t = Number(b.dataset.top);
  if (t === heatmapTop) return;
  heatmapTop = t;
  document.querySelectorAll(".hm-top").forEach((x) => x.classList.toggle("active", x === b));
  loadSectors();
}));
$("btn-cup-refresh").addEventListener("click", loadCupHandle);
document.querySelectorAll(".cup-r-tab").forEach((b) => b.addEventListener("click", () => {
  const r = Number(b.dataset.r);
  if (r === cupMinR) return;
  cupMinR = r;
  document.querySelectorAll(".cup-r-tab").forEach((x) => x.classList.toggle("active", x === b));
  loadCupHandle();
}));
$("btn-cup-picks").addEventListener("click", (e) => {
  cupPicksOnly = !cupPicksOnly;
  e.target.classList.toggle("active", cupPicksOnly);
  renderCupChips();
});
$("btn-cup-bt").addEventListener("click", (e) => {
  const box = $("cup-bt");
  const show = box.classList.contains("hidden");
  box.classList.toggle("hidden", !show);
  e.target.classList.toggle("active", show);
  if (show && !cupBtLoaded) loadCupBacktest();
});
$("btn-tr-add").addEventListener("click", async () => {
  const st = $("tr-status"); st.textContent = "記錄中…";
  try {
    const r = await fetch("/api/trades", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: $("tr-code").value.trim(), shares: parseInt($("tr-shares").value, 10) || 0,
        entry_date: $("tr-date").value, entry_price: parseFloat($("tr-price").value) || 0,
        note: $("tr-note").value.trim() }) }).then((x) => x.json());
    if (!r.ok) { st.textContent = r.error || "記錄失敗"; return; }
    ["tr-code", "tr-shares", "tr-price", "tr-note"].forEach((id) => { $(id).value = ""; });
    st.textContent = "已記錄 ✓"; setTimeout(() => { st.textContent = ""; }, 1500);
    renderTrades(r);
  } catch (e) { st.textContent = "記錄失敗：" + e.message; }
});
$("tr-open").addEventListener("click", trTableClick);
$("tr-closed").addEventListener("click", trTableClick);
$("cup-list").addEventListener("click", (e) => {
  const a = e.target.closest(".cup-chip"); if (!a) return;
  e.preventDefault();
  document.querySelectorAll("#cup-list .cup-chip").forEach((x) => x.classList.toggle("active", x === a));
  drawCupChart(cupMatches[+a.dataset.i]);
  renderCupRisk(cupMatches[+a.dataset.i]);
});
$("btn-line-test").addEventListener("click", async () => {
  const st = $("set-line-status"); st.textContent = "推播中…";
  try {
    const r = await (await fetch("/api/line/test", { method: "POST" })).json();
    st.textContent = r.ok ? "已送出，看手機 ✓" : "失敗：" + (r.error || r.status);
  } catch (e) { st.textContent = "失敗：" + e.message; }
});

// 大盤圖控制
document.querySelectorAll('input[name="idx"]').forEach((el) => el.addEventListener("change", (e) => { idxSymbol = e.target.value; loadIndexChart(); }));
document.querySelectorAll("#view-overview .tf").forEach((btn) => btn.addEventListener("click", () => {
  document.querySelectorAll("#view-overview .tf").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active"); idxInterval = btn.dataset.iv; loadIndexChart();
}));
$("wave-help-toggle").addEventListener("click", (e) => { e.preventDefault(); $("wave-help").classList.toggle("hidden"); });
$("wave-chk").addEventListener("change", (e) => { overviewWaves = e.target.checked; if (idxChart && lastIndexData) idxChart.setOption(candlestickOption(lastIndexData, lastIndexData.candles.length > 120 ? 70 : 0, overviewWaves, wavePct), true); });
$("wave-pct").addEventListener("input", (e) => {
  wavePct = Number(e.target.value) / 100; $("wave-pct-val").textContent = `轉折 ${e.target.value}%`;
  if (overviewWaves && idxChart && lastIndexData) idxChart.setOption(candlestickOption(lastIndexData, lastIndexData.candles.length > 120 ? 70 : 0, overviewWaves, wavePct), true);
});

// 個股圖控制
$("watch-add").addEventListener("click", addWatch);
$("watch-input").addEventListener("keydown", (e) => { if (e.key === "Enter") addWatch(); });
$("watch-table").addEventListener("click", async (e) => {
  const a = e.target.closest(".watch-del"); if (!a) return; e.preventDefault();
  try { await fetch(`/api/watchlist/${encodeURIComponent(a.dataset.code)}`, { method: "DELETE" }); } catch (er) { /* ignore */ }
  loadWatchlist();
});
$("stock-go").addEventListener("click", () => loadStock($("stock-input").value));
$("stock-input").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock($("stock-input").value); });
document.querySelectorAll(".ktf").forEach((btn) => btn.addEventListener("click", () => {
  document.querySelectorAll(".ktf").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active"); stockInterval = btn.dataset.iv; if (stockCode) loadStock(stockCode);
}));
$("stock-wave-chk").addEventListener("change", (e) => { stockWaves = e.target.checked; if (stockChart && lastStockData) stockChart.setOption(candlestickOption(lastStockData, lastStockData.candles.length > 120 ? 60 : 0, stockWaves, wavePct), true); });
// 集保「補歷史」：從 TDCC 智能網逐週回補該股歷史（opendata 只給當週）；單次回補整段、可能較久
$("custody-backfill").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!stockCode) return;
  const link = $("custody-backfill");
  link.style.pointerEvents = "none"; link.textContent = "補齊中…";
  try {
    for (let guard = 0; guard < 20; guard++) {
      const r = await (await fetch(`/api/stock/${encodeURIComponent(stockCode)}/custody/backfill?weeks=52`)).json();
      if (r.busy) { await new Promise((s) => setTimeout(s, 1500)); continue; }
      break;
    }
    await loadStockCustody(stockCode);
  } catch (err) { /* 顯示於下方 note */ }
  finally { link.style.pointerEvents = ""; link.textContent = "補歷史"; }
});

// 點股號 → 跳到個股查詢頁
document.addEventListener("click", (e) => {
  const a = e.target.closest("a.stock");
  if (a) { e.preventDefault(); showView("stock"); $("stock-input").value = a.dataset.code; loadStock(a.dataset.code, a.dataset.name); }
});
document.querySelectorAll(".ctf").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".ctf").forEach((x) => x.classList.toggle("active", x === b));
  chipMetric = b.dataset.metric; loadChipTrend();
}));
document.querySelectorAll(".rkf").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".rkf").forEach((x) => x.classList.toggle("active", x === b));
  rankWho = b.dataset.who; loadInstRanking();
}));
document.querySelectorAll(".rku").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".rku").forEach((x) => x.classList.toggle("active", x === b));
  rankUnit = b.dataset.unit; loadInstRanking();
}));
window.addEventListener("resize", () => { idxChart && idxChart.resize(); stockChart && stockChart.resize(); chipChart && chipChart.resize(); stockChipsChart && stockChipsChart.resize(); if (sectorChart) { sectorChart.resize(); if (lastHeatmapData) fitHeatmapFonts(lastHeatmapData); } cupChart && cupChart.resize(); txVolChart && txVolChart.resize(); });
// 粉圓/M PLUS 是 async 載入。若熱力圖在字型載入前已排版，measureText 量到的是系統字寬度，
// 字型 swap 後實際寬度改變 → 可能截字。字型就緒後重跑一次字級擬合（重用既有 refit 路徑）。
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => { if (sectorChart && lastHeatmapData) fitHeatmapFonts(lastHeatmapData); });
}

// ========== 初始載入 ==========
(async () => {
  // 公開模式：只跑總覽所需的唯讀載入。設定/跨週非總覽；autoUpdate 會 POST /api/update/run
  // （寫 DB、打外部 API），匿名訪客絕不可觸發。
  const d = await loadDashboard();
  loadIndexChart();
  loadBreadth();
  loadMovers();
  loadSectors();
  loadMarketSummary(false);  // 讀快取即回；排程更新完會自動預先生成，開頁不另扣費
  loadInstRanking();
  loadOptionsSentiment();
  if (PUBLIC) return;
  loadDates();
  loadWeekly();
  // 自動更新：無資料、或資料非當日（平日尚未更新到最新交易日）時，自動抓一次
  if (!d || !d.latest || !d.latest.date || d.data_stale) autoUpdate();
})();
