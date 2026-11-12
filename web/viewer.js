/* ビューアー (設計書 12.1-12.3)。素の JS + Canvas。 */
"use strict";
const API = "cgi-bin/api.py";
const STATE_COLORS = { BUSY: "#4caf50", IDLE: "#9e9e9e", RESERVED: "#4fc3f7",
                       SETUP: "#ff9800", DOWN: "#e53935", PM: "#fdd835" };
const EQUIP_W = 40, EQUIP_H = 30, STOCKER_W = 60, STOCKER_H = 40;

const V = {
  t: 0, speed: 100, playing: false, live: false,
  lay: null, win: { t0: -1, t1: -1, data: null },
  fetching: false, lastKpiFetch: 0, sel: null,
  zoom: 1, panX: 0, panY: 0, lastFetchTs: 0,
};
const $ = id => document.getElementById(id);
const cv = $("cv"), ctx = cv.getContext("2d");

async function api(action, params, method, body) {
  let url = API + "?action=" + encodeURIComponent(action);
  let opt = { headers: {} };
  if (method === "POST") {
    opt.method = "POST";
    opt.headers["X-Requested-With"] = "fabsim";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body || params);
  } else if (params) {
    url += "&" + new URLSearchParams(params);
  }
  const r = await fetch(url, opt);
  return r.json();
}

// ---------- 初期化 ----------
async function init() {
  const r = await api("models");
  $("model").innerHTML = r.models.map(m =>
    `<option>${m}</option>`).join("");
  await refreshRuns();
  const requestedRun = new URLSearchParams(location.search).get("run");
  if (requestedRun && [...$("run").options].some(o => o.value === requestedRun)) {
    $("run").value = requestedRun;
    await loadRun(requestedRun);
  }
  fit();
  requestAnimationFrame(frame);
  setInterval(pollStatus, 2500);
}
async function refreshRuns() {
  const r = await api("runs");
  const sel = $("run"), cur = sel.value;
  sel.innerHTML = "<option value=''>-</option>" +
    (r.runs || []).map(x =>
      `<option value="${x.run_id}" ${x.run_id === cur ? "selected" : ""}>` +
      `${x.run_id} (${x.status})</option>`).join("");
}

// ---------- 実行 ----------
$("btnRun").onclick = async () => {
  const r = await api("start", {}, "POST", {
    model: $("model").value, rule: $("rule").value,
    seed: +$("seed").value, duration: +$("duration").value });
  if (!r.ok) { $("status").textContent = "失敗: " + r.error; return; }
  await refreshRuns();
  $("run").value = r.run_id;
  $("btnCancel").disabled = false;
  $("status").textContent = "running " + r.run_id;
  V.live = true;
  V.lay = null;
  loadRun(r.run_id);
};
$("btnCancel").onclick = async () => {
  await api("cancel", {}, "POST", { run: $("run").value });
};
async function pollStatus() {
  const rid = $("run").value;
  if (!rid) return;
  const r = await api("status", { run: rid });
  if (!r.ok) return;
  const pct = r.duration ? Math.round(100 * r.progress_time / r.duration) : 0;
  $("status").textContent = `${r.status} ${pct}%`;
  $("btnCancel").disabled = !(r.status === "running" || r.status === "queued");
  if (r.status === "done" || r.status === "failed" ||
      r.status === "cancelled") {
    if (r.status === "done" && !V.lay) loadRun(rid);
    if (r.status !== "running") V.live = false;
    refreshRuns();
  }
}

// ---------- run の読込・窓取得 ----------
async function loadRun(rid) {
  const r = await api("layout", { run: rid });
  if (!r.ok) { $("status").textContent = "layout: " + r.error; return; }
  V.lay = r;
  V.t = 0;
  V.win = { t0: -1, t1: -1, data: null };
  fit();
  $("seek").max = Math.floor(+r.meta.duration || 1000);
}
$("run").onchange = () => { V.lay = null; V.t = 0; loadRun($("run").value); };
$("btnResults").onclick = () => {
  const q = $("run").value
    ? "?run=" + encodeURIComponent($("run").value) : "";
  location.href = "results.html" + q;
};

