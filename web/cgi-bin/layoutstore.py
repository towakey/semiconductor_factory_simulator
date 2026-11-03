"""layout.csv の読書き・ロック・リビジョン・履歴・保存時検証 (12.5.5, 12.5.6)。
ファイルはすべてモデルディレクトリ配下の layout.csv / .layout.lock /
layout_history/ に限定する。"""
import hashlib
import json
import math
import os
import re
import tempfile
import time

LOCK_TTL = 180.0          # 秒
HIST_KEEP = 20            # 履歴世代数
MAX_BODY = 256 * 1024     # 保存本文の上限

_NODE_TYPES = ("area", "equip", "stocker")
_USER_RE = re.compile(r"[^A-Za-z0-9_-]")


def _paths(model_dir):
    return {
        "layout": os.path.join(model_dir, "layout.csv"),
        "lock": os.path.join(model_dir, ".layout.lock"),
        "hist": os.path.join(model_dir, "layout_history"),
    }


def revision(model_dir):
    """layout.csv の内容ハッシュ(SHA-1 先頭16桁)。無ければ 'none'。"""
    path = _paths(model_dir)["layout"]
    if not os.path.exists(path):
        return "none"
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def _sanitize_user(user):
    u = _USER_RE.sub("_", user or "")
    return u or "anon"


def lock_status(model_dir, client_id, now=None):
    """{locked, owner, mine, remaining} を返す。"""
    now = time.time() if now is None else now
    path = _paths(model_dir)["lock"]
    if not os.path.exists(path):
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lk = json.load(f)
    except (OSError, ValueError):
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    exp = float(lk.get("expires", 0))
    if exp <= now:
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    mine = lk.get("client_id") == client_id
    return {"locked": not mine, "owner": lk.get("owner"), "mine": mine,
            "remaining": int(exp - now)}


def acquire_lock(model_dir, client_id, owner, now=None):
    """O_EXCL でロック取得。自分のロックなら期限を更新。
    戻り値 (True, {}) or (False, {owner, remaining})。
    shortcut: 期限切れロックの置換は O_EXCL 非競合前提で、
    競合が起き得るのはごく短い窓のみ(必要なら flock 等に格上げ)。"""
    now = time.time() if now is None else now
    p = _paths(model_dir)["lock"]
    st = lock_status(model_dir, client_id, now)
    if st["mine"]:
        _write_lock(p, client_id, owner, now)
        return True, {}
    if st["locked"]:
        return False, {"owner": st["owner"], "remaining": st["remaining"]}
    # 無人 or 期限切れ → 新規取得
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        # 他のクライアントが先に取得したか、期限切れファイルが残っている
        if os.path.exists(p):
            st = lock_status(model_dir, client_id, now)
            if st["locked"] and not st["mine"]:
                return False, {"owner": st["owner"],
                               "remaining": st["remaining"]}
            try:
                os.remove(p)
            except OSError:
                pass
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                st = lock_status(model_dir, client_id, now)
                return False, {"owner": st.get("owner"),
                               "remaining": st.get("remaining", 0)}
        else:
            raise
    body = json.dumps({"owner": owner or "anon", "client_id": client_id,
                       "expires": now + LOCK_TTL})
    os.write(fd, body.encode("utf-8"))
    os.close(fd)
    return True, {}


def _write_lock(path, client_id, owner, now):
    body = json.dumps({"owner": owner or "anon", "client_id": client_id,
                       "expires": now + LOCK_TTL})
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def release_lock(model_dir, client_id):
    """自分のロックを解放。自分のものでなければ何もしない。"""
    p = _paths(model_dir)["lock"]
    if not os.path.exists(p):
        return True
    try:
        with open(p, "r", encoding="utf-8") as f:
            lk = json.load(f)
    except (OSError, ValueError):
        return True
    if lk.get("client_id") == client_id:
        try:
            os.remove(p)
        except OSError:
            pass
    return True


def expected_node_set(model):
    s = set()
    for a in model.areas:
        s.add(("area", a))
    for e in model.equipments:
        s.add(("equip", e))
    for st in model.stockers:
        s.add(("stocker", st))
    return s


MAX_POINTS = 200          # cl_point / agv_point 各々の個数上限


