/* モデル設定編集 (設計書 12.6)。素の JS。 */
"use strict";
const API = "cgi-bin/api.py";
const TABS = [
  ["全体", ["sim.csv"]],
  ["搬送", ["areas.csv", "stockers.csv", "cleanliner.csv"]],
  ["設備", ["equipments.csv", "tool_groups.csv", "qualifications.csv"]],
  ["レシピ", ["recipes.csv", "setups.csv"]],
  ["製品・工程", ["products.csv", "routes.csv"]],
  ["投入", ["release.csv"]],
];
const ME = {
  cid: "", files: {}, spec: {}, simSpec: {}, rev: "none",
  model: "", readonly: false, dirty: false, errs: [],
  undo: [], redo: [], tab: 0, sub: 0, routeFilter: "",
};
// client_id はタブ単位で保持(リロードで「他者ロック」化しないため)
ME.cid = sessionStorage.fabsim_client_id || "";
if (!/^[0-9a-f]{16,32}$/.test(ME.cid)) {
  for (let i = 0; i < 16; i++)
    ME.cid += "0123456789abcdef"[Math.floor(Math.random() * 16)];
  sessionStorage.fabsim_client_id = ME.cid;
}
const $m = id => document.getElementById(id);

function mApi(a, p, post) {
  let url = API + "?action=" + a, o = { headers: {} };
  if (post) {
    o.method = "POST";
    o.headers["X-Requested-With"] = "fabsim";
    o.headers["Content-Type"] = "application/json";
    o.body = JSON.stringify(p);
  } else if (p) url += "&" + new URLSearchParams(p);
  return fetch(url, o).then(r => r.json());
}
const specOf = fn => ME.spec[fn] || [];

// ---------- 読込 ----------
async function meLoadModels() {
  const r = await mApi("models");
  const q = new URLSearchParams(location.search).get("model");
  $m("meModel").innerHTML = r.models.map(m =>
    `<option ${m === q ? "selected" : ""}>${m}</option>`).join("");
}
async function meLoad() {
  ME.model = $m("meModel").value;
  const r = await mApi("model_files",
                       { model: ME.model, client_id: ME.cid });
  if (!r.ok) { alert("model_files: " + r.error); return; }
  ME.files = r.files; ME.spec = r.spec; ME.simSpec = r.sim_spec;
  ME.rev = r.revision;
  ME.readonly = !r.can_edit || (r.lock.locked && !r.lock.mine);
  if (r.can_edit && !r.lock.mine) {
    const lk = await mApi("model_lock",
      { model: ME.model, client_id: ME.cid }, true);
    if (!lk.ok) {
      ME.readonly = true;
      alert("他のユーザーが編集中です: " + (lk.owner || "?") + (lk.remaining ? "(残り " + lk.remaining + " 秒)" : ""));
    }
  }
  ME.dirty = false; ME.undo = []; ME.redo = []; ME.errs = [];
  meStatus();
  meRenderTabs();
}
function meStatus() {
  $m("meStatus").textContent =
    (ME.readonly ? "読み取り専用 " : "") +
    (ME.dirty ? "未保存変更あり" : "保存済み");
}
$m("meModel").onchange = () => { if (meConfirmDirty()) meLoad(); };
function meConfirmDirty() {
  return !ME.dirty || confirm("未保存の変更を破棄しますか?");
}

// ---------- タブ・テーブル描画 ----------
function meRenderTabs() {
  $m("meTabs").innerHTML = TABS.map(([name], i) =>
    `<button class="${i === ME.tab ? "on" : ""}" data-i="${i}">${name}</button>`
  ).join("");
  $m("meTabs").querySelectorAll("button").forEach(b =>
    b.onclick = () => { ME.tab = +b.dataset.i; ME.sub = 0; meRenderTabs(); });
  const files = TABS[ME.tab][1];
  ME.sub = Math.min(ME.sub, files.length - 1);
  $m("meSubTabs").innerHTML = files.map((f, i) =>
    `<button class="${i === ME.sub ? "on" : ""}" data-i="${i}">${f}</button>`
  ).join("") + meExtraButtons(files[ME.sub]);
  $m("meSubTabs").querySelectorAll("button[data-i]").forEach(b =>
    b.onclick = () => { ME.sub = +b.dataset.i; meRenderTabs(); });
  meRenderTable(files[ME.sub]);
  meRenderErrs();
}
function meExtraButtons(fn) {
  let s = `<button id="meAddRow">+行追加</button>`;
  if (fn === "routes.csv")
    s += ` <label>製品 <select id="meRouteFilter"></select></label>` +
         `<button id="meSeqFix">seqを10刻みで振り直し</button>`;
  return s;
}

