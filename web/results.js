"use strict";
const API = "cgi-bin/api.py";
const $ = id => document.getElementById(id);
const R = { data: null, run: "", t: 0, max: 0, duration: 0, mode: "cum",
  window: 60, tab: "total", rows: [], sort: "", desc: false,
  timer: null, shown: [], chartMetric: "wip", chartScope: "total",
  chartIds: ["total"], chartMode: "bucket", chartStep: 60,
  chartData: null, chartSeries: [], chartVisible: {}, chartHover: -1,
  chartApi: false };

async function api(action, params) {
  const u = new URL(API, location.href);
  u.searchParams.set("action", action);
  Object.entries(params || {}).forEach(([k,v]) => u.searchParams.set(k, v));
  const r = await fetch(u);
  return r.json();
}
function timeLabel(v) {
  if (v == null || !Number.isFinite(+v)) return "-";
  const n = Math.max(0, +v), d = Math.floor(n / 1440);
  const hh = String(Math.floor(n % 1440 / 60)).padStart(2, "0");
  const mm = String(Math.floor(n % 60)).padStart(2, "0");
  return `${n.toFixed(1)} 分 (${d}日 ${hh}:${mm})`;
}
function fmt(key, v) {
  if (v == null) return "-";
  if (typeof v === "string" && v.trim() !== "" && !Number.isFinite(+v))
    return v;
  if (!Number.isFinite(+v)) return "-";
  if (key === "utilization" || key === "utilization_gross" ||
      key === "on_time_rate") return `${(+v * 100).toFixed(1)}%`;
  if (["avg_process_time","dispatch_wait_avg","dispatch_wait_max",
       "reserved_start_wait_avg"].includes(key) || key.endsWith("_minutes"))
    return timeLabel(v);
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(1);
  return String(v);
}
function setMax(t) {
  R.max = Math.max(0, t);
  $("slider").max = String(Math.floor(R.max / 60));
  R.t = Math.min(R.t, R.max);
  syncTime();
}
function syncTime() {
  $("slider").value = String(Math.floor(R.t / 60));
  const m = Math.floor(R.t);
  $("day").value = String(Math.floor(m / 1440));
  $("hour").value = String(Math.floor(m % 1440 / 60));
  $("minute").value = String(m % 60);
}
function chooseTime(t) {
  R.t = Math.max(0, Math.min(R.max, +t || 0));
  syncTime();
  scheduleFetch();
}
function scheduleFetch() {
  clearTimeout(R.timer);
  R.timer = setTimeout(fetchResults, 220);
}
async function loadRuns(preferred) {
  const r = await api("runs");
  const sel = $("run");
  const old = preferred || sel.value;
  sel.innerHTML = (r.runs || []).map(x =>
    `<option value="${x.run_id}">${x.run_id} (${x.status || "?"})` +
    `${x.model ? " / " + x.model : ""}</option>`).join("");
  if (!(r.runs || []).length) {
    sel.innerHTML = "<option value=''>run がありません</option>";
    R.run = "";
    $("status").textContent = "run がありません";
    return;
  }
  R.run = (r.runs || []).some(x => x.run_id === old) ? old : r.runs[0].run_id;
  sel.value = R.run;
  const requestedTime = new URLSearchParams(location.search).get("t");
  if (requestedTime != null && Number.isFinite(+requestedTime))
    R.t = +requestedTime;
  $("viewer").href = `index.html?run=${encodeURIComponent(R.run)}`;
  await refreshRun();
}
async function refreshRun() {
  if (!R.run) return;
  const st = await api("status", { run: R.run });
  if (!st.ok) { $("status").textContent = st.error; return; }
  R.duration = +st.duration || 0;
  const max = st.status === "running" || st.status === "queued"
    ? +st.progress_time || 0 : +st.duration || 0;
  setMax(max);
  $("status").textContent = `${st.status} / ${timeLabel(max)}`;
  if (R.t === 0 && max > 0 && !new URLSearchParams(location.search).has("t"))
    R.t = max;
  syncTime();
  await fetchResults();
  await fetchSeries();
  await fetchSeries();
}
async function fetchResults() {
  if (!R.run) return;
  const params = { run: R.run, t: R.t, mode: R.mode };
  if (R.mode === "window") {
    const limit = R.duration || R.max || R.window;
    if (R.window > limit) {
      R.window = limit;
      $("windowPreset").value = "custom";
      $("windowCustom").value = String(Math.max(1, Math.floor(limit)));
      $("windowCustom").disabled = false;
    }
    params.window = R.window;
  }
  const r = await api("results", params);
  if (!r.ok) {
    $("status").textContent = `取得失敗: ${r.error}`;
    $("summary").innerHTML = "";
    return;
  }
  R.data = r;
  R.max = r.status === "running" || r.status === "queued"
    ? +r.progress_time : +r.duration;
  $("status").textContent =
    `${r.status} / 時点 ${timeLabel(r.t)} / 集計 ${timeLabel(r.p_start)}〜${timeLabel(r.t)}`;
  render();
}
function card(label, value, sub) {
  return `<div class="card"><div class="label">${label}</div>` +
    `<div class="value">${value}</div><div class="sub">${sub || ""}</div></div>`;
}
function renderSummary() {
  const d = R.data, x = d.total;
  const modeDone = x.completed_lots_period;
  let h = card("投入ロット累計", fmt("released_lots", x.released_lots));
  h += card("完了ロット累計", fmt("completed_lots", x.completed_lots),
    `集計期間の完了: ${fmt("completed_lots_period", modeDone)}`);
  h += card("WIP（瞬時値）", fmt("wip", x.wip),
    `グループ待ち ${fmt("wip_wait",x.wip_wait)} / 設備上 ${fmt("wip_equip",x.wip_equip)} / 搬送等 ${fmt("wip_other",x.wip_other)}`);
  h += card("平均サイクルタイム", timeLabel(x.avg_cycle_time),
    "選択集計期間に完了したロット");
  h += card("全設備稼働率", fmt("utilization", x.utilization),
    `BUSY/稼働可能時間 · 総時間比 ${fmt("utilization_gross",x.utilization_gross)}`);
  h += card("平均ディスパッチ待ち", timeLabel(x.dispatch_wait_avg),
    `最大 ${timeLabel(x.dispatch_wait_max)} / 搬入〜開始 ${timeLabel(x.reserved_start_wait_avg)}`);
  h += card("完了工程", fmt("completed_steps", x.completed_steps),
    `平均処理 ${timeLabel(x.avg_process_time)} (段取り除く)` +
    (d.has_wafers ? ` / ウェハ ${fmt("wafer_count",x.wafer_count)}` : ""));
  if (x.on_time_rate != null)
    h += card("納期遵守率", fmt("on_time_rate", x.on_time_rate));
  if (d.warnings && d.warnings.length)
    h += `<div id="warnings">${d.warnings.map(esc).join("<br>")}</div>`;
  $("summary").innerHTML = h;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function columns() {
  const d = R.data;
  const common = [
    ["wip","WIP"],["wip_wait","グループ待ち"],["wip_equip","設備上"],
    ["wip_processing","処理中"],["wip_reserved","段取り/搬入待ち"],
    ["completed_steps","完了工程"],
    ["avg_process_time","平均処理時間(分)"],
  ];
  if (d.has_wafers) common.push(["wafer_count","ウェハ枚数"]);
  common.push(["dispatch_wait_avg","平均ディスパッチ待ち(分)"],
    ["dispatch_wait_max","最大ディスパッチ待ち(分)"],
    ["reserved_start_wait_avg","搬入〜開始待ち(分)"]);
  for (const s of ["BUSY","IDLE","RESERVED","SETUP","DOWN","PM"])
    common.push(["state_" + s, s + "時間(分)"]);
  common.push(["utilization","稼働率(可用時間比)"],
              ["utilization_gross","稼働率(全時間比)"]);
  if (R.tab === "groups") return [["tool_group","装置グループ"], ...common];
  return [["equip_id","装置"],["tool_group","装置グループ"],["area","エリア"],
    ["ports","ポート数"], ...common];
}
function valueOf(row, key) {
  if (key.startsWith("state_"))
    return row.state_minutes ? row.state_minutes[key.slice(6)] : null;
  return row[key];
}
function filteredRows() {
  let rows = R.tab === "groups" ? (R.data.groups || []) : (R.data.equips || []);
  if (R.tab === "equips") {
    const g = $("groupFilter").value, a = $("areaFilter").value;
    rows = rows.filter(x => (!g || x.tool_group === g) && (!a || x.area === a));
  }
  if (R.sort) rows = rows.slice().sort((a,b) => {
    const x=valueOf(a,R.sort), y=valueOf(b,R.sort);
    const n = x == null ? (y == null ? 0 : 1) : y == null ? -1 :
      typeof x === "number" && typeof y === "number" ? x-y :
      String(x).localeCompare(String(y),"ja");
    return R.desc ? -n : n;
  });
  return rows;
}
function renderTable() {
  const cols = columns(), rows = filteredRows();
  R.shown = rows;
  const table = $("table");
  table.querySelector("thead").innerHTML = `<tr>${cols.map(([k,n]) =>
    `<th data-key="${k}">${esc(n)}${R.sort===k?(R.desc?" ▼":" ▲"):""}</th>`).join("")}</tr>`;
  const wrap = $("tableWrap"), rowH=31, chunk=250;
  const start = Math.max(0, Math.floor(wrap.scrollTop/rowH)-10);
  const end = Math.min(rows.length, start+chunk);
  const sp = (n) => n ? `<tr aria-hidden="true"><td colspan="${cols.length}" style="height:${n*rowH}px;padding:0;border:0"></td></tr>` : "";
  let body=sp(start);
  for (let i=start;i<end;i++) {
    const r=rows[i];
    const scope = R.tab === "groups" ? "group" : "equip";
    const id = scope === "group" ? r.tool_group : r.equip_id;
    const selected = R.chartScope === scope && R.chartIds.includes(id);
    body += `<tr data-chart-id="${esc(id)}" class="${selected ? "chart-selected" : ""}">` +
      `${cols.map(([k]) => `<td>${esc(fmt(k,valueOf(r,k)))}</td>`).join("")}</tr>`;
  }
  body += sp(rows.length-end);
  table.querySelector("tbody").innerHTML = body ||
    `<tr><td class="empty" colspan="${cols.length}">該当データなし</td></tr>`;
  table.querySelectorAll("tbody tr[data-chart-id]").forEach(tr =>
    tr.onclick = () => {
      const id = tr.dataset.chartId;
      if (R.chartScope !== (R.tab === "groups" ? "group" : "equip")) return;
      if (R.chartMetric === "state_time") {
        if (R.chartIds.includes(id))
          R.chartIds = R.chartIds.length > 1
            ? R.chartIds.filter(x => x !== id) : [];
        else R.chartIds = [id];
      } else if (R.chartIds.includes(id)) {
        R.chartIds = R.chartIds.filter(x => x !== id);
      } else if (R.chartIds.length >= 6) {
        $("chartHint").textContent = "系列は最大6件です";
        return;
      } else {
        R.chartIds.push(id);
      }
      renderTable();
      fetchSeries();
    });
  table.querySelectorAll("th").forEach(th => th.onclick = () => {
    const k=th.dataset.key;
    R.desc = R.sort === k ? !R.desc : false; R.sort=k; renderTable();
  });
}
function render() {
  renderSummary();
  renderLegend();
  drawChart();
  $("filters").hidden = R.tab !== "equips";
  $("tableWrap").hidden = R.tab === "total";
  if (R.tab === "total") return;
  const g = $("groupFilter"), a = $("areaFilter");
  const groups = [...new Set((R.data.equips||[]).map(x=>x.tool_group))].sort();
  const areas = [...new Set((R.data.equips||[]).map(x=>x.area))].sort();
  const fill = (sel, values) => {
    const old=sel.value; sel.innerHTML="<option value=''>すべて</option>"+
      values.map(v=>`<option>${esc(v)}</option>`).join("");
    if(values.includes(old)) sel.value=old;
  };
  fill(g,groups); fill(a,areas);
  renderTable();
}
function downloadCsv() {
  let rows, headers;
  if (R.tab === "total") {
    headers=["項目","値"];
    rows=[
      ["時点(分)",R.data.t],["集計開始(分)",R.data.p_start],
      ["投入累計",R.data.total.released_lots],
      ["完了累計",R.data.total.completed_lots],
      ["完了期間",R.data.total.completed_lots_period],
      ["WIP",R.data.total.wip],["グループ待ち",R.data.total.wip_wait],
      ["設備上",R.data.total.wip_equip],["搬送等",R.data.total.wip_other],
      ["平均サイクルタイム(分)",R.data.total.avg_cycle_time],
      ["稼働率",R.data.total.utilization],
      ["平均待ち(分)",R.data.total.dispatch_wait_avg]
    ];
  } else {
    const cs=columns(); headers=cs.map(x=>x[1]);
    rows=R.shown.map(r=>cs.map(([k])=>valueOf(r,k)));
  }
  const csv=[headers,...rows].map(row=>row.map(v=>{
    const s=String(v==null?"":v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s;
  }).join(",")).join("\r\n");
  const blob=new Blob(["\ufeff",csv],{type:"text/csv;charset=utf-8"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob); a.download=`${R.run}_${R.tab}.csv`;
  a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
function bind() {
  $("run").onchange=async()=>{R.run=$("run").value;R.t=0;
    R.chartIds=R.chartScope==="total"?["total"]:[];
    $("viewer").href=`index.html?run=${encodeURIComponent(R.run)}`;
    await refreshRun();};
  $("refresh").onclick=refreshRun;
  $("slider").oninput=()=>chooseTime(+$("slider").value*60);
  for (const id of ["day","hour","minute"])
    $(id).onchange=()=>chooseTime((+$("day").value*1440)+
      (+$("hour").value*60)+(+$("minute").value));
  $("prev").onclick=()=>chooseTime(R.t-60);
  $("next").onclick=()=>chooseTime(R.t+60);
  $("last").onclick=()=>chooseTime(R.max);
  document.querySelectorAll("input[name=mode]").forEach(x=>x.onchange=()=>{
    R.mode=x.value;
    $("windowPreset").disabled=R.mode!=="window";
    $("windowCustom").disabled=R.mode!=="window"||
      $("windowPreset").value!=="custom";
    if (R.mode === "window")
      R.window = $("windowPreset").value === "custom"
        ? +$("windowCustom").value : +$("windowPreset").value;
    scheduleFetch();
  });
  $("windowPreset").onchange=()=>{
    $("windowCustom").disabled=$("windowPreset").value!=="custom";
    if($("windowPreset").value!=="custom") R.window=+$("windowPreset").value;
    else R.window=+$("windowCustom").value;
    scheduleFetch();
  };
  $("windowCustom").onchange=()=>{R.window=+$("windowCustom").value;scheduleFetch();};
  document.querySelectorAll("#tabs button[data-tab]").forEach(b=>b.onclick=()=>{
    R.tab=b.dataset.tab;
    R.chartScope=R.tab==="groups"?"group":
      R.tab==="equips"?"equip":"total";
    R.chartIds=R.chartScope==="total"?["total"]:[];
    $("chartScope").value=R.chartScope;
    document.querySelectorAll("#tabs button[data-tab]").forEach(x=>x.classList.toggle("active",x===b));
    render();
    fetchSeries();
  });
  $("groupFilter").onchange=renderTable;
  $("areaFilter").onchange=renderTable;
  $("download").onclick=downloadCsv;
  $("tableWrap").addEventListener("scroll",()=>{
    if(R.tab!=="total" && R.data) renderTable();
  });
}
bind();
bindChart();
loadRuns(new URLSearchParams(location.search).get("run"));
