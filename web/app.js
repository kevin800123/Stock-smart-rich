"use strict";

const $ = (id) => document.getElementById(id);
// HTML 跳脫：所有嵌入 innerHTML 的外部/CSV 字串（股名、產業、檔名、錯誤訊息）都要經過，
// 防止惡意 CSV 的股名如 <img onerror> 被當標記執行（儲存型 XSS）。兼顧屬性情境（含 " '）。
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const chgClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const chgText = (v) => (v === null || v === undefined ? "" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v)));

async function getJSON(url) {
  const r = await fetch(url);
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
let txVolChart = null;
let stockChipsChart = null, stockCustodyChart = null;
let sectorChart = null;
let cupChart = null, cupMatches = [], cupLoaded = false;
let rankWho = "foreign", rankUnit = "shares";
const MA_DEFS = [
  { n: 5, color: "#5b8ff9" }, { n: 20, color: "#5ad8a6" },
  { n: 60, color: "#f6bd16" }, { n: 120, color: "#e8684a" },
];

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
  const candle = { name: "K線", type: "candlestick", data: data.candles, itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" } };
  if (showW) {
    const pctKey = Math.round(pct * 100).toString();
    const waves = (data.waves && data.waves[pctKey]) || [];
    if (waves.length) candle.markPoint = {
      symbol: "circle", symbolSize: 20,
      label: { color: "#1a1a1a", fontWeight: 700, fontSize: 12, formatter: (p) => p.data.value },
      data: waves.map((w) => ({ value: w.label, coord: [data.dates[w.index], data.candles[w.index][3]], itemStyle: { color: /[ABC]/.test(w.label) ? "#6cb6ff" : "#f0a500" } })),
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
    legend: { data: ["K線", ...MA_DEFS.map((m) => "MA" + m.n)], textStyle: { color: "#ccc" } },
    grid: [{ left: 60, right: 20, top: 30, height: "60%" }, { left: 60, right: 20, top: "76%", height: "15%" }],
    xAxis: [{ type: "category", data: data.dates, axisLabel: { color: "#999" } }, { type: "category", data: data.dates, gridIndex: 1, axisLabel: { show: false } }],
    yAxis: [{ scale: true, axisLabel: { color: "#999" } }, { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: startPct }, { type: "slider", xAxisIndex: [0, 1], start: startPct, bottom: 0, height: 16 }],
    series: [candle, ...maSeries, { name: "量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: data.volumes }],
  };
}

// ========== 視圖切換 ==========
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  if (name === "overview") { idxChart && idxChart.resize(); chipChart && chipChart.resize(); sectorChart && sectorChart.resize(); txVolChart && txVolChart.resize(); }
  if (name === "stock") { stockChart && stockChart.resize(); stockChipsChart && stockChipsChart.resize(); stockCustodyChart && stockCustodyChart.resize(); }
  if (name === "rotation") { loadRotation(); loadCross(); }
  if (name === "osfut") loadOsFutures(false);  // 首次切換載入（讀快取即回）
  if (name === "cup") { if (!cupLoaded) loadCupHandle(); else cupChart && cupChart.resize(); }
  if (name === "weekly") loadCsvSummary(false);  // 讀快取即回；匯入後才會重新生成
  if (name === "watch") loadWatchlist();
  if (name === "trades") loadTrades();
  if (name === "signals") loadSignals();
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

// ========== 訊號追蹤與前瞻測試 ==========
async function loadSignals() {
  if (!$("sig-perf")) return;
  try {
    renderSignals(await getJSON("/api/signals/performance"));
  } catch (e) {
    $("sig-perf").innerHTML = `<span class="muted small">載入失敗: ${esc(e.message)}</span>`;
  }
}
function renderSignals(d) {
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const pct = (v) => v == null ? "—" : (v > 0 ? "+" : "") + fmt(v, 2) + "%";

  const perf = d.performance || {};
  const us = d.user_stats || {};

  const sources = [
    { key: "filtered_picks", name: "籌碼/基本選股" },
    { key: "cup_handle", name: "杯柄選股" }
  ];

  let perfHtml = "";
  sources.forEach(src => {
    const s = perf[src.key] || {};
    perfHtml += `
      <div class="card-group" style="grid-column: 1 / -1; margin-top: 8px;">
        <div class="group-title">${src.name}</div>
        <div class="stats-grid" style="max-width:none">
          <div class="stat">
            <div class="stat-k">5日勝率 / 平均</div>
            <div class="stat-v ${cls(s.ret5?.avg_ret)}">${s.ret5?.win_rate == null ? "—" : fmt(s.ret5.win_rate, 1) + "%"} / ${pct(s.ret5?.avg_ret)}</div>
            <div class="stat-k" style="margin-top:4px">樣本數: ${s.ret5?.count || 0}</div>
          </div>
          <div class="stat">
            <div class="stat-k">10日勝率 / 平均</div>
            <div class="stat-v ${cls(s.ret10?.avg_ret)}">${s.ret10?.win_rate == null ? "—" : fmt(s.ret10.win_rate, 1) + "%"} / ${pct(s.ret10?.avg_ret)}</div>
            <div class="stat-k" style="margin-top:4px">樣本數: ${s.ret10?.count || 0}</div>
          </div>
          <div class="stat">
            <div class="stat-k">20日勝率 / 平均</div>
            <div class="stat-v ${cls(s.ret20?.avg_ret)}">${s.ret20?.win_rate == null ? "—" : fmt(s.ret20.win_rate, 1) + "%"} / ${pct(s.ret20?.avg_ret)}</div>
            <div class="stat-k" style="margin-top:4px">樣本數: ${s.ret20?.count || 0}</div>
          </div>
        </div>
      </div>
    `;
  });
  $("sig-perf").innerHTML = perfHtml;

  $("sig-comparison").innerHTML = `
    <div class="comparison-card">
      <div class="comparison-title">👤 我的實際交易（帳本已平倉）</div>
      <div style="display:flex; flex-direction:column; gap:8px">
        <div>已平倉筆數: <b>${us.closed_n || 0}</b></div>
        <div>實際勝率: <b class="${(us.win_rate || 0) > 50 ? "up" : ""}">${us.win_rate == null ? "—" : fmt(us.win_rate, 1) + "%"}</b></div>
        <div>平均勝過大盤 (Alpha): <b class="${cls(us.avg_alpha)}">${pct(us.avg_alpha)}</b></div>
        <div>期望值 / 筆: <b class="${cls(us.expectancy)}">${pct(us.expectancy)}</b></div>
      </div>
    </div>
    <div class="comparison-card">
      <div class="comparison-title">🤖 訊號全買（理論 20日持有）</div>
      <div style="display:flex; flex-direction:column; gap:8px">
        <div>籌碼選股 20日勝率: <b class="${(perf.filtered_picks?.ret20?.win_rate || 0) > 50 ? "up" : ""}">${perf.filtered_picks?.ret20?.win_rate == null ? "—" : fmt(perf.filtered_picks.ret20.win_rate, 1) + "%"}</b></div>
        <div>籌碼選股 20日平均: <b class="${cls(perf.filtered_picks?.ret20?.avg_ret)}">${pct(perf.filtered_picks?.ret20?.avg_ret)}</b></div>
        <div>杯柄選股 20日勝率: <b class="${(perf.cup_handle?.ret20?.win_rate || 0) > 50 ? "up" : ""}">${perf.cup_handle?.ret20?.win_rate == null ? "—" : fmt(perf.cup_handle.ret20.win_rate, 1) + "%"}</b></div>
        <div>杯柄選股 20日平均: <b class="${cls(perf.cup_handle?.ret20?.avg_ret)}">${pct(perf.cup_handle?.ret20?.avg_ret)}</b></div>
      </div>
    </div>
  `;
}

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
    ln.textContent = s.line_configured ? `已設定 ✓（速報 ${s.line_push_time}・完整版 ${s.schedule_time}）` : "未設定";
    ln.className = "set-badge " + (s.line_configured ? "ok" : "no");
    $("set-picks-only").checked = !!s.intraday_picks_only;
    $("set-loss-tol").value = s.loss_tolerance || "";
    $("set-stats").innerHTML = [
      ["快照天數", s.snapshots], ["台指期歷史天數", s.tx_history_days], ["最新大盤日期", s.last_market_date || "—"],
    ].map(([k, v]) => `<div class="stat"><div class="stat-k">${k}</div><div class="stat-v">${v}</div></div>`).join("");
    renderNavOrder();
  } catch (e) { /* 忽略 */ }
}

// 左側分頁順序：套用（重排 .nav DOM，側欄與手機底部列共用同一批元素）
function applyNavOrder(order) {
  const bar = document.querySelector(".sidebar"); if (!bar) return;
  const byView = {}; bar.querySelectorAll(".nav").forEach((n) => { byView[n.dataset.view] = n; });
  const all = [...bar.querySelectorAll(".nav")].map((n) => n.dataset.view);
  const full = (order || []).filter((v) => byView[v]).concat(all.filter((v) => !(order || []).includes(v)));
  full.forEach((v) => bar.appendChild(byView[v]));  // 依序移到尾端＝完成重排
}
function renderNavOrder() {
  const box = $("nav-order"); if (!box) return;
  const navs = [...document.querySelectorAll(".sidebar .nav")];
  box.innerHTML = navs.map((n, i) =>
    `<div class="no-row"><span class="no-lbl">${esc(n.querySelector(".lbl").textContent)}</span>` +
    `<button class="no-up" data-i="${i}"${i === 0 ? " disabled" : ""}>↑</button>` +
    `<button class="no-dn" data-i="${i}"${i === navs.length - 1 ? " disabled" : ""}>↓</button></div>`).join("");
}
async function moveNav(i, dir) {
  const bar = document.querySelector(".sidebar");
  const navs = [...bar.querySelectorAll(".nav")];
  const j = i + dir; if (j < 0 || j >= navs.length) return;
  if (dir < 0) bar.insertBefore(navs[i], navs[j]); else bar.insertBefore(navs[j], navs[i]);
  renderNavOrder();
  const order = [...bar.querySelectorAll(".nav")].map((n) => n.dataset.view);
  try { await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nav_order: order }) }); } catch (e) { /* ignore */ }
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
// label, value, chg(可空), pct(可空), unit
function card(label, value, chg, pct, unit = "") {
  let sub = "";
  if (chg !== undefined && chg !== null) sub = `<div class="card-chg ${chgClass(chg)}">${chgText(chg)}${pctTag(pct)}</div>`;
  else if (pct !== undefined && pct !== null) sub = `<div class="card-chg ${chgClass(pct)}">${pct > 0 ? "▲" : pct < 0 ? "▼" : ""}${fmt(Math.abs(pct), 2)}%</div>`;
  return `<div class="card"><div class="card-label">${label}</div><div class="card-val">${value}${unit}</div>${sub}</div>`;
}
// 未平倉口數卡：依淨多/淨空上色（紅多綠空），附「較昨日」增減口數與百分比
function oiCard(label, v, prev) {
  if (v === null || v === undefined) return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const head = (v > 0 ? "淨多 " : v < 0 ? "淨空 " : "") + fmt(Math.abs(v), 0) + " 口";
  const { chg, pct } = dod(v, prev);
  const sub = chg == null ? "" : `<div class="card-chg ${chgClass(chg)}">較昨 ${chg > 0 ? "+" : ""}${fmt(chg, 0)} 口${pctTag(pct)}</div>`;
  return `<div class="card"><div class="card-label">${label}</div><div class="card-val ${cls}">${head}</div>${sub}</div>`;
}
// 買賣超/淨額卡：當日淨流量，數值依正負上色，附「較昨日」增減金額
// （淨流量基數會翻號、趨近 0，算百分比會失真，故只給金額增減、不給 %）
function flowCard(label, v, prev, unit = "") {
  if (v === null || v === undefined) return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  const chg = prev == null ? null : v - prev;
  const sub = chg == null ? "" : `<div class="card-chg ${chgClass(chg)}">較昨 ${chg > 0 ? "+" : ""}${fmt(chg)}${unit}</div>`;
  return `<div class="card"><div class="card-label">${label}</div><div class="card-val ${chgClass(v)}">${fmt(v)}${unit}</div>${sub}</div>`;
}
// 餘額卡（融資/融券）：當日尚未公布（晚間才出）時，退而顯示最近一筆有資料的交易日，並標註日期
function balanceCard(label, srcRow, curDate, balKey, chgKey) {
  if (!srcRow || srcRow[balKey] === null || srcRow[balKey] === undefined) {
    return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  }
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  return card(lbl, fmt(srcRow[balKey], 0), srcRow[chgKey], pctOf(srcRow[balKey], srcRow[chgKey]));
}
// 融資維持率卡：DB 未存官方逐日漲跌（不像融資/融券有 margin_chg/short_chg 現成值），
// 故從 hist 找 srcRow 當日之前最近一筆有值的交易日自行算較昨——比較基準是 srcRow 自己的日期，
// 而非「今天」，避免資料延遲時把「vs 6 天前」誤標成「較昨」
function marginMaintCard(hist, srcRow, curDate) {
  const label = "融資維持率";
  if (!srcRow || srcRow.margin_maintenance === null || srcRow.margin_maintenance === undefined) {
    return `<div class="card"><div class="card-label">${label}</div><div class="card-val">—</div></div>`;
  }
  const stale = srcRow.date && srcRow.date !== curDate;
  const lbl = label + (stale ? ` <span class="asof">截至 ${srcRow.date.slice(5)}</span>` : "");
  const idx = hist.findIndex((r) => r && r.date === srcRow.date);
  const priorRow = idx > 0
    ? [...hist.slice(0, idx)].reverse().find((r) => r && r.margin_maintenance != null)
    : null;
  const chg = priorRow ? srcRow.margin_maintenance - priorRow.margin_maintenance : null;
  return card(lbl, fmt(srcRow.margin_maintenance, 1) + "%", chg, pctOf(srcRow.margin_maintenance, chg));
}
function renderCards(m, prev = {}, hist = []) {
  if (!m || !m.date) { $("cards-tw").innerHTML = '<div class="muted">尚無大盤資料。</div>'; $("cards-fut").innerHTML = ""; $("cards-intl").innerHTML = ""; $("data-date").textContent = ""; return; }
  $("data-date").textContent = "資料日期：" + m.date;
  const ls = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "散戶偏多 " : v < 0 ? "散戶偏空 " : "") + fmt(v, 3));
  const sum3 = (r) => [r.inst_foreign, r.inst_trust, r.inst_dealer].every((x) => x != null)
    ? r.inst_foreign + r.inst_trust + r.inst_dealer : null;
  const lsm = dod(m.retail_ls_mtx, prev.retail_ls_mtx);
  const lst = dod(m.retail_ls_tmf, prev.retail_ls_tmf);
  // 融資/融券：當日有就用當日，否則退到最近一筆有資料的交易日（晚間才公布的容錯）
  const marginRow = [...hist].reverse().find((r) => r && r.margin_balance != null) || m;
  $("cards-tw").innerHTML = [
    card("加權指數", fmt(m.taiex), m.taiex_chg, pctOf(m.taiex, m.taiex_chg)),
    flowCard("外資買賣超", m.inst_foreign, prev.inst_foreign, " 億"),
    flowCard("投信買賣超", m.inst_trust, prev.inst_trust, " 億"),
    flowCard("自營買賣超", m.inst_dealer, prev.inst_dealer, " 億"),
    flowCard("三大法人合計", sum3(m), sum3(prev), " 億"),
    balanceCard("融資餘額(張)", marginRow, m.date, "margin_balance", "margin_chg"),
    balanceCard("融券餘額(張)", marginRow, m.date, "short_balance", "short_chg"),
    marginMaintCard(hist, marginRow, m.date),
  ].join("");
  $("cards-fut").innerHTML = [
    card("台指期", fmt(m.tx_price), m.tx_chg, pctOf(m.tx_price, m.tx_chg)),
    oiCard("外資台指淨未平倉", m.tx_foreign_oi, prev.tx_foreign_oi),
    oiCard("散戶小台淨未平倉", m.retail_oi_mtx, prev.retail_oi_mtx),
    card("小台散戶多空比", ls(m.retail_ls_mtx), lsm.chg, lsm.pct),
    card("微台散戶多空比", ls(m.retail_ls_tmf), lst.chg, lst.pct),
    card("VIX 恐慌指數", fmt(m.vix), chgPts(m.vix, m.vix_chg), m.vix_chg),
  ].join("");
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
  renderCards(d.latest, prev, hist);
  lastHistory = hist; loadChipTrend();
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
function sectorColor(chg) {
  if (chg == null) return "#2b3038";
  const t = 0.35 + 0.65 * Math.min(Math.abs(chg) / 3, 1); // 小漲跌也看得出方向
  return _hex("#2b3038", chg >= 0 ? "#e04545" : "#2ea043", t);
}

