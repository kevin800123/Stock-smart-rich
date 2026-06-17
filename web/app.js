"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
// 台灣慣例：紅漲綠跌
const chgClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const chgText = (v, d = 2) => (v === null || v === undefined ? "" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v), d));

let trendChart, klineChart;

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

function renderTrend(history) {
  if (!trendChart) trendChart = echarts.init($("trend"));
  const dates = history.map((r) => r.date);
  const series = [
    { key: "taiex", name: "加權指數" },
    { key: "tx_price", name: "台指期" },
  ].map((s) => ({ name: s.name, type: "line", smooth: true, showSymbol: false, data: history.map((r) => r[s.key]) }));
  trendChart.setOption({
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#ccc" } },
    grid: { left: 60, right: 20, top: 30, bottom: 30 },
    xAxis: { type: "category", data: dates, axisLabel: { color: "#999" } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#999" } },
    series,
  });
}

async function loadDashboard() {
  const d = await getJSON("/api/dashboard");
  renderCards(d.latest);
  renderTrend(d.history || []);
  if (d.latest && d.latest.updated_at) {
    $("last-updated").textContent = "更新：" + d.latest.updated_at.replace("T", " ").slice(0, 19);
  }
}

async function loadMarketSummary() {
  try {
    const s = await getJSON("/api/market/summary");
    $("market-summary").textContent = s.text || "";
    $("market-summary").classList.toggle("disabled", !s.enabled);
  } catch (e) { /* 忽略 */ }
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
    await loadMarketSummary();
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

function renderDaily(rows) {
  if (!rows || !rows.length) { $("daily").innerHTML = '<div class="muted">尚無資料</div>'; return; }
  const head = "<tr><th>股票</th><th>分數</th><th>大戶增比</th><th>人數降比</th><th>年增%</th><th>訊號</th></tr>";
  const body = rows.map((r) =>
    `<tr><td>${stockLink(r.code, r.name)}</td><td>${fmt(r.score, 3)}</td><td>${fmt(r.big_holder_ratio, 2)}</td><td>${fmt(r.holder_drop_ratio, 2)}</td><td>${fmt(r.rev_yoy, 1)}</td><td>${flagBadges(r.flags)}</td></tr>`
  ).join("");
  $("daily").innerHTML = `<table>${head}${body}</table>`;
}

function renderIndustry(rows) {
  if (!rows || !rows.length) { $("industry").innerHTML = '<div class="muted">尚無資料</div>'; return; }
  const head = "<tr><th>產業</th><th>檔數</th><th>平均分數</th></tr>";
  const body = rows.map((r) => `<tr><td>${r.industry}</td><td>${r.count}</td><td>${fmt(r.avg_score, 3)}</td></tr>`).join("");
  $("industry").innerHTML = `<table>${head}${body}</table>`;
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

async function loadDaily() {
  try {
    const d = await getJSON("/api/analysis/daily");
    renderDaily(d.daily_top || []);
    if (d.snap_date) $("upload-info").textContent = `最新快照 ${d.snap_date}`;
  } catch (e) { /* 忽略 */ }
}

async function loadWeeklyAndSummary() {
  try {
    const w = await getJSON("/api/analysis/weekly");
    renderWeekly(w);
    renderIndustry(w.industry || []);
  } catch (e) { /* 忽略 */ }
  try {
    const s = await getJSON("/api/analysis/summary");
    $("csv-summary").textContent = s.text || "";
    $("csv-summary").classList.toggle("disabled", !s.enabled);
  } catch (e) { /* 忽略 */ }
}

async function uploadCsv(file) {
  const info = $("upload-info");
  info.textContent = "解析中…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/csv/upload", { method: "POST", body: fd });
    const res = await r.json();
    info.textContent = `已匯入 ${res.snap_date}，共 ${res.count} 檔`;
    renderDaily(res.daily_top || []);
    await loadWeeklyAndSummary();
  } catch (e) {
    info.textContent = "上傳失敗：" + e.message;
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

async function openKline(code, name) {
  $("kline-modal").classList.remove("hidden");
  $("kline-title").textContent = `${code} ${name || ""} 日K線`;
  if (!klineChart) klineChart = echarts.init($("kline"));
  klineChart.showLoading();
  try {
    const d = await getJSON(`/api/stock/${encodeURIComponent(code)}/kline?period=1y`);
    const closes = d.candles.map((c) => c[1]);
    klineChart.hideLoading();
    klineChart.setOption({
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { data: ["K線", "MA5", "MA20"], textStyle: { color: "#ccc" } },
      grid: [{ left: 55, right: 20, top: 30, height: "55%" }, { left: 55, right: 20, top: "72%", height: "16%" }],
      xAxis: [
        { type: "category", data: d.dates, axisLabel: { color: "#999" } },
        { type: "category", data: d.dates, gridIndex: 1, axisLabel: { show: false } },
      ],
      yAxis: [
        { scale: true, axisLabel: { color: "#999" } },
        { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 60 }, { type: "slider", xAxisIndex: [0, 1], start: 60 }],
      series: [
        { name: "K線", type: "candlestick", data: d.candles, itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" } },
        { name: "MA5", type: "line", data: ma(closes, 5), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
        { name: "MA20", type: "line", data: ma(closes, 20), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
        { name: "量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: d.volumes },
      ],
    });
  } catch (e) {
    klineChart.hideLoading();
    $("kline-title").textContent = `${code} 無 K 線資料（${e.message}）`;
  }
}

// ---------- 事件綁定 ----------
$("btn-update").addEventListener("click", runUpdate);
$("csv").addEventListener("change", (e) => { if (e.target.files[0]) uploadCsv(e.target.files[0]); });
$("kline-close").addEventListener("click", () => $("kline-modal").classList.add("hidden"));
$("kline-modal").addEventListener("click", (e) => { if (e.target.id === "kline-modal") $("kline-modal").classList.add("hidden"); });
document.addEventListener("click", (e) => {
  const a = e.target.closest("a.stock");
  if (a) { e.preventDefault(); openKline(a.dataset.code, a.dataset.name); }
});
window.addEventListener("resize", () => { trendChart && trendChart.resize(); klineChart && klineChart.resize(); });

// 初始載入
loadDashboard();
loadDaily();
loadWeeklyAndSummary();
