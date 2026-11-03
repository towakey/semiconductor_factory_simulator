"""CGI API (設計書 13.3, 13.4)。環境変数のみに依存し、IIS でも
python -m http.server --cgi でも動く。テストからは dispatch() を直接呼ぶ。
"""
import csv
import datetime
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import layoutstore  # noqa: E402
import modelstore   # noqa: E402

MODEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RUN_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
CLIENT_RE = re.compile(r"^[0-9a-f]{16,32}$")
BAD_CHARS = re.compile(r"[/\\\x00]|\.\.")
MAX_WINDOW = 4320.0        # 3日分
MAX_ROWS = 50000
GET_ACTIONS = {"models", "runs", "status", "layout", "window", "kpi",
               "summary", "results", "model_layout", "model_files"}
POST_ACTIONS = {"start", "cancel", "layout_lock", "layout_unlock",
                "layout_save", "layout_auto", "model_validate",
                "model_lock", "model_unlock", "model_save", "model_clone"}
CT_ALLOWED = ("application/json", "application/x-www-form-urlencoded")


def data_dir():
    return os.environ.get("FABSIM_DATA_DIR") or os.path.join(_ROOT, "data")


def web_config():
    d = {"run_mode": "spawn", "max_concurrent": 2, "layout_editors": [],
         "allow_anonymous_edit": False, "disk_free_min_mb": 100,
         "duration_max": 525600.0}
    try:
        with open(os.path.join(data_dir(), "web_config.json"),
                  encoding="utf-8") as f:
            d.update(json.load(f))
    except (OSError, ValueError):
        pass
    return d


def _err(status, code, **kw):
    r = {"ok": False, "error": code}
    r.update(kw)
    return status, r


def _bad(v):
    return v is None or BAD_CHARS.search(v)


def _valid_model(model):
    return isinstance(model, str) and MODEL_RE.match(model) and \
        os.path.isdir(os.path.join(data_dir(), "models", model))


def _valid_run(run):
    return isinstance(run, str) and RUN_RE.match(run) and \
        os.path.isdir(_run_dir(run))


def _run_dir(run):
    p = os.path.realpath(os.path.join(data_dir(), "runs", run))
    base = os.path.realpath(os.path.join(data_dir(), "runs"))
    if not (p == base or p.startswith(base + os.sep)):
        return None
    return p


def _db_path(run):
    d = _run_dir(run)
    p = os.path.join(d, "result.db") if d else None
    return p if p and os.path.exists(p) else None


def _db(run):
    path = _db_path(run)
    if not path:
        return None
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _meta(db):
    return dict(db.execute("SELECT key,value FROM meta"))


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _load_model_cfg(model):
    from fabsim import config
    mdir = os.path.join(data_dir(), "models", model)
    model_obj, errors, warnings = config.load_model(mdir)
    return model_obj, errors, warnings, mdir


def _can_edit(cfg, remote_user):
    editors = cfg.get("layout_editors") or []
    if remote_user:
        return not editors or remote_user in editors
    return bool(cfg.get("allow_anonymous_edit"))


# ---------------------------------------------------------- 実行管理
def _pid_alive(pid):
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
            timeout=10)
        return (b'"' in out) and (b"No tasks" not in out)
    except Exception:
        return True   # 確認できなければ生存扱い(誤 failed を避ける)


