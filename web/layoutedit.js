/* レイアウト編集モード (設計書 12.5)。素の JS。 */
"use strict";
const LE = {
  on: false, model: "", nodes: {}, order: [], world: { w: 1000, h: 600 },
  rev: "none", cid: "", dirty: false, readonly: false,
  sel: new Set(), undo: [], redo: [], drag: null, marquee: null,
  zoom: 1, panX: 0, panY: 0, panning: false, space: false,
  gridOn: localStorage.le_grid_on !== "0",
  grid: +(localStorage.le_grid || 10), clStations: [],
};
// client_id はタブ単位で保持(リロードで「他者ロック」化しないため)
LE.cid = sessionStorage.fabsim_client_id || "";
if (!/^[0-9a-f]{16,32}$/.test(LE.cid)) {
  for (let i = 0; i < 16; i++)
    LE.cid += "0123456789abcdef"[Math.floor(Math.random() * 16)];
  sessionStorage.fabsim_client_id = LE.cid;
}
const EQUIP_W2 = 40, EQUIP_H2 = 30, STOCKER_W2 = 60, STOCKER_H2 = 40;

function leApi(a, p, post) {
  let url = API + "?action=" + a, o = { headers: {} };
  if (post) {
    o.method = "POST";
    o.headers["X-Requested-With"] = "fabsim";
    o.headers["Content-Type"] = "application/json";
    o.body = JSON.stringify(p);
  } else if (p) url += "&" + new URLSearchParams(p);
  return fetch(url, o).then(r => r.json());
}
const lex = x => (x - LE.panX) / LE.zoom, ley = y => (y - LE.panY) / LE.zoom;
const lsx = x => LE.panX + x * LE.zoom, lsy = y => LE.panY + y * LE.zoom;
const snap = v => LE.gridOn ? Math.round(v / LE.grid) * LE.grid : v;

// ---------- 開始/終了 ----------
async function leEnter() {
  LE.model = $("model").value;
  const r = await leApi("model_layout", { model: LE.model, client_id: LE.cid });
  if (!r.ok) { alert("model_layout: " + r.error); return; }
  LE.nodes = {}; LE.order = [];
  for (const n of r.nodes) { LE.nodes[n.node_type + "/" + n.node_id] = n; LE.order.push(n.node_type + "/" + n.node_id); }
  LE.world = r.world; LE.rev = r.revision; LE.clStations = r.cl_stations || [];
  LE.struct = r.structure || {}; LE.mrev = r.model_revision || "none";
  LE.structDirty = false; LE.mlock = r.model_lock || {};
  LE.readonly = !r.can_edit || (r.lock.locked && !r.lock.mine);
  if (r.can_edit && !r.lock.mine) {
    const lk = await leApi("layout_lock", { model: LE.model, client_id: LE.cid }, true);
    if (!lk.ok) { LE.readonly = true; alert("他のユーザーが編集中: " + (lk.owner || "?") + (lk.remaining ? "(残り " + lk.remaining + " 秒)" : "")); }
  }
  if (r.can_edit && !LE.readonly) {
    if (LE.hb) clearInterval(LE.hb);
    LE.hb = setInterval(() => leApi("layout_lock",
      { model: LE.model, client_id: LE.cid }, true), 60000);   // ハートビート
  }
  LE.on = true; V.playing = false;
  LE.sel.clear(); LE.undo = []; LE.redo = []; LE.dirty = false;
  leBuildBar(); leFit();
  if (r.warnings.length) alert("警告:\n" + r.warnings.join("\n"));
}
function leExit() {
  if (LE.on && !LE.readonly)
    leApi("layout_unlock", { model: LE.model, client_id: LE.cid }, true);
  LE.on = false;
  if (LE.hb) { clearInterval(LE.hb); LE.hb = null; }
  const bar = document.getElementById("leBar");
  if (bar) bar.remove();
  document.getElementById("lePanel")?.remove();
  fit();
}
$("btnEdit").onclick = () => LE.on ? leExit() : leEnter();
window.addEventListener("beforeunload", e => {
  if (LE.on && LE.dirty) { e.preventDefault(); e.returnValue = ""; }
  if (LE.on && !LE.readonly)
    fetch(API, { method: "POST", keepalive: true,
                 headers: { "X-Requested-With": "fabsim",
                            "Content-Type": "application/json" },
                 body: JSON.stringify({ action: "layout_unlock",
                   model: LE.model, client_id: LE.cid }) }).catch(() => {});
});

function leBuildBar() {
  document.getElementById("leBar")?.remove();   // 冪等: 再構築前に除去
  document.getElementById("lePanel")?.remove();
  const d = document.createElement("div");
  d.className = "edbar"; d.id = "leBar";
  d.innerHTML =
    `<b>レイアウト編集 ${LE.readonly ? "(読み取り専用)" : ""}</b>` +
    `<button id="leUndo">元に戻す</button><button id="leRedo">やり直し</button>` +
    `<label>グリッド <input type="checkbox" id="leGridOn" ${LE.gridOn ? "checked" : ""}>` +
    `<input type="number" id="leGrid" value="${LE.grid}" style="width:50px"></label>` +
    `<select id="leAlign"><option value="">整列▼</option>` +
    `<option value="l">左</option><option value="r">右</option><option value="t">上</option>` +
    `<option value="b">下</option><option value="hc">水平中央</option><option value="vc">垂直中央</option>` +
    `<option value="dh">水平等間隔</option><option value="dv">垂直等間隔</option></select>` +
    `<button id="leAuto">自動配置に戻す</button>` +
    `<button id="leAddEq">設備追加</button><button id="leAddArea">エリア追加</button>` +
    `<button id="leDelNode">削除</button><button id="leValStruct">検証</button>` +
    `<button id="leSave">保存*</button><button id="leDiscard">破棄</button>` +
    `<button id="leClose">閉じる</button>`;
  document.body.insertBefore(d, document.querySelector("main"));
  const p = document.createElement("div");
  p.id = "lePanel"; p.className = "edbar";
  p.innerHTML = `選択 <span id="leSelId">-</span> ` +
    `X<input id="leX" size=5> Y<input id="leY" size=5> ` +
    `幅<input id="leW" size=5> 高<input id="leH" size=5> ` +
    `警告一覧 <span id="warnList"></span>`;
  document.body.insertBefore(p, document.querySelector("main"));
  $("leUndo").onclick = leUndo; $("leRedo").onclick = leRedo;
  $("leGridOn").onchange = e => { LE.gridOn = e.target.checked;
    localStorage.le_grid_on = LE.gridOn ? "1" : "0"; };
  $("leGrid").onchange = e => { LE.grid = +e.target.value || 10;
    localStorage.le_grid = LE.grid; };
  $("leAlign").onchange = e => { leAlign(e.target.value); e.target.value = ""; };
  $("leAuto").onclick = leAuto;
  $("leAddEq").onclick = leAddEquip;
  $("leAddArea").onclick = leAddArea;
  $("leDelNode").onclick = leDeleteSel;
  $("leValStruct").onclick = leValidateStruct;
  $("leSave").onclick = leSave;
  $("leDiscard").onclick = () => { if (confirm("変更を破棄しますか")) leEnter(); };
  $("leClose").onclick = leExit;
  for (const id of ["leX", "leY", "leW", "leH"])
    $(id).onchange = leApplyNum;
  leUpdatePanel();
}

