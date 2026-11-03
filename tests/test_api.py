"""API 層 (設計書 13.3, 13.4, 9章)。dispatch() を直接呼ぶ。"""
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "web", "cgi-bin"))
import apilib  # noqa: E402

from fabsim import config
from fabsim.dbwriter import DBWriter
from fabsim.engine import Simulator
from fabsim.layout import load_layout_for_model
from fabsim.transport.cleanliner import CleanLiner
from tests.helpers import write_model

RUN = "20260101-000000-a1b2c3"
CID = "a1b2c3d4e5f60718"


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = cls.data
        cls.mdir = os.path.join(cls.data, "models", "m1")
        write_model(cls.mdir)
        model, errors, _ = config.load_model(cls.mdir)
        assert not errors
        rdir = os.path.join(cls.data, "runs", RUN)
        os.makedirs(rdir)
        db = DBWriter(os.path.join(rdir, "result.db"))
        db.open()
        lay = load_layout_for_model(model, cls.mdir)
        cl = CleanLiner(model.cl_stations, 1, 1, 1, 1)
        db.write_static(model, lay["nodes"], lay["world"], cl)
        for k, v in (("schema_version", "1"), ("run_id", RUN),
                     ("status", "running"), ("world_w", lay["world"]["w"]),
                     ("world_h", lay["world"]["h"]),
                     ("duration", model.params.duration),
                     ("cl_carriers", 1), ("cl_slots", 1),
                     ("cl_period", cl.T)):
            db.set_meta(k, v)
        db.commit(0.0)
        sim = Simulator(model, db=db)
        sim.run()
        db.finish(sim.stats.summary(sim))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        os.environ.pop("FABSIM_DATA_DIR", None)

    def d(self, action, **kw):
        return apilib.dispatch(action, kw.pop("params", {}), **kw)

    def test_models_runs(self):
        st, r = self.d("models")
        self.assertEqual(r["models"], ["m1"])
        st, r = self.d("runs")
        self.assertTrue(any(x["run_id"] == RUN for x in r["runs"]))

    def test_bad_inputs_rejected(self):
        st, _ = self.d("layout", params={"run": "../etc"})
        self.assertEqual(st, 400)
        st, _ = self.d("layout", params={"run": "not-a-run"})
        self.assertEqual(st, 400)
        st, _ = self.d("model_layout", params={"model": "../x"})
        self.assertEqual(st, 400)
        st, _ = self.d("model_layout", params={"model": "a/b"})
        self.assertEqual(st, 400)
        st, _ = self.d("window", params={"run": RUN, "t0": "x", "t1": "1"})
        self.assertEqual(st, 400)
        st, _ = self.d("window", params={"run": RUN, "t0": "10", "t1": "5"})
        self.assertEqual(st, 400)
        st, _ = self.d("summary", params={"run": RUN + "x"})
        self.assertEqual(st, 400)
        st, _ = self.d("nope")
        self.assertEqual(st, 400)

    def test_window_matches_manual_and_truncates(self):
        st, r = self.d("window", params={"run": RUN, "t0": "0", "t1": "500"})
        self.assertEqual(st, 200)
        import sqlite3
        c = sqlite3.connect(os.path.join(self.data, "runs", RUN,
                                         "result.db"))
        n = c.execute("SELECT COUNT(*) FROM equip_state"
                      " WHERE t_start<=500 AND (t_end IS NULL OR t_end>0)"
                      ).fetchone()[0]
        self.assertEqual(len(r["equip_state"]["rows"]), n)
        self.assertFalse(r["truncated"])
        old = apilib.MAX_ROWS
        try:
            apilib.MAX_ROWS = 5
            st, r = self.d("window",
                           params={"run": RUN, "t0": "0", "t1": "10000"})
            self.assertTrue(r["truncated"])
        finally:
            apilib.MAX_ROWS = old

    def test_window_too_wide(self):
        st, r = self.d("window",
                       params={"run": RUN, "t0": "0", "t1": "99999"})
        self.assertTrue(r["truncated"])

    def test_post_only_and_csrf(self):
        st, _ = self.d("start", method="GET",
                       params={"model": "m1", "rule": "FIFO", "seed": "1",
                               "duration": "60"})
        self.assertEqual(st, 400)                       # GET は拒否
        st, _ = self.d("start", method="POST",
                       params={"model": "m1", "rule": "FIFO", "seed": "1",
                               "duration": "60"})
        self.assertEqual(st, 400)                       # XRW ヘッダ無し
        st, _ = self.d("start", method="POST",
                       params={"model": "m1", "rule": "FIFO", "seed": "1",
                               "duration": "60"},
                       x_requested_with="fabsim",
                       content_type="text/plain")
        self.assertEqual(st, 400)                       # Content-Type 制限

    def test_start_cancel_cycle(self):
        st, r = self.d("start", method="POST",
                       params={"model": "m1", "rule": "FIFO", "seed": "1",
                               "duration": "60"},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 200)
        rid = r["run_id"]
        st, r = self.d("cancel", method="POST", params={"run": rid},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertIn(st, (200, 409))   # 早く終われば done の場合あり
        st, r = self.d("status", params={"run": rid})
        self.assertIn(r["status"], ("cancelled", "done", "running"))

    def test_kpi_summary_layout(self):
        st, r = self.d("summary", params={"run": RUN})
        self.assertTrue(r["ok"])
        self.assertIn("completed_lots", r["summary"])
        st, r = self.d("layout", params={"run": RUN})
        self.assertTrue(r["areas"])
        self.assertTrue(r["equipments"])
        st, r = self.d("kpi", params={"run": RUN, "t": "120"})
        self.assertTrue(r["ok"])

    def test_layout_edit_api(self):
        st, r = self.d("model_layout",
                       params={"model": "m1", "client_id": CID})
        self.assertTrue(r["ok"])
        self.assertIn("nodes", r)
        rev = r["revision"]
        st, r = self.d("layout_lock", method="POST",
                       params={"model": "m1", "client_id": CID},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u1")
        self.assertEqual(st, 200)
        st, r = self.d("layout_lock", method="POST",
                       params={"model": "m1", "client_id": "ffffffffffffffff"},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u2")
        self.assertEqual(st, 409)                      # 他者ロック中
        nodes = [n for n in apilib.dispatch(
            "model_layout", {"model": "m1", "client_id": CID})[1]["nodes"]]
        st, r = self.d("layout_save", method="POST",
                       params={"model": "m1", "client_id": "ffffffffffffffff",
                               "base_revision": rev, "nodes": nodes},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u2")
        self.assertEqual(st, 409)                      # 他者ロック中の保存
        st, r = self.d("layout_save", method="POST",
                       params={"model": "m1", "client_id": CID,
                               "base_revision": "0" * 16, "nodes": nodes},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u1")
        self.assertEqual(st, 409)                      # リビジョン不一致
        st, r = self.d("layout_save", method="POST",
                       params={"model": "m1", "client_id": CID,
                               "base_revision": rev, "nodes": nodes},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u1")
        self.assertEqual(st, 200)
        st, r = self.d("layout_unlock", method="POST",
                       params={"model": "m1", "client_id": CID},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 200)
        st, r = self.d("layout_save", method="POST",
                       params={"model": "m1", "client_id": CID,
                               "base_revision": rev, "nodes": nodes},
                       x_requested_with="fabsim",
                       content_type="application/json",
                       remote_user="u1")
        self.assertEqual(st, 403)                      # ロック無し保存


if __name__ == "__main__":
    unittest.main()
