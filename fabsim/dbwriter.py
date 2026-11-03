"""SQLite 出力 (設計書 11章)。区間ログ・WAL・定期 COMMIT・終了時クローズ。"""
import json
import os
import sqlite3

SCHEMA_VERSION = "1"

SCHEMA = [
    """CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE areas(area TEXT PRIMARY KEY, agv_count INTEGER,
                         x REAL, y REAL, w REAL, h REAL)""",
    """CREATE TABLE equipments(equip_id TEXT PRIMARY KEY, area TEXT,
                              tool_group TEXT, x REAL, y REAL,
                              shape TEXT, w REAL, h REAL,
                              ports INTEGER)""",
    """CREATE TABLE equip_port_lots(equip_id TEXT, lot_id TEXT,
                                 t_start REAL, t_end REAL)""",
    """CREATE INDEX idx_epl_t ON equip_port_lots(t_start, t_end)""",
    """CREATE TABLE stockers(stocker_id TEXT PRIMARY KEY, area TEXT,
                            capacity INTEGER, x REAL, y REAL,
                            w REAL, h REAL)""",
    """CREATE TABLE cl_stations(seq INTEGER, stocker_id TEXT,
                               base_time REAL, time_to_next REAL)""",
    """CREATE TABLE cl_points(from_stocker TEXT, k INTEGER,
                            x REAL, y REAL)""",
    """CREATE TABLE agv_points(equip_id TEXT, k INTEGER,
                             x REAL, y REAL)""",
    """CREATE TABLE lots(lot_id TEXT PRIMARY KEY, product TEXT,
                        priority INTEGER, release_time REAL, due_date REAL,
                        complete_time REAL)""",
    """CREATE TABLE lot_steps(lot_id TEXT, seq INTEGER, step_name TEXT,
                             recipe TEXT, equip_id TEXT,
                             t_stocker_in REAL, t_reserved REAL,
                             t_start REAL, t_end REAL)""",
    """CREATE TABLE equip_state(equip_id TEXT, state TEXT, lot_id TEXT,
                               recipe TEXT, t_start REAL, t_end REAL)""",
    """CREATE TABLE stocker_lots(stocker_id TEXT, lot_id TEXT,
                                t_start REAL, t_end REAL)""",
    """CREATE TABLE agv_moves(agv_id TEXT, area TEXT, phase TEXT,
                             from_node TEXT, to_node TEXT, lot_id TEXT,
                             t_start REAL, t_end REAL)""",
    """CREATE TABLE cl_rides(lot_id TEXT, carrier_id INTEGER, slot INTEGER,
                            from_station TEXT, to_station TEXT,
                            t_request REAL, t_load REAL, t_unload REAL)""",
    """CREATE TABLE kpi_ts(t REAL, metric TEXT, key TEXT, value REAL)""",
    """CREATE TABLE events(t REAL, type TEXT, obj_id TEXT, lot_id TEXT,
                          detail TEXT)""",
    "CREATE INDEX idx_es_t ON equip_state(t_start, t_end)",
    "CREATE INDEX idx_agv_t ON agv_moves(t_start, t_end)",
    "CREATE INDEX idx_clr_t ON cl_rides(t_load, t_unload)",
    "CREATE INDEX idx_sl_t ON stocker_lots(t_start, t_end)",
    "CREATE INDEX idx_ls_lot ON lot_steps(lot_id)",
    "CREATE INDEX idx_kpi ON kpi_ts(metric, t)",
]

# バッファ insert するテーブルと列
PLAIN = {
    "agv_moves": ("agv_id", "area", "phase", "from_node", "to_node",
                  "lot_id", "t_start", "t_end"),
    "cl_rides": ("lot_id", "carrier_id", "slot", "from_station",
                 "to_station", "t_request", "t_load", "t_unload"),
    "kpi_ts": ("t", "metric", "key", "value"),
    "events": ("t", "type", "obj_id", "lot_id", "detail"),
}

# 開区間を持つライブテーブル: key列 / 全列
LIVE = {
    "lots": (("lot_id",),
             ("lot_id", "product", "priority", "release_time", "due_date",
              "complete_time")),
    "lot_steps": (("lot_id", "seq"),
                  ("lot_id", "seq", "step_name", "recipe", "equip_id",
                   "t_stocker_in", "t_reserved", "t_start", "t_end")),
    "stocker_lots": (("stocker_id", "lot_id", "t_start"),
                     ("stocker_id", "lot_id", "t_start", "t_end")),
    "equip_state": (("equip_id", "t_start"),
                    ("equip_id", "state", "lot_id", "recipe",
                     "t_start", "t_end")),
    "equip_port_lots": (("equip_id", "lot_id", "t_start"),
                        ("equip_id", "lot_id", "t_start", "t_end")),
}