function leFit() {
  const w = $("canvasWrap").clientWidth, h = $("canvasWrap").clientHeight;
  cv.width = w; cv.height = h;
  LE.zoom = Math.min(w / LE.world.w, h / LE.world.h) * 0.95;
  LE.panX = (w - LE.world.w * LE.zoom) / 2;
  LE.panY = (h - LE.world.h * LE.zoom) / 2;
}

// ---------- 描画 ----------
function leDraw() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  // CL ループ(折れ点込み)
  ctx.strokeStyle = "#5a6b7d"; ctx.lineWidth = 3; ctx.beginPath();
  LE.clStations.forEach((s, i) => {
    const a = LE.nodes["stocker/" + s.stocker_id];
    const b = LE.nodes["stocker/" +
      LE.clStations[(i + 1) % LE.clStations.length].stocker_id];
    if (!a || !b) return;
    leClPoly(s.stocker_id, a, b).forEach((p, j) =>
      (i === 0 && j === 0) ? ctx.moveTo(lsx(p.x), lsy(p.y))
                          : ctx.lineTo(lsx(p.x), lsy(p.y)));
  });
  ctx.stroke();
  ctx.fillStyle = "#8fa3b8";
  LE.clStations.forEach((s, i) => {
    const a = LE.nodes["stocker/" + s.stocker_id],
          b = LE.nodes["stocker/" + LE.clStations[
            (i + 1) % LE.clStations.length].stocker_id];
    if (a && b) clArrows(ctx, leClPoly(s.stocker_id, a, b), lsx, lsy);
  });
  // AGV 経路(点線)
  ctx.strokeStyle = "#4a5563"; ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type !== "equip") continue;
    const sn = LE.order.map(o => LE.nodes[o])
      .find(s => s.node_type === "stocker" && s.area === n.area);
    if (!sn) continue;
    leAgvPoly(n.node_id, sn, n).forEach((p, i) =>
      i ? ctx.lineTo(lsx(p.x), lsy(p.y)) : ctx.moveTo(lsx(p.x), lsy(p.y)));
    ctx.stroke();
  }
  ctx.setLineDash([]);
  if (LE.gridOn) {
    ctx.strokeStyle = "#ffffff10"; ctx.lineWidth = 1;
    for (let x = 0; x <= LE.world.w; x += LE.grid) {
      ctx.beginPath(); ctx.moveTo(lsx(x), lsy(0)); ctx.lineTo(lsx(x), lsy(LE.world.h)); ctx.stroke();
    }
    for (let y = 0; y <= LE.world.h; y += LE.grid) {
      ctx.beginPath(); ctx.moveTo(lsx(0), lsy(y)); ctx.lineTo(lsx(LE.world.w), lsy(y)); ctx.stroke();
    }
  }
  const colors = { area: "#7c8ba0", equip: "#4a7fa5", stocker: "#2e6e5e" };
  for (const k of LE.order) {
    const n = LE.nodes[k], sel = LE.sel.has(k);
    if (n.node_type === "area") {
      ctx.strokeStyle = sel ? "#fff" : "#7c8ba0"; ctx.lineWidth = 2;
      ctx.strokeRect(lsx(n.x), lsy(n.y), n.w * LE.zoom, n.h * LE.zoom);
      ctx.fillStyle = "#aab"; ctx.font = "12px sans-serif";
      ctx.fillText(n.node_id, lsx(n.x) + 4, lsy(n.y) + 2);
    } else if (n.node_type === "equip" || n.node_type === "stocker") {
      ctx.fillStyle = n.node_type === "equip" ? "#4a7fa5" : "#2e6e5e";
      if (n.node_type === "equip" && typeof shapePath === "function")
        { shapePath(ctx, n.shape || "rect", lsx(n.x), lsy(n.y),
                    n.w * LE.zoom, n.h * LE.zoom); ctx.fill(); }
      else ctx.fillRect(lsx(n.x), lsy(n.y), n.w * LE.zoom, n.h * LE.zoom);
      ctx.strokeStyle = sel ? "#fff" : "#333";
      ctx.strokeRect(lsx(n.x), lsy(n.y), n.w * LE.zoom, n.h * LE.zoom);
      ctx.fillStyle = "#fff"; ctx.font = "9px sans-serif";
      ctx.fillText(n.node_id, lsx(n.x) + 2, lsy(n.y) + 2);
      if (n.node_type === "stocker") {
        const sq = LE.clStations.findIndex(
          s => s.stocker_id === n.node_id);
        if (sq >= 0) {
          ctx.fillStyle = "#ffd54f"; ctx.font = "bold 10px sans-serif";
          ctx.fillText(String(sq + 1), lsx(n.x + n.w) - 10, lsy(n.y) + 2);
        }
      }
      if (sel) {
        ctx.strokeStyle = "#fff";
        ctx.strokeRect(lsx(n.x), lsy(n.y), n.w * LE.zoom, n.h * LE.zoom);
      }
    }
  }
  if (LE.marquee) {
    ctx.strokeStyle = "#8fd"; ctx.setLineDash([4, 4]);
    ctx.strokeRect(lsx(LE.marquee.x0), lsy(LE.marquee.y0),
                   (LE.marquee.x1 - LE.marquee.x0) * LE.zoom,
                   (LE.marquee.y1 - LE.marquee.y0) * LE.zoom);
    ctx.setLineDash([]);
  }
  // 折れ点ハンドル
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (!n.node_type.endsWith("_point")) continue;
    ctx.beginPath(); ctx.arc(lsx(n.x), lsy(n.y), 4, 0, 7);
    ctx.fillStyle = LE.sel.has(k) ? "#ffdd55" : "#8fd";
    ctx.fill();
    ctx.strokeStyle = "#135"; ctx.stroke();
  }
  // リサイズハンドル(選択中 or ホバー中ノード、readonly 時は非表示)
  if (!LE.readonly) {
    const hs = new Set();
    if (LE.sel.size === 1) hs.add([...LE.sel][0]);
    if (LE.hoverNode) hs.add(LE.hoverNode);
    for (const k of hs) {
      const n = LE.nodes[k];
      if (!n || n.node_type.endsWith("_point")) continue;
      for (const [c, hx, hy] of leHandlesFor(k)) {
        ctx.beginPath(); ctx.arc(lsx(hx), lsy(hy), HANDLE_R, 0, 7);
        ctx.fillStyle = "#fff"; ctx.fill();
        ctx.strokeStyle = "#1565c0"; ctx.lineWidth = 1.5; ctx.stroke();
      }
    }
    ctx.lineWidth = 1;
  }
}
const _origFrame = frame;
setInterval(() => { if (LE.on) leDraw(); }, 33);

