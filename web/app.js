"use strict";

const $ = (id) => document.getElementById(id);
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

// 艾略特波浪（與後端 elliott.py 同邏輯）
function zigzag(vals, pct) {
  const n = vals.length; if (!n) return [];
  const piv = []; let pi = 0, pv = vals[0], trend = 0;
  for (let i = 1; i < n; i++) {
    const v = vals[i];
    if (trend === 0) { if (pv && Math.abs(v - pv) / Math.abs(pv) >= pct) { trend = v > pv ? 1 : -1; piv.push(pi); pi = i; pv = v; } }
    else if (trend === 1) { if (v > pv) { pi = i; pv = v; } else if (pv && (pv - v) / Math.abs(pv) >= pct) { piv.push(pi); trend = -1; pi = i; pv = v; } }
    else { if (v < pv) { pi = i; pv = v; } else if (pv && (v - pv) / Math.abs(pv) >= pct) { piv.push(pi); trend = 1; pi = i; pv = v; } }
  }
  piv.push(pi); return piv;
}
function impulseLabels(closes, seg) {
  const p = seg.map((i) => closes[i]); const up = p[1] > p[0];
  let shape, r2, r3t, r4;
  if (up) { shape = p[1] > p[0] && p[2] < p[1] && p[3] > p[2] && p[4] < p[3] && p[5] > p[4]; r2 = p[2] > p[0]; r3t = p[3] > p[1]; r4 = p[4] > p[1]; }
  else { shape = p[1] < p[0] && p[2] > p[1] && p[3] < p[2] && p[4] > p[3] && p[5] < p[4]; r2 = p[2] < p[0]; r3t = p[3] < p[1]; r4 = p[4] < p[1]; }
  const w1 = Math.abs(p[1] - p[0]), w3 = Math.abs(p[3] - p[2]), w5 = Math.abs(p[5] - p[4]);
  if (!(shape && r2 && r3t && r4 && !(w3 < w1 && w3 < w5))) return [];
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

function candlestickOption(data, startPct, showW, pct) {
  const closes = data.candles.map((c) => c[1]);
  const maSeries = MA_DEFS.map((m) => ({ name: "MA" + m.n, type: "line", data: ma(closes, m.n), smooth: true, showSymbol: false, lineStyle: { width: 1, color: m.color }, itemStyle: { color: m.color } }));
  const candle = { name: "K線", type: "candlestick", data: data.candles, itemStyle: { color: "#e04545", color0: "#2ea043", borderColor: "#e04545", borderColor0: "#2ea043" } };
  if (showW) {
    const waves = elliottWaves(closes, pct);
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
  if (name === "overview" && idxChart) idxChart.resize();
  if (name === "stock" && stockChart) stockChart.resize();
  if (name === "rotation") { loadRotation(); loadCross(); }
  if (name === "settings") loadSettings();
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
    $("set-stats").innerHTML = [
      ["快照天數", s.snapshots], ["台指期歷史天數", s.tx_history_days], ["最新大盤日期", s.last_market_date || "—"],
    ].map(([k, v]) => `<div class="stat"><div class="stat-k">${k}</div><div class="stat-v">${v}</div></div>`).join("");
  } catch (e) { /* 忽略 */ }
}
async function saveSettings() {
  $("set-saved").textContent = "儲存中…";
  try {
    await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schedule_time: $("set-schedule").value, data_dir: $("set-datadir").value }) });
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
  renderStale(d);
  if (d.latest && d.latest.updated_at) $("last-updated").textContent = "更新：" + d.latest.updated_at.replace("T", " ").slice(0, 19);
  return d;
}