class DBWriter(object):
    def __init__(self, path, trace=False):
        self.path = path
        self.trace = trace
        self.conn = None
        self.buf = {t: [] for t in PLAIN}
        self.live = {t: {} for t in LIVE}
        self._es_open = {}    # equip_id -> live key
        self._sl_open = {}    # (stocker_id, lot_id) -> live key
        self._epl_open = {}   # (equip_id, lot_id) -> live key

    def open(self):
        d = os.path.dirname(self.path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        for sql in SCHEMA:
            self.conn.execute(sql)
        self.conn.commit()

    # ---- meta / 静的テーブル ----
    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)",
            (key, str(value)))

    def write_static(self, model, nodes, world, cl, points=None):
        cur = self.conn.cursor()
        for a in sorted(model.areas.values(), key=lambda x: x.name):
            r = nodes.get(("area", a.name), {})
            cur.execute(
                "INSERT INTO areas VALUES(?,?,?,?,?,?)",
                (a.name, a.agv_count, r.get("x"), r.get("y"),
                 r.get("w"), r.get("h")))
        for e in sorted(model.equipments.values(), key=lambda x: x.equip_id):
            r = nodes.get(("equip", e.equip_id), {})
            cur.execute(
                "INSERT INTO equipments VALUES(?,?,?,?,?,?,?,?,?)",
                (e.equip_id, e.area, e.tool_group,
                 r.get("x"), r.get("y"),
                 getattr(model.tool_groups.get(e.tool_group), "shape",
                         "rect") if model.tool_groups.get(e.tool_group)
                 else "rect",
                 r.get("w"), r.get("h"), e.ports))
        for s in sorted(model.stockers.values(), key=lambda x: x.stocker_id):
            r = nodes.get(("stocker", s.stocker_id), {})
            cur.execute(
                "INSERT INTO stockers VALUES(?,?,?,?,?,?,?)",
                (s.stocker_id, s.area, s.capacity,
                 r.get("x"), r.get("y"), r.get("w"), r.get("h")))
        for i, (seq, sid, ttn) in enumerate(model.cl_stations):
            cur.execute("INSERT INTO cl_stations VALUES(?,?,?,?)",
                        (seq, sid, round(cl.base[i], 6) if cl else None, ttn))
        for ref, pts in ((points or {}).get("cl_point") or {}).items():
            for p in pts:
                cur.execute("INSERT INTO cl_points VALUES(?,?,?,?)",
                            (ref, p["k"], p["x"], p["y"]))
        for ref, pts in ((points or {}).get("agv_point") or {}).items():
            for p in pts:
                cur.execute("INSERT INTO agv_points VALUES(?,?,?,?)",
                            (ref, p["k"], p["x"], p["y"]))
        cur.close()

    # ---- バッファ系 ----
    def add(self, table, row):
        self.buf[table].append(row)

    # ---- live 系 ----
    def _live_put(self, table, key, row):
        row["_ins"] = False
        row["_dirty"] = True
        self.live[table][key] = row

    def _live_touch(self, table, key):
        row = self.live[table].get(key)
        if row is not None:
            row["_dirty"] = True
        return row

    def lot_insert(self, lot):
        self._live_put("lots", (lot.lot_id,), {
            "lot_id": lot.lot_id, "product": lot.product.name,
            "priority": lot.priority,
            "release_time": round(lot.release_time, 6),
            "due_date": round(lot.due_date, 6), "complete_time": None})

    def lot_complete(self, lot_id, t):
        row = self._live_touch("lots", (lot_id,))
        if row is not None:
            row["complete_time"] = round(t, 6)

    def lot_step_open(self, lot_id, seq, step_name, recipe, t_stocker_in):
        self._live_put("lot_steps", (lot_id, seq), {
            "lot_id": lot_id, "seq": seq, "step_name": step_name,
            "recipe": recipe, "equip_id": None,
            "t_stocker_in": round(t_stocker_in, 6),
            "t_reserved": None, "t_start": None, "t_end": None})

    def lot_step_update(self, lot_id, seq, **fields):
        row = self._live_touch("lot_steps", (lot_id, seq))
        if row is None:
            return
        for k, v in fields.items():
            row[k] = round(v, 6) if isinstance(v, float) else v

    def lot_steps_close_all(self, duration):
        for row in self.live["lot_steps"].values():
            if row["t_end"] is None:
                row["t_end"] = round(duration, 6)
                row["_dirty"] = True

    def equip_state_open(self, eid, state, lot_id, recipe, t_start):
        key = (eid, round(t_start, 6))
        self._live_put("equip_state", key, {
            "equip_id": eid, "state": state, "lot_id": lot_id,
            "recipe": recipe, "t_start": round(t_start, 6), "t_end": None})
        self._es_open[eid] = key

    def equip_state_close(self, eid, t_end):
        key = self._es_open.pop(eid, None)
        row = self._live_touch("equip_state", key) if key else None
        if row is not None:
            row["t_end"] = round(t_end, 6)

    def equip_state_rewrite(self, eid, state, lot_id, recipe):
        key = self._es_open.get(eid)
        row = self._live_touch("equip_state", key) if key else None
        if row is not None:
            row["state"] = state
            row["lot_id"] = lot_id
            row["recipe"] = recipe

    def stocker_lot_open(self, sid, lot_id, t):
        key = (sid, lot_id, round(t, 6))
        self._live_put("stocker_lots", key, {
            "stocker_id": sid, "lot_id": lot_id,
            "t_start": round(t, 6), "t_end": None})
        self._sl_open[(sid, lot_id)] = key

    def stocker_lot_close(self, sid, lot_id, t_end):
        key = self._sl_open.pop((sid, lot_id), None)
        row = self._live_touch("stocker_lots", key) if key else None
        if row is not None:
            row["t_end"] = round(t_end, 6)

    def equip_port_open(self, eid, lot_id, t):
        key = (eid, lot_id, round(t, 6))
        self._live_put("equip_port_lots", key, {
            "equip_id": eid, "lot_id": lot_id,
            "t_start": round(t, 6), "t_end": None})
        self._epl_open[(eid, lot_id)] = key

    def equip_port_close(self, eid, lot_id, t_end):
        key = self._epl_open.pop((eid, lot_id), None)
        row = self._live_touch("equip_port_lots", key) if key else None
        if row is not None:
            row["t_end"] = round(t_end, 6)

    def equip_port_lots_close_all(self, duration):
        for k2, key in list(self._epl_open.items()):
            row = self._live_touch("equip_port_lots", key)
            if row is not None:
                row["t_end"] = round(duration, 6)
            del self._epl_open[k2]

    def stocker_lots_close_all(self, sid, duration):
        for (s2, lot_id), key in list(self._sl_open.items()):
            if s2 == sid:
                row = self._live_touch("stocker_lots", key)
                if row is not None:
                    row["t_end"] = round(duration, 6)
                del self._sl_open[(s2, lot_id)]

    # ---- flush / commit / close ----
    def flush(self):
        cur = self.conn.cursor()
        for table, cols in PLAIN.items():
            rows = self.buf[table]
            if rows:
                cur.executemany(
                    "INSERT INTO %s VALUES(%s)" % (table, ",".join("?" * len(cols))),
                    rows)
                del rows[:]
        for table, (keycols, cols) in LIVE.items():
            upd_cols = [c for c in cols if c not in keycols]
            for key, row in self.live[table].items():
                if not row["_ins"]:
                    cur.execute(
                        "INSERT INTO %s(%s) VALUES(%s)"
                        % (table, ",".join(cols), ",".join("?" * len(cols))),
                        tuple(row[c] for c in cols))
                    row["_ins"] = True
                    row["_dirty"] = False
                elif row["_dirty"]:
                    cur.execute(
                        "UPDATE %s SET %s WHERE %s"
                        % (table,
                           ",".join("%s=?" % c for c in upd_cols),
                           " AND ".join("%s=?" % c for c in keycols)),
                        tuple(row[c] for c in upd_cols) +
                        tuple(row[c] for c in keycols))
                    row["_dirty"] = False
        cur.close()

    def commit(self, t):
        self.flush()
        self.set_meta("progress_time", round(t, 3))
        self.conn.commit()

    def finish(self, summary):
        self.flush()
        self.set_meta("status", "done")
        self.set_meta("progress_time", summary.get("duration"))
        self.set_meta("summary_json", json.dumps(summary,
                                                 ensure_ascii=False))
        self.conn.commit()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.close()
        self.conn = None

    def fail(self, message):
        if self.conn is None:
            return
        try:
            self.flush()
            self.set_meta("status", "failed")
            self.set_meta("error", str(message)[:2000])
            self.conn.commit()
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("PRAGMA journal_mode=DELETE")
            self.conn.close()
        except sqlite3.Error:
            pass
        self.conn = None