// ---------- 選択・ドラッグ ----------
function leNodeAt(x, y) {
  for (let i = LE.order.length - 1; i >= 0; i--) {
    const n = LE.nodes[LE.order[i]];
    if (n.node_type !== "area" &&
        x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h)
      return LE.order[i];
  }
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type === "area" &&
        x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h)
      return k;
  }
  return null;
}
const leChildren = k => LE.order.filter(o => {
  const n = LE.nodes[o];
  return o !== k && n.node_type !== "area" &&
    n.area === LE.nodes[k].node_id;
});

// ---------- 折れ点(cl_point/agv_point) ----------
function lePtsOf(nt, ref) {
  return LE.order.filter(k => {
    const n = LE.nodes[k];
    return n.node_type === nt && String(n.node_id).startsWith(ref + ":");
  }).map(k => LE.nodes[k])
    .sort((a, b) => +a.node_id.split(":").pop() - +b.node_id.split(":").pop());
}
function leClPoly(sid, a, b) {
  const pts = lePtsOf("cl_point", sid);
  return [{x: a.x + a.w / 2, y: a.y + a.h / 2}, ...pts,
          {x: b.x + b.w / 2, y: b.y + b.h / 2}];
}
function leAgvPoly(eid, st, eq) {   // ストッカー→設備
  const pts = lePtsOf("agv_point", eid);
  return [{x: st.x + st.w / 2, y: st.y + st.h / 2}, ...pts,
          {x: eq.x + eq.w / 2, y: eq.y + eq.h / 2}];
}
function distSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  const k = l2 ? Math.max(0, Math.min(1,
    ((px - ax) * dx + (py - ay) * dy) / l2)) : 0;
  return Math.hypot(px - ax - k * dx, py - ay - k * dy);
}
const HANDLE_R = 5.5;            // 画面上のハンドル半径(px)
function leHandlesFor(k) {
  const n = LE.nodes[k];
  return [["nw", n.x, n.y], ["ne", n.x + n.w, n.y],
          ["sw", n.x, n.y + n.h], ["se", n.x + n.w, n.y + n.h]];
}
function leHandleAt(x, y) {
  if (LE.readonly) return null;
  const th = 8 / LE.zoom;
  const cands = [];
  if (LE.sel.size === 1) cands.push([...LE.sel][0]);
  if (LE.hoverNode && !cands.includes(LE.hoverNode))
    cands.push(LE.hoverNode);
  for (const k of cands) {
    const n = LE.nodes[k];
    if (!n || n.node_type.endsWith("_point")) continue;
    for (const [c, hx, hy] of leHandlesFor(k))
      if (Math.abs(x - hx) <= th && Math.abs(y - hy) <= th)
        return { k, corner: c };
  }
  return null;
}
function lePointAt(x, y) {
  const rad = 7 / LE.zoom;
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type.endsWith("_point") &&
        Math.hypot(n.x - x, n.y - y) <= rad) return k;
  }
  return null;
}
function leSegHit(x, y) {
  const th = 6 / LE.zoom;
  const st = LE.clStations;
  for (let i = 0; i < st.length; i++) {
    const a = LE.nodes["stocker/" + st[i].stocker_id],
          b = LE.nodes["stocker/" +
                     st[(i + 1) % st.length].stocker_id];
    if (!a || !b) continue;
    const poly = leClPoly(st[i].stocker_id, a, b);
    for (let j = 0; j < poly.length - 1; j++)
      if (distSeg(x, y, poly[j].x, poly[j].y,
                  poly[j+1].x, poly[j+1].y) < th)
        return { nt: "cl_point", ref: st[i].stocker_id, idx: j };
  }
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type !== "equip") continue;
    const sn = LE.order.map(o => LE.nodes[o])
      .find(s => s.node_type === "stocker" && s.area === n.area);
    if (!sn) continue;
    const poly = leAgvPoly(n.node_id, sn, n);
    for (let j = 0; j < poly.length - 1; j++)
      if (distSeg(x, y, poly[j].x, poly[j].y,
                  poly[j+1].x, poly[j+1].y) < th)
        return { nt: "agv_point", ref: n.node_id, idx: j };
  }
  return null;
}
function lePtSnap(nt, ref) {
  const m = {};
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type === nt && String(n.node_id).startsWith(ref + ":"))
      m[k] = { ...n };
  }
  return m;
}
function leReindex(nt, ref, ordered) {
  ordered.forEach((n, i) => {
    const ok = nt + "/" + n.node_id, nk = nt + "/" + ref + ":" + i;
    if (ok !== nk) {
      delete LE.nodes[ok];
      LE.order = LE.order.filter(o => o !== ok);
      LE.sel.delete(ok); LE.sel.add(nk);
    }
    n.node_id = ref + ":" + i;
    LE.nodes[nk] = n;
    if (!LE.order.includes(nk)) LE.order.push(nk);
  });
}
function leAddPoint(seg, x, y) {
  const pts = lePtsOf(seg.nt, seg.ref);
  const np = { node_type: seg.nt, node_id: seg.ref + ":0",
               x: snap(x), y: snap(y), w: 0, h: 0 };
  pts.splice(seg.idx, 0, np);
  LE.nodes[seg.nt + "/" + np.node_id] = np;
  LE.order.push(seg.nt + "/" + np.node_id);
  leReindex(seg.nt, seg.ref, pts);
  return seg.nt + "/" + np.node_id;
}

