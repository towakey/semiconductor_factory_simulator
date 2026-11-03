"""レイアウト (設計書 11.3): 自動配置・補完・無視ノード・BOM。"""
import os
import shutil
import tempfile
import unittest

from fabsim import config, layout

from tests.helpers import write_model


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write_model(self.dir)
        self.model, errors, _ = config.load_model(self.dir)
        assert not errors

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_auto_layout_covers_all_nodes(self):
        nodes, world = layout.auto_layout(self.model)
        self.assertIn(("area", "A1"), nodes)
        self.assertIn(("equip", "EQ1"), nodes)
        self.assertIn(("stocker", "S1"), nodes)
        for k, r in nodes.items():
            self.assertGreaterEqual(r["x"], 0)
            self.assertGreaterEqual(r["y"], 0)
            self.assertLessEqual(r["x"] + r["w"], world["w"] + 1e-6)
            self.assertLessEqual(r["y"] + r["h"], world["h"] + 1e-6)

    def test_load_layout_completes_missing_and_ignores_unknown(self):
        with open(os.path.join(self.dir, "layout.csv"), "w",
                  encoding="utf-8") as f:
            f.write("node_type,node_id,x,y,w,h\n")
            f.write("equip,EQ1,100,50,40,30\n")
            f.write("equip,GHOST,1,2,3,4\n")
        out = layout.load_layout_for_model(self.model, self.dir)
        nodes = out["nodes"]
        self.assertEqual(nodes[("equip", "EQ1")]["x"], 100.0)
        self.assertIn(("stocker", "S1"), nodes)   # 未記載は自動配置で補完
        warns = "\n".join(out["warnings"])
        self.assertIn("GHOST", warns)              # 無視ノードの警告
        self.assertIn("補完", warns)

    def test_layout_csv_bom(self):
        path = os.path.join(self.dir, "layout.csv")
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbfnode_type,node_id,x,y,w,h\n"
                    b"world,world,0,0,1200,700\n"
                    b"equip,EQ1,10,10,40,30\n")
        out = layout.load_layout_for_model(self.model, self.dir)
        self.assertEqual(out["world"]["w"], 1200.0)
        self.assertEqual(nodes_x(out), 10.0)


def nodes_x(out):
    return out["nodes"][("equip", "EQ1")]["x"]


if __name__ == "__main__":
    unittest.main()


def test_points_read_and_warn():
    import tempfile, shutil
    dir_ = tempfile.mkdtemp()
    write_model(dir_)
    with open(os.path.join(dir_, "layout.csv"), "w") as f:
        f.write("node_type,node_id,x,y,w,h\n"
                "cl_point,S1:0,11,22,0,0\n"
                "cl_point,ZZ:0,1,1,0,0\n"
                "agv_point,EQ1:0,33,44,0,0\n")
    model, errors, _ = config.load_model(dir_)
    assert not errors
    lay = layout.load_layout_for_model(model, dir_)
    assert lay["points"]["cl_point"]["S1"] == [{"k": 0, "x": 11.0, "y": 22.0}]
    assert lay["points"]["agv_point"]["EQ1"][0]["x"] == 33.0
    assert any("ZZ" in w for w in lay["warnings"])
    shutil.rmtree(dir_, ignore_errors=True)
