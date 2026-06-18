"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
// 台灣慣例：紅漲綠跌
const chgClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const chgText = (v, d = 2) => (v === null || v === undefined ? "" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v), d));

let idxChart, klineChart;
let idxSymbol = "taiex", idxInterval = "1d";
let showWaves = false;
let wavePct = 0.05;
let lastIndexData = null, lastKlineData = null;
const MA_DEFS = [
  { n: 5, color: "#5b8ff9" },
  { n: 20, color: "#5ad8a6" },
  { n: 60, color: "#f6bd16" },
  { n: 120, color: "#e8684a" },
];

// 艾略特波浪偵測（與後端 elliott.py 同邏輯，前端版供靈敏度即時調整）
function zigzag(vals, pct) {
  const n = vals.length;
  if (!n) return [];
  const piv = [];
  let pi = 0, pv = vals[0], trend = 0;
  for (let i = 1; i < n; i++) {
    const v = vals[i];
    if (trend === 0) {
      if (pv && Math.abs(v - pv) / Math.abs(pv) >= pct) { trend = v > pv ? 1 : -1; piv.push(pi); pi = i; pv = v; }
    } else if (trend === 1) {
      if (v > pv) { pi = i; pv = v; }
      else if (pv && (pv - v) / Math.abs(pv) >= pct) { piv.push(pi); trend = -1; pi = i; pv = v; }
    } else {
      if (v < pv) { pi = i; pv = v; }
      else if (pv && (v - pv) / Math.abs(pv) >= pct) { piv.push(pi); trend = 1; pi = i; pv = v; }
    }
  }
  piv.push(pi);
  return piv;
}
function impulseLabels(closes, seg) {
  const p = seg.map((i) => closes[i]); const up = p[1] > p[0];
  let shape, r2, r3t, r4;
  if (up) { shape = p[1] > p[0] && p[2] < p[1] && p[3] > p[2] && p[4] < p[3] && p[5] > p[4]; r2 = p[2] > p[0]; r3t = p[3] > p[1]; r4 = p[4] > p[1]; }
  else { shape = p[1] < p[0] && p[2] > p[1] && p[3] < p[2] && p[4] > p[3] && p[5] < p[4]; r2 = p[2] < p[0]; r3t = p[3] < p[1]; r4 = p[4] < p[1]; }
  const w1 = Math.abs(p[1] - p[0]), w3 = Math.abs(p[3] - p[2]), w5 = Math.abs(p[5] - p[4]);
  const r3s = !(w3 < w1 && w3 < w5);
  if (!(shape && r2 && r3t && r4 && r3s)) return [];
  return [0, 1, 2, 3, 4].map((k) => ({ index: seg[k + 1], label: String(k + 1) }));
}
function abcLabels(closes, seg, up) {
  const p = seg.map((i) => closes[i]);
  const ok = up ? (p[1] < p[0] && p[2] > p[1] && p[3] < p[2]) : (p[1] > p[0] && p[2] < p[1] && p[3] > p[2]);
  if (!ok) return [];
  return [["A", 1], ["B", 2], ["C", 3]].map(([l, i]) => ({ index: seg[i], label: l }));
}
function elliottWaves(closes, pct) {
  const piv = zigzag(closes, pct);
  if (piv.length >= 9) {
    const imp = impulseLabels(closes, piv.slice(-9, -3));
    if (imp.length) {
      const up = closes[piv[piv.length - 8]] > closes[piv[piv.length - 9]];
      const abc = abcLabels(closes, piv.slice(-4), up);
      if (abc.length) return imp.concat(abc);
    }
  }
  if (piv.length >= 6) return impulseLabels(closes, piv.slice(-6));
  return [];
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

// ---------- 大盤儀表板 ----------
function card(label, value, chg, unit = "") {
  const cc = chg === undefined ? "" : chgClass(chg);
  const ct = chg === undefined ? "" : `<div class="card-chg ${cc}">${chgText(chg)}</div>`;
  return `<div class="card"><div class="card-label">${label}</div><div class="card-val">${value}${unit}</div>${ct}</div>`;
}

function renderCards(m) {
  if (!m || !m.date) {
    $("cards").innerHTML = '<div class="muted">尚無大盤資料，請按「一鍵更新」。</div>';
    return;
  }
  const lsTxt = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "散戶偏多 " : v < 0 ? "散戶偏空 " : "") + fmt(v, 3));
  const cards = [
    card("加權指數", fmt(m.taiex), m.taiex_chg),
    card("外資買賣超", fmt(m.inst_foreign), m.inst_foreign, " 億"),
    card("投信買賣超", fmt(m.inst_trust), m.inst_trust, " 億"),
    card("自營買賣超", fmt(m.inst_dealer), m.inst_dealer, " 億"),
    card("台指期", fmt(m.tx_price), m.tx_chg),
    card("小台散戶多空比", lsTxt(m.retail_ls_mtx), m.retail_ls_mtx),
    card("微台散戶多空比", lsTxt(m.retail_ls_tmf), m.retail_ls_tmf),
    card("融資餘額(張)", fmt(m.margin_balance, 0), m.margin_chg),
    card("融券餘額(張)", fmt(m.short_balance, 0), m.short_chg),
    card("費城半導體", fmt(m.sox)),
    card("日經225", fmt(m.n225)),
    card("韓股KOSPI", fmt(m.kospi)),
    card("黃金", fmt(m.gold)),
    card("比特幣", fmt(m.btc)),
  ];
  $("cards").innerHTML = cards.join("");
}