async function ensureWindow() {
  const W = Math.max(V.speed * 30, 60);
  if (V.win.data && V.t >= V.win.t0 && V.t < V.win.t0 + W / 2 && V.t < V.win.t1)
    return;                                   // まだ窓内
  if (V.fetching) return;
  const now = Date.now();
  if (now - V.lastFetchTs < 500) return;        // 2回/秒以下
  V.fetching = true; V.lastFetchTs = now;
  const t0 = Math.max(0, V.t - 1);
  const r = await api("window", { run: $("run").value, t0: t0, t1: t0 + W });
  V.fetching = false;
  if (r.ok) V.win = { t0: t0, t1: t0 + W, data: r };
}

// ---------- 座標 ----------
function fit() {
  const w = $("canvasWrap").clientWidth, h = $("canvasWrap").clientHeight;
  cv.width = w; cv.height = h;
  if (!V.lay) { V.zoom = 1; V.panX = 0; V.panY = 0; return; }
  const ww = +V.lay.meta.world_w || 1000, wh = +V.lay.meta.world_h || 600;
  V.zoom = Math.min(w / ww, h / wh) * 0.98;
  V.panX = (w - ww * V.zoom) / 2;
  V.panY = (h - wh * V.zoom) / 2;
}
window.addEventListener("resize", fit);
const sx = x => V.panX + x * V.zoom, sy = y => V.panY + y * V.zoom;
const sw = v => v * V.zoom;

