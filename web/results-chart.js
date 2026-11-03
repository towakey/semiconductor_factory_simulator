"use strict";

const CHART_COLORS = ["#56b4e9", "#e69f00", "#009e73", "#f0e442",
  "#cc79a7", "#d55e00"];
const CHART_DASH = [[], [7, 4], [2, 3], [10, 3, 2, 3], [5, 2, 1, 2],
  [1, 3]];
const CHART_TOTAL_ONLY = new Set(
  ["completed_lots", "avg_cycle_time", "on_time_rate"]);

async function fetchSeries() {
  const metric = R.chartMetric;
  let scope = R.chartScope;
  if (CHART_TOTAL_ONLY.has(metric) && scope !== "total") {
    scope = R.chartScope = "total";
    R.chartIds = ["total"];
    $("chartScope").value = scope;
  }
  if (scope !== "total" && !R.chartIds.length) {
    R.chartData = {times: [], series: [], unit: "分", warnings: []};
    R.chartSeries = [];
    $("chartHint").textContent = "表の行をクリックして系列を追加してください";
    renderLegend(); drawChart();
    return;
  }
  if (metric === "state_time" && scope !== "total" &&
      R.chartIds.length !== 1) {
    R.chartData = {times: [], series: [], unit: "分", warnings: []};
    R.chartSeries = [];
    $("chartHint").textContent = "状態時間は対象を1件選択してください";
    renderLegend(); drawChart();
    return;
  }
  const p = {run: R.run, metric, scope, step: R.chartStep,
    mode: R.chartMode, ids: R.chartIds.join(",")};
  const d = await api("results_series", p);
  if (!d.ok) {
    R.chartData = {times: [], series: [], unit: "", warnings: []};
    R.chartSeries = [];
    $("chartHint").textContent = `グラフ取得失敗: ${d.error}`;
    renderLegend(); drawChart();
    return;
  }
  R.chartData = d;
  R.chartSeries = d.series || [];
  for (const s of R.chartSeries)
    if (!(s.id in R.chartVisible)) R.chartVisible[s.id] = true;
  if (+d.step > R.chartStep + 1e-8) {
    R.chartStep = +d.step;
    $("chartStep").value = "custom";
    $("chartStepCustom").value = String(d.step);
    $("chartStepCustom").disabled = false;
    $("chartHint").textContent =
      `点数上限(500)のため刻みを ${fmtStep(d.step)} 分へ自動拡大しました`;
  } else {
    $("chartHint").textContent = d.warnings && d.warnings.length
      ? d.warnings.join(" ")
      : "表の行をクリックして系列を追加/削除(最大6)。WIP は各時点の瞬時値。";
  }
  renderLegend(); drawChart();
}

function fmtStep(v) {
  return Number.isInteger(+v) ? String(+v) : (+v).toFixed(1);
}

function renderLegend() {
  const el = $("chartLegend");
  if (!el) return;
  el.innerHTML = (R.chartSeries || []).map((s, i) => {
    const on = R.chartVisible[s.id] !== false;
    const color = CHART_COLORS[i % CHART_COLORS.length];
    const dash = CHART_DASH[i % CHART_DASH.length];
    const style = dash.length ? (i % 2 ? "dashed" : "dotted") : "solid";
    return `<button class="chart-legend ${on ? "" : "off"}" data-id="${esc(s.id)}">` +
      `<span class="chart-swatch" style="color:${color};border-top-style:${style}"></span>` +
      `${esc(s.label)}</button>`;
  }).join("");
  el.querySelectorAll("button[data-id]").forEach(b => b.onclick = () => {
    const id = b.dataset.id;
    R.chartVisible[id] = R.chartVisible[id] === false;
    renderLegend(); drawChart();
  });
}

function chartValue(v, unit) {
  if (v == null || !Number.isFinite(+v)) return "-";
  return unit === "%" ? `${(+v * 100).toFixed(1)}%` :
    `${(+v).toFixed(1)} ${unit || ""}`.trim();
}