def _job(run_dir):
    try:
        with open(os.path.join(run_dir, "job.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_job(run_dir, job):
    tmp = os.path.join(run_dir, "job.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f)
    os.replace(tmp, os.path.join(run_dir, "job.json"))


def _running_jobs():
    rd = os.path.join(data_dir(), "runs")
    out = []
    if not os.path.isdir(rd):
        return out
    for name in os.listdir(rd):
        if not RUN_RE.match(name):
            continue
        d = os.path.join(rd, name)
        job = _job(d)
        st = job.get("status")
        if st in ("queued", "running"):
            pid = job.get("pid")
            if pid and not _pid_alive(int(pid)):
                job["status"] = "failed"
                job["error"] = "process exited without status update"
                _write_job(d, job)
            else:
                out.append((name, job))
    return out


def _start(params, cfg, remote_user):
    model = params.get("model", "")
    rule = params.get("rule", "") or "FIFO"
    seed = params.get("seed", "")
    duration = params.get("duration", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    from fabsim.rules import RULES
    if rule not in RULES:
        return _err(400, "bad_rule")
    try:
        seed_i = int(seed)
        if not (-2**31 <= seed_i <= 2**31 - 1):
            raise ValueError
    except (TypeError, ValueError):
        return _err(400, "bad_seed")
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        return _err(400, "bad_duration")
    if not (0 < dur <= float(cfg["duration_max"])):
        return _err(400, "bad_duration")
    if len(_running_jobs()) >= int(cfg["max_concurrent"]):
        return _err(409, "busy")
    try:
        free_mb = _free_mb(data_dir())
        if free_mb is not None and free_mb < float(cfg["disk_free_min_mb"]):
            return _err(409, "disk_full")
    except OSError:
        pass

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + \
        "-" + uuid.uuid4().hex[:6]
    run_dir = _run_dir(run_id)
    os.makedirs(run_dir, exist_ok=False)
    src = os.path.join(data_dir(), "models", model)
    cfg_dst = os.path.join(run_dir, "config")
    os.makedirs(cfg_dst)
    for fn in os.listdir(src):
        if fn.endswith(".csv"):
            with open(os.path.join(src, fn), "rb") as fi, \
                    open(os.path.join(cfg_dst, fn), "wb") as fo:
                fo.write(fi.read())
    job = {"status": "queued", "model": model, "rule": rule,
           "seed": seed_i, "duration": dur, "run_id": run_id,
           "started_at": datetime.datetime.now().isoformat(
               timespec="seconds")}
    _write_job(run_dir, job)
    stdout = open(os.path.join(run_dir, "stdout.log"), "ab")
    argv = [sys.executable, "-m", "fabsim", "run",
            "--config-dir", cfg_dst, "--out", run_dir,
            "--run-id", run_id,
            "--progress-file", os.path.join(run_dir, "progress.json"),
            "--duration", str(dur), "--seed", str(seed_i),
            "--rule", rule]
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP",
                 "CREATE_BREAKAWAY_FROM_JOB"):
        flags |= getattr(subprocess, name, 0)
    try:
        proc = subprocess.Popen(argv, cwd=_ROOT, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=subprocess.STDOUT,
                                close_fds=True, creationflags=flags)
    except OSError:
        flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        proc = subprocess.Popen(argv, cwd=_ROOT, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=subprocess.STDOUT,
                                close_fds=True, creationflags=flags)
    job["status"] = "running"
    job["pid"] = proc.pid
    _write_job(run_dir, job)
    return 200, {"ok": True, "run_id": run_id}


def _free_mb(path):
    try:
        import ctypes
        free = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            str(path), None, None, ctypes.byref(free))
        return free.value / (1024 * 1024)
    except Exception:
        return None


def _cancel(params, remote_user):
    run = params.get("run", "")
    if not _valid_run(run):
        return _err(400, "bad_run")
    d = _run_dir(run)
    job = _job(d)
    if job.get("status") not in ("queued", "running"):
        return _err(409, "not_running")
    pid = job.get("pid")
    if pid:
        if not _pid_alive(int(pid)):
            job["status"] = "failed"
            _write_job(d, job)
            return _err(409, "dead")
        if not _cmdline_matches(int(pid), run):
            return _err(409, "pid_mismatch")
        subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job["status"] = "cancelled"
    _write_job(d, job)
    _mark_db_status(d, "cancelled")
    return 200, {"ok": True}


def _cmdline_matches(pid, run_id):
    """PID のコマンドラインに run_id が含まれるか(PID 再利用対策)。"""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d')"
             ".CommandLine" % pid], timeout=15)
        return run_id.encode() in out
    except Exception:
        return True   # 確認不能なら止めない誤検知を避ける


def _mark_db_status(run_dir, status):
    path = os.path.join(run_dir, "result.db")
    if not os.path.exists(path):
        return
    try:
        c = sqlite3.connect(path)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('status',?)",
                  (status,))
        c.commit()
        c.close()
    except sqlite3.Error:
        pass


def _status(params):
    run = params.get("run", "")
    if not _valid_run(run):
        return _err(400, "bad_run")
    d = _run_dir(run)
    job = _job(d)
    st = job.get("status", "unknown")
    if st in ("queued", "running") and job.get("pid") and \
            not _pid_alive(int(job["pid"])):
        st = "failed"
        job["status"] = "failed"
        job["error"] = "process exited without status update"
        _write_job(d, job)
    prog = {}
    try:
        with open(os.path.join(d, "progress.json"), encoding="utf-8") as f:
            prog = json.load(f)
    except (OSError, ValueError):
        pass
    out = {"ok": True, "run_id": run, "status": st,
           "model": job.get("model"), "rule": job.get("rule"),
           "seed": job.get("seed"),
           "duration": prog.get("duration", job.get("duration")),
           "progress_time": prog.get("progress_time", 0.0)}
    db = _db(run)
    if db is not None:
        try:
            m = _meta(db)
            out["status"] = m.get("status", st)
            out["progress_time"] = _f(m.get("progress_time"),
                                      out["progress_time"])
            if "error" in m:
                out["error"] = m["error"]
        finally:
            db.close()
    if out["status"] == "failed" and job.get("error"):
        out["error"] = job["error"]
    if out["status"] != st:
        job["status"] = out["status"]
        _write_job(d, job)
    return 200, out


# ---------------------------------------------------------- 読取り系
def _models(params):
    md = os.path.join(data_dir(), "models")
    out = []
    if os.path.isdir(md):
        for name in sorted(os.listdir(md)):
            if MODEL_RE.match(name) and \
                    os.path.isdir(os.path.join(md, name)):
                out.append(name)
    return 200, {"ok": True, "models": out}


def _runs(params):
    out = []
    for name, job in _running_jobs_listing():
        out.append({"run_id": name, "model": job.get("model"),
                    "status": job.get("status"),
                    "started_at": job.get("started_at"),
                    "rule": job.get("rule"), "seed": job.get("seed")})
    return 200, {"ok": True, "runs": out}


def _running_jobs_listing():
    rd = os.path.join(data_dir(), "runs")
    out = []
    if not os.path.isdir(rd):
        return out
    for name in sorted(os.listdir(rd)):
        if not RUN_RE.match(name):
            continue
        out.append((name, _job(os.path.join(rd, name))))
    return out