// 台股漲跌家數：紅漲綠跌的市場氣氛長條 + 漲停/跌停家數
async function loadBreadth() {
  const el = $("breadth"); if (!el) return;
  const note = $("breadth-note");
  try {
    const d = await getJSON("/api/breadth");
    if (d.up == null && d.down == null) { el.innerHTML = ""; if (note) note.textContent = ""; return; }
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
  } catch (e) { el.innerHTML = ""; }
}

// 亞當杯柄型態選股：清單 + K 線疊「趨勢線(左緣→右緣)＋壓力線(右緣水平)」
function cupChartOption(d, m) {
  const closes = d.candles.map((c) => c[1]);
  const maSeries = MA_DEFS.map((x) => ({ name: "MA" + x.n, type: "line", data: ma(closes, x.n),
    smooth: true, showSymbol: false, lineStyle: { width: 1, color: x.color } }));
  const lastDate = d.dates[d.dates.length - 1];
  const candle = {
    name: "K線", type: "candlestick", data: d.candles,
    itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" },
    markLine: {
      symbol: ["none", "none"],
      // 標籤放線段中段（非末端）：末端貼右緣會被 grid 裁切破版；中段有留白、也不會
      // 跟右緣 pin 疊在一起（右緣點＝趨勢線終點＝壓力線起點，三者同座標）
      label: { show: true, position: "middle", color: "#fff", fontSize: 11,
               backgroundColor: "rgba(0,0,0,0.55)", padding: [2, 4], borderRadius: 3 },
      data: [
        // 趨勢線不標字（左右緣已有 pin），避免與右緣 pin 疊字
        [{ coord: [m.left_date, m.left_price], lineStyle: { color: "#f0a500", width: 2 }, label: { show: false } },
         { coord: [m.right_date, m.right_price] }],
        [{ name: `壓力 ${fmt(m.resistance, 2)}`, coord: [m.right_date, m.resistance],
          lineStyle: { color: "#6cb6ff", width: 2, type: "dashed" } },
         { coord: [lastDate, m.resistance] }],
      ],
    },
    markPoint: {
      // 圖釘原本跟趨勢線同橘色、融進線裡不明顯：改亮黃＋白色描邊讓圖釘從線上「跳出來」，
      // 並用 symbolOffset 把圖釘往上提，避開與趨勢線／K棒交叉處的視覺重疊。
      symbol: "pin", symbolSize: 40, symbolOffset: [0, -10],
      itemStyle: { color: "#ffd23f", borderColor: "#fff", borderWidth: 1.5,
                   shadowColor: "rgba(0,0,0,0.5)", shadowBlur: 4 },
      label: { color: "#1a1a1a", fontSize: 12, fontWeight: 700, formatter: (p) => p.data.value },
      data: [{ value: "左緣", coord: [m.left_date, m.left_price] },
             { value: "右緣", coord: [m.right_date, m.right_price] }],
    },
  };
  if (m.stop_loss != null && m.stop_loss > 0)  // 停損線＝突破價−2×ATR14（部位管理，見下方說明）
    candle.markLine.data.push(
      [{ name: `停損 ${fmt(m.stop_loss, 2)}`, coord: [m.right_date, m.stop_loss],
        lineStyle: { color: "#e04545", width: 2, type: "dashed" } },
       { coord: [lastDate, m.stop_loss] }]);
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["K線", ...MA_DEFS.map((x) => "MA" + x.n)], textStyle: { color: "#ccc" } },
    grid: { left: 55, right: 30, top: 30, bottom: 50 },
    xAxis: { type: "category", data: d.dates, axisLabel: { color: "#999" } },
    yAxis: { scale: true, axisLabel: { color: "#999" } },
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
let cupData = null, cupPicksOnly = false;
async function loadCupHandle() {
  const list = $("cup-list"); if (!list) return;
  list.innerHTML = '<span class="muted small">篩選中…</span>';
  try {
    cupData = await getJSON("/api/patterns/cup-handle");
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
  if (note) note.textContent = `（${d.date}　符合 ${d.count} 檔`
    + (d.has_picks ? `／同時符合籌碼基本 ${d.picks_count} 檔` : "") + `／掃描 ${d.bars} 根）`;
  if (cupPicksOnly && !d.has_picks) { list.innerHTML = '<span class="muted small">尚未載入當日 CSV，無「籌碼/基本選股」可交集（請先到該分頁上傳）</span>'; if (cupChart) cupChart.clear(); renderCupRisk(null); return; }
  if (!cupMatches.length) { list.innerHTML = `<span class="muted small">${cupPicksOnly ? "無同時符合兩者的個股" : "今日無符合杯柄型態的個股"}</span>`; if (cupChart) cupChart.clear(); renderCupRisk(null); return; }
  list.innerHTML = cupMatches.map((m, i) =>
    `<a href="#" class="cup-chip${i === 0 ? " active" : ""}${m.in_picks ? " pick" : ""}" data-i="${i}">${esc(m.code)} ${esc(m.name || "")}<span class="cup-r">%R ${fmt(m.percent_r, 0)}</span></a>`).join("");
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
async function loadOsFutures(refresh) {
  const el = $("osfut"); if (!el) return;
  if (refresh || !el.innerHTML) el.innerHTML = '<div class="muted small">載入報價中…（首次抓取約 5–10 秒）</div>';
  try {
    const d = await getJSON("/api/os-futures" + (refresh ? "?refresh=1" : ""));
    const t = $("osfut-time");
    if (t && d.updated_at) t.textContent = "更新：" + d.updated_at.slice(0, 16).replace("T", " ");
    if (!d.categories || !d.categories.every) { el.innerHTML = '<div class="muted small">暫無報價，稍後按更新重試</div>'; return; }
    el.innerHTML = d.categories.filter((g) => g.items.length).map((g) => {
      const cards = g.items.map((it) => {
        const dp = osDecimals(it.value);
        const ps = it.chg_pct == null ? "" : (it.chg_pct >= 0 ? "+" : "") + fmt(it.chg_pct, 2) + "%";
        const cs = it.chg == null ? "" : (it.chg >= 0 ? "+" : "") + fmt(it.chg, dp);
        return `<div class="mv-card" style="background:${sectorColor(it.chg_pct)}">
          <div class="of-top"><span class="of-name">${esc(it.name)}</span><span class="of-price">${fmt(it.value, dp)}</span></div>
          <div class="of-bot"><span>${ps}</span><span>${cs}</span></div>
        </div>`;
      }).join("");
      return `<div class="card-group"><div class="group-title">${esc(g.category)}</div><div class="mv-grid">${cards}</div></div>`;
    }).join("");
    if (!el.innerHTML) el.innerHTML = '<div class="muted small">暫無報價，稍後按更新重試</div>';
  } catch (e) { el.innerHTML = '<div class="muted small">載入失敗：' + esc(e.message) + "</div>"; }
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

async function loadSectors() {
  const el = $("sectors");
  if (!el) return;
  const note = $("sectors-note");
  try {
    const d = await getJSON("/api/sectors");
    if (!d.sectors || !d.sectors.length) {
      renderSectorPills(el, note, null);
      el.innerHTML = '<div class="muted small">尚無類股資料</div>'; if (note) note.textContent = ""; return;
    }
    const useMcap = d.sectors.some((s) => s.mcap != null);
    if (note) {
      const up = d.sectors.filter((s) => s.chg_pct > 0).length;
      const down = d.sectors.filter((s) => s.chg_pct < 0).length;
      note.textContent = `（${d.date}　紅漲 ${up}・綠跌 ${down}，面積＝${useMcap ? "市值" : "成交值"}、顏色＝漲跌幅）`;
    }
    // 面積優先用市值（成分股股數×收盤加總）；抓不到市值時退回成交值
    const sizeOf = (s) => (useMcap ? s.mcap : s.turnover);
    const raw = d.sectors.filter((s) => sizeOf(s) && s.chg_pct != null);
    if (!raw.length) { renderSectorPills(el, note, d.sectors); return; } // 無面積數據→退回條列
    // 給最小面積下限，讓佔比很低的類股也看得到、點得到
    const maxV = Math.max(...raw.map(sizeOf));
    const floor = maxV * 0.014;
    const nodes = raw.map((s) => ({
      name: s.name, value: Math.max(sizeOf(s), floor), chg: s.chg_pct,
      mcap: s.mcap, turnover: s.turnover,
      itemStyle: { color: sectorColor(s.chg_pct) },
    }));
    // 切成圖表容器
    el.classList.remove("sectors");
    el.style.height = "380px";
    if (!sectorChart || sectorChart.getDom() !== el) sectorChart = echarts.init(el);
    sectorChart.setOption({
      tooltip: {
        formatter: (p) => {
          if (p.data.chg == null) return "上市類股";
          const sign = p.data.chg >= 0 ? "+" : "";
          const mc = p.data.mcap == null ? "" : `<br/>市值 ${fmt(p.data.mcap, 0)} 億`;
          const tv = p.data.turnover == null ? "" : `<br/>成交值 ${fmt(p.data.turnover / 1e8, 1)} 億`;
          return `${esc(p.name)}<br/>漲跌 <b>${sign}${fmt(p.data.chg, 2)}%</b>${mc}${tv}<br/><span style="color:#8a94a3">點擊看成分股</span>`;
        },
      },
      series: [{
        type: "treemap", roam: false, nodeClick: false, animationDuration: 300,
        breadcrumb: { show: false },
        left: 1, right: 1, top: 1, bottom: 1,
        itemStyle: { borderColor: "#0f1419", borderWidth: 2, gapWidth: 2 },
        label: {
          show: true, overflow: "truncate",
          formatter: (p) => {
            const sign = p.data.chg >= 0 ? "+" : "";
            return `{n|${esc(p.name)}}\n{v|${sign}${fmt(p.data.chg, 2)}%}`;
          },
          rich: {
            n: { fontSize: 13, fontWeight: 700, color: "#fff", lineHeight: 17 },
            v: { fontSize: 12, color: "#fff", lineHeight: 15 },
          },
        },
        data: nodes,
      }],
    }, true);
    const validNames = new Set(nodes.map((n) => n.name));
    sectorChart.off("click");
    sectorChart.on("click", (p) => { if (p && validNames.has(p.name)) loadSectorStocks(p.name, d.date); });
    sectorChart.resize();
  } catch (e) { el.innerHTML = '<div class="muted small">類股載入失敗</div>'; }
}

// 點類股 → 載入該族群成分股當日漲跌
async function loadSectorStocks(sector, date) {
  const box = $("sector-stocks");
  if (!box) return;
  box.innerHTML = `<div class="ss-head"><b>${esc(sector)}</b> 成分股　<span class="muted small">載入中…</span></div>`;
  try {
    const d = await getJSON(`/api/sectors/${encodeURIComponent(sector)}/stocks?date=${date || ""}`);
    if (!d.stocks || !d.stocks.length) { box.innerHTML = `<div class="ss-head"><b>${esc(sector)}</b> 成分股　<span class="muted small">尚無資料</span></div>`; return; }
    const cap = 60;
    const shown = d.stocks.slice(0, cap);
    const more = d.count > cap ? `（依市值排序，顯示前 ${cap} 檔，共 ${d.count} 檔）` : `（依市值排序，共 ${d.count} 檔）`;
    const cells = shown.map((s) => {
      const cls = chgClass(s.chg_pct);
      const sign = s.chg_pct > 0 ? "+" : "";
      const tip = s.mcap == null ? "" : ` title="市值約 ${fmt(s.mcap, 0)} 億"`;
      return `<div class="ss-cell ${cls}"${tip}><span class="ss-name">${esc(s.code)} ${esc(s.name || "")}</span><span class="ss-chg">${s.chg_pct == null ? "—" : sign + fmt(s.chg_pct, 2) + "%"}</span></div>`;
    }).join("");
    box.innerHTML = `<div class="ss-head"><b>${esc(sector)}</b> 成分股 <span class="muted small">${more}</span> <span class="ss-close" title="收合">✕</span></div><div class="ss-grid">${cells}</div>`;
    const close = box.querySelector(".ss-close");
    if (close) close.addEventListener("click", () => { box.innerHTML = ""; });
  } catch (e) { box.innerHTML = `<div class="ss-head"><b>${esc(sector)}</b> 成分股　<span class="muted small">載入失敗</span></div>`; }
}

// 退回原本的條列色塊（後端無成交值時的降級顯示）
function renderSectorPills(el, note, sectors) {
  if (sectorChart) { sectorChart.dispose(); sectorChart = null; }
  const ssbox = $("sector-stocks"); if (ssbox) ssbox.innerHTML = "";
  el.style.height = ""; el.classList.add("sectors");
  if (!sectors) { el.innerHTML = ""; return; }
  el.innerHTML = sectors.map((s) => {
    const cls = chgClass(s.chg_pct);
    const arrow = s.chg_pct > 0 ? "▲" : s.chg_pct < 0 ? "▼" : "";
    const pct = s.chg_pct == null ? "—" : arrow + fmt(Math.abs(s.chg_pct), 2) + "%";
    return `<div class="sector ${cls}"><span class="sec-name">${esc(s.name)}</span><span class="sec-chg">${pct}</span></div>`;
  }).join("");
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
        `<td><a href="#" class="watch-del" data-code="${s.code}" style="color:#e08585">移除</a></td></tr>`;
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
      series: [bar("外資", "inst_foreign", "#e0a23c"), bar("投信", "inst_trust", "#6cb6ff"), bar("自營", "inst_dealer", "#a07cff"), taiexLine] };
  }
  if (metric === "foreign_oi") {
    return { ...base, yAxis: [{ type: "value", name: "口", axisLabel: { color: "#999" } }, axisTaiex],
      series: [{ ...LP, name: "外資台指淨未平倉", data: hist.map((r) => r.tx_foreign_oi), areaStyle: { opacity: 0.08 }, lineStyle: { color: "#e0a23c" }, itemStyle: { color: "#e0a23c" }, markLine: zeroMark }, taiexLine] };
  }
  if (metric === "retail_ls") {
    return { ...base, yAxis: [{ type: "value", name: "多空比", axisLabel: { color: "#999" } }, axisTaiex],
      series: [
        { ...LP, name: "小台散戶多空比", data: hist.map((r) => r.retail_ls_mtx), lineStyle: { color: "#e0a23c" }, itemStyle: { color: "#e0a23c" }, markLine: zeroMark },
        { ...LP, name: "微台散戶多空比", data: hist.map((r) => r.retail_ls_tmf), lineStyle: { color: "#6cb6ff" }, itemStyle: { color: "#6cb6ff" } },
        taiexLine] };
  }
  // margin：融資（左軸）+ 融券（右軸，量級差很多）
  return { ...base, yAxis: [
      { type: "value", name: "融資(張)", axisLabel: { color: "#999" } },
      { type: "value", name: "融券(張)", position: "right", axisLabel: { color: "#999" }, splitLine: { show: false } }],
    series: [
      { ...LP, name: "融資餘額", data: hist.map((r) => r.margin_balance), lineStyle: { color: "#e04545" }, itemStyle: { color: "#e04545" } },
      { ...LP, name: "融券餘額", yAxisIndex: 1, data: hist.map((r) => r.short_balance), lineStyle: { color: "#2ea043" }, itemStyle: { color: "#2ea043" } }] };
}
function loadChipTrend() {
  if (!$("chipchart")) return;
  if (!chipChart) chipChart = echarts.init($("chipchart"));
  if (!lastHistory.length) { chipChart.clear(); return; }
  chipChart.setOption(chipTrendOption(lastHistory, chipMetric), true);
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
      { name: "日盤量", type: "bar", data: d.day_volume, itemStyle: { color: "#6cb6ff" } },
      { name: "夜盤量", type: "bar", data: d.night_volume, itemStyle: { color: "#e0a23c" } },
      { name: "夜/日比", type: "line", yAxisIndex: 1, data: d.ratio, symbolSize: 4,
        lineStyle: { color: "#e04545" }, itemStyle: { color: "#e04545" } },
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
  } catch (e) { txVolChart.clear(); }
}

