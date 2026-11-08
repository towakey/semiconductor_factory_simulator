"""CSV 設定ディレクトリの読込・検証 (設計書 6.1)。"""
import csv
import os

from .models import (Area, Equipment, Model, Product, Recipe, ReleaseRow,
                     SimParams, Step, StockerCfg, ToolGroup)

RULES = ("FIFO", "PRIORITY", "EDD", "SPT", "MIN_SETUP", "CR")
TIME_UNITS = ("lot", "wafer")
DISTS = ("fixed", "triangular", "normal")
RELEASE_MODES = ("fixed", "poisson", "schedule")
RELEASE_CONTROLS = ("fixed", "conwip")
AREA_SELECTS = ("LEAST_LOADED", "NEAREST")
FAILURE_POLICIES = ("resume", "restart")
SHAPES = ("rect", "round", "circle", "diamond", "hexagon", "triangle",
          "octagon", "parallelogram")
SHAPE_LABELS = {"rect": "四角", "round": "角丸四角", "circle": "円",
                "diamond": "ひし形", "hexagon": "六角形",
                "triangle": "三角形", "octagon": "八角形",
                "parallelogram": "平行四辺形"}


def resolve_shapes(tg_rows):
    """(tool_group, shape) から {tg: 形状}。空は行順で循環割当。"""
    out = {}
    for i, (tg, shape) in enumerate(tg_rows):
        out[tg] = shape if shape else SHAPES[i % len(SHAPES)]
    return out

# file -> (required cols, known cols)
SCHEMA = {
    "sim.csv": (["key", "value"], ["key", "value"]),
    "products.csv": (["product"], ["product", "ratio", "priority", "due_factor", "wafers"]),
    "recipes.csv": (["recipe", "tool_group", "process_time"],
                    ["recipe", "tool_group", "process_time", "time_unit", "dist",
                     "dist_param", "batch_min", "batch_max", "batch_wait_max"]),
    "routes.csv": (["product", "seq", "recipe"], ["product", "seq", "recipe", "step_name"]),
    "tool_groups.csv": (["tool_group"],
                        ["tool_group", "dispatch_rule", "shape"]),
    "equipments.csv": (["equip_id", "tool_group", "area", "agv_time"],
                       ["equip_id", "tool_group", "area", "agv_time", "capacity",
                        "ports", "mtbf", "mttr", "pm_interval", "pm_duration",
                        "initial_recipe"]),
    "qualifications.csv": (["equip_id", "recipe"], ["equip_id", "recipe"]),
    "setups.csv": (["tool_group", "from_recipe", "to_recipe", "setup_time"],
                   ["tool_group", "from_recipe", "to_recipe", "setup_time"]),
    "release.csv": (["lot_id", "product", "release_time"],
                    ["lot_id", "product", "release_time", "priority", "due_date"]),
    "areas.csv": (["area", "agv_count"],
                  ["area", "agv_count", "agv_load_time", "agv_unload_time",
                   "agv_return_home"]),
    "stockers.csv": (["stocker_id", "area", "capacity"],
                     ["stocker_id", "area", "capacity", "in_ports", "out_ports", "port_time"]),
    "cleanliner.csv": (["seq", "stocker_id", "time_to_next"],
                       ["seq", "stocker_id", "time_to_next"]),
    "layout.csv": (["node_type", "node_id", "x", "y"],
                   ["node_type", "node_id", "x", "y", "w", "h"]),
}

OPTIONAL_FILES = ("qualifications.csv", "setups.csv", "release.csv", "layout.csv")

# Web 編集対象の CSV(layout.csv はレイアウト編集側が担当)
EDITABLE_FILES = ("sim.csv", "areas.csv", "stockers.csv", "cleanliner.csv",
                  "equipments.csv", "tool_groups.csv", "qualifications.csv",
                  "recipes.csv", "setups.csv", "products.csv", "routes.csv",
                  "release.csv")