// 共用蠟燭圖 option（K + MA5 + MA20 + 量），dashboard 指數圖與個股 K 線共用
function candlestickOption(data, startPct) {
  const closes = data.candles.map((c) => c[1]);
  const maSeries = MA_DEFS.map((m) => ({
    name: "MA" + m.n, type: "line", data: ma(closes, m.n), smooth: true, showSymbol: false,
    lineStyle: { width: 1, color: m.color }, itemStyle: { color: m.color },
  }));
  const candle = {
    name: "K線", type: "candlestick", data: data.candles,
    itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" },
  };
  if (showWaves) {
    const waves = elliottWaves(closes, wavePct);
    if (waves.length) {
      candle.markPoint = {
        symbol: "circle", symbolSize: 20,
        label: { color: "#1a1a1a", fontWeight: 700, fontSize: 12, formatter: (p) => p.data.value },
        data: waves.map((w) => ({
          value: w.label,
          coord: [data.dates[w.index], data.candles[w.index][3]],
          itemStyle: { color: /[ABC]/.test(w.label) ? "#6cb6ff" : "#f0a500" }, // 修正浪藍、推動浪橘
        })),
      };
    }
  }
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["K線", ...MA_DEFS.map((m) => "MA" + m.n)], textStyle: { color: "#ccc" } },
    grid: [
      { left: 60, right: 20, top: 30, height: "62%" },
      { left: 60, right: 20, top: "78%", height: "14%" },
    ],
    xAxis: [
      { type: "category", data: data.dates, axisLabel: { color: "#999" } },
      { type: "category", data: data.dates, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: "#999" } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: startPct },
      { type: "slider", xAxisIndex: [0, 1], start: startPct, bottom: 0, height: 16 },
    ],
    series: [candle, ...maSeries, { name: "量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: data.volumes }],
  };
}

async function loadIndexChart() {
  if (!idxChart) idxChart = echarts.init($("idxchart"));
  idxChart.showLoading();
  try {
    const d = await getJSON(`/api/index/kline?symbol=${idxSymbol}&interval=${idxInterval}`);
    idxChart.hideLoading();
    if (!d.candles || !d.candles.length) {
      idxChart.clear();
      $("idx-note").textContent = idxSymbol === "tx" ? "台指期 K 線由每日更新累積，請先按幾天「一鍵更新」" : "尚無資料";
      return;
    }
    $("idx-note").textContent = d.proxy
      ? "（台指期歷史不足，暫以加權指數近似；每日更新累積後改用真實台指期）"
      : (idxSymbol === "tx" ? "（台指期：每日更新累積）" : "");
    lastIndexData = d;
    idxChart.setOption(candlestickOption(d, d.candles.length > 120 ? 70 : 0), true);
  } catch (e) {
    idxChart.hideLoading();
    $("idx-note").textContent = "載入失敗：" + e.message;
  }
}

