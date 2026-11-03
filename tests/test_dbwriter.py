"""SQLite 出力 (設計書 11章, 9章): 区間クローズ・連続性・集計一致。"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from fabsim import config
from fabsim.dbwriter import DBWriter
from fabsim.engine import Simulator
from fabsim.layout import load_layout_for_model
from fabsim.transport.cleanliner import CleanLiner

from tests.helpers import write_model


def _open_db_for(sim_db_path, model):
    db = DBWriter(sim_db_path)
    db.open()
    lay = load_layout_for_model(model, model.source_dir)
    cl = CleanLiner(model.cl_stations, model.params.cl_carriers,
                    model.params.cl_slots, model.params.cl_load_time,
                    model.params.cl_unload_time)
    db.write_static(model, lay["nodes"], lay["world"], cl)
    db.set_meta("schema_version", "1")
    db.set_meta("run_id", "test")
    db.set_meta("status", "running")
    db.set_meta("world_w", lay["world"]["w"])
    db.set_meta("world_h", lay["world"]["h"])
    db.commit(0.0)
    return db


class DBWriterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write_model(self.dir)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.model = model
        self.db_path = os.path.join(self.dir, "result.db")
        self.db = _open_db_for(self.db_path, model)
        self.sim = Simulator(model, db=self.db)
        self.sim.run()
        self.summary = self.sim.stats.summary(self.sim)
        self.db.finish(self.summary)
        self.c = sqlite3.connect(self.db_path)

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_all_intervals_closed(self):
        """終了時点で t_end が NULL の行が残らない。"""
        for t in ("equip_state", "stocker_lots"):
            n = self.c.execute(
                "SELECT COUNT(*) FROM %s WHERE t_end IS NULL" % t
            ).fetchone()[0]
            self.assertEqual(n, 0, t)

    def test_equip_state_contiguous(self):
        """同一設備の equip_state 区間は重ならず隙間なく並ぶ。"""
        dur = self.model.params.duration
        for (eid,) in self.c.execute(
                "SELECT DISTINCT equip_id FROM equip_state"):
            rows = self.c.execute(
                "SELECT t_start,t_end FROM equip_state"
                " WHERE equip_id=? ORDER BY t_start", (eid,)).fetchall()
            self.assertAlmostEqual(rows[0][0], 0.0, places=6)
            prev = rows[0][1]
            for s, e in rows[1:]:
                self.assertAlmostEqual(s, prev, places=6,
                                       msg="%s gap/overlap" % eid)
                prev = e
            self.assertAlmostEqual(prev, dur, places=6)

    def test_db_aggregates_match_summary(self):
        """DB の集計値が summary と一致する。"""
        n_done = self.c.execute(
            "SELECT COUNT(*) FROM lots WHERE complete_time IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(n_done, self.summary["completed_lots"])
        # BUSY 時間の合計 → 稼働率が一致
        busy = self.c.execute(
            "SELECT SUM(t_end-t_start) FROM equip_state WHERE state='BUSY'"
        ).fetchone()[0] or 0.0
        dur = self.model.params.duration
        n_eq = self.c.execute("SELECT COUNT(*) FROM equipments").fetchone()[0]
        tg_util = self.summary["utilization"]["TG1"]["util_total"]
        self.assertAlmostEqual(busy / (dur * n_eq), tg_util, places=4)

    def test_meta(self):
        meta = dict(self.c.execute("SELECT key,value FROM meta"))
        self.assertEqual(meta["status"], "done")
        self.assertEqual(meta["schema_version"], "1")
        self.assertIn("summary_json", meta)
        self.assertIn("world_w", meta)

    def test_journal_delete_after_finish(self):
        """正常終了後は WAL ではなく単一ファイルになる。"""
        self.assertFalse(os.path.exists(self.db_path + "-wal"))


if __name__ == "__main__":
    unittest.main()


def test_points_tables(tmpdir=None):
    import shutil, tempfile, sqlite3
    from fabsim.layout import load_layout_for_model
    dir_ = tempfile.mkdtemp()
    mdir = os.path.join(dir_, "model")
    write_model(mdir)
    with open(os.path.join(mdir, "layout.csv"), "w") as f:
        f.write("node_type,node_id,x,y,w,h\n"
                "cl_point,S1:0,11,22,0,0\nagv_point,EQ1:0,33,44,0,0\n")
    model, errors, _ = config.load_model(mdir)
    assert not errors
    db = DBWriter(os.path.join(dir_, "r.db"))
    db.open()
    lay = load_layout_for_model(model, mdir)
    db.write_static(model, lay["nodes"], lay["world"], None,
                    lay.get("points"))
    c = sqlite3.connect(os.path.join(dir_, "r.db"))
    assert c.execute("SELECT x,y FROM cl_points").fetchall() == [(11.0, 22.0)]
    assert c.execute("SELECT x,y FROM agv_points").fetchall() == [(33.0, 44.0)]
    c.close()
    shutil.rmtree(dir_, ignore_errors=True)
