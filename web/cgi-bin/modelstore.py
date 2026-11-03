"""モデル CSV の Web 編集ストア (設計書 12.6)。
layoutstore.py と同じ仕組み: ロック(.model.lock, O_EXCL, 180秒)・
リビジョン(SHA-1 先頭16桁)・履歴(model_history/, 20世代)・
一時ディレクトリでの検証・tempfile+os.replace による置換。"""
import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time

from fabsim import config

LOCK_TTL = 180.0
HIST_KEEP = 20
MAX_BODY = 1024 * 1024   # 保存本文 1MB
_OPTIONAL_DELETABLE = ("qualifications.csv", "setups.csv", "release.csv")
_BAD_CELL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _paths(mdir):
    return {"lock": os.path.join(mdir, ".model.lock"),
            "hist": os.path.join(mdir, "model_history")}


def revision(mdir):
    """編集対象 CSV 全体の内容ハッシュ(無いファイルは無視)。"""
    h = hashlib.sha1()
    for fn in config.EDITABLE_FILES:
        p = os.path.join(mdir, fn)
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(fn.encode("utf-8") + f.read())
    return h.hexdigest()[:16]


def lock_status(mdir, client_id, now=None):
    now = time.time() if now is None else now
    p = _paths(mdir)["lock"]
    if not os.path.exists(p):
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    try:
        with open(p, "r", encoding="utf-8") as f:
            lk = json.load(f)
    except (OSError, ValueError):
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    exp = float(lk.get("expires", 0))
    if exp <= now:
        return {"locked": False, "owner": None, "mine": False, "remaining": 0}
    mine = lk.get("client_id") == client_id
    return {"locked": not mine, "owner": lk.get("owner"), "mine": mine,
            "remaining": int(exp - now)}


def acquire_lock(mdir, client_id, owner, now=None):
    now = time.time() if now is None else now
    p = _paths(mdir)["lock"]
    st = lock_status(mdir, client_id, now)
    if st["mine"]:
        _write_lock(p, client_id, owner, now)
        return True, {}
    if st["locked"]:
        return False, {"owner": st["owner"], "remaining": st["remaining"]}
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        if os.path.exists(p):
            st = lock_status(mdir, client_id, now)
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
                return False, {"owner": None, "remaining": 0}
        else:
            raise
    body = json.dumps({"owner": owner or "anon", "client_id": client_id,
                       "expires": now + LOCK_TTL})
    os.write(fd, body.encode("utf-8"))
    os.close(fd)
    return True, {}


def _write_lock(path, client_id, owner, now):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"owner": owner or "anon",
                            "client_id": client_id,
                            "expires": now + LOCK_TTL}))


def release_lock(mdir, client_id):
    p = _paths(mdir)["lock"]
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


def read_files(mdir):
    """{fname: {"cols": [...], "rows": [[...]]}}。無い任意ファイルは空表。"""
    out = {}
    for fn in config.EDITABLE_FILES:
        p = os.path.join(mdir, fn)
        spec_cols = [c[0] for c in config.CSV_SPECS[fn]]
        cols = list(spec_cols)
        rows = []
        if os.path.exists(p):
            lines = []
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                for line in csv.reader(f):
                    if not line or line[0].strip().startswith("#"):
                        continue
                    if all(not c.strip() for c in line):
                        continue
                    lines.append(line)
            if lines:
                cols = [c.strip() for c in lines[0]]
                rows = [[r[i] if i < len(r) else ""
                         for i in range(len(cols))]
                        for r in lines[1:]]
                for c in spec_cols:         # ヘッダに無い spec 列を補完
                    if c not in cols:
                        cols.append(c)
                        for r in rows:
                            r.append("")
        out[fn] = {"cols": cols, "rows": rows}
    return out


def _sanity(files):
    """files ペイロードの基本検証。エラー文字列のリスト。"""
    errors = []
    if not isinstance(files, dict):
        return ["files が不正です"]
    for fn, tbl in files.items():
        if fn not in config.EDITABLE_FILES:
            errors.append("編集対象外のファイル: %s" % fn)
            continue
        if not isinstance(tbl, dict) or not isinstance(
                tbl.get("cols"), list) or not isinstance(
                tbl.get("rows"), list):
            errors.append("%s の形式が不正です" % fn)
            continue
        ncol = len(tbl["cols"])
        for i, r in enumerate(tbl["rows"]):
            if not isinstance(r, list) or len(r) != ncol:
                errors.append("%s:%d行目の列数が不正です" % (fn, i + 2))
                break
            for c in r:
                if isinstance(c, (dict, list)) or _BAD_CELL.search(str(c)):
                    errors.append("%s:%d行目に不正な文字があります" % (fn, i + 2))
                    break
    return errors