async function loadDashboard() {
  const d = await getJSON("/api/dashboard");
  renderCards(d.latest);
  if (d.latest && d.latest.updated_at) {
    $("last-updated").textContent = "更新：" + d.latest.updated_at.replace("T", " ").slice(0, 19);
  }
}

async function loadMarketSummary(refresh) {
  const box = $("market-summary");
  box.textContent = "AI 生成中…";
  try {
    const s = await getJSON("/api/market/summary" + (refresh ? "?refresh=1" : ""));
    box.textContent = s.text || "";
    box.classList.toggle("disabled", !s.enabled);
  } catch (e) { box.textContent = "AI 摘要失敗：" + e.message; }
}

async function loadCsvSummary(refresh) {
  const box = $("csv-summary");
  box.textContent = "AI 生成中…";
  try {
    const s = await getJSON("/api/analysis/summary" + (refresh ? "?refresh=1" : ""));
    box.textContent = s.text || "";
    box.classList.toggle("disabled", !s.enabled);
  } catch (e) { box.textContent = "AI 分析失敗：" + e.message; }
}

// ---------- 一鍵更新 ----------
async function runUpdate() {
  const btn = $("btn-update");
  btn.disabled = true;
  btn.textContent = "更新中…";
  const bar = $("update-status");
  bar.classList.remove("hidden");
  bar.textContent = "正在抓取 TWSE / TAIFEX / 國際指數…";
  try {
    const r = await fetch("/api/update/run", { method: "POST" });
    const res = await r.json();
    const ok = (res.success || []).join("、");
    const fail = (res.failed || []).map((f) => `${f.name}(${f.error})`).join("；");
    bar.innerHTML = `✅ 成功：${ok || "無"}` + (fail ? `　❌ 失敗：${fail}` : "");
    bar.className = "status-bar " + (fail ? "warn" : "ok");
    await loadDashboard();
    await loadIndexChart();
  } catch (e) {
    bar.textContent = "更新失敗：" + e.message;
    bar.className = "status-bar err";
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ 一鍵更新";
  }
}

// ---------- CSV 分析 ----------
function flagBadges(f) {
  if (!f) return "";
  const b = [];
  if (f.w55_bull) b.push('<span class="badge tech">W55翻多</span>');
  if (f.rev_growth) b.push('<span class="badge fund">營收增</span>');
  if (f.inst_buy) b.push('<span class="badge chip">法人買</span>');
  return b.join("");
}

function stockLink(code, name) {
  return `<a href="#" class="stock" data-code="${code}" data-name="${name || ""}">${code} ${name || ""}</a>`;
}

// 蘭值（蘭質/LPE）：>60 標粗紅
function lanCell(v) {
  if (v === null || v === undefined) return "—";
  return v > 60 ? `<b style="color:var(--up)">${fmt(v, 1)}</b>` : fmt(v, 1);
}