# UI/検証で共有する列メタデータ。
# (列名, 必須, 型[str/int/float/enum/ref], 補足[enum候補 or ref先 "file:col"],
#  既定値, ラベル)
CSV_SPECS = {
    "sim.csv": [
        ("key", 1, "str", None, "", "キー"),
        ("value", 1, "str", None, "", "値"),
    ],
    "areas.csv": [
        ("area", 1, "str", None, "", "エリアID"),
        ("agv_count", 1, "int", None, 1, "AGV台数"),
        ("agv_load_time", 0, "float", None, 2, "AGV積込み時間"),
        ("agv_unload_time", 0, "float", None, 2, "AGV荷下ろし時間"),
        ("agv_return_home", 0, "int", None, 1, "AGVストッカー帰還(0/1)"),
    ],
    "stockers.csv": [
        ("stocker_id", 1, "str", None, "", "ストッカーID"),
        ("area", 1, "ref", "areas.csv:area", "", "エリア"),
        ("capacity", 1, "int", None, 60, "容量"),
        ("in_ports", 0, "int", None, 2, "入庫ポート数"),
        ("out_ports", 0, "int", None, 2, "出庫ポート数"),
        ("port_time", 0, "float", None, 3, "ポート処理時間"),
    ],

    "cleanliner.csv": [
        ("seq", 1, "int", None, "", "順序"),
        ("stocker_id", 1, "ref", "stockers.csv:stocker_id", "", "ストッカー"),
        ("time_to_next", 1, "float", None, "", "次駅までの時間"),
    ],
    "tool_groups.csv": [
        ("tool_group", 1, "str", None, "", "グループ名"),
        ("dispatch_rule", 0, "enum", RULES, "", "ルール(空=全体)"),
        ("shape", 0, "enum", SHAPES, "", "描画形状(空=自動)"),
    ],
    "equipments.csv": [
        ("equip_id", 1, "str", None, "", "設備ID"),
        ("tool_group", 1, "ref", "tool_groups.csv:tool_group", "", "グループ"),
        ("area", 1, "ref", "areas.csv:area", "", "エリア"),
        ("agv_time", 1, "float", None, "", "AGV搬送時間"),
        ("capacity", 0, "int", None, 1, "同時処理数"),
        ("ports", 0, "int", None, "", "ポート数(空=capacity)"),
        ("mtbf", 0, "float", None, 0, "MTBF(0=無故障)"),
        ("mttr", 0, "float", None, 0, "MTTR"),
        ("pm_interval", 0, "float", None, 0, "PM間隔(0=無)"),
        ("pm_duration", 0, "float", None, 0, "PM時間"),
        ("initial_recipe", 0, "ref", "recipes.csv:recipe", "", "初期レシピ"),
    ],
    "qualifications.csv": [
        ("equip_id", 1, "ref", "equipments.csv:equip_id", "", "設備ID"),
        ("recipe", 1, "ref", "recipes.csv:recipe", "", "レシピ"),
    ],
    "recipes.csv": [
        ("recipe", 1, "str", None, "", "レシピ名"),
        ("tool_group", 1, "ref", "tool_groups.csv:tool_group", "", "グループ"),
        ("process_time", 1, "float", None, "", "処理時間(分)"),
        ("time_unit", 0, "enum", TIME_UNITS, "lot", "時間単位"),
        ("dist", 0, "enum", DISTS, "fixed", "分布"),
        ("dist_param", 0, "float", None, 0, "分布パラメータ"),
        ("batch_min", 0, "int", None, 1, "バッチ最小"),
        ("batch_max", 0, "int", None, 1, "バッチ最大"),
        ("batch_wait_max", 0, "float", None, 0, "バッチ待ち上限"),
    ],
    "setups.csv": [
        ("tool_group", 1, "ref", "tool_groups.csv:tool_group", "", "グループ"),
        ("from_recipe", 1, "ref", "recipes.csv:recipe", "", "移行前レシピ"),
        ("to_recipe", 1, "ref", "recipes.csv:recipe", "", "移行後レシピ"),
        ("setup_time", 1, "float", None, "", "段取り時間"),
    ],
    "products.csv": [
        ("product", 1, "str", None, "", "製品名"),
        ("ratio", 0, "float", None, 1.0, "投入比率"),
        ("priority", 0, "int", None, 0, "優先度"),
        ("due_factor", 0, "float", None, 4.0, "納期係数"),
        ("wafers", 0, "int", None, 25, "ウェーハ数"),
    ],
    "routes.csv": [
        ("product", 1, "ref", "products.csv:product", "", "製品"),
        ("seq", 1, "int", None, "", "順序"),
        ("recipe", 1, "ref", "recipes.csv:recipe", "", "レシピ"),
        ("step_name", 0, "str", None, "", "工程名"),
    ],
    "release.csv": [
        ("lot_id", 1, "str", None, "", "ロットID"),
        ("product", 1, "ref", "products.csv:product", "", "製品"),
        ("release_time", 1, "float", None, "", "投入時刻"),
        ("priority", 0, "int", None, 0, "優先度"),
        ("due_date", 0, "float", None, 0, "納期"),
    ],
}