def write_csv_file(path, cols, rows):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_tree(dst, files):
    """一時ディレクトリに全 CSV を書き出す。"""
    os.makedirs(dst, exist_ok=True)
    for fn in config.EDITABLE_FILES:
        tbl = files.get(fn)
        if not tbl or not tbl.get("rows"):
            if fn in _OPTIONAL_DELETABLE:
                continue
            tbl = tbl or {"cols": [c[0] for c in config.CSV_SPECS[fn]],
                          "rows": []}
        with open(os.path.join(dst, fn), "w", encoding="utf-8",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(tbl["cols"])
            w.writerows(tbl["rows"])


def validate_files(mdir, files):
    """files を一時ディレクトリに書いて config 検証。
    戻り値 (errors, warnings)。"""
    tmp = tempfile.mkdtemp(dir=mdir)
    try:
        _write_tree(tmp, files)
        _, errors, warnings = config.load_model(tmp)
        return errors, warnings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def archive_history(mdir, user):
    """現行の編集対象 CSV を model_history/<ts>_<user>/ に退避。"""
    hist = _paths(mdir)["hist"]
    existing = [fn for fn in config.EDITABLE_FILES
                if os.path.exists(os.path.join(mdir, fn))]
    if not existing:
        return
    os.makedirs(hist, exist_ok=True)
    u = re.sub(r"[^A-Za-z0-9_-]", "_", user or "") or "anon"
    d = os.path.join(hist, "%s_%06d_%s" % (
        time.strftime("%Y%m%d_%H%M%S"), int((time.time() % 1) * 1e6), u))
    os.makedirs(d)
    for fn in existing:
        shutil.copy2(os.path.join(mdir, fn), os.path.join(d, fn))
    dirs = sorted(x for x in os.listdir(hist)
                  if os.path.isdir(os.path.join(hist, x)))
    for x in dirs[:-HIST_KEEP]:
        shutil.rmtree(os.path.join(hist, x), ignore_errors=True)


def save_model(mdir, files, client_id, base_revision, user, can_edit,
               now=None):
    """保存手順。戻り値 (http_status, response_dict)。"""
    if not can_edit:
        return 403, {"ok": False, "error": "forbidden"}
    st = lock_status(mdir, client_id, now)
    if not st["mine"]:
        if st["locked"]:
            return 409, {"ok": False, "error": "locked",
                         "owner": st["owner"], "remaining": st["remaining"]}
        return 403, {"ok": False, "error": "no_lock"}
    if revision(mdir) != base_revision:
        return 409, {"ok": False, "error": "revision",
                     "revision": revision(mdir)}
    errors = _sanity(files)
    if errors:
        return 400, {"ok": False, "error": "invalid", "detail": errors}
    verr, vwarn = validate_files(mdir, files)
    if verr:
        return 400, {"ok": False, "error": "validation",
                     "detail": verr, "warnings": vwarn}
    archive_history(mdir, user)
    # 全ファイルを一時書出し→置換(書込み失敗を置換前に検出)
    tmps = []
    try:
        for fn in config.EDITABLE_FILES:
            tbl = files.get(fn)
            if not tbl or not tbl.get("rows"):
                if fn in _OPTIONAL_DELETABLE:
                    p = os.path.join(mdir, fn)
                    if os.path.exists(p):
                        os.remove(p)
                    continue
                tbl = {"cols": [c[0] for c in config.CSV_SPECS[fn]],
                       "rows": []}
            fd, tmp = tempfile.mkstemp(dir=mdir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(tbl["cols"])
                w.writerows(tbl["rows"])
            tmps.append((tmp, os.path.join(mdir, fn)))
        for tmp, dst in tmps:
            os.replace(tmp, dst)
    except Exception:
        for tmp, _ in tmps:
            if os.path.exists(tmp):
                os.remove(tmp)
        raise
    return 200, {"ok": True, "revision": revision(mdir),
                 "warnings": vwarn}


_STRUCT_FILES = ("equipments.csv", "areas.csv", "stockers.csv",
                 "cleanliner.csv")


def merge_structure(cur, structure):
    """現行 files に structure の行群を反映した新しい files を返す。
    qualifications は存在しない設備の行を除去(カスケード)。"""
    errors = []
    files = {fn: {"cols": list(cur[fn]["cols"]),
                  "rows": [list(r) for r in cur[fn]["rows"]]}
             for fn in cur}
    for fn in _STRUCT_FILES + ("qualifications.csv",):
        tbl = structure.get(fn)
        if not tbl:
            continue
        cols = tbl.get("cols", [])
        if cols != files[fn]["cols"]:
            errors.append("%s の列構成が一致しません" % fn)
            continue
        files[fn]["rows"] = [[str(c) for c in r]
                             for r in tbl.get("rows", [])]
    if errors:
        return files, errors
    eq_col = files["equipments.csv"]["cols"].index("equip_id")
    eqs = {r[eq_col] for r in files["equipments.csv"]["rows"]}
    qc = files["qualifications.csv"]
    qi = qc["cols"].index("equip_id")
    qc["rows"] = [r for r in qc["rows"] if r[qi] in eqs]
    # cleanliner の参照先ストッカーも除去(削除されたエリアの駅)
    st_col = files["stockers.csv"]["cols"].index("stocker_id")
    sts = {r[st_col] for r in files["stockers.csv"]["rows"]}
    cc = files["cleanliner.csv"]
    ci = cc["cols"].index("stocker_id")
    cc["rows"] = [r for r in cc["rows"] if r[ci] in sts]
    for i, r in enumerate(cc["rows"], 1):
        r[cc["cols"].index("seq")] = str(i)
    return files, errors


def clone_model(src_mdir, dst_mdir):
    """全 CSV + layout.csv を複製(ロック・履歴は複製しない)。
    戻り値 (errors, warnings)。"""
    os.makedirs(dst_mdir)
    for fn in os.listdir(src_mdir):
        if fn.endswith(".csv"):
            shutil.copy2(os.path.join(src_mdir, fn),
                         os.path.join(dst_mdir, fn))
    _, errors, warnings = config.load_model(dst_mdir)
    return errors, warnings