// 通用可排序表格：點欄位標題即排序（數字欄位數值排序）
const sortState = {};
function renderSortable(elId, columns, rows, emptyMsg, onRowClick) {
  if (!rows || !rows.length) { $(elId).innerHTML = `<div class="muted">${emptyMsg || "無資料"}</div>`; return; }
  const st = sortState[elId] || {};
  const data = rows.slice();
  if (st.key) {
    const col = columns.find((c) => c.key === st.key) || {};
    data.sort((a, b) => {
      let va = a[st.key], vb = b[st.key];
      if (col.numeric) {
        va = va === null || va === undefined ? -Infinity : Number(va);
        vb = vb === null || vb === undefined ? -Infinity : Number(vb);
        return st.asc ? va - vb : vb - va;
      }
      va = va == null ? "" : String(va); vb = vb == null ? "" : String(vb);
      return st.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    });
  }
  const head = "<tr>" + columns.map((c) => {
    const arrow = st.key === c.key ? (st.asc ? " ▲" : " ▼") : "";
    return `<th class="sortable" data-sort="${c.key}">${c.label}${arrow}</th>`;
  }).join("") + "</tr>";
  const body = data.map((r, i) => `<tr data-i="${i}"${onRowClick ? ' class="clickrow"' : ""}>` + columns.map((c) =>
    `<td>${c.render ? c.render(r) : fmt(r[c.key], c.dp === undefined ? 2 : c.dp)}</td>`
  ).join("") + "</tr>").join("");
  $(elId).innerHTML = `<table>${head}${body}</table>`;
  $(elId).querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.sort, cur = sortState[elId] || {};
      sortState[elId] = { key, asc: cur.key === key ? !cur.asc : false };
      renderSortable(elId, columns, rows, emptyMsg, onRowClick);
    })
  );
  if (onRowClick) {
    $(elId).querySelectorAll("tr[data-i]").forEach((tr) =>
      tr.addEventListener("click", () => onRowClick(data[Number(tr.dataset.i)]))
    );
  }
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

let currentPicks = [];
let subFilter = null;

function renderSubFilterChip() {
  const el = $("sub-filter");
  if (subFilter) {
    el.innerHTML = `篩選細產業：<b>${subFilter}</b>（${currentPicks.filter((p) => p.sub_industry === subFilter).length} 檔） <a href="#" id="clear-sub">✕ 顯示全部</a>`;
    const clr = $("clear-sub");
    if (clr) clr.addEventListener("click", (e) => { e.preventDefault(); subFilter = null; renderDailyView(); });
  } else {
    el.innerHTML = `<span class="muted">共 ${currentPicks.length} 檔（點上方細產業可篩選）</span>`;
  }
}

function renderDaily(picks) {
  if (!sortState.daily) sortState.daily = { key: "lan_value", asc: false };
  renderSortable("daily", PICK_COLS, picks, "無符合條件的個股（W55翻多＋大戶增＋營收年增＞0＋推估EPS＞0）");
}

function renderDailyView() {
  const rows = subFilter ? currentPicks.filter((p) => p.sub_industry === subFilter) : currentPicks;
  renderSubFilterChip();
  renderDaily(rows);
}

function renderIndustry(subind) {
  if (!sortState.industry) sortState.industry = { key: "count", asc: false };
  renderSortable("industry", SUBIND_COLS, subind, "無符合條件的個股",
    (r) => { subFilter = r.sub_industry; renderDailyView(); });
}

function statusBadge(s) {
  const map = { "新進榜": "new", "加速": "acc", "持平": "flat2", "退榜": "out" };
  return `<span class="status ${map[s] || ""}">${s}</span>`;
}

function renderWeekly(data) {
  if (data.note) { $("weekly").innerHTML = `<div class="muted">${data.note}</div>`; return; }
  $("weekly-dates").textContent = data.this_date ? `（${data.last_date} → ${data.this_date}）` : "";
  const rows = (data.stocks || []).filter((r) => r.status !== "持平");
  if (!rows.length) {
    $("weekly").innerHTML = '<div class="muted">本週與上週相比無新進榜／加速／退榜的個股（或兩份資料相同）。</div>';
    return;
  }
  rows.sort((a, b) => (b.custody_delta || -999) - (a.custody_delta || -999));
  const head = "<tr><th>股票</th><th>狀態</th><th>集保Δ</th><th>大戶增比</th><th>產業</th></tr>";
  const body = rows.map((r) =>
    `<tr><td>${stockLink(r.code, r.name)}</td><td>${statusBadge(r.status)}</td><td>${fmt(r.custody_delta, 2)}</td><td>${fmt(r.big_holder_ratio, 2)}</td><td>${r.industry || ""}</td></tr>`
  ).join("");
  $("weekly").innerHTML = `<table>${head}${body}</table>`;
}