async function loadMarketSummary(refresh) {
  const box = $("market-summary"); box.textContent = "AI 生成中…";
  try { const s = await getJSON("/api/market/summary" + (refresh ? "?refresh=1" : "")); box.textContent = s.text || ""; box.classList.toggle("disabled", !s.enabled); }
  catch (e) { box.textContent = "AI 摘要失敗：" + e.message; }
}
async function loadCsvSummary(refresh) {
  const box = $("csv-summary"); box.textContent = "AI 生成中…";
  try { const s = await getJSON("/api/analysis/summary" + (refresh ? "?refresh=1" : "")); box.textContent = s.text || ""; box.classList.toggle("disabled", !s.enabled); }
  catch (e) { box.textContent = "AI 分析失敗：" + e.message; }
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

let currentPicks = [], subFilter = null;
function renderSubFilterChip() {
  const el = $("sub-filter");
  if (subFilter) {
    el.innerHTML = `篩選：<b>${esc(subFilter)}</b>（${currentPicks.filter((p) => p.sub_industry === subFilter).length} 檔） <a href="#" id="clear-sub">✕ 全部</a>`;
    const clr = $("clear-sub"); if (clr) clr.addEventListener("click", (e) => { e.preventDefault(); subFilter = null; renderDailyView(); });
  } else { el.innerHTML = `共 ${currentPicks.length} 檔`; }
}
function renderDaily(picks) { if (!sortState.daily) sortState.daily = { key: "lan_value", asc: false }; renderSortable("daily", PICK_COLS, picks, "無符合條件的個股"); }
function renderDailyView() { renderSubFilterChip(); renderDaily(subFilter ? currentPicks.filter((p) => p.sub_industry === subFilter) : currentPicks); }
function renderIndustry(subind) {
  if (!sortState.industry) sortState.industry = { key: "count", asc: false };
  renderSortable("industry", SUBIND_COLS, subind, "無資料", (r) => { subFilter = r.sub_industry; renderDailyView(); });
}
async function loadDaily(date) {
  try {
    const d = await getJSON("/api/analysis/daily" + (date ? `?date=${encodeURIComponent(date)}` : ""));
    currentPicks = d.picks || []; subFilter = null;
    renderIndustry(d.subindustry || []); renderDailyView();
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
    ["TWSE估值", [["本益比(TWSE)", fmt(v.pe)], ["殖利率%", fmt(v.yield)], ["淨值比", fmt(v.pb)]]],
  ];
  el.innerHTML = groups.map(([t, items]) => `<div class="pf-group"><span class="pf-title">${t}</span>${items.map(([k, val]) => `<span class="pf-item"><b>${k}</b> ${val}</span>`).join("")}</div>`).join("");
}
async function loadStockChips(code) {
  const wrap = $("stock-chips-wrap");
  if (!wrap) return;
  wrap.classList.remove("hidden");
  if (!stockChipsChart) stockChipsChart = echarts.init($("stock-chips"));
  stockChipsChart.showLoading();
  const note = $("stock-chips-note");
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/chips?days=10`);
    stockChipsChart.hideLoading();
    if (!d.total || !d.total.some((v) => v != null)) { stockChipsChart.clear(); if (note) note.textContent = "（查無此股三大法人資料）"; return; }
    const last = [...d.total].reverse().find((v) => v != null);
    const mk = d.market === "tpex" ? "上櫃" : "上市";
    if (note) note.textContent = `（${mk}・最新合計 ${last > 0 ? "+" : ""}${fmt(last, 0)} 張）`;
    const bar = (name, arr, color) => ({ name, type: "bar", stack: "三大法人", data: arr, itemStyle: { color } });
    stockChipsChart.setOption({
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { textStyle: { color: "#ccc" }, top: 0 },
      grid: { left: 58, right: 16, top: 26, bottom: 24 },
      xAxis: { type: "category", data: d.dates.map((x) => x.slice(5)), axisLabel: { color: "#999" } },
      yAxis: { type: "value", name: "張", axisLabel: { color: "#999" }, splitLine: { lineStyle: { color: "#262d38" } } },
      series: [bar("外資", d.foreign, "#e0a23c"), bar("投信", d.trust, "#6cb6ff"), bar("自營", d.dealer, "#a07cff")],
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
    const line = (name, key, color) => ({ name, type: "line", smooth: 0.2, showSymbol: true, symbolSize: 5, data: d.trend.map((t) => t[key]), lineStyle: { color }, itemStyle: { color } });
    stockCustodyChart.setOption({
      tooltip: { trigger: "axis" }, legend: { textStyle: { color: "#ccc" }, top: 0 },
      grid: { left: 48, right: 16, top: 26, bottom: 24 },
      xAxis: { type: "category", data: wk, boundaryGap: false, axisLabel: { color: "#999" } },
      yAxis: { type: "value", name: "%", axisLabel: { color: "#999" }, splitLine: { lineStyle: { color: "#262d38" } } },
      series: [line("千張大戶%", "big1000_pct", "#e0a23c"), line("400張↑大戶%", "big400_pct", "#6cb6ff")],
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
  if (res.error && !res.count) { info.innerHTML = `<span style="color:#e08585">⚠ ${esc(res.error)}</span>`; return; }
  if (!res.count) { info.innerHTML = `<span style="color:#e08585">⚠ 讀到 0 檔（${res.snap_date}）。請確認是籌碼匯出檔。</span>`; return; }
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
$("btn-export").addEventListener("click", () => {
  const url = `/api/analysis/export?date=${encodeURIComponent($("date-select").value || "")}` + (subFilter ? `&sub=${encodeURIComponent(subFilter)}` : "");
  window.location.href = url;
});
$("btn-save-settings").addEventListener("click", saveSettings);
$("btn-osfut-refresh").addEventListener("click", () => loadOsFutures(true));
$("btn-cup-refresh").addEventListener("click", loadCupHandle);
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
$("nav-order").addEventListener("click", (e) => {
  const up = e.target.closest(".no-up"), dn = e.target.closest(".no-dn");
  if (up) moveNav(+up.dataset.i, -1); else if (dn) moveNav(+dn.dataset.i, 1);
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
window.addEventListener("resize", () => { idxChart && idxChart.resize(); stockChart && stockChart.resize(); chipChart && chipChart.resize(); stockChipsChart && stockChipsChart.resize(); sectorChart && sectorChart.resize(); cupChart && cupChart.resize(); txVolChart && txVolChart.resize(); });

// ========== 初始載入 ==========
(async () => {
  try { applyNavOrder((await getJSON("/api/settings")).nav_order); } catch (e) { /* 用預設順序 */ }
  const d = await loadDashboard();
  loadIndexChart();
  loadBreadth();
  loadMovers();
  loadSectors();
  loadMarketSummary(false);  // 讀快取即回；排程更新完會自動預先生成，開頁不另扣費
  loadInstRanking();
  loadOptionsSentiment();
  loadDates();
  loadWeekly();
  // 自動更新：無資料、或資料非當日（平日尚未更新到最新交易日）時，自動抓一次
  if (!d || !d.latest || !d.latest.date || d.data_stale) autoUpdate();
})();