def _layout(params):
    run = params.get("run", "")
    if not _valid_run(run):
        return _err(400, "bad_run")
    db = _db(run)
    if db is None:
        return _err(404, "no_db")
    try:
        meta = _meta(db)
        areas = [{"area": r[0], "agv_count": r[1], "x": r[2], "y": r[3],
                  "w": r[4], "h": r[5]}
                 for r in db.execute("SELECT * FROM areas ORDER BY area")]
        eq_cols = [c[1] for c in db.execute(
            "PRAGMA table_info(equipments)")]
        has_shape = "shape" in eq_cols
        has_wh = "w" in eq_cols
        has_ports = "ports" in eq_cols
        shape_map = {}
        if not has_shape:
            # 古い run: run/config/tool_groups.csv から形状を解決
            from fabsim import config as _cfg
            tgcsv = os.path.join(data_dir(), "runs", run,
                                 "config", "tool_groups.csv")
            try:
                with open(tgcsv, encoding="utf-8-sig", newline="") as f:
                    rows = [r for r in csv.reader(f)
                            if r and not r[0].startswith("#")]
                if rows:
                    cols = rows[0]
                    shape_map = _cfg.resolve_shapes(
                        [(r[cols.index("tool_group")],
                          r[cols.index("shape")]
                          if "shape" in cols else "")
                         for r in rows[1:]])
            except Exception:
                pass
        equips = [{"equip_id": r[0], "area": r[1], "tool_group": r[2],
                   "x": r[3], "y": r[4],
                   "shape": (r[5] or "rect") if has_shape else
                            shape_map.get(r[2], "rect"),
                   "w": (r[6] or 40) if has_wh else 40,
                   "h": (r[7] or 30) if has_wh else 30,
                   "ports": (r[8] or 1) if has_ports else 1}
                  for r in db.execute("SELECT * FROM equipments"
                                      " ORDER BY equip_id")]
        st_cols = [c[1] for c in db.execute(
            "PRAGMA table_info(stockers)")]
        st_wh = "w" in st_cols
        stockers = [{"stocker_id": r[0], "area": r[1], "capacity": r[2],
                     "x": r[3], "y": r[4],
                     "w": (r[5] or 60) if st_wh else 60,
                     "h": (r[6] or 40) if st_wh else 40}
                    for r in db.execute("SELECT * FROM stockers"
                                        " ORDER BY stocker_id")]
        stations = [{"seq": r[0], "stocker_id": r[1], "base_time": r[2],
                     "time_to_next": r[3]}
                    for r in db.execute("SELECT * FROM cl_stations"
                                        " ORDER BY seq")]
        cl_points, agv_points = [], []
        try:
            for r in db.execute("SELECT from_stocker,k,x,y FROM cl_points"
                                " ORDER BY from_stocker,k"):
                cl_points.append({"from_stocker": r[0], "k": r[1],
                                  "x": r[2], "y": r[3]})
            for r in db.execute("SELECT equip_id,k,x,y FROM agv_points"
                                " ORDER BY equip_id,k"):
                agv_points.append({"equip_id": r[0], "k": r[1],
                                   "x": r[2], "y": r[3]})
        except sqlite3.Error:
            pass   # 古い run(テーブル無し)は空配列
        return 200, {"ok": True, "areas": areas, "equipments": equips,
                     "stockers": stockers, "cl_stations": stations,
                     "cl": {"carriers": int(_f(meta.get("cl_carriers"), 1)),
                            "slots": int(_f(meta.get("cl_slots"), 1)),
                            "period": _f(meta.get("cl_period"), 0)},
                     "cl_points": cl_points, "agv_points": agv_points,
                     "meta": meta}
    finally:
        db.close()


def _window(params):
    run, t0, t1 = params.get("run", ""), params.get("t0"), params.get("t1")
    if not _valid_run(run):
        return _err(400, "bad_run")
    try:
        t0, t1 = float(t0), float(t1)
    except (TypeError, ValueError):
        return _err(400, "bad_range")
    truncated = False
    if t1 - t0 > MAX_WINDOW:
        t1 = t0 + MAX_WINDOW
        truncated = True
    if t1 < t0:
        return _err(400, "bad_range")
    db = _db(run)
    if db is None:
        return _err(404, "no_db")
    out = {"ok": True, "truncated": truncated}
    total = 0
    try:
        for table, cols in (
                ("equip_state", ("equip_id", "state", "lot_id", "recipe",
                                 "t_start", "t_end")),
                ("stocker_lots", ("stocker_id", "lot_id", "t_start",
                                  "t_end")),
                ("agv_moves", ("agv_id", "area", "phase", "from_node",
                               "to_node", "lot_id", "t_start", "t_end")),
                ("cl_rides", ("lot_id", "carrier_id", "slot",
                              "from_station", "to_station",
                              "t_request", "t_load", "t_unload"))):
            tc = "t_load" if table == "cl_rides" else "t_start"
            te = "t_unload" if table == "cl_rides" else "t_end"
            sql = ("SELECT %s FROM %s WHERE %s<=? AND (%s IS NULL OR %s>?)"
                   " ORDER BY %s" % (",".join(cols), table, tc, te, te, tc))
            rows = []
            for r in db.execute(sql, (t1, t0)):
                rows.append(list(r))
                if total + len(rows) >= MAX_ROWS:
                    truncated = True
                    break
            total += len(rows)
            out[table] = {"cols": list(cols), "rows": rows}
            if truncated and total >= MAX_ROWS:
                break
        # equip_port_lots は古い run に無い場合がある
        try:
            rows = []
            for r in db.execute(
                    "SELECT equip_id,lot_id,t_start,t_end FROM"
                    " equip_port_lots WHERE t_start<=? AND"
                    " (t_end IS NULL OR t_end>?) ORDER BY t_start",
                    (t1, t0)):
                rows.append(list(r))
            out["equip_port_lots"] = rows
        except sqlite3.Error:
            out["equip_port_lots"] = []
        out["truncated"] = truncated
        return 200, out
    finally:
        db.close()