// ---------- 時刻 t の状態 ----------
function stateAt(t) {
  const d = V.win.data;
  const es = {}, sl = {}, am = [], rides = [], pl = {};
  if (!d) return { es, sl, am, rides, pl };
  for (const r of d.equip_state.rows)
    if (r[4] <= t && (r[5] === null || r[5] > t)) es[r[0]] = r;
  for (const r of d.equip_port_lots || [])
    if (r[2] <= t && (r[3] === null || r[3] > t))
      (pl[r[0]] = pl[r[0]] || []).push(r[1]);
  for (const r of d.stocker_lots.rows)
    if (r[2] <= t && (r[3] === null || r[3] > t)) {
      sl[r[0]] = sl[r[0]] || [];
      sl[r[0]].push(r[1]);
    }
  for (const r of d.agv_moves.rows) am.push(r);
  for (const r of d.cl_rides.rows)
    if (r[6] <= t && r[7] > t) rides.push(r);
  return { es, sl, am, rides, pl };
}
const nodePos = {};   // id -> {x,y,w,h}
// 折れ線補助: 弧長比率での補間
function polyLenSegs(poly) {
  const lens = []; let total = 0;
  for (let i = 0; i < poly.length - 1; i++) {
    const l = Math.hypot(poly[i+1].x - poly[i].x, poly[i+1].y - poly[i].y);
    lens.push(l); total += l;
  }
  return { lens, total };
}
function polyPoint(poly, f) {
  const { lens, total } = polyLenSegs(poly);
  let d = Math.min(1, Math.max(0, f)) * total;
  for (let i = 0; i < lens.length; i++) {
    if (d <= lens[i] || i === lens.length - 1) {
      const k = lens[i] ? d / lens[i] : 0;
      return { x: poly[i].x + (poly[i+1].x - poly[i].x) * k,
               y: poly[i].y + (poly[i+1].y - poly[i].y) * k };
    }
    d -= lens[i];
  }
  return poly[poly.length - 1];
}
// 折れ線に沿って進行方向の矢印を描く(画面座標で呼ぶ sx/sy を渡す)
function clArrows(ctx, poly, sx, sy) {
  const { total } = polyLenSegs(poly);
  if (total < 1) return;
  const n = Math.max(1, Math.min(6, Math.round(total / 140)));
  const d = Math.min(0.02, 4 / total);
  for (let j = 0; j < n; j++) {
    const f = (j + 1) / (n + 1);
    const p = polyPoint(poly, f), a = polyPoint(poly, f - d),
          b = polyPoint(poly, f + d);
    const ang = Math.atan2(sy(b.y) - sy(a.y), sx(b.x) - sx(a.x));
    const cx = sx(p.x), cy = sy(p.y), L = 13;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(ang) * L, cy + Math.sin(ang) * L);
    ctx.lineTo(cx + Math.cos(ang + 2.6) * L * 0.5,
               cy + Math.sin(ang + 2.6) * L * 0.5);
    ctx.lineTo(cx + Math.cos(ang - 2.6) * L * 0.5,
               cy + Math.sin(ang - 2.6) * L * 0.5);
    ctx.closePath(); ctx.fill();
  }
}
function clPoly(sid) {           // 駅 i → 駅 i+1 の折れ線
  const st = V.lay.cl_stations;
  const a = nodePos[sid],
        b = nodePos[st[(st.findIndex(s => s.stocker_id === sid) + 1) %
                      st.length].stocker_id];
  const pts = ((V._clPts || {})[sid] || []).map(p => ({x: p.x, y: p.y}));
  return [{x: a.x + a.w/2, y: a.y + a.h/2}, ...pts,
          {x: b.x + b.w/2, y: b.y + b.h/2}];
}
function agvPoly(eid) {          // ストッカー→設備 方向の折れ線
  const eq = nodePos[eid];
  const st = V.lay.stockers.find(s => s.stocker_id ===
    (V.lay.equipments.find(e => e.equip_id === eid) || {}).area ||
    (nodePos[eid] || {}).area);
  const sN = nodePos[st ? st.stocker_id : ""];
  if (!eq || !sN) return null;
  const pts = ((V._agvPts || {})[eid] || []).map(p => ({x: p.x, y: p.y}));
  return [{x: sN.x + sN.w/2, y: sN.y + sN.h/2}, ...pts,
          {x: eq.x + eq.w/2, y: eq.y + eq.h/2}];
}
function buildPos() {
  for (const a of V.lay.areas)
    nodePos[a.area] = { x: a.x, y: a.y, w: a.w, h: a.h };
  for (const e of V.lay.equipments)
    nodePos[e.equip_id] = { x: e.x, y: e.y, w: e.w || EQUIP_W, h: e.h || EQUIP_H };
  for (const s of V.lay.stockers)
    nodePos[s.stocker_id] = { x: s.x, y: s.y, w: s.w || STOCKER_W, h: s.h || STOCKER_H };
}
function interpPos(am, t) {
  // [t_start,t_end] に from→to を折れ線(折れ点あれば)で補間
  const f = nodePos[am[3]], g = nodePos[am[4]];
  if (!f || !g) return null;
  const k = Math.min(1, Math.max(0, (t - am[6]) / (am[7] - am[6] || 1)));
  const fIsEq = V.lay.equipments.some(e => e.equip_id === am[3]);
  const gIsEq = V.lay.equipments.some(e => e.equip_id === am[4]);
  // 片方が設備・片方がストッカーのときだけ折れ線適用
  if (fIsEq !== gIsEq) {
    const eid = fIsEq ? am[3] : am[4];
    let poly = agvPoly(eid);           // ストッカー→設備
    if (poly) {
      if (fIsEq) poly = [...poly].reverse();
      return polyPoint(poly, k);
    }
  }
  return { x: f.x + f.w / 2 + (g.x + g.w / 2 - f.x - f.w / 2) * k,
           y: f.y + f.h / 2 + (g.y + g.h / 2 - f.y - f.h / 2) * k };
}
function agvPosAt(agvId, moves, t) {
  // t を含む移動区間 → 補間。なければ直前の to_node。
  let last = null;
  for (const r of moves) {
    if (r[0] !== agvId) continue;
    if (r[6] <= t && r[7] > t) return interpPos(r, t);
    if (r[7] <= t) last = r;
  }
  if (last) {
    const p = nodePos[last[4]];
    if (p) return { x: p.x + p.w / 2, y: p.y + p.h / 2, node: last[4] };
  }
  return null;
}
function carrierPos(k, t) {
  const cl = V.lay.cl, st = V.lay.cl_stations;
  const M = cl.carriers, T = cl.period;
  const u = ((k * T / M) + t) % T;
  // 区間を特定し、区間時間比率 f を折れ線の弧長比率に写像
  let acc = 0;
  for (let i = 0; i < st.length; i++) {
    const next = acc + st[i].time_to_next;
    if (u < next) {
      const f = (u - acc) / st[i].time_to_next;
      return polyPoint(clPoly(st[i].stocker_id), f);
    }
    acc = next;
  }
  const p = nodePos[st[0].stocker_id];
  return { x: p.x + p.w / 2, y: p.y + p.h / 2 };
}