function axisTime(t) {
  const m = Math.max(0, Math.floor(+t || 0));
  return `${Math.floor(m / 1440)}日 ${String(Math.floor(m % 1440 / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function chartIndexAt(x) {
  const times = R.chartData && R.chartData.times || [];
  if (times.length < 2) return 0;
  const w = $("chartCanvas").clientWidth, left = 58, right = 14;
  const f = Math.max(0, Math.min(1, (x - left) / (w - left - right)));
  const target = f * times[times.length - 1];
  let best = 0, dist = Infinity;
  for (let i = 0; i < times.length; i++) {
    const d = Math.abs(times[i] - target);
    if (d < dist) { dist = d; best = i; }
  }
  return best;
}

function drawChart() {
  const c = $("chartCanvas");
  if (!c || !$("chartSection").open) return;
  const rect = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, rect.width), h = Math.max(1, rect.height);
  const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
  if (c.width !== bw || c.height !== bh) { c.width = bw; c.height = bh; }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#20242a"; ctx.fillRect(0, 0, w, h);
  const data = R.chartData, times = data && data.times || [];
  const left = 58, right = 14, top = 24, bottom = 30;
  const pw = Math.max(1, w - left - right), ph = Math.max(1, h - top - bottom);
  const visible = (R.chartSeries || []).map((s, i) => ({s, i}))
    .filter(x => R.chartVisible[x.s.id] !== false);
  const values = visible.flatMap(x => x.s.values || [])
    .filter(v => v != null && Number.isFinite(+v)).map(Number);
  const fixedRate = data && data.unit === "%";
  let ymax = fixedRate ? 1 : Math.max(1, ...values);
  if (!fixedRate) ymax = Math.ceil(ymax * 1.1 * 10) / 10 || 1;
  const xmax = times.length ? times[times.length - 1] : 0;
  const px = t => left + (xmax > 0 ? (t / xmax) * pw : 0);
  const py = v => top + ph - (Math.max(0, Math.min(ymax, +v)) / ymax) * ph;
  ctx.font = "10px Segoe UI, Yu Gothic, sans-serif";
  ctx.strokeStyle = "#39414a"; ctx.fillStyle = "#aab4c0"; ctx.lineWidth = 1;
  ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.fillText(fixedRate ? "稼働率 (%)" : (data && data.unit || ""), left, top - 5);
  for (let i = 0; i <= 4; i++) {
    const y = top + ph * i / 4, v = ymax * (4 - i) / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + pw, y); ctx.stroke();
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    ctx.fillText(fixedRate ? (v * 100).toFixed(0) + "%" :
      v.toFixed(ymax < 10 ? 1 : 0), left - 6, y);
  }
  if (times.length) {
    for (let i = 0; i <= 4; i++) {
      const t = xmax * i / 4, x = px(t);
      ctx.strokeStyle = "#343b43"; ctx.beginPath(); ctx.moveTo(x, top);
      ctx.lineTo(x, top + ph); ctx.stroke();
      ctx.fillStyle = "#aab4c0";
      ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center";
      ctx.textBaseline = "top"; ctx.fillText(axisTime(t), x, top + ph + 5);
    }
    for (const {s, i} of visible) {
      ctx.beginPath(); ctx.strokeStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.lineWidth = 2; ctx.setLineDash(CHART_DASH[i % CHART_DASH.length]);
      let pen = false;
      for (let j = 0; j < times.length; j++) {
        const v = s.values[j];
        if (v == null || !Number.isFinite(+v)) { pen = false; continue; }
        if (!pen) { ctx.moveTo(px(times[j]), py(v)); pen = true; }
        else ctx.lineTo(px(times[j]), py(v));
      }
      ctx.stroke(); ctx.setLineDash([]);
    }
    if (R.t >= 0 && R.t <= xmax) {
      const x = px(R.t);
      ctx.beginPath(); ctx.setLineDash([4, 3]); ctx.strokeStyle = "#ffd166";
      ctx.lineWidth = 1.5; ctx.moveTo(x, top); ctx.lineTo(x, top + ph);
      ctx.stroke(); ctx.setLineDash([]);
    }
    if (R.chartHover >= 0 && R.chartHover < times.length) {
      const idx = R.chartHover, x = px(times[idx]);
      ctx.beginPath(); ctx.setLineDash([2, 3]); ctx.strokeStyle = "#fff";
      ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke();
      ctx.setLineDash([]);
      for (const {s, i} of visible) {
        const v = s.values[idx];
        if (v == null || !Number.isFinite(+v)) continue;
        ctx.beginPath(); ctx.arc(x, py(v), 3, 0, Math.PI * 2);
        ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length]; ctx.fill();
        ctx.strokeStyle = "#fff"; ctx.stroke();
      }
      showChartTooltip(idx);
    } else $("chartTooltip").hidden = true;
  } else {
    ctx.fillStyle = "#b7c1cb"; ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const msg = data && data.warnings && data.warnings.length
      ? data.warnings.join(" ") : "系列を選択するとグラフを表示します";
    ctx.fillText(msg, left + pw / 2, top + ph / 2);
    $("chartTooltip").hidden = true;
  }
}

function showChartTooltip(index) {
  const tip = $("chartTooltip"), times = R.chartData.times;
  const lines = [axisTime(times[index])];
  for (const s of R.chartSeries) {
    if (R.chartVisible[s.id] === false) continue;
    lines.push(`${s.label}: ${chartValue(s.values[index], R.chartData.unit)}`);
  }
  tip.textContent = lines.join("\n"); tip.hidden = false;
  const r = $("chartCanvas").getBoundingClientRect();
  const w = tip.offsetWidth, h = tip.offsetHeight;
  const x = 58 + (times[index] / (times[times.length - 1] || 1)) *
    (r.width - 72);
  tip.style.left = `${Math.max(0, Math.min(r.width - w, x + 10))}px`;
  tip.style.top = `${Math.max(0, Math.min(r.height - h, 14))}px`;
}

function downloadChartCsv() {
  const d = R.chartData;
  if (!d || !d.times || !d.times.length) return;
  const series = R.chartSeries || [];
  const rows = [["時点(分)", ...series.map(s => s.label)]];
  for (let i = 0; i < d.times.length; i++)
    rows.push([d.times[i], ...series.map(s => s.values[i])]);
  const text = rows.map(row => row.map(v => {
    const x = String(v == null ? "" : v);
    return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x;
  }).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\ufeff", text],
    {type: "text/csv;charset=utf-8"}));
  a.download = `${R.run}_${R.chartMetric}_${R.chartMode}.csv`;
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function bindChart() {
  $("chartMetric").onchange = () => {
    R.chartMetric = $("chartMetric").value;
    if (CHART_TOTAL_ONLY.has(R.chartMetric)) {
      R.chartScope = "total"; R.chartIds = ["total"];
      $("chartScope").value = "total";
      document.querySelector('#tabs button[data-tab="total"]').click();
    } else {
      fetchSeries();
    }
  };
  $("chartScope").onchange = () => {
    if (CHART_TOTAL_ONLY.has(R.chartMetric) &&
        $("chartScope").value !== "total") {
      $("chartScope").value = "total";
      $("chartHint").textContent = "この指標は全体のみです";
    }
    R.chartScope = $("chartScope").value;
    R.chartIds = R.chartScope === "total" ? ["total"] : [];
    const tab = R.chartScope === "group" ? "groups" :
      R.chartScope === "equip" ? "equips" : "total";
    document.querySelector(`#tabs button[data-tab="${tab}"]`).click();
  };
  $("chartStep").onchange = () => {
    const v = $("chartStep").value;
    $("chartStepCustom").disabled = v !== "custom";
    if (v !== "custom") R.chartStep = +v;
    else R.chartStep = +$("chartStepCustom").value;
    fetchSeries();
  };
  $("chartStepCustom").onchange = () => {
    R.chartStep = +$("chartStepCustom").value; fetchSeries();
  };
  document.querySelectorAll("input[name=chartMode]").forEach(x =>
    x.onchange = () => { R.chartMode = x.value; fetchSeries(); });
  $("chartAddTotal").onclick = () => {
    R.chartScope = "total"; R.chartIds = ["total"];
    $("chartScope").value = "total";
    document.querySelector('#tabs button[data-tab="total"]').click();
  };
  $("chartCsv").onclick = downloadChartCsv;
  $("chartCanvas").addEventListener("mousemove", e => {
    const r = $("chartCanvas").getBoundingClientRect();
    R.chartHover = chartIndexAt(e.clientX - r.left); drawChart();
  });
  $("chartCanvas").addEventListener("mouseleave", () => {
    R.chartHover = -1; drawChart();
  });
  $("chartCanvas").addEventListener("click", e => {
    const times = R.chartData && R.chartData.times;
    if (times && times.length) {
      const r = $("chartCanvas").getBoundingClientRect();
      chooseTime(times[chartIndexAt(e.clientX - r.left)]);
    }
  });
  $("chartSection").addEventListener("toggle", () => {
    if ($("chartSection").open) drawChart();
  });
  window.addEventListener("resize", drawChart);
}