cv.addEventListener("mousedown", e => {
  if (!LE.on) return;
  e.preventDefault();
  const r = cv.getBoundingClientRect(),
        x = lex(e.clientX - r.left), y = ley(e.clientY - r.top);
  if (LE.space || e.button === 1) {
    LE.panning = { x: e.clientX, y: e.clientY }; return; }
  const hd = leHandleAt(x, y);      // ハンドルは最優先
  if (hd) {
    if (!LE.sel.has(hd.k)) { LE.sel = new Set([hd.k]); leUpdatePanel(); }
    const n = LE.nodes[hd.k];
    LE.drag = { x0: x, y0: y, resize: hd.corner,
                before: { [hd.k]: { x: n.x, y: n.y, w: n.w, h: n.h } } };
    return;
  }
  const kp = lePointAt(x, y);
  if (kp) {
    if (e.shiftKey) {
      LE.sel.has(kp) ? LE.sel.delete(kp) : LE.sel.add(kp);
      leUpdatePanel(); return; }
    if (!LE.sel.has(kp)) { LE.sel = new Set([kp]); leUpdatePanel(); }
    if (LE.readonly) return;
    const n = LE.nodes[kp];
    LE.drag = { x0: x, y0: y, point: true,
                before: { [kp]: { x: n.x, y: n.y, w: 0, h: 0 } } };
    return;
  }
  const k = leNodeAt(x, y);
  const kNonArea = k && LE.nodes[k].node_type !== "area" ? k : null;
  const kArea = k && LE.nodes[k].node_type === "area" ? k : null;
  if (kNonArea && e.shiftKey) {
    LE.sel.has(kNonArea) ? LE.sel.delete(kNonArea) : LE.sel.add(kNonArea);
    leUpdatePanel(); return; }
  if (kNonArea) {
    if (!LE.sel.has(kNonArea)) {
      LE.sel = new Set([kNonArea]); leUpdatePanel(); }
    if (LE.readonly) return;
    const before = {};
    for (const s of [...LE.sel]) {
      const m = LE.nodes[s];
      if (m.node_type.endsWith("_point")) continue;
      before[s] = { x: m.x, y: m.y, w: m.w, h: m.h };
    }
    LE.drag = { x0: x, y0: y, before };
    return;
  }
  const seg = !LE.readonly && leSegHit(x, y);
  if (seg) {
    const nk = leAddPoint(seg, x, y);
    LE.sel = new Set([nk]); leUpdatePanel();
    const n = LE.nodes[nk];
    LE.drag = { x0: x, y0: y, point: true, created: nk,
                before: { [nk]: { x: n.x, y: n.y, w: 0, h: 0 } } };
    return;
  }
  if (kArea) {
    if (e.shiftKey) {
      LE.sel.has(kArea) ? LE.sel.delete(kArea) : LE.sel.add(kArea);
      leUpdatePanel(); return; }
    if (!LE.sel.has(kArea)) { LE.sel = new Set([kArea]); leUpdatePanel(); }
    if (LE.readonly) return;
    const n = LE.nodes[kArea];
    const before = {};
    for (const s of [...LE.sel]) {
      const m = LE.nodes[s];
      if (m.node_type.endsWith("_point")) continue;
      before[s] = { x: m.x, y: m.y, w: m.w, h: m.h };
      if (!e.altKey)
        for (const c of leChildren(s)) {
          const cn = LE.nodes[c];
          before[c] = { x: cn.x, y: cn.y, w: cn.w, h: cn.h };
        }
    }
    LE.drag = { x0: x, y0: y, before };
    return;
  }
  LE.marquee = { x0: x, y0: y, x1: x, y1: y };
});
window.addEventListener("mousemove", e => {
  if (!LE.on) return;
  const r = cv.getBoundingClientRect(),
        x = lex(e.clientX - r.left), y = ley(e.clientY - r.top);
  if (LE.panning) {
    LE.panX += e.clientX - LE.panning.x; LE.panY += e.clientY - LE.panning.y;
    LE.panning = { x: e.clientX, y: e.clientY }; return;
  }
  if (LE.marquee) { LE.marquee.x1 = x; LE.marquee.y1 = y; return; }
  if (!LE.drag) {
    // ホバー追跡とカーソル
    LE.hoverNode = leNodeAt(x, y);
    const h = leHandleAt(x, y);
    cv.style.cursor = h
      ? (h.corner === "nw" || h.corner === "se"
         ? "nwse-resize" : "nesw-resize")
      : (LE.hoverNode ? "move" : "default");
    return;
  }
  const dx = x - LE.drag.x0, dy = y - LE.drag.y0;
  for (const [k, b] of Object.entries(LE.drag.before)) {
    const n = LE.nodes[k];
    if (LE.drag.point) {
      n.x = Math.max(0, Math.min(LE.world.w, snap(b.x + dx)));
      n.y = Math.max(0, Math.min(LE.world.h, snap(b.y + dy)));
    } else if (LE.drag.resize) {
      const c = LE.drag.resize;      // "nw"/"ne"/"sw"/"se" 角
      let l = b.x, t2 = b.y, r2 = b.x + b.w, bo = b.y + b.h;
      if (c[1] === "w") l = snap(b.x + dx);
      else if (c[1] === "e") r2 = snap(b.x + b.w + dx);
      if (c[0] === "n") t2 = snap(b.y + dy);
      else if (c[0] === "s") bo = snap(b.y + b.h + dy);
      l = Math.max(0, Math.min(l, r2 - 10));
      r2 = Math.min(LE.world.w, Math.max(r2, l + 10));
      t2 = Math.max(0, Math.min(t2, bo - 10));
      bo = Math.min(LE.world.h, Math.max(bo, t2 + 10));
      n.x = l; n.y = t2; n.w = r2 - l; n.h = bo - t2;
    } else {
      n.x = Math.max(0, Math.min(LE.world.w - n.w, snap(b.x + dx)));
      n.y = Math.max(0, Math.min(LE.world.h - n.h, snap(b.y + dy)));
    }
  }
  LE.dirty = true;
});
window.addEventListener("mouseup", e => {
  if (!LE.on) return;
  if (LE.panning) { LE.panning = false; return; }
  const r = cv.getBoundingClientRect(),
        x = lex(e.clientX - r.left), y = ley(e.clientY - r.top);
  if (LE.marquee) {
    const m = LE.marquee; LE.marquee = null;
    const x0 = Math.min(m.x0, m.x1), x1 = Math.max(m.x0, m.x1);
    const y0 = Math.min(m.y0, m.y1), y1 = Math.max(m.y0, m.y1);
    if (x1 - x0 > 1 || y1 - y0 > 1) {
      if (!e.shiftKey) LE.sel.clear();
      for (const k of LE.order) {
        const n = LE.nodes[k];
        if (n.node_type.endsWith("_point")) continue;
        if (n.x + n.w / 2 >= x0 && n.x + n.w / 2 <= x1 &&
            n.y + n.h / 2 >= y0 && n.y + n.h / 2 <= y1) LE.sel.add(k);
      }
      leUpdatePanel();
      return;
    }
    if (!e.shiftKey) { LE.sel.clear(); leUpdatePanel(); }
    return;
  }
  if (LE.drag) {
    const before = LE.drag.before;
    if (LE.drag.created) {
      const n = LE.nodes[LE.drag.created],
            ref = n.node_id.split(":")[0];
      LE.undo.push([{ op: "ptbatch", nt: n.node_type, ref,
                      before: {},
                      after: lePtSnap(n.node_type, ref) }]);
    } else {
      const ops = [];
      for (const [k, b] of Object.entries(before)) {
        const n = LE.nodes[k];
        if (!n) continue;
        if (n.x !== b.x || n.y !== b.y || n.w !== b.w || n.h !== b.h)
          ops.push({ k, before: b,
                     after: { x: n.x, y: n.y, w: n.w, h: n.h } });
      }
      if (ops.length) LE.undo.push(ops);
    }
    if (LE.undo.length > 100) LE.undo.shift();
    LE.redo = [];
    LE.drag = null;
    leCheckAreaMove();
  }
});
cv.addEventListener("dblclick", e => {
  if (!LE.on || LE.readonly) return;
  const r = cv.getBoundingClientRect(),
        x = lex(e.clientX - r.left), y = ley(e.clientY - r.top);
  const kp = lePointAt(x, y);
  if (!kp) return;
  const n = LE.nodes[kp], ref = n.node_id.split(":")[0];
  const before = lePtSnap(n.node_type, ref);
  const rest = lePtsOf(n.node_type, ref).filter(p => p !== n);
  delete LE.nodes[kp];
  LE.order = LE.order.filter(k => k !== kp);
  LE.sel.delete(kp);
  leReindex(n.node_type, ref, rest);
  LE.undo.push([{ op: "ptbatch", nt: n.node_type, ref, before,
                  after: lePtSnap(n.node_type, ref) }]);
  LE.redo = [];
  LE.dirty = true; leUpdatePanel();
});
cv.addEventListener("wheel", e => {
  if (!LE.on) return;
  e.preventDefault();
  const f = e.deltaY < 0 ? 1.1 : 0.9;
  LE.zoom = Math.min(10, Math.max(0.1, LE.zoom * f));
}, { passive: false });
window.addEventListener("keydown", e => {
  if (!LE.on) return;
  if (e.code === "Space") LE.space = true;
  if (e.ctrlKey && e.key === "s") { e.preventDefault(); leSave(); }
  if (e.ctrlKey && (e.key === "z" || (e.shiftKey && e.key === "Z"))) { e.preventDefault(); leUndo(); }
  if (e.ctrlKey && e.key === "y") { e.preventDefault(); leRedo(); }
  if ((e.key === "Delete" || e.key === "Backspace") && LE.sel.size &&
      !LE.readonly && !/input|select/i.test(e.target.tagName)) {
    e.preventDefault(); leDeleteSel();
  }
  if (e.key.startsWith("Arrow") && LE.sel.size && !LE.readonly) {
    e.preventDefault();
    const g = (e.shiftKey ? 10 : 1) * LE.grid;
    const dx = e.key === "ArrowRight" ? g : e.key === "ArrowLeft" ? -g : 0;
    const dy = e.key === "ArrowDown" ? g : e.key === "ArrowUp" ? -g : 0;
    const ops = [];
    for (const k of LE.sel) {
      const n = LE.nodes[k];
      const b = { x: n.x, y: n.y, w: n.w, h: n.h };
      n.x = Math.max(0, Math.min(LE.world.w - n.w, n.x + dx));
      n.y = Math.max(0, Math.min(LE.world.h - n.h, n.y + dy));
      ops.push({ k, before: b, after: { x: n.x, y: n.y, w: n.w, h: n.h } });
      if (n.node_type === "area")
        for (const c of leChildren(k)) {
          const cn = LE.nodes[c], cb = { x: cn.x, y: cn.y, w: cn.w, h: cn.h };
          cn.x += n.x - b.x; cn.y += n.y - b.y;
          ops.push({ k: c, before: cb, after: { x: cn.x, y: cn.y, w: cn.w, h: cn.h } });
        }
    }
    LE.undo.push(ops); LE.dirty = true;
  }
});
window.addEventListener("keyup", e => { if (e.code === "Space") LE.space = false; });