async function loadDaily(date) {
  try {
    const q = date ? `?date=${encodeURIComponent(date)}` : "";
    const d = await getJSON("/api/analysis/daily" + q);
    currentPicks = d.picks || [];
    subFilter = null;
    renderIndustry(d.subindustry || []);
    renderDailyView();
    if (d.snap_date) $("date-select").value = d.snap_date;
  } catch (e) { /* 忽略 */ }
}

async function loadDates() {
  try {
    const s = await getJSON("/api/snapshots");
    const dates = s.dates || [];
    const sel = $("date-select");
    sel.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
    const latest = dates[dates.length - 1];
    await loadDaily(latest);
  } catch (e) { /* 忽略 */ }
}

async function loadWeekly() {
  try {
    const w = await getJSON("/api/analysis/weekly");
    renderWeekly(w);
  } catch (e) { /* 忽略 */ }
}

async function applyImportResult(res) {
  const info = $("upload-info");
  if (res.error && !res.count) {
    info.innerHTML = `<span style="color:#e08585">⚠ ${res.error}</span>`;
    return;
  }
  if (!res.count) {
    info.innerHTML = `<span style="color:#e08585">⚠ 讀到 0 檔（${res.snap_date}）。請確認是籌碼匯出檔（含「代碼／商品／大戶增比」等欄位）。</span>`;
    return;
  }
  info.textContent = `已匯入 ${res.file ? res.file + "：" : ""}${res.snap_date}，共 ${res.count} 檔`;
  await loadDates();
  await loadWeekly();
}

async function uploadCsv(file) {
  $("upload-info").textContent = "解析中…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/csv/upload", { method: "POST", body: fd });
    await applyImportResult(await r.json());
  } catch (e) {
    $("upload-info").textContent = "上傳失敗：" + e.message;
  }
}

async function importLatest() {
  $("upload-info").textContent = "讀取資料夾最新檔…";
  try {
    const r = await fetch("/api/csv/import-latest", { method: "POST" });
    await applyImportResult(await r.json());
  } catch (e) {
    $("upload-info").textContent = "讀取失敗：" + e.message;
  }
}

// ---------- K 線 ----------
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

let klineCode = "", klineName = "", klineInterval = "1d";
const TF_LABEL = { "1h": "1小時", "1d": "日", "1wk": "週", "1mo": "月" };