def _kpi(params):
    run, t = params.get("run", ""), params.get("t")
    if not _valid_run(run):
        return _err(400, "bad_run")
    try:
        t = float(t)
    except (TypeError, ValueError):
        return _err(400, "bad_t")
    db = _db(run)
    if db is None:
        return _err(404, "no_db")
    try:
        row = db.execute(
            "SELECT t FROM kpi_ts ORDER BY ABS(t-?) LIMIT 1",
            (t,)).fetchone()
        if not row:
            return 200, {"ok": True, "kpi": {}}
        tsel = row[0]
        kpi = {}
        for m, k, v in db.execute(
                "SELECT metric,key,value FROM kpi_ts WHERE t=?", (tsel,)):
            kpi.setdefault(m, {})[k] = v
        return 200, {"ok": True, "t": tsel, "kpi": kpi}
    finally:
        db.close()


def _summary(params):
    run = params.get("run", "")
    if not _valid_run(run):
        return _err(400, "bad_run")
    db = _db(run)
    if db is None:
        return _err(404, "no_db")
    try:
        row = db.execute(
            "SELECT value FROM meta WHERE key='summary_json'").fetchone()
        if not row:
            return _err(404, "no_summary")
        return 200, {"ok": True, "summary": json.loads(row[0])}
    finally:
        db.close()