// ---------- 操作 ----------
function lePtRestore(map) {
  const drop = k => LE.nodes[k].node_type.endsWith("_point") &&
    (!map._nt || (LE.nodes[k].node_type === map._nt &&
     String(LE.nodes[k].node_id).startsWith(map._ref + ":")));
  for (const k of Object.keys(LE.nodes))
    if (drop(k)) delete LE.nodes[k];
  LE.order = LE.order.filter(k => LE.nodes[k]);
  for (const k in map) {
    if (k === "_nt" || k === "_ref") continue;
    LE.nodes[k] = { ...map[k] };
    if (!LE.order.includes(k)) LE.order.push(k);
  }
}
function leUndo() {
  const ops = LE.undo.pop(); if (!ops) return;
  for (const o of ops) {
    if (o.op === "ptbatch")
      lePtRestore(Object.assign({ _nt: o.nt, _ref: o.ref }, o.before));
    else if (o.op === "ptall") lePtRestore(o.before);
    else if (o.op === "fullsnap") leStructRestore(o.before);
    else if (LE.nodes[o.k]) Object.assign(LE.nodes[o.k], o.before);
  }
  LE.redo.push(ops); LE.dirty = true;
}
function leRedo() {
  const ops = LE.redo.pop(); if (!ops) return;
  for (const o of ops) {
    if (o.op === "ptbatch")
      lePtRestore(Object.assign({ _nt: o.nt, _ref: o.ref }, o.after));
    else if (o.op === "ptall") lePtRestore(o.after);
    else if (o.op === "fullsnap") leStructRestore(o.after);
    else if (LE.nodes[o.k]) Object.assign(LE.nodes[o.k], o.after);
  }
  LE.undo.push(ops); LE.dirty = true;
}
function leAlign(op) {
  const ks = [...LE.sel]; if (ks.length < 2 || !op) return;
  const ns = ks.map(k => LE.nodes[k]);
  const ops = [];
  const rec = (n, f) => {
    const b = { x: n.x, y: n.y, w: n.w, h: n.h }; f(n);
    ops.push({ k: ks[ns.indexOf(n)], before: b,
               after: { x: n.x, y: n.y, w: n.w, h: n.h } });
  };
  const min = f => Math.min(...ns.map(f)), max = f => Math.max(...ns.map(f));
  if (op === "l") ns.forEach(n => rec(n, n => n.x = min(m => m.x)));
  if (op === "r") ns.forEach(n => rec(n, n => n.x = max(m => m.x + m.w) - n.w));
  if (op === "t") ns.forEach(n => rec(n, n => n.y = min(m => m.y)));
  if (op === "b") ns.forEach(n => rec(n, n => n.y = max(m => m.y + m.h) - n.h));
  if (op === "hc") ns.forEach(n => rec(n, n => n.x = ns.reduce((a, m) => a + m.x + m.w / 2, 0) / ns.length - n.w / 2));
  if (op === "vc") ns.forEach(n => rec(n, n => n.y = ns.reduce((a, m) => a + m.y + m.h / 2, 0) / ns.length - n.h / 2));
  if (op === "dh" || op === "dv") {
    if (ns.length < 3) return;
    const hor = op === "dh", sorted = [...ns].sort((a, b) => hor ? a.x - b.x : a.y - b.y);
    const lo = hor ? min(m => m.x) : min(m => m.y);
    const span = (hor ? max(m => m.x) : max(m => m.y)) - lo;
    sorted.forEach((n, i) => rec(n, n => {
      if (hor) n.x = lo + span * i / (ns.length - 1); else n.y = lo + span * i / (ns.length - 1);
    }));
  }
  if (ops.length) { LE.undo.push(ops); LE.dirty = true; }
}
async function leAuto() {
  if (LE.readonly || !confirm("自動配置に戻しますか")) return;
  const r = await leApi("layout_auto", { model: LE.model }, true);
  if (!r.ok) return;
  const ops = [];
  for (const n of r.nodes) {
    const k = n.node_type + "/" + n.node_id, cur = LE.nodes[k];
    if (!cur || n.node_type.endsWith("_point")) continue;
    ops.push({ k, before: { x: cur.x, y: cur.y, w: cur.w, h: cur.h },
               after: { x: n.x, y: n.y, w: n.w, h: n.h } });
    Object.assign(cur, { x: n.x, y: n.y, w: n.w, h: n.h });
  }
  const allPts = {};
  for (const k of Object.keys(LE.nodes))
    if (LE.nodes[k].node_type.endsWith("_point"))
      allPts[k] = { ...LE.nodes[k] };
  ops.push({ op: "ptall", before: allPts, after: {} });
  lePtRestore({});                 // 折れ点を全削除
  LE.undo.push(ops); LE.dirty = true;
}
function leUpdatePanel() {
  const ks = [...LE.sel];
  $("leSelId").textContent = ks.join(",") || "-";
  if (ks.length === 1) {
    const n = LE.nodes[ks[0]];
    $("leX").value = n.x; $("leY").value = n.y;
    $("leW").value = n.w; $("leH").value = n.h;
  }
  leUpdateWarnings();
  leRenderStructPanel();
}
function leRenderStructPanel() {
  let el = document.getElementById("leStructPanel");
  if (!el) {
    el = document.createElement("div");
    el.id = "leStructPanel";
    document.getElementById("side").appendChild(el);
  }
  let h = "<h3>CL 駅順</h3>";
  const t = LE.struct["cleanliner.csv"];
  if (t) {
    const si = t.cols.indexOf("stocker_id"), ti = t.cols.indexOf("time_to_next");
    h += t.rows.map((r, i) =>
      `<div>${i + 1}. ${r[si]} ` +
      `<input type="number" step="any" style="width:55px" data-i="${i}" ` +
      `value="${r[ti]}" ${LE.readonly ? "disabled" : ""}> ` +
      `<button data-mu="${i}" ${LE.readonly ? "disabled" : ""}>▲</button>` +
      `<button data-md="${i}" ${LE.readonly ? "disabled" : ""}>▼</button></div>`
    ).join("");
  }
  el.innerHTML = h;
  el.querySelectorAll("input[data-i]").forEach(inp =>
    inp.onchange = () => leClSetTtn(+inp.dataset.i, inp.value));
  el.querySelectorAll("button[data-mu]").forEach(b =>
    b.onclick = () => leClStationMove(+b.dataset.mu, -1));
  el.querySelectorAll("button[data-md]").forEach(b =>
    b.onclick = () => leClStationMove(+b.dataset.md, 1));
}
function leApplyNum() {
  if (LE.sel.size !== 1 || LE.readonly) return;
  const k = [...LE.sel][0], n = LE.nodes[k];
  const b = { x: n.x, y: n.y, w: n.w, h: n.h };
  const isPt = n.node_type.endsWith("_point");
  n.x = Math.max(0, Math.min(LE.world.w - n.w, +$("leX").value || n.x));
  n.y = Math.max(0, Math.min(LE.world.h - n.h, +$("leY").value || n.y));
  if (!isPt) {
    n.w = Math.max(10, Math.min(LE.world.w - n.x, +$("leW").value || n.w));
    n.h = Math.max(10, Math.min(LE.world.h - n.y, +$("leH").value || n.h));
  }
  LE.undo.push([{ k, before: b, after: { x: n.x, y: n.y, w: n.w, h: n.h } }]);
  LE.dirty = true;
}
function leUpdateWarnings() {
  const warns = [];
  const areas = LE.order.filter(k => LE.nodes[k].node_type === "area");
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type === "area" || n.node_type.endsWith("_point")) continue;
    const own = areas.find(a => LE.nodes[a].node_id === n.area);
    if (own) {
      const A = LE.nodes[own];
      if (n.x < A.x || n.y < A.y || n.x + n.w > A.x + A.w || n.y + n.h > A.y + A.h)
        warns.push([k, "所属エリア外にはみ出し"]);
      for (const a of areas)
        if (a !== own) {
          const B = LE.nodes[a];
          if (n.x >= B.x && n.y >= B.y && n.x + n.w <= B.x + B.w && n.y + n.h <= B.y + B.h)
            warns.push([k, "他エリア枠内にあります"]);
        }
    }
    if (n.w < 10 || n.h < 10) warns.push([k, "最小サイズ未満"]);
    for (const k2 of LE.order) {
      if (k2 <= k) continue;
      const m = LE.nodes[k2];
      if (m.node_type === n.node_type &&
          n.x < m.x + m.w && m.x < n.x + n.w &&
          n.y < m.y + m.h && m.y < n.y + n.h)
        warns.push([k + "&" + k2, "ノードが重なっています"]);
    }
  }
  $("warnList").innerHTML = warns.map(([k, m]) =>
    `<div data-k="${k}">⚠ ${k}: ${m}</div>`).join("") || "<div>なし</div>";
  $("warnList").querySelectorAll("div").forEach(d =>
    d.onclick = () => { const k = d.dataset.k.split("&")[0];
      if (LE.nodes[k]) { LE.sel = new Set([k]); leUpdatePanel(); } });
}
async function leSave() {
  if (LE.readonly) return;
  const nodes = LE.order.map(k => LE.nodes[k]);
  const body = { model: LE.model, client_id: LE.cid,
                 base_revision: LE.rev, nodes };
  if (LE.structDirty) {
    if (!confirm("構成変更を保存します(CSVの#コメントは失われます)。よろしいですか?"))
      return;
    body.structure = LE.struct;
    body.base_model_revision = LE.mrev;
  }
  const r = await leApi("layout_save", body, true);
  if (!r.ok) {
    if (r.error === "revision" && confirm("他の人が保存しました。再読込しますか?")) leEnter();
    else alert("保存失敗: " + r.error);
    return;
  }
  LE.rev = r.revision;
  if (r.model_revision) { LE.mrev = r.model_revision; LE.structDirty = false; }
  LE.dirty = false;
  alert("保存しました (rev " + r.revision + ")");
}