# sim.csv の既知キー: (型, enum候補 or None, ラベル)
SIM_SPECS = {
    "duration": ("float", None, "シミュレーション期間(分)"),
    "warmup": ("float", None, "ウォームアップ(分)"),
    "seed": ("int", None, "乱数シード"),
    "dispatch_rule": ("enum", RULES, "ディスパッチルール"),
    "release_mode": ("enum", RELEASE_MODES, "投入方式"),
    "lots_per_day": ("float", None, "1日あたり投入ロット数"),
    "release_control": ("enum", RELEASE_CONTROLS, "投入制御"),
    "wip_limit": ("int", None, "WIP上限(conwip時)"),
    "release_stocker": ("ref:stockers.csv:stocker_id", None, "投入ストッカー"),
    "area_select": ("enum", AREA_SELECTS, "エリア選択"),
    "agv_out_first": ("int", None, "AGV出庫優先(0/1)"),
    "cl_carriers": ("int", None, "CLキャリア数"),
    "cl_slots": ("int", None, "CLスロット数"),
    "cl_load_time": ("float", None, "CL積込み時間"),
    "cl_unload_time": ("float", None, "CL荷下ろし時間"),
    "failure_policy": ("enum", FAILURE_POLICIES, "故障時方針"),
}

SIM_KEYS = {
    "duration": "float", "warmup": "float", "seed": "int",
    "dispatch_rule": "str", "release_mode": "str", "lots_per_day": "float",
    "release_control": "str", "wip_limit": "int", "release_stocker": "str",
    "area_select": "str", "agv_out_first": "int", "cl_carriers": "int",
    "cl_slots": "int", "cl_load_time": "float", "cl_unload_time": "float",
    "failure_policy": "str",
}


class ConfigError(Exception):
    pass


def _err(fname, lineno, col, msg, content=""):
    return "%s:%s: 列[%s] %s (内容:%r)" % (fname, lineno, col, msg, content)