def _results(params):
    run = params.get("run", "")
    if not _valid_run(run):
        return _err(400, "bad_run")
    mode = params.get("mode", "cum")
    if mode not in ("cum", "window"):
        return _err(400, "bad_mode")
    db = _db(run)
    if db is None:
        return _err(404, "no_db")
    try:
        meta = _meta(db)
        job = _job(_run_dir(run))
        duration = _f(meta.get("duration"), _f(job.get("duration")))
        status = meta.get("status", job.get("status", "unknown"))
        progress = _f(meta.get("progress_time"))
        try:
            with open(os.path.join(_run_dir(run), "progress.json"),
                      encoding="utf-8") as f:
                progress = max(progress,
                               _f(json.load(f).get("progress_time")))
        except (OSError, ValueError):
            pass
        upper = min(duration, progress) if status in ("running", "queued") \
            else duration
        try:
            t = float(params.get("t"))
        except (TypeError, ValueError):
            return _err(400, "bad_t")
        if not math.isfinite(t) or t < 0 or t > upper:
            return _err(400, "bad_range", max_time=upper)
        if mode == "window":
            try:
                window = float(params.get("window"))
            except (TypeError, ValueError):
                return _err(400, "bad_window")
            if not math.isfinite(window) or window < 1 or window > duration:
                return _err(400, "bad_window")
            p_start = max(0.0, t - window)
        else:
            window = None
            p_start = 0.0
        period = max(0.0, t - p_start)

        def cols(table):
            return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}

        # recipe -> tool_group: 新規DBテーブル、古いDBは run/config CSV。
        recipe_tg = {}
        warnings = []
        try:
            recipe_tg = dict(db.execute(
                "SELECT recipe,tool_group FROM recipes"))
        except sqlite3.Error:
            recipe_tg = {}
        if not recipe_tg:
            path = os.path.join(_run_dir(run), "config", "recipes.csv")
            try:
                with open(path, encoding="utf-8-sig", newline="") as f:
                    raw = [r for r in csv.reader(f)
                           if r and not r[0].strip().startswith("#")]
                if raw:
                    ix = raw[0].index("recipe")
                    it = raw[0].index("tool_group")
                    recipe_tg = {r[ix]: r[it] for r in raw[1:]
                                 if len(r) > max(ix, it)}
            except (OSError, ValueError, IndexError):
                pass
        if not recipe_tg:
            warnings.append(
                "この run には recipes 対応表がありません。"
                "ツールグループ別の待ち/WIP 帰属は算出できません。")

        equip_cols = cols("equipments")
        eq_query = ("SELECT equip_id,area,tool_group"
                    + (",ports" if "ports" in equip_cols else "")
                    + " FROM equipments ORDER BY equip_id")
        equips = {}
        for r in db.execute(eq_query):
            equips[r[0]] = {
                "equip_id": r[0], "area": r[1], "tool_group": r[2],
                "ports": (r[3] if "ports" in equip_cols and r[3] else 1)}
        groups = sorted(set(recipe_tg.values()) |
                        {e["tool_group"] for e in equips.values()})
        by_group = {g: [] for g in groups}
        for e in equips.values():
            by_group.setdefault(e["tool_group"], []).append(e["equip_id"])

        def new_acc():
            return {
                "completed_steps": 0, "process_sum": 0.0,
                "process_n": 0, "wafer_count": 0, "wafer_n": 0,
                "dispatch_sum": 0.0, "dispatch_n": 0,
                "dispatch_max": None, "reserved_start_sum": 0.0,
                "reserved_start_n": 0,
                "state_minutes": dict((s, 0.0) for s in
                    ("BUSY", "IDLE", "RESERVED", "SETUP", "DOWN", "PM")),
                "wip_wait_ids": set(), "wip_equip_ids": set(),
                "wip_processing_ids": set(), "wip_reserved_ids": set(),
                "period_cycles": [], "period_ontime": 0,
                "period_due_n": 0, "period_completed_lots": 0,
            }

        total = new_acc()
        group_acc = {g: new_acc() for g in groups}
        equip_acc = {eid: new_acc() for eid in equips}
        lot_cols = cols("lots")
        wafer_ok = "wafers" in lot_cols
        lot_query = ("SELECT lot_id,product,release_time,due_date,complete_time"
                     + (",wafers" if wafer_ok else "") + " FROM lots")
        lots = {}
        released = completed_cum = 0
        wip_ids = set()
        for r in db.execute(lot_query):
            wafers = r[5] if wafer_ok else None
            lots[r[0]] = {"product": r[1], "release": r[2], "due": r[3],
                          "complete": r[4], "wafers": wafers}
            if r[2] is not None and r[2] <= t:
                released += 1
                if r[4] is None or r[4] > t:
                    wip_ids.add(r[0])
                else:
                    completed_cum += 1
            if r[4] is not None and p_start < r[4] <= t:
                total["period_completed_lots"] += 1
                total["period_cycles"].append(r[4] - r[2])
                if r[3] is not None:
                    total["period_due_n"] += 1
                    if r[4] <= r[3]:
                        total["period_ontime"] += 1

        def add_wait(acc, step):
            reserved, stock_in, start = step[5], step[4], step[6]
            if reserved is not None and p_start < reserved <= t \
                    and stock_in is not None:
                w = max(0.0, reserved - stock_in)
                acc["dispatch_sum"] += w
                acc["dispatch_n"] += 1
                acc["dispatch_max"] = (w if acc["dispatch_max"] is None
                    else max(acc["dispatch_max"], w))
            if reserved is not None and start is not None \
                    and p_start < reserved <= t:
                acc["reserved_start_sum"] += max(0.0, start - reserved)
                acc["reserved_start_n"] += 1

        steps = db.execute(
            "SELECT lot_id,seq,recipe,equip_id,t_stocker_in,t_reserved,"
            "t_start,t_end"
            " FROM lot_steps WHERE t_stocker_in<=? OR t_reserved<=? OR"
            " t_end<=?", (t, t, t)).fetchall()
        current_seq = {}
        for st in steps:
            if st[0] in wip_ids:
                current_seq[st[0]] = max(current_seq.get(st[0], st[1]), st[1])
        group_wait = {}
        for st in steps:
            lot_id, seq, recipe, eid, stock_in, reserved, start, end = st
            tg = recipe_tg.get(recipe)
            ga = group_acc.get(tg)
            ea = equip_acc.get(eid)
            sentinel_end = (lot_id in wip_ids and
                            current_seq.get(lot_id) == seq and
                            status == "done" and
                            end == duration and t == duration)
            if (not sentinel_end and
                    p_start < (end if end is not None else -1) <= t):
                total["completed_steps"] += 1
                for acc in (ga, ea):
                    if acc is None:
                        continue
                    acc["completed_steps"] += 1
                    if start is not None:
                        acc["process_sum"] += end - start
                        acc["process_n"] += 1
                        if lot_id in lots and lots[lot_id]["wafers"] is not None:
                            acc["wafer_count"] += lots[lot_id]["wafers"]
                            acc["wafer_n"] += 1
                if start is not None:
                    total["process_sum"] += end - start
                    total["process_n"] += 1
                    if lot_id in lots and lots[lot_id]["wafers"] is not None:
                        total["wafer_count"] += lots[lot_id]["wafers"]
                        total["wafer_n"] += 1
            for acc in (total, ga, ea):
                if acc is not None:
                    add_wait(acc, st)
            if lot_id in wip_ids and current_seq.get(lot_id) == seq:
                if reserved is None or reserved > t:
                    if stock_in is not None and stock_in <= t:
                        group_wait.setdefault(tg, set()).add(lot_id)
                        if ga is not None:
                            ga["wip_wait_ids"].add(lot_id)
                elif reserved <= t and (end is None or end > t or
                                        sentinel_end):
                    if eid in equips:
                        e_group = equips[eid]["tool_group"]
                        equip_acc[eid]["wip_equip_ids"].add(lot_id)
                        group_acc[e_group]["wip_equip_ids"].add(lot_id)
                        if start is not None and start <= t:
                            equip_acc[eid]["wip_processing_ids"].add(lot_id)
                            group_acc[e_group]["wip_processing_ids"].add(lot_id)
                        else:
                            equip_acc[eid]["wip_reserved_ids"].add(lot_id)
                            group_acc[e_group]["wip_reserved_ids"].add(lot_id)
                    total["wip_equip_ids"].add(lot_id)

        # Group waiting sets already derive from recipe; only count once.
        total_wait = set().union(*group_wait.values()) if group_wait else set()
        total["wip_wait_ids"] = total_wait
        total["wip_processing_ids"] = set().union(
            *(a["wip_processing_ids"] for a in equip_acc.values())) \
            if equip_acc else set()
        total["wip_reserved_ids"] = total["wip_equip_ids"] - \
            total["wip_processing_ids"]
        # Interval state durations, clipped to P.
        state_rows = db.execute(
            "SELECT equip_id,state,t_start,t_end FROM equip_state"
            " WHERE t_start<? AND (t_end IS NULL OR t_end>?)",
            (t, p_start))
        for eid, state, start, end in state_rows:
            dt = max(0.0, min(t, end if end is not None else t) -
                     max(p_start, start))
            if dt <= 0:
                continue
            total["state_minutes"][state] = \
                total["state_minutes"].get(state, 0.0) + dt
            if eid in equips:
                ea = equip_acc[eid]
                ea["state_minutes"][state] = \
                    ea["state_minutes"].get(state, 0.0) + dt
                ga = group_acc.get(equips[eid]["tool_group"])
                if ga:
                    ga["state_minutes"][state] = \
                        ga["state_minutes"].get(state, 0.0) + dt

        def avg(acc, key, nkey):
            return acc[key] / acc[nkey] if acc[nkey] else None

        def output(acc, n_equip=0):
            sm = acc["state_minutes"]
            busy = sm.get("BUSY", 0.0)
            pm, down = sm.get("PM", 0.0), sm.get("DOWN", 0.0)
            gross_den = period * n_equip
            usable = gross_den - pm - down
            return {
                "completed_steps": acc["completed_steps"],
                "avg_process_time": avg(acc, "process_sum", "process_n"),
                "wafer_count": acc["wafer_count"] if acc["wafer_n"] else None,
                "state_minutes": sm,
                "utilization": busy / usable if usable > 0 else None,
                "utilization_gross": busy / gross_den if gross_den > 0 else None,
                "dispatch_wait_avg": avg(acc, "dispatch_sum", "dispatch_n"),
                "dispatch_wait_max": acc["dispatch_max"],
                "reserved_start_wait_avg": avg(
                    acc, "reserved_start_sum", "reserved_start_n"),
                "wip_wait": len(acc["wip_wait_ids"]),
                "wip_equip": len(acc["wip_equip_ids"]),
                "wip_processing": len(acc["wip_processing_ids"]),
                "wip_reserved": len(acc["wip_reserved_ids"]),
                "wip": len(acc["wip_wait_ids"] | acc["wip_equip_ids"]),
            }

        out_total = output(total, len(equips))
        out_total.update({
            "released_lots": released,
            "completed_lots": completed_cum,
            "completed_lots_period": total["period_completed_lots"],
            "wip": len(wip_ids),
            "wip_other": max(0, len(wip_ids) - len(total["wip_wait_ids"])
                             - len(total["wip_equip_ids"])),
            "avg_cycle_time": (sum(total["period_cycles"]) /
                               len(total["period_cycles"])
                               if total["period_cycles"] else None),
            "on_time_rate": (total["period_ontime"] /
                             total["period_due_n"]
                             if total["period_due_n"] else None),
        })
        groups_out = []
        for tg in groups:
            acc = group_acc[tg]
            item = {"tool_group": tg}
            item.update(output(acc, len(by_group.get(tg, []))))
            groups_out.append(item)
        equips_out = []
        for eid, e in equips.items():
            item = dict(e)
            item.update(output(equip_acc[eid], 1))
            equips_out.append(item)
        return 200, {
            "ok": True, "run": run, "status": status, "t": t,
            "mode": mode, "window": window, "p_start": p_start,
            "progress_time": progress, "duration": duration,
            "total": out_total, "groups": groups_out,
            "equips": equips_out, "has_wafers": wafer_ok,
            "warnings": warnings,
        }
    finally:
        db.close()