def validate_nodes(nodes, model, world):
    """12.5.6 の検証。戻り値はエラー文字列のリスト(空ならOK)。"""
    errors = []
    expected = expected_node_set(model)
    seen = set()
    n_clp = n_agvp = 0
    if not isinstance(nodes, list):
        return ["nodes はリストではありません"]
    ww = world.get("w", 0.0)
    wh = world.get("h", 0.0)
    for i, n in enumerate(nodes):
        nt, nid = n.get("node_type"), n.get("node_id")
        if nt in ("cl_point", "agv_point"):
            if nt == "cl_point":
                n_clp += 1
            else:
                n_agvp += 1
            ref, _, kk = nid.rpartition(":") if isinstance(nid, str) \
                else ("", "", "")
            ok_ref = ref in model.stockers if nt == "cl_point" \
                else ref in model.equipments
            if not (isinstance(nid, str) and ok_ref and kk.isdigit()):
                errors.append("折れ点の参照が不正です: %s/%s" % (nt, nid))
                continue
            x, y = n.get("x"), n.get("y")
            if not isinstance(x, (int, float)) or \
                    not isinstance(y, (int, float)) or \
                    not math.isfinite(x) or not math.isfinite(y):
                errors.append("折れ点 %s の座標が不正です" % nid)
            elif x < 0 or y < 0 or x > ww or y > wh:
                errors.append("折れ点 %s が world 範囲外です" % nid)
            continue
        key = (nt, nid)
        if key in seen:
            errors.append("ノード %s/%s が重複しています" % key)
            continue
        seen.add(key)
        if key not in expected:
            errors.append("未知のノード %s/%s" % key)
            continue
        for c in ("x", "y", "w", "h"):
            v = n.get(c)
            if not isinstance(v, (int, float)) or \
                    not math.isfinite(v):
                errors.append("ノード %s/%s の %s が不正です" % (key + (c,)))
                break
        else:
            x, y, w, h = n["x"], n["y"], n["w"], n["h"]
            if x < 0 or y < 0 or x + w > ww or y + h > wh:
                errors.append("ノード %s/%s が world 範囲外です" % key)
            if w < 1 or h < 1 or w > ww or h > wh:
                errors.append("ノード %s/%s のサイズが不正です" % key)
    missing = expected - seen
    if missing:
        errors.append("ノードが不足しています: %s"
                      % ", ".join("%s/%s" % k for k in sorted(missing)))
    if n_clp > MAX_POINTS or n_agvp > MAX_POINTS:
        errors.append("折れ点が多すぎます(cl:%d, agv:%d, 上限 %d)"
                      % (n_clp, n_agvp, MAX_POINTS))
    return errors


def write_layout_csv(model_dir, nodes, world):
    """tempfile + os.replace で原子的に書き出す。小数第2位に丸める。"""
    path = _paths(model_dir)["layout"]
    fd, tmp = tempfile.mkstemp(dir=model_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write("node_type,node_id,x,y,w,h\n")
            f.write("world,world,0,0,%.2f,%.2f\n"
                    % (world["w"], world["h"]))
            order = {"area": 0, "equip": 1, "stocker": 2,
                     "cl_point": 3, "agv_point": 4}
            for n in sorted(nodes, key=lambda n:
                            (order.get(n["node_type"], 9), n["node_id"])):
                f.write("%s,%s,%.2f,%.2f,%.2f,%.2f\n"
                        % (n["node_type"], n["node_id"], n["x"], n["y"],
                           n.get("w") or 0, n.get("h") or 0))
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def archive_history(model_dir, user):
    """現行 layout.csv を layout_history/ にコピーし20世代に保つ。"""
    import shutil
    p = _paths(model_dir)
    src = p["layout"]
    if not os.path.exists(src):
        return
    os.makedirs(p["hist"], exist_ok=True)
    name = "layout_%s_%06d_%s.csv" % (
        time.strftime("%Y%m%d_%H%M%S"),
        int((time.time() % 1) * 1000000), _sanitize_user(user))
    shutil.copy2(src, os.path.join(p["hist"], name))
    files = sorted(f for f in os.listdir(p["hist"])
                   if f.startswith("layout_") and f.endswith(".csv"))
    for f in files[:-HIST_KEEP]:
        os.remove(os.path.join(p["hist"], f))


def save_layout(model_dir, nodes, client_id, base_revision, user,
                can_edit, model, world, now=None):
    """保存手順(12.5.5)。ロック所有→リビジョン→検証→履歴→原子的書換え。
    戻り値 (http_status, response_dict)。"""
    if not can_edit:
        return 403, {"ok": False, "error": "forbidden"}
    st = lock_status(model_dir, client_id, now)
    if not st["mine"]:
        if st["locked"]:
            return 409, {"ok": False, "error": "locked",
                         "owner": st["owner"],
                         "remaining": st["remaining"]}
        return 403, {"ok": False, "error": "no_lock"}
    cur = revision(model_dir)
    if cur != base_revision:
        return 409, {"ok": False, "error": "revision",
                     "revision": cur}
    errors = validate_nodes(nodes, model, world)
    if errors:
        return 400, {"ok": False, "error": "invalid",
                     "detail": errors}
    archive_history(model_dir, user)
    write_layout_csv(model_dir, nodes, world)
    return 200, {"ok": True, "revision": revision(model_dir)}