// ---------- 描画 ----------
function draw() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!V.lay) return;
  buildPos();
  V._clPts = {};
  (V.lay.cl_points || []).forEach(p =>
    (V._clPts[p.from_stocker] = V._clPts[p.from_stocker] || []).push(p));
  V._agvPts = {};
  (V.lay.agv_points || []).forEach(p =>
    (V._agvPts[p.equip_id] = V._agvPts[p.equip_id] || []).push(p));
  const t = V.t, S = stateAt(t);
  // AGV 経路(設備↔ストッカー、点線)
  ctx.strokeStyle = "#4a5563"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
  for (const e of V.lay.equipments) {
    const poly = agvPoly(e.equip_id);
    if (!poly) continue;
    ctx.beginPath();
    poly.forEach((p, i) => i ? ctx.lineTo(sx(p.x), sy(p.y))
                             : ctx.moveTo(sx(p.x), sy(p.y)));
    ctx.stroke();
  }
  ctx.setLineDash([]);
  // CL ループ(折れ点を通る折れ線)
  const st = V.lay.cl_stations;
  ctx.strokeStyle = "#5a6b7d"; ctx.lineWidth = 3; ctx.beginPath();
  st.forEach((s, i) => {
    clPoly(s.stocker_id).forEach((p, j) =>
      (i === 0 && j === 0) ? ctx.moveTo(sx(p.x), sy(p.y))
                          : ctx.lineTo(sx(p.x), sy(p.y)));
  });
  ctx.stroke();
  ctx.fillStyle = "#8fa3b8";
  for (const s of st)
    clArrows(ctx, clPoly(s.stocker_id), sx, sy);
  // エリア
  ctx.textBaseline = "top";
  for (const a of V.lay.areas) {
    ctx.strokeStyle = "#7c8ba0"; ctx.lineWidth = 2;
    ctx.strokeRect(sx(a.x), sy(a.y), sw(a.w), sw(a.h));
    ctx.fillStyle = "#aab"; ctx.font = "12px sans-serif";
    ctx.fillText(a.area, sx(a.x) + 4, sy(a.y) + 2);
  }
  // 設備
  for (const e of V.lay.equipments) {
    const row = S.es[e.equip_id];
    const state = row ? row[1] : "IDLE";
    ctx.fillStyle = STATE_COLORS[state] || "#888";
    shapePath(ctx, e.shape || "rect", sx(e.x), sy(e.y),
              sw(e.w || EQUIP_W), sw(e.h || EQUIP_H));
    ctx.fill();
    ctx.strokeStyle = V.sel === e.equip_id ? "#fff" : "#333";
    ctx.strokeRect(sx(e.x), sy(e.y), sw(e.w || EQUIP_W), sw(e.h || EQUIP_H));
    ctx.fillStyle = "#000"; ctx.font = "9px sans-serif";
    ctx.fillText(e.equip_id, sx(e.x) + 2, sy(e.y) + 2);
    if (row && row[2]) {
      ctx.fillStyle = "#003";
      ctx.fillText(String(row[2]).split(",")[0], sx(e.x) + 2, sy(e.y) + 14);
    }
    // ポート(下辺の小枠): 処理中=黄 / 待機=水色 / 空き=黒
    const pw = e.w || EQUIP_W, ph = e.h || EQUIP_H;
    const np = Math.max(1, e.ports || 1);
    const occ = (S.pl && S.pl[e.equip_id]) || [];
    const slotW = Math.min(6, (pw - 4) / np), gap = (pw - slotW * np) / (np + 1);
    for (let pi = 0; pi < np; pi++) {
      const px = sx(e.x + gap * (pi + 1) + slotW * pi),
            py = sy(e.y + ph - 5);
      const occupied = pi < occ.length;
      const processing = occupied &&
        row && row[2] && row[2].split(",").includes(occ[pi]);
      ctx.fillStyle = occupied
        ? (processing ? "#ffb300" : "#4fc3f7")
        : "#1a1a1a";
      ctx.fillRect(px, py, sw(slotW), sw(4));
      ctx.strokeStyle = "#666";
      ctx.strokeRect(px, py, sw(slotW), sw(4));
      if (occupied) {                  // ロット=白●(ポート枠内)
        const rr = Math.max(1.2,
          Math.min(sw(slotW), sw(4)) / 2 - 0.6);
        ctx.beginPath();
        ctx.arc(px + sw(slotW) / 2, py + sw(4) / 2, rr, 0, 7);
        ctx.fillStyle = "#fff"; ctx.fill();
      }
    }
  }
  // ストッカー
  for (const s of V.lay.stockers) {
    const n = (S.sl[s.stocker_id] || []).length;
    const full = n >= s.capacity;
    ctx.fillStyle = "#28425f";
    ctx.fillRect(sx(s.x), sy(s.y), sw(s.w || STOCKER_W), sw(s.h || STOCKER_H));
    // 容量ぶんのスロット枠(ラベル用に上部 12 論理単位を空ける)
    const SW = s.w || STOCKER_W, SH = s.h || STOCKER_H;
    const cap = Math.max(1, s.capacity), gy = 12, gh = SH - gy - 2, gw = SW - 4;
    const cols = Math.ceil(Math.sqrt(cap * gw / gh)), rows = Math.ceil(cap / cols);
    const cw = gw / cols, ch = gh / rows;
    for (let i = 0; i < cap; i++) {
      const cx = sx(s.x + 2 + (i % cols) * cw), cy = sy(s.y + gy + Math.floor(i / cols) * ch);
      if (i < n) { ctx.fillStyle = full ? "#e53935" : "#42a5f5"; ctx.fillRect(cx, cy, sw(cw) - 0.5, sw(ch) - 0.5); }
      else { ctx.strokeStyle = "#4a6584"; ctx.lineWidth = 0.5; ctx.strokeRect(cx, cy, sw(cw) - 0.5, sw(ch) - 0.5); }
    }
    ctx.lineWidth = 1;
    ctx.strokeStyle = full ? "#ff8a80" : "#7c8ba0";
    ctx.strokeRect(sx(s.x), sy(s.y), sw(SW), sw(SH));
    ctx.fillStyle = "#fff"; ctx.font = "9px sans-serif";
    ctx.fillText(`${s.stocker_id} ${n}/${s.capacity}`, sx(s.x) + 2, sy(s.y) + 2);
  }
  // AGV
  const agvIds = [...new Set(S.am.map(r => r[0]))];
  agvIds.forEach((id, ai) => {
    const mv = S.am.find(r => r[0] === id && r[6] <= t && r[7] > t);
    const p = agvPosAt(id, S.am, t);
    if (!p) return;
    // 待機中: ストッカー上辺の外側に台ごとずらして並べる
    let ax = sx(p.x), ay = sy(p.y);
    if (!mv && p.node) {
      const stn = V.lay.stockers.find(st => st.stocker_id === p.node);
      if (stn) {
        ax = sx(stn.x + 6 + ai * 14);
        ay = sy(stn.y) - 8;
      }
    }
    const carrying = mv && mv[2] !== "EMPTY";
    ctx.fillStyle = carrying ? "#7e57c2" : "#90a4ae";   // 紫/灰青
    ctx.fillRect(ax - 5, ay - 5, 10, 10);               // 四角(ロットと区別)
    ctx.strokeStyle = "#263238"; ctx.strokeRect(ax - 5, ay - 5, 10, 10);
    if (carrying && mv[5]) {                            // 積載ロット=白●
      ctx.beginPath(); ctx.arc(ax, ay, 2.5, 0, 7);
      ctx.fillStyle = "#fff"; ctx.fill();
    }
    if (mv && mv[5]) { ctx.fillStyle = "#ffd"; ctx.font = "8px sans-serif";
      ctx.fillText(mv[5], ax + 6, ay - 6); }
  });
  // CL キャリア(ロット積載中のみ表示)
  const cl = V.lay.cl;
  for (let k = 0; k < cl.carriers; k++) {
    const loads = S.rides.filter(r => r[1] === k);
    if (!loads.length) continue;
    const p = carrierPos(k, t);
    ctx.beginPath(); ctx.arc(sx(p.x), sy(p.y), 7, 0, 7);
    ctx.fillStyle = "#26a69a"; ctx.fill();
    ctx.strokeStyle = V.sel === "cl" + k ? "#fff" : "#135";
    ctx.stroke();
    loads.forEach((r, j) => {
      ctx.beginPath(); ctx.arc(sx(p.x) - 4 + 5 * j, sy(p.y), 2, 0, 7);
      ctx.fillStyle = "#ff5252"; ctx.fill();
    });
  }
  // 時刻
  $("clock").textContent =
    `${Math.floor(t / 1440)}d ${String(Math.floor(t % 1440 / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  $("seek").value = Math.min(t, +$("seek").max);
}

async function updateKpi(t) {
  const now = Date.now();
  if (now - V.lastKpiFetch < 400) return;
  V.lastKpiFetch = now;
  const r = await api("kpi", { run: $("run").value, t: Math.floor(t) });
  if (!r.ok || !r.kpi) return;
  const k = r.kpi;
  const st0 = $("status");
  if (st0 && st0.title === "")
    st0.title = "レイアウトは run 作成時点のものです(以後の編集は反映されません)";
  const lg = document.getElementById("legend");
  if (V.lay && lg && !lg.dataset.shapes) {
    lg.dataset.shapes = "1";
    const seen = {};
    for (const e of V.lay.equipments)
      if (!(e.tool_group in seen)) seen[e.tool_group] = e.shape || "rect";
    lg.insertAdjacentHTML("beforeend", "<h3>形状</h3>" +
      Object.entries(seen).map(([tg, sh]) =>
        `<span class="lg" style="background:#263238;color:#cfe">${tg}:${sh}</span>`
      ).join("")) +
      `<span class="lg" style="color:#8aa">下辺小枠=ポート(処理=状態色/待機=水色)、白●=ロット</span>` +
      `<span class="lg" style="color:#8aa">■AGV(灰青=空走・待機/紫=搬送中)</span>`;
  }
  $("kpiBody").textContent =
    `WIP ${k.wip ? k.wip.total : "-"}  完了 ${k.completed ? k.completed.total : "-"}\n` +
    (k.util ? Object.entries(k.util).map(([g, v]) =>
      `${g} ${(v * 100).toFixed(0)}%`).join("  ") : "") +
    `\nAGV ${k.agv_util ? (k.agv_util.total * 100).toFixed(1) : "-"}%` +
    `  CL ${k.cl_util ? (k.cl_util.total * 100).toFixed(1) : "-"}%`;
}

// ---------- 選択 ----------
cv.addEventListener("click", e => {
  if (!V.lay || (typeof LE !== "undefined" && LE.on)) return;
  if (V.suppressClick) { V.suppressClick = false; return; }
  const r = cv.getBoundingClientRect();
  const x = (e.clientX - r.left - V.panX) / V.zoom,
        y = (e.clientY - r.top - V.panY) / V.zoom;
  const S = stateAt(V.t);
  for (const eq of V.lay.equipments)
    if (x >= eq.x && x <= eq.x + (eq.w || EQUIP_W) && y >= eq.y && y <= eq.y + (eq.h || EQUIP_H)) {
      V.sel = eq.equip_id;
      const row = S.es[eq.equip_id];
      const pl = (S.pl && S.pl[eq.equip_id]) || [];
      const proc = row && row[2] ? row[2].split(",") : [];
      $("selBody").textContent =
        `設備 ${eq.equip_id}\ntool_group: ${eq.tool_group}\n` +
        `area: ${eq.area}\n状態: ${row ? row[1] : "IDLE"}\n` +
        `ロット: ${row && row[2] ? row[2] : "-"}\n` +
        `レシピ: ${row && row[3] ? row[3] : "-"}\n` +
        `ポート: ${pl.length}/${eq.ports || 1}\n` +
        (pl.length ? `  処理中: ${pl.filter(l => proc.includes(l)).join(",") || "-"}\n` +
                    `  待機: ${pl.filter(l => !proc.includes(l)).join(",") || "-"}` : "");
      return;
    }
  for (const s of V.lay.stockers)
    if (x >= s.x && x <= s.x + (s.w || STOCKER_W) && y >= s.y && y <= s.y + (s.h || STOCKER_H)) {
      V.sel = s.stocker_id;
      const lots = S.sl[s.stocker_id] || [];
      $("selBody").textContent =
        `ストッカー ${s.stocker_id}\narea: ${s.area}\n` +
        `格納: ${lots.length}/${s.capacity}\n` +
        `ロット: ${lots.slice(0, 12).join(",")}${lots.length > 12 ? "…" : ""}`;
      return;
    }
  V.sel = null; $("selBody").textContent = "-";
});

// ---------- 再生 ----------
let prevTs = 0;
function frame(ts) {
  const dt = prevTs ? (ts - prevTs) / 1000 : 0;
  prevTs = ts;
  if (typeof LE !== "undefined" && LE.on) {   // 編集モード中は leDraw 側に描画を任せる
    requestAnimationFrame(frame);
    return;
  }
  const bp = $("btnPlay"), ic = V.playing ? "\u23F8" : "\u25B6";
  if (bp.textContent !== ic) { bp.textContent = ic; bp.title = V.playing ? "一時停止" : "再生"; }
  if (V.playing && V.lay) {
    V.t += V.speed * dt;
    const dur = +V.lay.meta.duration;
    const maxT = V.live ? (+V.lay.meta.progress_time || dur) : dur;
    if (V.t >= maxT) { V.t = maxT; if (!V.live) V.playing = false; }
    ensureWindow();
    draw();
    updateKpi(V.t);
  } else if (V.lay) {
    ensureWindow(); draw();
  }
  requestAnimationFrame(frame);
}
$("btnPlay").onclick = () => { V.playing = !V.playing; V.live = false; };
$("btnRew").onclick = () => { V.t = 0; draw(); };
$("btnLive").onclick = () => { V.live = true; V.playing = true; };
document.querySelectorAll("#speeds button").forEach(b =>
  b.onclick = () => {
    document.querySelectorAll("#speeds button").forEach(x =>
      x.classList.remove("on"));
    b.classList.add("on");
    V.speed = +b.dataset.s;
  });
$("seek").oninput = () => {
  V.t = +$("seek").value;
  V.win = { t0: -1, t1: -1, data: null };
  ensureWindow();
};

init();

// ---------- ズーム/パン ----------
let vpDrag = null, vpSpace = false;
const VP_MIN = 0.5, VP_MAX = 20;
function vpFitZoom() {
  const w = $("canvasWrap").clientWidth, h = $("canvasWrap").clientHeight;
  if (!V.lay) return 1;
  const ww = +V.lay.meta.world_w || 1000,
        wh = +V.lay.meta.world_h || 600;
  return Math.min(w / ww, h / wh) * 0.98;
}
function vpZoomAt(cx, cy, factor) {
  const r = cv.getBoundingClientRect();
  const mx = cx - r.left, my = cy - r.top;
  const base = vpFitZoom();
  const nz = Math.min(base * VP_MAX,
                      Math.max(base * VP_MIN, V.zoom * factor));
  const lx = (mx - V.panX) / V.zoom, ly = (my - V.panY) / V.zoom;
  V.zoom = nz;
  V.panX = mx - lx * nz; V.panY = my - ly * nz;
  const el = $("vpZoomPct");
  if (el) el.textContent = Math.round(V.zoom * 100) + "%";
}
function vpNodeHit(cx, cy) {          // 空白判定(設備/ストッカー)
  const r = cv.getBoundingClientRect();
  const x = (cx - r.left - V.panX) / V.zoom,
        y = (cy - r.top - V.panY) / V.zoom;
  for (const e of V.lay.equipments)
    if (x >= e.x && x <= e.x + (e.w || EQUIP_W) &&
        y >= e.y && y <= e.y + (e.h || EQUIP_H)) return true;
  for (const st of V.lay.stockers)
    if (x >= st.x && x <= st.x + (st.w || STOCKER_W) &&
        y >= st.y && y <= st.y + (st.h || STOCKER_H)) return true;
  return false;
}
cv.addEventListener("wheel", e => {
  if (typeof LE !== "undefined" && LE.on) return;
  if (!V.lay) return;
  e.preventDefault();
  vpZoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
}, { passive: false });
cv.addEventListener("dblclick", e => {
  if (typeof LE !== "undefined" && LE.on) return;
  if (!V.lay) return;
  fit(); vpZoomAt(e.clientX, e.clientY, 1);   // ラベル更新用
});
cv.addEventListener("mousedown", e => {
  if (typeof LE !== "undefined" && LE.on) return;
  if (!V.lay) return;
  const blank = !vpNodeHit(e.clientX, e.clientY);
  if (e.button === 1 || (e.button === 0 && (vpSpace || blank)))
    vpDrag = { x: e.clientX, y: e.clientY,
               px: V.panX, py: V.panY, moved: false };
});
window.addEventListener("mousemove", e => {
  if (!vpDrag) return;
  const dx = e.clientX - vpDrag.x, dy = e.clientY - vpDrag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) vpDrag.moved = true;
  if (vpDrag.moved) {
    V.panX = vpDrag.px + dx; V.panY = vpDrag.py + dy;
  }
});
window.addEventListener("mouseup", () => {
  if (vpDrag && vpDrag.moved) V.suppressClick = true;
  vpDrag = null;
});
window.addEventListener("keydown", e => {
  if (e.code === "Space") vpSpace = true;
});
window.addEventListener("keyup", e => {
  if (e.code === "Space") vpSpace = false;
});
// オーバーレイボタン
(function () {
  const wrap = $("canvasWrap");
  wrap.style.position = "relative";
  const d = document.createElement("div");
  d.style.cssText = "position:absolute;right:8px;bottom:8px;" +
    "z-index:5;display:flex;gap:4px;align-items:center;";
  d.innerHTML =
    '<button id="vpIn">+</button><button id="vpOut">&minus;</button>' +
    '<button id="vpFit">全体</button>' +
    '<span id="vpZoomPct" style="font-size:11px;color:#8aa">100%</span>';
  wrap.appendChild(d);
  const center = () => {
    const r = cv.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  };
  $("vpIn").onclick = () => {
    const [cx, cy] = center(); vpZoomAt(cx, cy, 1.25); };
  $("vpOut").onclick = () => {
    const [cx, cy] = center(); vpZoomAt(cx, cy, 1 / 1.25); };
  $("vpFit").onclick = () => {
    const [cx, cy] = center(); fit(); vpZoomAt(cx, cy, 1); };
})();