# ---------------------------------------------------------- レイアウト編集
def _model_layout(params, cfg, remote_user):
    model = params.get("model", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    cid = params.get("client_id", "")
    if cid and not CLIENT_RE.match(cid):
        return _err(400, "bad_client")
    model_obj, errors, warnings, mdir = _load_model_cfg(model)
    if errors:
        return _err(400, "bad_model_config", detail=errors)
    from fabsim.layout import load_layout_for_model
    lay = load_layout_for_model(model_obj, mdir)
    st = layoutstore.lock_status(mdir, cid or "")
    nodes = []
    for (nt, nid), r in sorted(lay["nodes"].items()):
        n = {"node_type": nt, "node_id": nid, "x": r["x"], "y": r["y"],
             "w": r["w"], "h": r["h"]}
        if nt == "equip":
            e = model_obj.equipments.get(nid)
            n["tool_group"] = e.tool_group if e else ""
            n["area"] = e.area if e else ""
            tg = model_obj.tool_groups.get(n["tool_group"])
            n["shape"] = tg.shape if tg else "rect"
        if nt == "stocker":
            n["area"] = model_obj.stockers[nid].area
        nodes.append(n)
    points = []
    for nt, refs in lay.get("points", {}).items():
        for ref, pts in refs.items():
            for p in pts:
                points.append({"node_type": nt, "node_id":
                               "%s:%d" % (ref, p["k"]),
                               "x": p["x"], "y": p["y"],
                               "w": 0, "h": 0})
    mfiles = modelstore.read_files(mdir)
    return 200, {"ok": True, "nodes": nodes + points,
                 "world": lay["world"],
                 "revision": layoutstore.revision(mdir),
                 "lock": st, "can_edit": _can_edit(cfg, remote_user),
                 "warnings": lay["warnings"],
                 "cl_stations": [{"seq": s, "stocker_id": sid,
                                  "time_to_next": t}
                                 for s, sid, t in model_obj.cl_stations],
                 "structure": {fn: mfiles[fn] for fn in
                               ("equipments.csv", "areas.csv",
                                "stockers.csv", "cleanliner.csv",
                                "qualifications.csv")},
                 "model_revision": modelstore.revision(mdir),
                 "model_lock": modelstore.lock_status(mdir, cid or "")}


def _layout_lock(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    if not _can_edit(cfg, remote_user):
        return _err(403, "forbidden")
    mdir = _mdir(model)
    ok, st = layoutstore.acquire_lock(mdir, cid, remote_user)
    if not ok:
        return _err(409, "locked", owner=st.get("owner"),
                    remaining=st.get("remaining"))
    ok2, st2 = modelstore.acquire_lock(mdir, cid, remote_user)
    if not ok2:
        return _err(409, "model_locked", owner=st2.get("owner"),
                    remaining=st2.get("remaining"))
    return 200, {"ok": True, "expires_in": int(layoutstore.LOCK_TTL)}


def _layout_unlock(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    mdir = _mdir(model)
    layoutstore.release_lock(mdir, cid)
    modelstore.release_lock(mdir, cid)
    return 200, {"ok": True}


def _layout_save(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    base = params.get("base_revision", "")
    nodes = params.get("nodes")
    structure = params.get("structure")
    model_obj, errors, warnings, mdir = _load_model_cfg(model)
    if errors:
        return _err(400, "bad_model_config")
    from fabsim.layout import load_layout_for_model
    world = load_layout_for_model(model_obj, mdir)["world"]
    if structure:
        return _layout_save_struct(
            mdir, nodes, structure, cid, base,
            params.get("base_model_revision", ""), remote_user,
            _can_edit(cfg, remote_user), model_obj, world)
    return layoutstore.save_layout(mdir, nodes, cid, base, remote_user,
                                   _can_edit(cfg, remote_user),
                                   model_obj, world)


def _layout_save_struct(mdir, nodes, structure, cid, base_rev,
                        base_mrev, user, can_edit, model_obj, world):
    """構成(structure)付き保存。レイアウト+モデルの両ロック・両リビジョンが必要。"""
    if not can_edit:
        return _err(403, "forbidden")
    st = layoutstore.lock_status(mdir, cid)
    if not st["mine"]:
        return (_err(409, "locked", owner=st["owner"],
                     remaining=st["remaining"]) if st["locked"]
                else _err(403, "no_lock"))
    st2 = modelstore.lock_status(mdir, cid)
    if not st2["mine"]:
        return (_err(409, "model_locked", owner=st2["owner"],
                     remaining=st2["remaining"]) if st2["locked"]
                else _err(403, "no_model_lock"))
    if layoutstore.revision(mdir) != base_rev:
        return _err(409, "revision", revision=layoutstore.revision(mdir))
    if modelstore.revision(mdir) != base_mrev:
        return _err(409, "model_revision",
                    revision=modelstore.revision(mdir))
    if not isinstance(nodes, list) or not isinstance(structure, dict):
        return _err(400, "invalid")
    cur = modelstore.read_files(mdir)
    merged, merr = modelstore.merge_structure(cur, structure)
    if merr:
        return _err(400, "invalid", detail=merr)
    verr, vwarn = modelstore.validate_files(mdir, merged)
    if verr:
        return _err(400, "validation", detail=verr, warnings=vwarn)
    # ノード集合を「新モデル」に対して検証
    class _M(object):
        pass
    nm = _M()
    eq_col = merged["equipments.csv"]["cols"].index("equip_id")
    ar_col = merged["areas.csv"]["cols"].index("area")
    st_col = merged["stockers.csv"]["cols"].index("stocker_id")
    nm.equipments = {r[eq_col]: None
                     for r in merged["equipments.csv"]["rows"]}
    nm.areas = {r[ar_col]: None for r in merged["areas.csv"]["rows"]}
    nm.stockers = {r[st_col]: None
                   for r in merged["stockers.csv"]["rows"]}
    nerr = layoutstore.validate_nodes(nodes, nm, world)
    if nerr:
        return _err(400, "invalid", detail=nerr)
    layoutstore.archive_history(mdir, user)
    modelstore.archive_history(mdir, user)
    # 一時ファイルに全部書いてから置換
    tmps = []
    try:
        for fn in config_files_of(structure, merged):
            tbl = merged[fn]
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
    layoutstore.write_layout_csv(mdir, nodes, world)
    return 200, {"ok": True, "revision": layoutstore.revision(mdir),
                 "model_revision": modelstore.revision(mdir),
                 "warnings": vwarn}


def config_files_of(structure, merged):
    return [fn for fn in modelstore._STRUCT_FILES +
            ("qualifications.csv",) if fn in merged]


def _layout_auto(params, cfg, remote_user):
    model = params.get("model", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    model_obj, errors, warnings, mdir = _load_model_cfg(model)
    if errors:
        return _err(400, "bad_model_config")
    from fabsim.layout import auto_layout
    nodes, world = auto_layout(model_obj)
    out = [{"node_type": nt, "node_id": nid, "x": r["x"], "y": r["y"],
            "w": r["w"], "h": r["h"]}
           for (nt, nid), r in sorted(nodes.items())]
    return 200, {"ok": True, "nodes": out, "world": world}


# ---------------------------------------------------------- モデル編集
def _mdir(model):
    return os.path.join(data_dir(), "models", model)


def _model_files(params, cfg, remote_user):
    model = params.get("model", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    cid = params.get("client_id", "")
    if cid and not CLIENT_RE.match(cid):
        return _err(400, "bad_client")
    mdir = _mdir(model)
    from fabsim import config
    return 200, {"ok": True, "files": modelstore.read_files(mdir),
                 "spec": {fn: [list(c) for c in config.CSV_SPECS[fn]]
                          for fn in config.EDITABLE_FILES},
                 "sim_spec": {k: list(v) for k, v in
                              config.SIM_SPECS.items()},
                 "revision": modelstore.revision(mdir),
                 "lock": modelstore.lock_status(mdir, cid or ""),
                 "can_edit": _can_edit(cfg, remote_user)}


def _model_validate(params, cfg, remote_user):
    model = params.get("model", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    files = params.get("files")
    structure = params.get("structure")
    mdir = _mdir(model)
    if files is None and structure is not None:
        merged, merr = modelstore.merge_structure(
            modelstore.read_files(mdir), structure)
        if merr:
            return _err(400, "invalid", detail=merr)
        verr, vwarn = modelstore.validate_files(mdir, merged)
        return 200, {"ok": not verr, "errors": verr, "warnings": vwarn}
    errors = modelstore._sanity(files)
    if errors:
        return 400, {"ok": False, "error": "invalid", "detail": errors}
    verr, vwarn = modelstore.validate_files(mdir, files)
    return 200, {"ok": not verr, "errors": verr, "warnings": vwarn}


def _model_lock(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    if not _can_edit(cfg, remote_user):
        return _err(403, "forbidden")
    ok, st = modelstore.acquire_lock(_mdir(model), cid, remote_user)
    if not ok:
        return _err(409, "locked", owner=st.get("owner"),
                    remaining=st.get("remaining"))
    return 200, {"ok": True, "expires_in": int(modelstore.LOCK_TTL)}


def _model_unlock(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    modelstore.release_lock(_mdir(model), cid)
    return 200, {"ok": True}


def _model_save(params, cfg, remote_user):
    model, cid = params.get("model", ""), params.get("client_id", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not CLIENT_RE.match(cid or ""):
        return _err(400, "bad_client")
    return modelstore.save_model(
        _mdir(model), params.get("files"), cid,
        params.get("base_revision", ""), remote_user,
        _can_edit(cfg, remote_user))


def _model_clone(params, cfg, remote_user):
    model, name = params.get("model", ""), params.get("new_name", "")
    if not _valid_model(model):
        return _err(400, "bad_model")
    if not isinstance(name, str) or not MODEL_RE.match(name):
        return _err(400, "bad_name")
    dst = _mdir(name)
    if os.path.exists(dst):
        return _err(409, "exists")
    errors, warnings = modelstore.clone_model(_mdir(model), dst)
    return 200, {"ok": not errors, "errors": errors,
                 "warnings": warnings}


DISPATCH = {
    "models": _models, "runs": _runs, "status": _status,
    "layout": _layout, "window": _window, "kpi": _kpi,
    "summary": _summary, "model_layout": _model_layout,
    "layout_lock": _layout_lock, "layout_unlock": _layout_unlock,
    "layout_save": _layout_save, "layout_auto": _layout_auto,
    "start": _start, "cancel": _cancel,
    "results": _results,
    "model_files": _model_files, "model_validate": _model_validate,
    "model_lock": _model_lock, "model_unlock": _model_unlock,
    "model_save": _model_save, "model_clone": _model_clone,
}


def dispatch(action, params, method="GET", body=b"", content_type="",
             x_requested_with="", remote_user="", raw_body_len=0):
    """戻り値 (http_status, dict)。dict は JSON 化して返す。"""
    try:
        cfg = web_config()
        if action in GET_ACTIONS:
            if method != "GET":
                return _err(400, "method")
            if action in ("model_layout", "model_files"):
                return DISPATCH[action](params, cfg, remote_user)
            return DISPATCH[action](params)
        if action in POST_ACTIONS:
            if method != "POST":
                return _err(400, "method")
            if x_requested_with != "fabsim":
                return _err(400, "csrf_header")
            ct = (content_type or "").split(";")[0].strip()
            if ct not in CT_ALLOWED:
                return _err(400, "content_type")
            if action == "layout_save" and raw_body_len > \
                    layoutstore.MAX_BODY:
                return _err(400, "too_large")
            if action in ("model_save", "model_validate") and \
                    raw_body_len > modelstore.MAX_BODY:
                return _err(400, "too_large")
            if action == "start":
                return _start(params, cfg, remote_user)
            if action == "cancel":
                return _cancel(params, remote_user)
            return DISPATCH[action](params, cfg, remote_user)
        return _err(400, "bad_action")
    except Exception:
        _log_error()
        return _err(500, "internal")


def _log_error():
    try:
        with open(os.path.join(data_dir(), "api_error.log"), "a",
                  encoding="utf-8") as f:
            f.write("[%s]\n%s\n" % (datetime.datetime.now().isoformat(),
                                   traceback.format_exc()))
    except OSError:
        pass


def parse_query(env):
    q = env.get("QUERY_STRING", "")
    return dict(urllib.parse.parse_qsl(q, keep_blank_values=True))


def cgi_main():
    """api.py から呼ばれる CGI エントリ。stdout.buffer に直接書く。"""
    env = os.environ
    method = env.get("REQUEST_METHOD", "GET")
    params = parse_query(env)
    length = int(env.get("CONTENT_LENGTH") or 0)
    body = sys.stdin.buffer.read(length) if length > 0 else b""
    ct = env.get("CONTENT_TYPE", "")
    if method == "POST":
        base = ct.split(";")[0].strip()
        if base == "application/json":
            try:
                params.update(json.loads(body.decode("utf-8") or "{}"))
            except ValueError:
                params = {}
        else:
            params.update(dict(urllib.parse.parse_qsl(
                body.decode("utf-8", "replace"), keep_blank_values=True)))
    action = params.pop("action", "")
    status, obj = dispatch(
        action, params, method=method, body=body, content_type=ct,
        x_requested_with=env.get("HTTP_X_REQUESTED_WITH", ""),
        remote_user=env.get("REMOTE_USER", ""), raw_body_len=length)
    reasons = {200: "OK", 400: "Bad Request", 403: "Forbidden",
               404: "Not Found", 409: "Conflict", 500: "Server Error"}
    out = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    w = sys.stdout.buffer
    w.write(("Status: %d %s\r\n" % (status, reasons.get(status, "OK"))
             ).encode("ascii"))
    w.write(b"Content-Type: application/json; charset=utf-8\r\n")
    if action in ("runs", "status"):
        w.write(b"Cache-Control: no-store\r\n")
    w.write(b"X-Content-Type-Options: nosniff\r\n")
    w.write(b"\r\n")
    w.write(out)