// ---------- 構成編集(設備/エリア/CL駅順) ----------
function leStructSnap() {
  return { nodes: JSON.parse(JSON.stringify(LE.nodes)),
           order: [...LE.order],
           struct: JSON.parse(JSON.stringify(LE.struct)),
           stations: JSON.parse(JSON.stringify(LE.clStations)) };
}
function leStructRestore(s) {
  LE.nodes = JSON.parse(JSON.stringify(s.nodes));
  LE.order = [...s.order];
  LE.struct = JSON.parse(JSON.stringify(s.struct));
  LE.clStations = JSON.parse(JSON.stringify(s.stations));
  LE.sel.clear();
}
function leStructOp(fn) {          // 構成変更を Undo 1ステップとして記録
  const before = leStructSnap();
  fn();
  LE.undo.push([{ op: "fullsnap", before, after: leStructSnap() }]);
  LE.redo = []; LE.dirty = true; LE.structDirty = true;
}
function leStructRow(fn, key, val) {   // struct の行検索
  const t = LE.struct[fn];
  return t && t.rows.find(r => r[t.cols.indexOf(key)] === val);
}
function leEquipArea(eid) {
  const r = leStructRow("equipments.csv", "equip_id", eid);
  return r ? r[LE.struct["equipments.csv"].cols.indexOf("area")] : "";
}
function leSetArea(eid, area) {
  const r = leStructRow("equipments.csv", "equip_id", eid);
  if (r) r[LE.struct["equipments.csv"].cols.indexOf("area")] = area;
  LE.nodes["equip/" + eid].area = area;
}
function leAreaAt(x, y) {          // 点を含むエリアID
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type === "area" &&
        x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h)
      return n.node_id;
  }
  return null;
}
function leCheckAreaMove() {        // ドロップ位置で設備の所属を更新
  const ops = [];
  for (const k of LE.order) {
    const n = LE.nodes[k];
    if (n.node_type !== "equip") continue;
    const a = leAreaAt(n.x + n.w / 2, n.y + n.h / 2);
    const cur = leEquipArea(n.node_id);
    if (a && a !== cur) {
      leStructOp(() => leSetArea(n.node_id, a));
      leToast(`${n.node_id}: ${cur} → ${a} に所属変更`);
    }
  }
}
function leToast(msg) {
  const s = $("status");
  if (s) s.textContent = msg;
}
function leAddEquip() {
  if (LE.readonly) return;
  const tgs = LE.struct["tool_groups.csv"] ?
    LE.struct["tool_groups.csv"].rows.map(r => r[0]) : [];
  const areas = LE.struct["areas.csv"].rows.map(r => r[0]);
  const id = prompt("equip_id(必須):", "");
  if (!id) return;
  if (!/^[A-Za-z0-9_-]+$/.test(id)) return alert("形式が不正です");
  if (LE.nodes["equip/" + id] || leStructRow("equipments.csv", "equip_id", id))
    return alert("重複しています");
  const tg = prompt("tool_group(" + tgs.join("/") + "):", tgs[0] || "");
  if (!tgs.includes(tg)) return alert("tool_group が不正です");
  const area = prompt("area(" + areas.join("/") + "):", areas[0] || "");
  if (!areas.includes(area)) return alert("area が不正です");
  const eqc = LE.struct["equipments.csv"].cols;
  const peer = LE.struct["equipments.csv"].rows.find(r =>
    r[eqc.indexOf("tool_group")] === tg && r[eqc.indexOf("area")] === area);
  const agv = peer ? peer[eqc.indexOf("agv_time")] : "1";
  leStructOp(() => {
    const row = eqc.map(c => "");
    row[eqc.indexOf("equip_id")] = id;
    row[eqc.indexOf("tool_group")] = tg;
    row[eqc.indexOf("area")] = area;
    row[eqc.indexOf("agv_time")] = agv;
    for (const c of ["capacity", "mtbf", "mttr", "pm_interval",
                     "pm_duration", "initial_recipe"]) {
      const i = eqc.indexOf(c);
      if (i >= 0) row[i] = peer ? peer[i] : (c === "capacity" ? "1" : "0");
    }
    LE.struct["equipments.csv"].rows.push(row);
    const A = LE.nodes["area/" + area];
    let px = A.x + 10, py = A.y + 20;
    outer: for (let yy = A.y + 10; yy + 40 < A.y + A.h; yy += 45)
      for (let xx = A.x + 10; xx + 50 < A.x + A.w; xx += 50) {
        const clash = LE.order.some(k => {
          const n = LE.nodes[k];
          return n.node_type === "equip" &&
            xx < n.x + n.w && n.x < xx + 40 && yy < n.y + n.h && n.y < yy + 30;
        });
        if (!clash) { px = xx; py = yy; break outer; }
      }
    const k = "equip/" + id;
    LE.nodes[k] = { node_type: "equip", node_id: id, x: px, y: py,
                    w: 40, h: 30, tool_group: tg, area };
    LE.order.push(k);
  });
  leToast("設備 " + id + " を追加しました");
}
function leAddArea() {
  if (LE.readonly) return;
  const ac = LE.struct["areas.csv"].cols;
  const id = prompt("area id(必須):", "");
  if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) return alert("形式が不正です");
  if (LE.nodes["area/" + id] || leStructRow("areas.csv", "area", id))
    return alert("重複しています");
  const sid = prompt("stocker_id:", "S" +
    (LE.struct["stockers.csv"].rows.length + 1));
  if (!sid || LE.nodes["stocker/" + sid] ||
      leStructRow("stockers.csv", "stocker_id", sid))
    return alert("stocker_id が不正/重複です");
  const cl = LE.struct["cleanliner.csv"].cols;
  const after = prompt("CL駅の挿入位置(何番目の後か、0=先頭):",
                       String(LE.struct["cleanliner.csv"].rows.length));
  const ttn = prompt("time_to_next:", "15");
  const cap = Math.max(...LE.struct["stockers.csv"].rows.map(r =>
    +r[LE.struct["stockers.csv"].cols.indexOf("capacity")] || 0), 60);
  leStructOp(() => {
    const ar = ac.map(c => "");
    ar[ac.indexOf("area")] = id;
    ar[ac.indexOf("agv_count")] = "1";
    LE.struct["areas.csv"].rows.push(ar);
    const sc = LE.struct["stockers.csv"].cols;
    const proto = LE.struct["stockers.csv"].rows[0];
    const sr = sc.map((c, i) => proto ? proto[i] : "");
    sr[sc.indexOf("stocker_id")] = sid;
    sr[sc.indexOf("area")] = id;
    sr[sc.indexOf("capacity")] = String(cap);
    LE.struct["stockers.csv"].rows.push(sr);
    const seq_i = +after || 0;
    const cr = cl.map(c => "");
    cr[cl.indexOf("stocker_id")] = sid;
    cr[cl.indexOf("time_to_next")] = ttn;
    LE.struct["cleanliner.csv"].rows.splice(seq_i, 0, cr);
    LE.struct["cleanliner.csv"].rows.forEach((r, i) =>
      r[cl.indexOf("seq")] = String(i + 1));
    LE.clStations = LE.struct["cleanliner.csv"].rows.map((r, i) => ({
      seq: i + 1, stocker_id: r[cl.indexOf("stocker_id")],
      time_to_next: +r[cl.indexOf("time_to_next")] }));
    // world 空き位置に配置(既存エリアの右端のさらに右 or 下)
    let ax = 20, ay = 20;
    for (const k of LE.order) {
      const n = LE.nodes[k];
      if (n.node_type === "area") ax = Math.max(ax, n.x + n.w + 40);
    }
    const aw = Math.min(160, LE.world.w - ax - 10);
    const ah = 160;
    if (ax + aw > LE.world.w) { ax = 20; ay = Math.min(LE.world.h - ah - 10, 200); }
    LE.nodes["area/" + id] = { node_type: "area", node_id: id, x: ax,
                               y: ay, w: aw, h: ah };
    LE.order.push("area/" + id);
    LE.nodes["stocker/" + sid] = { node_type: "stocker", node_id: sid,
      x: ax + 10, y: ay + ah - 50, w: 60, h: 40, area: id };
    LE.order.push("stocker/" + sid);
  });
  leToast("エリア " + id + " + ストッカー " + sid + " を追加しました");
}function leDeleteSel() {
  if (LE.readonly || !LE.sel.size) return;
  const ks = [...LE.sel];
  for (const a of ks.filter(k => LE.nodes[k].node_type === "area")) {
    if (LE.struct["areas.csv"].rows.length <= 1)
      return alert("最後の1エリアは削除できません");
    const aid = LE.nodes[a].node_id;
    const has = LE.struct["equipments.csv"].rows.some(r =>
      r[LE.struct["equipments.csv"].cols.indexOf("area")] === aid);
    if (has) return alert(
      aid + " に設備があります。先に設備を移動/削除してください");
  }
  if (!confirm(ks.map(k => LE.nodes[k].node_id).join(", ") +
               " を削除しますか")) return;
  leStructOp(() => {
    for (const k of ks) {
      const n = LE.nodes[k];
      if (n.node_type === "equip") {
        const t = LE.struct["equipments.csv"];
        t.rows = t.rows.filter(r =>
          r[t.cols.indexOf("equip_id")] !== n.node_id);
        const q = LE.struct["qualifications.csv"];
        if (q) q.rows = q.rows.filter(r =>
          r[q.cols.indexOf("equip_id")] !== n.node_id);
      } else if (n.node_type === "stocker") {
        const t = LE.struct["stockers.csv"];
        t.rows = t.rows.filter(r =>
          r[t.cols.indexOf("stocker_id")] !== n.node_id);
        leRemoveClStation(n.node_id);
      } else if (n.node_type === "area") {
        const t = LE.struct["areas.csv"];
        t.rows = t.rows.filter(r =>
          r[t.cols.indexOf("area")] !== n.node_id);
      }
      delete LE.nodes[k];
      LE.order = LE.order.filter(x => x !== k);
      LE.sel.delete(k);
    }
    for (const pk of LE.order.filter(k => {
      const p = LE.nodes[k];
      if (!p.node_type.endsWith("_point")) return false;
      const ref = String(p.node_id).split(":")[0];
      return !LE.nodes["stocker/" + ref] && !LE.nodes["equip/" + ref];
    })) { delete LE.nodes[pk]; LE.order = LE.order.filter(x => x !== pk); }
  });
}
function leRemoveClStation(sid) {
  const t = LE.struct["cleanliner.csv"], ci = t.cols.indexOf("stocker_id");
  const ti = t.cols.indexOf("time_to_next");
  const idx = t.rows.findIndex(r => r[ci] === sid);
  if (idx < 0) return;
  const removed = +t.rows[idx][ti] || 0;
  t.rows.splice(idx, 1);
  if (t.rows.length) {
    const prev = t.rows[(idx - 1 + t.rows.length) % t.rows.length];
    prev[ti] = String((+prev[ti] || 0) + removed);
  }
  t.rows.forEach((r, i) => r[t.cols.indexOf("seq")] = String(i + 1));
  LE.clStations = t.rows.map((r, i) => ({
    seq: i + 1, stocker_id: r[ci], time_to_next: +r[ti] }));
  lePruneClPoints();
}
function leClNext(sid) {
  const ids = LE.struct["cleanliner.csv"].rows.map(r =>
    r[LE.struct["cleanliner.csv"].cols.indexOf("stocker_id")]);
  const i = ids.indexOf(sid);
  return i < 0 ? null : ids[(i + 1) % ids.length];
}
function lePruneClPoints() {
  for (const k of LE.order.filter(k =>
      LE.nodes[k].node_type === "cl_point")) {
    const sid = LE.nodes[k].node_id.split(":")[0];
    if (!leClNext(sid)) {
      delete LE.nodes[k]; LE.order = LE.order.filter(x => x !== k);
    }
  }
}
function leClStationMove(i, d) {
  const t = LE.struct["cleanliner.csv"], j = i + d;
  if (j < 0 || j >= t.rows.length) return;
  leStructOp(() => {
    [t.rows[i], t.rows[j]] = [t.rows[j], t.rows[i]];
    t.rows.forEach((r, k) => r[t.cols.indexOf("seq")] = String(k + 1));
    LE.clStations = t.rows.map((r, k) => ({
      seq: k + 1, stocker_id: r[t.cols.indexOf("stocker_id")],
      time_to_next: +r[t.cols.indexOf("time_to_next")] }));
  });
}
function leClSetTtn(i, v) {
  const t = LE.struct["cleanliner.csv"];
  leStructOp(() => {
    t.rows[i][t.cols.indexOf("time_to_next")] = v;
    LE.clStations[i].time_to_next = +v;
  });
}
async function leValidateStruct() {
  const r = await leApi("model_validate",
    { model: LE.model, structure: LE.struct }, true);
  const msgs = [...(r.errors || r.detail || []),
                ...(r.warnings || []).map(w => "⚠ " + w)];
  alert(msgs.length ? msgs.join("\n") : "検証 OK");
  return r.ok;
}