def read_csv(path, fname, warnings):
    """CSV を読む。utf-8-sig、# コメント・空行を除外。
    戻り値: (rows, errors)。rows は (行番号, dict) のリスト。"""
    rows, errors = [], []
    if not os.path.exists(path):
        return rows, errors
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.readlines()
    required, known = SCHEMA.get(fname, ([], []))
    header = None
    header_map = {}
    for i, line in enumerate(raw, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cells = next(csv.reader([line]))
        if header is None:
            header = [c.strip() for c in cells]
            for idx, name in enumerate(header):
                header_map[name] = idx
                if known and name not in known:
                    warnings.append("%s:%s: 未知の列 %r は無視します" % (fname, i, name))
            for col in required:
                if col not in header_map:
                    errors.append(_err(fname, i, col, "必須列がありません", ",".join(header)))
            continue
        row = {"__line__": i}
        for name, idx in header_map.items():
            row[name] = cells[idx].strip() if idx < len(cells) else ""
        rows.append((i, row))
    if header is None:
        errors.append(_err(fname, "-", "-", "ヘッダ行がありません"))
    return rows, errors


def _num(row, col, fname, errors, default=None, integer=False,
         allow_negative=False, positive=False):
    v = row.get(col, "")
    if v == "":
        return default
    try:
        x = int(v) if integer else float(v)
    except ValueError:
        errors.append(_err(fname, row["__line__"], col, "数値ではありません", v))
        return default
    if not allow_negative and x < 0:
        errors.append(_err(fname, row["__line__"], col, "負の値です", v))
    if positive and x <= 0:
        errors.append(_err(fname, row["__line__"], col, "正の値が必要です", v))
    return x


def _dup_check(rows, keys, fname, errors):
    seen = {}
    for lineno, row in rows:
        k = tuple(row.get(c, "") for c in keys)
        if k in seen:
            errors.append(_err(fname, lineno, "/".join(keys),
                               "主キーが重複しています", "/".join(k)))
        seen[k] = lineno


def load_model(config_dir):
    """戻り値 (model, errors, warnings)。errors が 1 件でもあれば不正。"""
    errors, warnings = [], []
    m = Model(params=SimParams(), source_dir=config_dir)

    def rd(fname):
        r, e = read_csv(os.path.join(config_dir, fname), fname, warnings)
        errors.extend(e)
        return r

    required_files = ["sim.csv", "products.csv", "recipes.csv", "routes.csv",
                      "tool_groups.csv", "equipments.csv", "areas.csv",
                      "stockers.csv", "cleanliner.csv"]
    for f in required_files:
        if not os.path.exists(os.path.join(config_dir, f)):
            errors.append("%s: 必須ファイルがありません" % f)

    sim_rows = rd("sim.csv")
    prod_rows = rd("products.csv")
    rec_rows = rd("recipes.csv")
    route_rows = rd("routes.csv")
    tg_rows = rd("tool_groups.csv")
    eq_rows = rd("equipments.csv")
    qual_rows = rd("qualifications.csv")
    setup_rows = rd("setups.csv")
    rel_rows = rd("release.csv")
    area_rows = rd("areas.csv")
    st_rows = rd("stockers.csv")
    cl_rows = rd("cleanliner.csv")

    p = m.params
    for lineno, row in sim_rows:
        key, val = row.get("key", ""), row.get("value", "")
        if key not in SIM_KEYS:
            warnings.append("sim.csv:%s: 未知のキー %r は無視します" % (lineno, key))
            continue
        typ = SIM_KEYS[key]
        if typ == "str":
            setattr(p, key, val)
        else:
            try:
                setattr(p, key, int(val) if typ == "int" else float(val))
            except ValueError:
                errors.append(_err("sim.csv", lineno, "value", "数値ではありません", val))
    _dup_check(sim_rows, ["key"], "sim.csv", errors)

    for lineno, row in prod_rows:
        name = row.get("product", "")
        m.products[name] = Product(
            name=name,
            ratio=_num(row, "ratio", "products.csv", errors, 1.0),
            priority=int(_num(row, "priority", "products.csv", errors, 0) or 0),
            due_factor=_num(row, "due_factor", "products.csv", errors, 4.0, positive=True),
            wafers=int(_num(row, "wafers", "products.csv", errors, 25, integer=True, positive=True) or 25))
    _dup_check(prod_rows, ["product"], "products.csv", errors)

    for lineno, row in rec_rows:
        r = Recipe(
            recipe=row.get("recipe", ""),
            tool_group=row.get("tool_group", ""),
            process_time=_num(row, "process_time", "recipes.csv", errors, 0.0) or 0.0,
            time_unit=row.get("time_unit", "") or "lot",
            dist=row.get("dist", "") or "fixed",
            dist_param=row.get("dist_param", ""),
            batch_min=int(_num(row, "batch_min", "recipes.csv", errors, 1, integer=True) or 1),
            batch_max=int(_num(row, "batch_max", "recipes.csv", errors, 1, integer=True) or 1),
            batch_wait_max=_num(row, "batch_wait_max", "recipes.csv", errors, 0.0) or 0.0)
        m.recipes[r.recipe] = r
        if r.time_unit not in TIME_UNITS:
            errors.append(_err("recipes.csv", lineno, "time_unit", "不正な値", r.time_unit))
        if r.dist not in DISTS:
            errors.append(_err("recipes.csv", lineno, "dist", "不正な値", r.dist))
        if r.batch_min > r.batch_max:
            errors.append(_err("recipes.csv", lineno, "batch_min",
                               "batch_min > batch_max", r.batch_min))
        if r.process_time <= 0:
            errors.append(_err("recipes.csv", lineno, "process_time", "正の値が必要です",
                               r.process_time))
    _dup_check(rec_rows, ["recipe"], "recipes.csv", errors)

    route_keys = {}
    route_lines = {}
    for lineno, row in route_rows:
        prod, seq, rec = row.get("product", ""), row.get("seq", ""), row.get("recipe", "")
        k = (prod, seq)
        if k in route_keys:
            errors.append(_err("routes.csv", lineno, "seq",
                               "product,seq が重複しています", "%s,%s" % k))
        route_keys[k] = lineno
        if prod not in m.products:
            errors.append(_err("routes.csv", lineno, "product", "products.csv に無い機種です", prod))
            continue
        if rec not in m.recipes:
            errors.append(_err("routes.csv", lineno, "recipe", "recipes.csv に無いレシピです", rec))
            continue
        try:
            sq = int(seq)
        except ValueError:
            errors.append(_err("routes.csv", lineno, "seq", "整数ではありません", seq))
            continue
        m.products[prod].route.append(Step(sq, row.get("step_name", ""), m.recipes[rec]))
        route_lines[(prod, sq)] = lineno
    for prod in m.products.values():
        prod.route.sort(key=lambda s: s.seq)

    for lineno, row in tg_rows:
        name = row.get("tool_group", "")
        rule = row.get("dispatch_rule", "")
        if rule and rule not in RULES:
            errors.append(_err("tool_groups.csv", lineno, "dispatch_rule", "不正なルール名", rule))
        shape = row.get("shape", "")
        if shape and shape not in SHAPES:
            errors.append(_err("tool_groups.csv", lineno, "shape",
                               "不正な形状", shape))
            shape = ""
        m.tool_groups[name] = ToolGroup(name=name, dispatch_rule=rule,
                                        shape=shape)
    _dup_check(tg_rows, ["tool_group"], "tool_groups.csv", errors)
    for _n, _s in resolve_shapes(
            [(n, m.tool_groups[n].shape) for n in m.tool_groups]).items():
        m.tool_groups[_n].shape = _s

    for lineno, row in eq_rows:
        e = Equipment(
            equip_id=row.get("equip_id", ""),
            tool_group=row.get("tool_group", ""),
            area=row.get("area", ""),
            agv_time=_num(row, "agv_time", "equipments.csv", errors, 0.0) or 0.0,
            capacity=int(_num(row, "capacity", "equipments.csv", errors, 1, integer=True) or 1),
            mtbf=_num(row, "mtbf", "equipments.csv", errors, 0.0) or 0.0,
            mttr=_num(row, "mttr", "equipments.csv", errors, 0.0) or 0.0,
            pm_interval=_num(row, "pm_interval", "equipments.csv", errors, 0.0) or 0.0,
            pm_duration=_num(row, "pm_duration", "equipments.csv", errors, 0.0) or 0.0,
            initial_recipe=row.get("initial_recipe", ""))
        pv = row.get("ports", "")
        e.ports = int(_num(row, "ports", "equipments.csv", errors,
                           e.capacity, integer=True) or e.capacity) \
            if pv != "" else e.capacity
        if e.ports < e.capacity:
            errors.append(_err("equipments.csv", lineno, "ports",
                               "capacity 以上が必要です", pv))
        elif e.capacity > 1 and e.ports > e.capacity:
            warnings.append(
                "equipments.csv:%d: バッチ設備 %s の ports=%s は "
                "capacity に丸めます" % (lineno, e.equip_id, pv))
            e.ports = e.capacity
        m.equipments[e.equip_id] = e
        if e.tool_group not in m.tool_groups:
            errors.append(_err("equipments.csv", lineno, "tool_group",
                               "tool_groups.csv に無いグループです", e.tool_group))
        else:
            m.tool_groups[e.tool_group].equipments.append(e)
        if e.capacity < 1:
            errors.append(_err("equipments.csv", lineno, "capacity", "1 以上が必要です", e.capacity))
    _dup_check(eq_rows, ["equip_id"], "equipments.csv", errors)

    quals = {}
    for lineno, row in qual_rows:
        eid, rec = row.get("equip_id", ""), row.get("recipe", "")
        if eid not in m.equipments:
            errors.append(_err("qualifications.csv", lineno, "equip_id",
                               "equipments.csv に無い設備です", eid))
            continue
        if rec not in m.recipes:
            errors.append(_err("qualifications.csv", lineno, "recipe",
                               "recipes.csv に無いレシピです", rec))
            continue
        quals.setdefault(eid, set()).add(rec)
    for eid, rs in quals.items():
        m.equipments[eid].qualified_recipes = rs

    for lineno, row in setup_rows:
        tg, fr, to = row.get("tool_group", ""), row.get("from_recipe", ""), row.get("to_recipe", "")
        if tg not in m.tool_groups:
            errors.append(_err("setups.csv", lineno, "tool_group", "無いツールグループです", tg))
        for c, v in (("from_recipe", fr), ("to_recipe", to)):
            if v not in m.recipes:
                errors.append(_err("setups.csv", lineno, c, "recipes.csv に無いレシピです", v))
        st = _num(row, "setup_time", "setups.csv", errors, 0.0) or 0.0
        m.setups[(tg, fr, to)] = st
    _dup_check(setup_rows, ["tool_group", "from_recipe", "to_recipe"], "setups.csv", errors)

    for lineno, row in rel_rows:
        prod = row.get("product", "")
        if prod not in m.products:
            errors.append(_err("release.csv", lineno, "product", "無い機種です", prod))
        m.release_rows.append(ReleaseRow(
            lot_id=row.get("lot_id", ""),
            product=prod,
            release_time=_num(row, "release_time", "release.csv", errors, 0.0) or 0.0,
            priority=_num(row, "priority", "release.csv", errors, None, integer=True),
            due_date=_num(row, "due_date", "release.csv", errors, None)))
    _dup_check(rel_rows, ["lot_id"], "release.csv", errors)

    area_lines = {}
    for lineno, row in area_rows:
        rh = _num(row, "agv_return_home", "areas.csv", errors, 1,
                  integer=True)
        rh = 1 if rh is None else int(rh)
        if rh not in (0, 1):
            errors.append(_err("areas.csv", lineno, "agv_return_home",
                               "0/1 が必要です", rh))
            rh = 1
        a = Area(name=row.get("area", ""),
                 agv_count=int(_num(row, "agv_count", "areas.csv", errors, 0, integer=True) or 0),
                 agv_load_time=_num(row, "agv_load_time", "areas.csv", errors, 0.0) or 0.0,
                 agv_unload_time=_num(row, "agv_unload_time", "areas.csv", errors, 0.0) or 0.0,
                 agv_return_home=int(rh))
        m.areas[a.name] = a
        area_lines[a.name] = lineno
        if a.agv_count == 0:
            no_agv_eq = sorted(e.equip_id for e in m.equipments.values()
                               if e.area == a.name)
            if no_agv_eq:
                warnings.append(
                    "areas.csv:%d: エリア %s は AGV 0 台のため設備 %s "
                    "は搬送できません"
                    % (lineno, a.name, ", ".join(no_agv_eq)))
    _dup_check(area_rows, ["area"], "areas.csv", errors)

    st_lines = {}
    for lineno, row in st_rows:
        s = StockerCfg(
            stocker_id=row.get("stocker_id", ""),
            area=row.get("area", ""),
            capacity=int(_num(row, "capacity", "stockers.csv", errors, 0, integer=True) or 0),
            in_ports=int(_num(row, "in_ports", "stockers.csv", errors, 1, integer=True) or 1),
            out_ports=int(_num(row, "out_ports", "stockers.csv", errors, 1, integer=True) or 1),
            port_time=_num(row, "port_time", "stockers.csv", errors, 0.0) or 0.0)
        m.stockers[s.stocker_id] = s
        st_lines[s.stocker_id] = lineno
        if s.capacity < 1:
            errors.append(_err("stockers.csv", lineno, "capacity", "1 以上が必要です", s.capacity))
        if s.in_ports < 1 or s.out_ports < 1:
            errors.append(_err("stockers.csv", lineno, "in_ports/out_ports",
                               "1 以上が必要です", "%s/%s" % (s.in_ports, s.out_ports)))
    _dup_check(st_rows, ["stocker_id"], "stockers.csv", errors)

    cl_seen = set()
    for lineno, row in cl_rows:
        ttn = _num(row, "time_to_next", "cleanliner.csv", errors, 0.0, positive=True) or 0.0
        try:
            sq = int(row.get("seq", ""))
        except ValueError:
            errors.append(_err("cleanliner.csv", lineno, "seq", "整数ではありません",
                               row.get("seq", "")))
            continue
        sid = row.get("stocker_id", "")
        if sid not in m.stockers:
            errors.append(_err("cleanliner.csv", lineno, "stocker_id",
                               "stockers.csv に無いストッカーです", sid))
        elif sid in cl_seen:
            errors.append(_err("cleanliner.csv", lineno, "stocker_id",
                               "駅が重複しています", sid))
        cl_seen.add(sid)
        m.cl_stations.append((sq, sid, ttn))
    m.cl_stations.sort(key=lambda x: x[0])
    _dup_check(cl_rows, ["seq"], "cleanliner.csv", errors)

    _cross_validate(m, errors, warnings,
                    {"area": area_lines, "stocker": st_lines,
                     "route": route_lines})
    m.warnings = warnings
    return m, errors, warnings


def _cross_validate(m, errors, warnings, lines):
    """整合性検証。方針: 存在しない ID への参照(タイプミス等)はエラー、
    構造的な未接続(要素は存在するがまだ接続されていない=到達不可)は警告。
    lines: {area|stocker|route: {キー: 行番号}} 警告・エラーの行番号付け用。"""
    p = m.params
    for r in m.recipes.values():
        if r.tool_group not in m.tool_groups:
            errors.append("recipes.csv: レシピ %r の tool_group %r は tool_groups.csv にありません"
                          % (r.recipe, r.tool_group))
    for e in m.equipments.values():
        if e.area not in m.areas:
            errors.append("equipments.csv: 設備 %r の area %r は areas.csv にありません"
                          % (e.equip_id, e.area))
    # 「到達可能なエリア」= ストッカーがあり、そのストッカーが CL 駅
    # (または投入ストッカー)で、AGV が 1 台以上あるエリア。
    cl_sids = set(sid for _, sid, _ in m.cl_stations)

    def reachable(area):
        a = m.areas.get(area)
        st = m.stocker_of_area(area)
        return (a is not None and a.agv_count >= 1 and st is not None
                and (st.stocker_id in cl_sids
                     or st.stocker_id == p.release_stocker))

    # 各レシピに、到達可能なエリアにある処理可能な設備が 1 台以上(警告)
    for prod in m.products.values():
        for st in prod.route:
            r = st.recipe
            ok = any(e.tool_group == r.tool_group and e.can_process(r)
                     and reachable(e.area)
                     for e in m.equipments.values())
            if not ok:
                warnings.append(
                    "routes.csv:%s: 機種 %r 工程 %r (レシピ %r) を処理できる"
                    "到達可能な設備がありません(この工程のロットは滞留します)"
                    % (lines["route"].get((prod.name, st.seq), "-"),
                       prod.name, st.seq, r.recipe))
    # 各エリアにストッカーが 1 基。0 基は未接続=警告、2 基以上は
    # 「エリアのストッカーが一意に決まらない」のでエラーのまま。
    for a in m.areas.values():
        n = sum(1 for s in m.stockers.values() if s.area == a.name)
        if n == 0:
            eqs = sorted(e.equip_id for e in m.equipments.values()
                         if e.area == a.name)
            warnings.append(
                "areas.csv:%s: エリア %r にストッカーがありません"
                "(このエリアへは搬送・格納できません)%s"
                % (lines["area"].get(a.name, "-"), a.name,
                   ("(設備 %s)" % ", ".join(eqs)) if eqs else ""))
        elif n > 1:
            errors.append("stockers.csv: エリア %r のストッカーが %d 基です(1 基必要)"
                          % (a.name, n))
    for s in m.stockers.values():
        if s.area not in m.areas:
            errors.append("stockers.csv: ストッカー %r の area %r は areas.csv にありません"
                          % (s.stocker_id, s.area))
        elif s.stocker_id not in cl_sids:
            warnings.append(
                "stockers.csv:%s: ストッカー %r は cleanliner.csv に駅がありません"
                "(CL では到達・出発できません)"
                % (lines["stocker"].get(s.stocker_id, "-"), s.stocker_id))
    # cleanliner 側の参照切れ(存在しない stocker_id)と駅重複は
    # 行ループでエラー検出済み。
    if p.release_stocker and p.release_stocker not in m.stockers:
        errors.append("sim.csv: release_stocker %r は stockers.csv にありません"
                      % p.release_stocker)
    if p.dispatch_rule not in RULES:
        errors.append("sim.csv: dispatch_rule %r が不正です %s"
                      % (p.dispatch_rule, str(RULES)))
    if p.release_mode not in RELEASE_MODES:
        errors.append("sim.csv: release_mode %r が不正です" % p.release_mode)
    if p.release_control not in RELEASE_CONTROLS:
        errors.append("sim.csv: release_control %r が不正です" % p.release_control)
    if p.area_select not in AREA_SELECTS:
        errors.append("sim.csv: area_select %r が不正です" % p.area_select)
    if p.failure_policy not in FAILURE_POLICIES:
        errors.append("sim.csv: failure_policy %r が不正です" % p.failure_policy)
    if p.duration <= 0:
        errors.append("sim.csv: duration は正の値が必要です")
    if p.release_mode in ("fixed", "poisson") and p.lots_per_day <= 0:
        errors.append("sim.csv: lots_per_day は正の値が必要です")
    if p.release_mode == "schedule" and not m.release_rows:
        errors.append("sim.csv: release_mode=schedule ですが release.csv がありません")
    if p.release_control == "conwip" and p.wip_limit < 1:
        errors.append("sim.csv: release_control=conwip には wip_limit >= 1 が必要です")
    if p.cl_carriers < 1:
        errors.append("sim.csv: cl_carriers は 1 以上が必要です")
    if p.cl_slots < 1:
        errors.append("sim.csv: cl_slots は 1 以上が必要です")
    if not p.release_stocker:
        errors.append("sim.csv: release_stocker が未設定です")


def format_errors(errors, warnings):
    out = []
    for w in warnings:
        out.append("警告: " + w)
    for e in errors:
        out.append("エラー: " + e)
    return "\n".join(out)