async function loadKline() {
  if (!klineChart) klineChart = echarts.init($("kline"));
  $("kline-title").textContent = `${klineCode} ${klineName || ""} ${TF_LABEL[klineInterval]}K線`;
  klineChart.showLoading();
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(klineCode)}/kline?interval=${klineInterval}`);
    klineChart.hideLoading();
    if (!d.candles || !d.candles.length) {
      klineChart.clear();
      $("kline-title").textContent = `${klineCode} 無 K 線資料`;
      return;
    }
    lastKlineData = d;
    klineChart.setOption(candlestickOption(d, d.candles.length > 120 ? 60 : 0), true);
  } catch (e) {
    klineChart.hideLoading();
    $("kline-title").textContent = `${klineCode} 無 K 線資料（${e.message}）`;
  }
}

function renderProfile(p) {
  const el = $("kline-profile");
  if (!p || !p.chip) { el.innerHTML = ""; return; }
  const c = p.chip, v = p.valuation || {};
  const groups = [
    ["籌碼面", [["大戶增比", fmt(c.big_holder_ratio)], ["人數降比", fmt(c.holder_drop_ratio)], ["集保大戶", fmt(c.custody)], ["投信3日", fmt(c.trust_3d)], ["外資3日", fmt(c.foreign_3d)]]],
    ["技術面", [["W55", Number(c.w55) >= 1 ? "翻多 ✓" : "—"]]],
    ["基本/財務", [["營收年增%", fmt(c.rev_yoy, 1)], ["推估EPS(下季)", fmt(c.est_profit)], ["蘭質(財評/15)", fmt(c.lan_score)], ["本益比(LPE)", fmt(c.lpe)], ["蘭值", lanCell(c.lan_value)], ["市值(億)", fmt(c.market_cap, 0)], ["股本(億)", fmt(c.capital)]]],
    ["TWSE估值", [["本益比(TWSE)", fmt(v.pe)], ["殖利率%", fmt(v.yield)], ["淨值比", fmt(v.pb)]]],
  ];
  el.innerHTML = groups.map(([title, items]) =>
    `<div class="pf-group"><span class="pf-title">${title}</span>${items.map(([k, val]) => `<span class="pf-item"><b>${k}</b> ${val}</span>`).join("")}</div>`
  ).join("");
}

async function loadProfile() {
  try {
    renderProfile(await getJSON(`/api/stock/${encodeURIComponent(klineCode)}/profile`));
  } catch (e) { $("kline-profile").innerHTML = ""; }
}

function openKline(code, name) {
  klineCode = code;
  klineName = name;
  klineInterval = "1d";
  document.querySelectorAll(".ktf").forEach((b) => b.classList.toggle("active", b.dataset.iv === "1d"));
  $("kline-profile").innerHTML = "";
  $("kline-modal").classList.remove("hidden");
  loadKline();
  loadProfile();
}

// ---------- 事件綁定 ----------
$("btn-update").addEventListener("click", runUpdate);
$("csv").addEventListener("change", (e) => { if (e.target.files[0]) uploadCsv(e.target.files[0]); });
$("btn-latest").addEventListener("click", importLatest);
document.querySelectorAll(".ktf").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".ktf").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    klineInterval = btn.dataset.iv;
    loadKline();
  })
);
$("kline-close").addEventListener("click", () => $("kline-modal").classList.add("hidden"));
$("kline-modal").addEventListener("click", (e) => { if (e.target.id === "kline-modal") $("kline-modal").classList.add("hidden"); });
document.addEventListener("click", (e) => {
  const a = e.target.closest("a.stock");
  if (a) { e.preventDefault(); openKline(a.dataset.code, a.dataset.name); }
});
window.addEventListener("resize", () => { idxChart && idxChart.resize(); klineChart && klineChart.resize(); });

// 指數圖：商品選擇（加權/台指期）
document.querySelectorAll('input[name="idx"]').forEach((el) =>
  el.addEventListener("change", (e) => { idxSymbol = e.target.value; loadIndexChart(); })
);
// 指數圖：日/週/月切換
document.querySelectorAll(".tf").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tf").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    idxInterval = btn.dataset.iv;
    loadIndexChart();
  })
);

$("date-select").addEventListener("change", (e) => loadDaily(e.target.value));
$("btn-ai-market").addEventListener("click", () => loadMarketSummary(true));
$("btn-ai-csv").addEventListener("click", () => loadCsvSummary(true));
function redrawCharts() {
  if (idxChart && lastIndexData) idxChart.setOption(candlestickOption(lastIndexData, lastIndexData.candles.length > 120 ? 70 : 0), true);
  if (klineChart && lastKlineData && !$("kline-modal").classList.contains("hidden")) {
    klineChart.setOption(candlestickOption(lastKlineData, lastKlineData.candles.length > 120 ? 60 : 0), true);
  }
}
$("wave-chk").addEventListener("change", (e) => { showWaves = e.target.checked; redrawCharts(); });
$("wave-pct").addEventListener("input", (e) => {
  wavePct = Number(e.target.value) / 100;
  $("wave-pct-val").textContent = `轉折 ${e.target.value}%`;
  if (showWaves) redrawCharts();
});
$("btn-export").addEventListener("click", () => {
  const date = $("date-select").value || "";
  const url = `/api/analysis/export?date=${encodeURIComponent(date)}` + (subFilter ? `&sub=${encodeURIComponent(subFilter)}` : "");
  window.location.href = url;
});

// 初始載入
loadDashboard();
loadIndexChart();
loadDates();
loadWeekly();