function refOptions(ref) {
  const [fn, col] = ref.split(":");
  const t = ME.files[fn];
  if (!t) return [];
  const idx = t.cols.indexOf(col);
  const set = new Set();
  for (const r of t.rows) if (r[idx] !== "") set.add(r[idx]);
  return [...set];
}
function simOpts(key) {
  const s = ME.simSpec[key];
  return s && s[0] === "enum" ? s[1] : null;
}
function simRef(key) {
  const s = ME.simSpec[key];
  return s && s[0] && s[0].startsWith("ref:") ? s[0].slice(4) : null;
}

function cellHtml(fn, ri, ci, col, val, spec) {
  const dis = ME.readonly ? "disabled" : "";
  const errCls = meHasErr(fn, ri, col) ? "err" : "";
  let inner;
  if (spec && spec[2] === "enum") {
    const opts = spec[3] || [];
    inner = `<select ${dis}>${["", ...opts].map(o =>
      `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}
      </select>`;
  } else if (spec && spec[2] === "ref") {
    let opts = refOptions(spec[3]);
    if (val && !opts.includes(val)) opts = [val, ...opts];
    inner = `<select ${dis}><option value=""></option>${opts.map(o =>
      `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}
      </select>`;
  } else if (spec && (spec[2] === "int" || spec[2] === "float")) {
    inner = `<input type="number" step="any" value="${esc(val)}" ${dis}>`;
  } else {
    inner = `<input type="text" value="${esc(val)}" ${dis}>`;
  }
  return `<td class="${errCls}" data-r="${ri}" data-c="${col}"
          title="${esc(spec ? spec[5] : "")}">${inner}</td>`;
}
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
function meHasErr(fn, ri, col) {
  return ME.errs.some(e => e.f === fn && e.r === ri &&
                       (e.c === col || e.c === "-"));
}

function meRenderTable(fn) {
  const t = ME.files[fn], spec = specOf(fn);
  let html = `<h3>${fn}</h3><table><tr>`;
  html += spec.map((c, i) =>
    `<th title="${esc(c[5])}">${c[0]}${c[1] ? "*" : ""}</th>`).join("");
  html += `<th></th></tr>`;
  const rows = fn === "routes.csv" && ME.routeFilter
    ? t.rows.map((r, i) => [r, i]).filter(([r]) =>
        r[t.cols.indexOf("product")] === ME.routeFilter)
    : t.rows.map((r, i) => [r, i]);
  for (const [row, ri] of rows) {
    html += `<tr>`;
    for (let ci = 0; ci < t.cols.length; ci++) {
      const col = t.cols[ci];
      let spec2 = spec.find(c => c[0] === col);
      if (fn === "sim.csv" && col === "value" && !spec2) spec2 = null;
      if (fn === "sim.csv" && col === "value") {
        const key = row[t.cols.indexOf("key")];
        const so = ME.simSpec[key];
        if (so && so[0] === "enum")
          spec2 = [col, 0, "enum", so[1], "", so[2]];
        else if (so && so[0] && so[0].startsWith("ref:"))
          spec2 = [col, 0, "ref", so[0].slice(4), "", so[2]];
        else if (so && (so[0] === "int" || so[0] === "float"))
          spec2 = [col, 0, so[0], null, "", so[2]];
      }
      html += cellHtml(fn, ri, ci, col, row[ci], spec2);
    }
    html += `<td class="rowbtns">` +
      `<button data-a="dup" data-r="${ri}" title="複製">⧉</button>` +
      `<button data-a="del" data-r="${ri}" title="削除">✕</button>` +
      (fn === "routes.csv" ?
       `<button data-a="up" data-r="${ri}" title="上へ">↑</button>` +
       `<button data-a="dn" data-r="${ri}" title="下へ">↓</button>` : "") +
      `</td></tr>`;
  }
  html += `</table>`;
  if (fn === "sim.csv")
    html += `<div id="meSimHint">` + Object.entries(ME.simSpec)
      .map(([k, v]) => `${k}: ${v[2]}`).join("　") + `</div>`;
  $m("meTable").innerHTML = html;
  if (fn === "routes.csv") {
    const pf = $m("meRouteFilter");
    if (pf) {
      const prods = refOptions("products.csv:product");
      pf.innerHTML = `<option value="">(すべて)</option>` +
        prods.map(p => `<option ${p === ME.routeFilter ? "selected" : ""}>
          ${p}</option>`).join("");
      pf.onchange = () => { ME.routeFilter = pf.value; meRenderTabs(); };
    }
    const sf = $m("meSeqFix");
    if (sf) sf.onclick = meSeqFix;
  }
  const ar = $m("meAddRow");
  if (ar) ar.onclick = () => meRowOp(fn, "add", -1);
  $m("meTable").querySelectorAll("input,select").forEach(el => {
    el.onchange = e => {
      const td = e.target.closest("td");
      meCellEdit(fn, +td.dataset.r, td.dataset.c, e.target.value);
    };
  });
  $m("meTable").querySelectorAll("button[data-a]").forEach(b =>
    b.onclick = () => meRowOp(fn, b.dataset.a, +b.dataset.r));
}

// ---------- 編集操作(Undo 対応・スナップショット) ----------
function meSnap() {
  ME.undo.push(JSON.stringify(ME.files));
  if (ME.undo.length > 100) ME.undo.shift();
  ME.redo = [];
}
function meUndo() {
  const s = ME.undo.pop();
  if (!s) return;
  ME.redo.push(JSON.stringify(ME.files));
  ME.files = JSON.parse(s);
  ME.dirty = true; meStatus(); meRenderTabs();
}
function meRedo() {
  const s = ME.redo.pop();
  if (!s) return;
  ME.undo.push(JSON.stringify(ME.files));
  ME.files = JSON.parse(s);
  ME.dirty = true; meStatus(); meRenderTabs();
}
function meCellEdit(fn, ri, col, val) {
  const t = ME.files[fn], ci = t.cols.indexOf(col);
  if (t.rows[ri][ci] === val) return;
  meSnap();
  t.rows[ri][ci] = val;
  ME.dirty = true; meStatus();
}
function meRowOp(fn, op, ri) {
  if (ME.readonly) return;
  meSnap();
  const t = ME.files[fn];
  if (op === "add") {
    t.rows.push(specOf(fn).map(c => c[4] == null ? "" : String(c[4])));
  } else if (op === "dup") {
    t.rows.splice(ri + 1, 0, [...t.rows[ri]]);
  } else if (op === "del") {
    const warn = meRefWarn(fn, t.rows[ri]);
    t.rows.splice(ri, 1);
    if (warn) alert("警告: " + warn);
  } else if (op === "up" && ri > 0) {
    [t.rows[ri - 1], t.rows[ri]] = [t.rows[ri], t.rows[ri - 1]];
  } else if (op === "dn" && ri < t.rows.length - 1) {
    [t.rows[ri], t.rows[ri + 1]] = [t.rows[ri + 1], t.rows[ri]];
  } else { ME.undo.pop(); return; }
  ME.dirty = true; meStatus(); meRenderTabs();
}
function meRefWarn(fn, row) {
  // 削除する行の主キー(先頭列)を参照している他表の行を警告
  const key = row[0];
  const refs = [];
  for (const [fn2, spec] of Object.entries(ME.spec)) {
    for (const c of spec)
      if (c[2] === "ref" && c[3] === fn + ":" + specOf(fn)[0][0]) {
        const t = ME.files[fn2], ci = t.cols.indexOf(c[0]);
        const n = t.rows.filter(r => r[ci] === key).length;
        if (n) refs.push(`${fn2}(${c[0]}): ${n}行`);
      }
  }
  return refs.length ? `${key} を参照する行があります: ${refs.join(", ")}`
                     : "";
}
function meSeqFix() {
  meSnap();
  const t = ME.files["routes.csv"];
  const pi = t.cols.indexOf("product"), si = t.cols.indexOf("seq");
  const groups = {};
  for (const r of t.rows) (groups[r[pi]] = groups[r[pi]] || []).push(r);
  for (const g of Object.values(groups))
    g.forEach((r, i) => { r[si] = String((i + 1) * 10); });
  ME.dirty = true; meStatus(); meRenderTabs();
}

// ---------- 検証・保存 ----------
function meErrLine(s) {
  const m = s.match(/^([^:]+\.csv):(\d+|-):?\s*(?:列\[([^\]]+)\])?/);
  if (!m) return { f: "", r: -1, c: "-", s };
  return { f: m[1], r: m[2] === "-" ? -1 : +m[2] - 2, c: m[3] || "-", s };
}
function meRenderErrs() {
  $m("meErrs").innerHTML = ME.errs.map(e =>
    `<div class="${e.warn ? "w" : ""}" data-f="${e.f}">${e.warn ? "⚠ " : ""}${esc(e.s)}</div>`
  ).join("") || "<div>なし</div>";
  $m("meErrs").querySelectorAll("div[data-f]").forEach(d =>
    d.onclick = () => meJump(d.dataset.f, ME.errs[
      [...$m("meErrs").querySelectorAll("div")].indexOf(d)]));
}
function meJump(f, e) {
  const ti = TABS.findIndex(t => t[1].includes(f));
  if (ti >= 0) { ME.tab = ti; ME.sub = TABS[ti][1].indexOf(f); meRenderTabs(); }
  if (e.r >= 0) {
    const td = $m("meTable").querySelector(`td[data-r="${e.r}"]`);
    if (td) { td.scrollIntoView({ block: "center" });
      const el = td.querySelector("input,select");
      if (el) el.focus(); }
  }
}
async function meValidate() {
  $m("meVStatus").textContent = "検証中…";
  const r = await mApi("model_validate",
                       { model: ME.model, files: ME.files }, true);
  if (!r.ok && r.error && r.error !== "invalid" &&
      r.error !== "validation") {
    $m("meVStatus").textContent = "失敗: " + r.error; return false;
  }
  ME.errs = [...(r.errors || r.detail || []).map(s => meErrLine(s)),
             ...(r.warnings || []).map(s =>
               Object.assign(meErrLine(s), { warn: true }))];
  $m("meVStatus").textContent = r.ok ? "OK(警告のみ)" : "エラーあり";
  meRenderTabs();
  return r.ok;
}
async function meSave() {
  if (ME.readonly) return;
  if (!await meValidate()) { alert("検証エラーがあります。修正してください。"); return; }
  const r = await mApi("model_save", {
    model: ME.model, client_id: ME.cid,
    base_revision: ME.rev, files: ME.files }, true);
  if (!r.ok) {
    if (r.error === "revision")
      alert("他の人が保存しました。再読込してください。");
    else if (r.error === "validation" || r.error === "invalid") {
      ME.errs = (r.detail || []).map(s => meErrLine(s));
      meRenderTabs();
      alert("保存できません(検証エラー)");
    } else alert("保存失敗: " + r.error);
    return;
  }
  ME.rev = r.revision; ME.dirty = false; meStatus();
  alert("保存しました (rev " + r.revision + ")");
}
$m("meSave").onclick = meSave;
$m("meValidate").onclick = meValidate;
$m("meUndo").onclick = meUndo;
$m("meRedo").onclick = meRedo;
$m("meDiscard").onclick = () => { if (meConfirmDirty()) meLoad(); };
$m("meClone").onclick = async () => {
  const name = prompt("複製先のモデル名(英数字と_-)", ME.model + "_copy");
  if (!name) return;
  const r = await mApi("model_clone",
    { model: ME.model, new_name: name }, true);
  if (!r.ok && !r.errors) { alert("複製失敗: " + r.error); return; }
  if (r.errors && r.errors.length)
    alert("複製しましたが検証エラーがあります:\n" + r.errors.join("\n"));
  else alert("複製しました: " + name);
  await meLoadModels();
  $m("meModel").value = name;
};
window.addEventListener("beforeunload", e => {
  if (ME.dirty) { e.preventDefault(); e.returnValue = ""; }
  if (ME.model && !ME.readonly)
    fetch(API, { method: "POST", keepalive: true,
                 headers: { "X-Requested-With": "fabsim",
                            "Content-Type": "application/json" },
                 body: JSON.stringify({ action: "model_unlock",
                   model: ME.model, client_id: ME.cid }) }).catch(() => {});
});
window.addEventListener("keydown", e => {
  if (e.ctrlKey && e.key === "s") { e.preventDefault(); meSave(); }
  if (e.ctrlKey && e.key === "z") { e.preventDefault(); meUndo(); }
  if (e.ctrlKey && e.key === "y") { e.preventDefault(); meRedo(); }
});
setInterval(() => {
  if (ME.model && !ME.readonly)
    mApi("model_lock", { model: ME.model, client_id: ME.cid }, true);
}, 60000);

(async () => { await meLoadModels(); await meLoad(); })();
