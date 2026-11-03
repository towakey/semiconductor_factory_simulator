"""レイアウト座標 (設計書 11.3)。layout.csv 読込 + 自動配置。"""
import csv
import math
import os

WORLD_W = 1000.0
WORLD_H = 600.0
EQUIP_W, EQUIP_H = 40.0, 30.0
STOCKER_W, STOCKER_H = 60.0, 40.0
_MARGIN = 12.0
_GAP = 24.0
_PAD = 12.0
_HEADER = 22.0


def auto_layout(model):
    """全ノードの矩形を自動配置して返す。
    戻り値: (nodes, world)。
    nodes: {(node_type, node_id): {"x","y","w","h"}}、world: {"w","h"}。"""
    areas = sorted(model.areas.values(), key=lambda a: a.name)
    eq_cell_w, eq_cell_h = EQUIP_W + 8.0, EQUIP_H + 8.0
    rects = {}          # area -> (w, h, [(equip, relx, rely)], stocker_rel)
    nat_w = _MARGIN * 2
    nat_h = 0.0
    for a in areas:
        equips = sorted([e for e in model.equipments.values()
                         if e.area == a.name], key=lambda e: e.equip_id)
        st = model.stocker_of_area(a.name)
        cols = max(1, int(math.ceil(math.sqrt(max(1, len(equips))))))
        rows = int(math.ceil(len(equips) / cols)) if equips else 0
        inner_w = max(cols * eq_cell_w - 8.0, STOCKER_W)
        aw = inner_w + _PAD * 2
        ah = (_HEADER + rows * eq_cell_h + _PAD +
              (STOCKER_H + _PAD if st else 0.0))
        cells = []
        for i, e in enumerate(equips):
            r, c = divmod(i, cols)
            cells.append((e.equip_id,
                          _PAD + c * eq_cell_w, _HEADER + r * eq_cell_h))
        rects[a.name] = (aw, ah, cells,
                         (_PAD, ah - _PAD - STOCKER_H) if st else None)
        nat_w += aw + _GAP
        nat_h = max(nat_h, ah + _MARGIN * 2)
    nat_w -= _GAP

    world = {"w": WORLD_W, "h": WORLD_H}
    scale = min(1.0, WORLD_W / nat_w, WORLD_H / nat_h) if nat_w > 0 else 1.0

    nodes = {}
    x = _MARGIN + max(0.0, (WORLD_W - nat_w * scale) / 2.0)
    for a in areas:
        aw, ah, cells, st_rel = rects[a.name]
        nodes[("area", a.name)] = {"x": x, "y": _MARGIN,
                                   "w": aw * scale, "h": ah * scale}
        for eid, rx, ry in cells:
            nodes[("equip", eid)] = {"x": x + rx * scale,
                                     "y": _MARGIN + ry * scale,
                                     "w": EQUIP_W * scale,
                                     "h": EQUIP_H * scale}
        if st_rel:
            st = model.stocker_of_area(a.name)
            nodes[("stocker", st.stocker_id)] = {
                "x": x + st_rel[0] * scale,
                "y": _MARGIN + st_rel[1] * scale,
                "w": STOCKER_W * scale, "h": STOCKER_H * scale}
        x += aw * scale + _GAP * scale
    return nodes, world


def read_layout_csv(path):
    """layout.csv を読む。戻り値 (rows, errors)。rows: (line, dict)。"""
    rows, errors = [], []
    if not os.path.exists(path):
        return rows, errors
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.readlines()
    header = None
    for i, line in enumerate(raw, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cells = next(csv.reader([line]))
        if header is None:
            header = [c.strip() for c in cells]
            continue
        row = {"__line__": i}
        for j, name in enumerate(header):
            row[name] = cells[j].strip() if j < len(cells) else ""
        rows.append((i, row))
    return rows, errors


def load_layout(model_dir):
    """モデルディレクトリを読み、全ノード座標と警告を返す。
    戻り値: {"nodes": {(type,id): rect}, "world": {"w","h"},
             "warnings": [str]}"""
    from .config import load_model   # 循環回避のため遅延 import
    model, errors, warnings = load_model(model_dir)
    warnings = list(warnings)
    if errors:
        return {"nodes": {}, "world": {"w": WORLD_W, "h": WORLD_H},
                "warnings": warnings + ["設定エラーのため自動配置のみ"],
                "errors": errors, "model": model}
    return load_layout_for_model(model, model_dir, warnings)


def load_layout_for_model(model, model_dir, warnings=None):
    warnings = list(warnings or [])
    nodes, world = auto_layout(model)
    points = {}
    known = {("area", a) for a in model.areas}
    known |= {("equip", e) for e in model.equipments}
    known |= {("stocker", s) for s in model.stockers}
    rows, _ = read_layout_csv(os.path.join(model_dir, "layout.csv"))
    seen = set()
    for lineno, row in rows:
        nt, nid = row.get("node_type", ""), row.get("node_id", "")
        try:
            x, y = float(row.get("x", "")), float(row.get("y", ""))
        except ValueError:
            warnings.append("layout.csv:%s: 座標が数値ではありません(%s,%s)"
                            % (lineno, nt, nid))
            continue
        w = row.get("w", "")
        h = row.get("h", "")
        try:
            w = float(w) if w != "" else None
            h = float(h) if h != "" else None
        except ValueError:
            warnings.append("layout.csv:%s: サイズが数値ではありません"
                            % lineno)
            continue
        if nt == "world":
            if w and h:
                world = {"w": w, "h": h}
            continue
        if nt in ("cl_point", "agv_point"):
            ref, _, kk = nid.rpartition(":")
            valid = (ref in model.stockers if nt == "cl_point"
                     else ref in model.equipments)
            if not valid or not kk.isdigit():
                warnings.append(
                    "layout.csv:%s: 参照先の無い折れ点 %s/%s は無視"
                    % (lineno, nt, nid))
                continue
            points.setdefault(nt, {}).setdefault(ref, []).append(
                {"k": int(kk), "x": x, "y": y})
            continue
        key = (nt, nid)
        if key not in known:
            warnings.append("layout.csv:%s: モデルに無いノード %s/%s は無視"
                            % (lineno, nt, nid))
            continue
        seen.add(key)
        base = nodes[key]
        nodes[key] = {"x": x, "y": y,
                      "w": w if w is not None else base["w"],
                      "h": h if h is not None else base["h"]}
    for key in known - seen:
        if rows:
            warnings.append("layout.csv: ノード %s/%s は自動配置で補完"
                            % (key[0], key[1]))
    for nt in points:
        for ref in points[nt]:
            points[nt][ref].sort(key=lambda p: p["k"])
    return {"nodes": nodes, "world": world, "warnings": warnings,
            "errors": [], "model": model, "points": points}