async function loadSectors() {
  const el = $("sectors");
  if (!el) return;
  try {
    const d = await getJSON("/api/sectors");
    const note = $("sectors-note");
    if (!d.sectors || !d.sectors.length) { el.innerHTML = '<div class="muted small">尚無類股資料</div>'; if (note) note.textContent = ""; return; }
    if (note) {
      const up = d.sectors.filter((s) => s.chg_pct > 0).length;
      const down = d.sectors.filter((s) => s.chg_pct < 0).length;
      note.textContent = `（${d.date}　紅漲 ${up}・綠跌 ${down}，依漲幅排序）`;
    }
    el.innerHTML = d.sectors.map((s) => {
      const cls = chgClass(s.chg_pct);
      const arrow = s.chg_pct > 0 ? "▲" : s.chg_pct < 0 ? "▼" : "";
      const pct = s.chg_pct == null ? "—" : arrow + fmt(Math.abs(s.chg_pct), 2) + "%";
      return `<div class="sector ${cls}"><span class="sec-name">${s.name}</span><span class="sec-chg">${pct}</span></div>`;
    }).join("");
  } catch (e) { el.innerHTML = '<div class="muted small">類股載入失敗</div>'; }
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
    const body = d.sectors.map((s) => `<tr><td>${s.name}</td>${s.series.map(cell).join("")}<td class="${chgClass(s.sum)}" style="text-align:right;font-weight:700">${s.sum > 0 ? "+" : ""}${fmt(s.sum, 2)}</td></tr>`).join("");
    el.innerHTML = `<table>${head}${body}</table>`;
  } catch (e) { el.innerHTML = '<div class="muted small">輪動載入失敗</div>'; }
}
async function loadCross() {
  const el = $("cross");
  if (!el) return;
  const note = $("cross-note");
  try {
    const d = await getJSON("/api/sectors/picks");
    if (!d.groups || !d.groups.length) { el.innerHTML = '<div class="muted small">尚無選股或族群資料（請先到「選股清單」載入當日 CSV）。</div>'; if (note) note.textContent = ""; return; }
    if (note) note.textContent = `（選股日 ${d.date || ""}，共 ${d.groups.length} 族群）`;
    el.innerHTML = d.groups.map((g) => {
      const cls = chgClass(g.chg_pct);
      const arrow = g.chg_pct > 0 ? "▲" : g.chg_pct < 0 ? "▼" : "";
      const pct = g.chg_pct == null ? '<span class="muted">—</span>' : `<span class="${cls}">${arrow}${fmt(Math.abs(g.chg_pct), 2)}%</span>`;
      const stocks = g.stocks.map((s) => stockLink(s.code, s.name)).join("　");
      return `<div class="cross-grp ${cls}"><div class="cross-h"><b>${g.sector}</b>　${pct}　<span class="muted">· ${g.count} 檔</span></div><div class="cross-stocks">${stocks}</div></div>`;
    }).join("");
  } catch (e) { el.innerHTML = '<div class="muted small">交叉選股載入失敗</div>'; }
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
    await loadDashboard(); await loadIndexChart(); loadSectors();
    setTimeout(() => bar.classList.add("hidden"), 5000);
  } catch (e) {
    bar.textContent = "自動更新失敗：" + e.message; bar.className = "status-bar err";
    await loadDashboard();
  } finally { autoUpdating = false; }
}

// ========== 選股清單 ==========
function stockLink(code, name) { return `<a href="#" class="stock" data-code="${code}" data-name="${name || ""}">${code} ${name || ""}</a>`; }
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
    el.innerHTML = `篩選：<b>${subFilter}</b>（${currentPicks.filter((p) => p.sub_industry === subFilter).length} 檔） <a href="#" id="clear-sub">✕ 全部</a>`;
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
  const body = rows.map((r) => `<tr><td>${stockLink(r.code, r.name)}</td><td>${statusBadge(r.status)}</td><td>${fmt(r.custody_delta, 2)}</td><td>${fmt(r.big_holder_ratio, 2)}</td><td>${r.industry || ""}</td></tr>`).join("");
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
async function loadStock(code, name) {
  code = (code || "").trim().toUpperCase();
  if (!code) return;
  if (!/\./.test(code)) code += ".TW";
  stockCode = code;
  if (!stockChart) stockChart = echarts.init($("stock-chart"));
  $("stock-note").textContent = "載入中…";
  try { renderProfile(await getJSON(`/api/stock/${encodeURIComponent(code)}/profile`)); } catch (e) { $("stock-profile").innerHTML = ""; }
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
  if (res.error && !res.count) { info.innerHTML = `<span style="color:#e08585">⚠ ${res.error}</span>`; return; }
  if (!res.count) { info.innerHTML = `<span style="color:#e08585">⚠ 讀到 0 檔（${res.snap_date}）。請確認是籌碼匯出檔。</span>`; return; }
  info.textContent = `已匯入 ${res.file ? res.file + "：" : ""}${res.snap_date}，共 ${res.count} 檔`;
  await loadDates(); await loadWeekly();
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
$("btn-ai-market").addEventListener("click", () => loadMarketSummary(true));
$("btn-ai-csv").addEventListener("click", () => loadCsvSummary(true));
$("btn-save-settings").addEventListener("click", saveSettings);

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
window.addEventListener("resize", () => { idxChart && idxChart.resize(); stockChart && stockChart.resize(); });

// ========== 初始載入 ==========
(async () => {
  const d = await loadDashboard();
  loadIndexChart();
  loadSectors();
  loadDates();
  loadWeekly();
  // 自動更新：無資料、或資料非當日（平日尚未更新到最新交易日）時，自動抓一次
  if (!d || !d.latest || !d.latest.date || d.data_stale) autoUpdate();
})();
