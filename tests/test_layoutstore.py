"""layoutstore (設計書 12.5.5, 12.5.6, 9章)。"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "web", "cgi-bin"))
import layoutstore  # noqa: E402

from fabsim import config, layout
from tests.helpers import write_model


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mdir = os.path.join(self.dir, "model")
        write_model(self.mdir)
        self.model, errors, _ = config.load_model(self.mdir)
        assert not errors
        self.world = layout.load_layout_for_model(self.model, self.mdir)[
            "world"]
        self.cid = "a1b2c3d4e5f60718"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _nodes(self):
        nodes, _ = layout.auto_layout(self.model)
        return [{"node_type": k[0], "node_id": k[1], "x": r["x"],
                 "y": r["y"], "w": r["w"], "h": r["h"]}
                for k, r in nodes.items()]

    def _lock(self, cid=None):
        ok, st = layoutstore.acquire_lock(self.mdir, cid or self.cid, "u1")
        assert ok, st

    def _save(self, nodes=None, cid=None, base=None):
        return layoutstore.save_layout(
            self.mdir, nodes if nodes is not None else self._nodes(),
            cid or self.cid, base if base is not None else
            layoutstore.revision(self.mdir), "u1", True,
            self.model, self.world)

    # ① ノード集合の過不足・重複・NaN・範囲外・巨大本文 → 400
    def test_validate_nodes(self):
        self._lock()
        n = self._nodes()
        status, r = self._save(nodes=n[:-1])
        self.assertEqual(status, 400)                       # 欠落
        status, r = self._save(nodes=n + [dict(n[0])])
        self.assertEqual(status, 400)                       # 重複
        bad = self._nodes()
        bad[0]["x"] = float("nan")
        status, r = self._save(nodes=bad)
        self.assertEqual(status, 400)                       # NaN
        bad = self._nodes()
        bad[0]["x"] = self.world["w"]
        status, r = self._save(nodes=bad)
        self.assertEqual(status, 400)                       # 範囲外
        bad = self._nodes()
        bad.append({"node_type": "equip", "node_id": "ZZ",
                    "x": 0, "y": 0, "w": 10, "h": 10})
        status, r = self._save(nodes=bad)
        self.assertEqual(status, 400)                       # 未知ID

    # ② 他クライアントのロック保持中は 409、期限切れなら取得可
    def test_lock_conflict_and_expire(self):
        self._lock("c1c1c1c1c1c1c1c1")
        ok, st = layoutstore.acquire_lock(self.mdir, self.cid, "u2")
        self.assertFalse(ok)
        self.assertIn("owner", st)
        # 期限切れにする
        p = os.path.join(self.mdir, ".layout.lock")
        with open(p) as f:
            lk = json.load(f)
        lk["expires"] = 0
        with open(p, "w") as f:
            json.dump(lk, f)
        ok, st = layoutstore.acquire_lock(self.mdir, self.cid, "u2")
        self.assertTrue(ok)

    # ③ ロックなし保存は 403、base_revision 不一致は 409 でファイル不変
    def test_save_without_lock_and_revision(self):
        status, r = self._save()
        self.assertEqual(status, 403)
        self._lock()
        self._save()
        before = layoutstore.revision(self.mdir)
        status, r = self._save(base="0000000000000000")
        self.assertEqual(status, 409)
        self.assertEqual(layoutstore.revision(self.mdir), before)

    # ④ 履歴が増え、21世代目で最古が削除される
    def test_history_generations(self):
        self._lock()
        for i in range(21):
            nodes = self._nodes()
            nodes[0]["x"] += 0.01 * i
            status, r = layoutstore.save_layout(
                self.mdir, nodes, self.cid,
                layoutstore.revision(self.mdir), "u1", True,
                self.model, self.world)
            self.assertEqual(status, 200)
        hist = os.path.join(self.mdir, "layout_history")
        files = [f for f in os.listdir(hist) if f.endswith(".csv")]
        self.assertEqual(len(files), 20)

    # ⑤ 保存が原子的(途中で例外でも元の layout.csv が残る)
    def test_atomic_save(self):
        self._lock()
        status, r = self._save()
        self.assertEqual(status, 200)
        before = open(os.path.join(self.mdir, "layout.csv"), "rb").read()
        with mock.patch("layoutstore.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                layoutstore.write_layout_csv(
                    self.mdir, self._nodes(), self.world)
        after = open(os.path.join(self.mdir, "layout.csv"), "rb").read()
        self.assertEqual(before, after)

    # ⑥ 一部のみ/余分なIDの layout.csv は補完・無視される
    def test_partial_and_extra_ids(self):
        with open(os.path.join(self.mdir, "layout.csv"), "w") as f:
            f.write("node_type,node_id,x,y,w,h\n")
            f.write("equip,EQ1,111,22,40,30\n")
            f.write("area,ZZ,1,1,10,10\n")
        out = layout.load_layout_for_model(self.model, self.mdir)
        self.assertEqual(out["nodes"][("equip", "EQ1")]["x"], 111.0)
        self.assertIn(("area", "A1"), out["nodes"])
        w = "\n".join(out["warnings"])
        self.assertIn("ZZ", w)

    # ⑦ BOM 付き layout.csv を読める
    def test_bom(self):
        with open(os.path.join(self.mdir, "layout.csv"), "wb") as f:
            f.write(b"\xef\xbb\xbfnode_type,node_id,x,y,w,h\n"
                    b"equip,EQ1,7,8,40,30\n")
        out = layout.load_layout_for_model(self.model, self.mdir)
        self.assertEqual(out["nodes"][("equip", "EQ1")]["x"], 7.0)
        self.assertNotEqual(layoutstore.revision(self.mdir), "none")


if __name__ == "__main__":
    unittest.main()


class PointTest(unittest.TestCase):
    """折れ点 (cl_point/agv_point) の保存・検証。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mdir = os.path.join(self.dir, "model")
        write_model(self.mdir)
        self.model, errors, _ = config.load_model(self.mdir)
        assert not errors
        self.world = layout.load_layout_for_model(self.model, self.mdir)[
            "world"]
        self.cid = "a1b2c3d4e5f60718"
        layoutstore.acquire_lock(self.mdir, self.cid, "u1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _nodes(self, extra=None):
        nodes, _ = layout.auto_layout(self.model)
        out = [{"node_type": k[0], "node_id": k[1], "x": r["x"],
                "y": r["y"], "w": r["w"], "h": r["h"]}
               for k, r in nodes.items()]
        return out + list(extra or [])

    def _save(self, nodes):
        return layoutstore.save_layout(
            self.mdir, nodes, self.cid, layoutstore.revision(self.mdir),
            "u1", True, self.model, self.world)

    def test_save_points(self):
        nodes = self._nodes([
            {"node_type": "cl_point", "node_id": "S1:0",
             "x": 100, "y": 200, "w": 0, "h": 0},
            {"node_type": "agv_point", "node_id": "EQ1:0",
             "x": 50, "y": 60, "w": 0, "h": 0}])
        status, r = self._save(nodes)
        self.assertEqual(status, 200)
        lay = layout.load_layout_for_model(self.model, self.mdir)
        self.assertEqual(lay["points"]["cl_point"]["S1"],
                         [{"k": 0, "x": 100.0, "y": 200.0}])
        self.assertEqual(lay["points"]["agv_point"]["EQ1"][0]["x"], 50.0)

    def test_bad_ref_400(self):
        nodes = self._nodes([
            {"node_type": "cl_point", "node_id": "ZZ:0",
             "x": 1, "y": 2, "w": 0, "h": 0}])
        self.assertEqual(self._save(nodes)[0], 400)
        nodes = self._nodes([
            {"node_type": "cl_point", "node_id": "S1",
             "x": 1, "y": 2, "w": 0, "h": 0}])        # :k 無し
        self.assertEqual(self._save(nodes)[0], 400)
        nodes = self._nodes([
            {"node_type": "cl_point", "node_id": "S1:0",
             "x": 99999, "y": 2, "w": 0, "h": 0}])    # 範囲外
        self.assertEqual(self._save(nodes)[0], 400)

    def test_point_limit(self):
        extra = [{"node_type": "agv_point", "node_id": "EQ1:%d" % i,
                  "x": 1, "y": 2, "w": 0, "h": 0} for i in range(201)]
        self.assertEqual(self._save(self._nodes(extra))[0], 400)

    def test_point_bom_and_order(self):
        with open(os.path.join(self.mdir, "layout.csv"), "wb") as f:
            f.write(b"\xef\xbb\xbfnode_type,node_id,x,y,w,h\n"
                    b"cl_point,S1:1,3,4,0,0\ncl_point,S1:0,1,2,0,0\n")
        lay = layout.load_layout_for_model(self.model, self.mdir)
        self.assertEqual([p["x"] for p in lay["points"]["cl_point"]["S1"]],
                         [1.0, 3.0])          # k 順に並ぶ
