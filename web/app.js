"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
// 台灣慣例：紅漲綠跌
const chgClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const chgText = (v, d = 2) => (v === null || v === undefined ? "" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v), d));

let idxChart, klineChart;
let idxSymbol = "taiex", idxInterval = "1d";

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
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["K線", "MA5", "MA20"], textStyle: { color: "#ccc" } },
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
    series: [
      { name: "K線", type: "candlestick", data: data.candles, itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" } },
      { name: "MA5", type: "line", data: ma(closes, 5), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
      { name: "MA20", type: "line", data: ma(closes, 20), smooth: true, showSymbol: false, lineStyle: { width: 1 } },
      { name: "量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: data.volumes },
    ],
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
    $("idx-note").textContent = idxSymbol === "tx" ? "（台指期：每日更新累積）" : "";
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
    await loadIndexChart();
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
  renderDaily(res.daily_top || []);
  await loadWeeklyAndSummary();
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
const TF_LABEL = { "1d": "日", "1wk": "週", "1mo": "月" };

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
    ["基本/財務", [["營收年增%", fmt(c.rev_yoy, 1)], ["本益比(LPE)", fmt(c.lpe)], ["市值(億)", fmt(c.market_cap, 0)], ["股本(億)", fmt(c.capital)]]],
    ["TWSE估值", [["本益比", fmt(v.pe)], ["殖利率%", fmt(v.yield)], ["淨值比", fmt(v.pb)]]],
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

// 初始載入
loadDashboard();
loadIndexChart();
loadDaily();
loadWeeklyAndSummary();
