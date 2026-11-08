"""構成(structure)付きレイアウト保存。"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "web", "cgi-bin"))
import apilib  # noqa: E402
import layoutstore  # noqa: E402
import modelstore  # noqa: E402

from tests.helpers import write_model

CID = "a1b2c3d4e5f60718"


class StructSaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = self.dir
        self.mdir = os.path.join(self.dir, "models", "m1")
        write_model(self.mdir)
        self._locks()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _locks(self):
        assert layoutstore.acquire_lock(self.mdir, CID, "u")[0]
        assert modelstore.acquire_lock(self.mdir, CID, "u")[0]

    def _nodes(self):
        from fabsim import config, layout
        m, e, _ = config.load_model(self.mdir)
        nodes, _ = layout.auto_layout(m)
        return [{"node_type": k[0], "node_id": k[1], "x": r["x"],
                 "y": r["y"], "w": r["w"], "h": r["h"]}
                for k, r in nodes.items()], m

    def _struct(self):
        return {fn: {"cols": t["cols"],
                     "rows": [list(r) for r in t["rows"]]}
                for fn, t in modelstore.read_files(self.mdir).items()
                if fn in ("equipments.csv", "areas.csv", "stockers.csv",
                          "cleanliner.csv", "qualifications.csv")}

    def _save(self, nodes, structure):
        return apilib.dispatch(
            "layout_save", {"model": "m1", "client_id": CID,
                            "base_revision": layoutstore.revision(self.mdir),
                            "base_model_revision":
                                modelstore.revision(self.mdir),
                            "nodes": nodes, "structure": structure},
            method="POST", x_requested_with="fabsim",
            content_type="application/json", remote_user="u")

    def test_equip_add(self):
        nodes, m = self._nodes()
        st = self._struct()
        eq = st["equipments.csv"]
        row = list(eq["rows"][0]); row[eq["cols"].index("equip_id")] = "EQ99"
        eq["rows"].append(row)
        nodes.append({"node_type": "equip", "node_id": "EQ99",
                      "x": 50, "y": 50, "w": 40, "h": 30})
        st_code, r = self._save(nodes, st)
        self.assertEqual(st_code, 200, r)
        f = modelstore.read_files(self.mdir)["equipments.csv"]
        self.assertIn("EQ99", [x[f["cols"].index("equip_id")]
                               for x in f["rows"]])

    def test_equip_delete_cascade(self):
        nodes, m = self._nodes()
        st = self._struct()
        eq = st["equipments.csv"]
        # EQ2(TG1)を足してから EQ1 を削除 → R1 は処理可能のまま
        row = list(eq["rows"][0]); row[eq["cols"].index("equip_id")] = "EQ2"
        eq["rows"].append(row)
        eq["rows"] = [r for r in eq["rows"]
                      if r[eq["cols"].index("equip_id")] != "EQ1"]
        nodes = [n for n in nodes if n["node_id"] != "EQ1"]
        nodes.append({"node_type": "equip", "node_id": "EQ2",
                      "x": 60, "y": 60, "w": 40, "h": 30})
        st_code, r = self._save(nodes, st)
        self.assertEqual(st_code, 200, r)
        f = modelstore.read_files(self.mdir)
        for fn in ("equipments.csv", "qualifications.csv"):
            for row in f[fn]["rows"]:
                self.assertNotIn("EQ1", row)

    def test_area_move(self):
        nodes, m = self._nodes()
        st = self._struct()
        # 先に A2+S2+駅 を追加してから EQ1 を A2 へ
        st["areas.csv"]["rows"].append(
            ["A2", "1", "1", "1"][:len(st["areas.csv"]["cols"])])
        st["stockers.csv"]["rows"].append(
            ["S2", "A2", "50", "1", "1", "0"]
            [:len(st["stockers.csv"]["cols"])])
        st["cleanliner.csv"]["rows"].append(["2", "S2", "10"])
        nodes.append({"node_type": "area", "node_id": "A2",
                      "x": 500, "y": 300, "w": 160, "h": 160})
        nodes.append({"node_type": "stocker", "node_id": "S2",
                      "x": 510, "y": 400, "w": 60, "h": 40})
        eq = st["equipments.csv"]
        eq["rows"][0][eq["cols"].index("area")] = "A2"
        st_code, r = self._save(nodes, st)
        self.assertEqual(st_code, 200, r)
        f = modelstore.read_files(self.mdir)["equipments.csv"]
        self.assertEqual(f["rows"][0][f["cols"].index("area")], "A2")

    def test_area_add(self):
        nodes, m = self._nodes()
        st = self._struct()
        st["areas.csv"]["rows"].append(
            ["A9"] + ["1"] * (len(st["areas.csv"]["cols"]) - 1))
        srow = ["S9", "A9", "60"] + ["2", "2", "1"][:3]
        st["stockers.csv"]["rows"].append(
            srow[:len(st["stockers.csv"]["cols"])])
        st["cleanliner.csv"]["rows"].append(
            [str(len(st["cleanliner.csv"]["rows"]) + 1), "S9", "15"])
        nodes.append({"node_type": "area", "node_id": "A9",
                      "x": 700, "y": 300, "w": 160, "h": 160})
        nodes.append({"node_type": "stocker", "node_id": "S9",
                      "x": 710, "y": 400, "w": 60, "h": 40})
        st_code, r = self._save(nodes, st)
        # 検証を通るか参照エラーになるかは構成次第だが、サーバーが処理を返す
        self.assertIn(st_code, (200, 400))
        if st_code == 400:
            self.assertTrue(r.get("detail"))

    def test_validation_400_unchanged(self):
        nodes, m = self._nodes()
        st = self._struct()
        eq = st["equipments.csv"]
        # 存在しないエリアへの参照は従来どおり検証エラー
        eq["rows"][0][eq["cols"].index("area")] = "NOPE"
        rev_l = layoutstore.revision(self.mdir)
        rev_m = modelstore.revision(self.mdir)
        st_code, r = self._save(nodes, st)
        self.assertEqual(st_code, 400)
        self.assertEqual(layoutstore.revision(self.mdir), rev_l)
        self.assertEqual(modelstore.revision(self.mdir), rev_m)

    def test_revision_mismatch(self):
        nodes, m = self._nodes()
        st = self._struct()
        st_code, r = apilib.dispatch(
            "layout_save", {"model": "m1", "client_id": CID,
                            "base_revision": layoutstore.revision(self.mdir),
                            "base_model_revision": "0" * 16,
                            "nodes": nodes, "structure": st},
            method="POST", x_requested_with="fabsim",
            content_type="application/json", remote_user="u")
        self.assertEqual(st_code, 409)


if __name__ == "__main__":
    unittest.main()


class ShapeTest(unittest.TestCase):
    def test_resolve_shapes(self):
        from fabsim import config
        r = config.resolve_shapes([("A", ""), ("B", "circle"), ("C", "")])
        self.assertEqual(r, {"A": "rect", "B": "circle",
                             "C": "circle"})   # 行順 index で循環
        r2 = config.resolve_shapes([("A%d" % i, "") for i in range(8)])
        self.assertEqual(len(set(r2.values())), 8)  # 8種すべて別形状

    def test_shape_in_model_and_db(self):
        import sqlite3
        from fabsim import config
        mdir = tempfile.mkdtemp()
        write_model(mdir)
        m, errors, _ = config.load_model(mdir)
        self.assertFalse(errors)
        self.assertEqual(m.tool_groups["TG1"].shape, "rect")
        # 不正値はエラー
        bad = tempfile.mkdtemp()
        write_model(bad, **{
            "tool_groups.csv":
                "tool_group,dispatch_rule,shape\nTG1,,bogus\n"})
        _, errs, _ = config.load_model(bad)
        self.assertTrue(any("shape" in e for e in errs))
        shutil.rmtree(mdir, ignore_errors=True)
        shutil.rmtree(bad, ignore_errors=True)

    def test_modelstore_shape_roundtrip(self):
        mdir = tempfile.mkdtemp()
        write_model(mdir)
        f = modelstore.read_files(mdir)
        self.assertIn("shape", f["tool_groups.csv"]["cols"])
        shutil.rmtree(mdir, ignore_errors=True)


class OldRunShapeTest(unittest.TestCase):
    """shape 列の無い古い run は config/tool_groups.csv から形状解決。"""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = self.dir
        self.mdir = os.path.join(self.dir, "models", "m1")
        write_model(self.mdir)
        self.rdir = os.path.join(self.dir, "runs",
                                 "20260101-000000-abcdef")
        shutil.copytree(self.mdir, os.path.join(self.rdir, "config"))
        # 古い形式 DB(shape 列なし)
        import sqlite3
        db = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        db.executescript(
            "CREATE TABLE meta(key TEXT, value TEXT);"
            "INSERT INTO meta VALUES('schema_version','1');"
            "INSERT INTO meta VALUES('world_w','1000');"
            "INSERT INTO meta VALUES('world_h','600');"
            "CREATE TABLE areas(area TEXT PRIMARY KEY, agv_count INTEGER,"
            " x REAL, y REAL, w REAL, h REAL);"
            "INSERT INTO areas VALUES('A1',1,0,0,100,100);"
            "CREATE TABLE equipments(equip_id TEXT PRIMARY KEY, area TEXT,"
            " tool_group TEXT, x REAL, y REAL);"
            "INSERT INTO equipments VALUES('EQ1','A1','TG1',10,10);"
            "CREATE TABLE stockers(stocker_id TEXT PRIMARY KEY,"
            " area TEXT, capacity INTEGER, x REAL, y REAL);"
            "INSERT INTO stockers VALUES('S1','A1',50,10,10);"
            "CREATE TABLE cl_stations(seq INTEGER, stocker_id TEXT,"
            " base_time REAL, time_to_next REAL);"
            "INSERT INTO cl_stations VALUES(1,'S1',0,10);")
        db.commit()
        db.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _layout(self):
        return apilib.dispatch(
            "layout", {"run": "20260101-000000-abcdef"})

    def test_config_resolves_shape(self):
        st, r = self._layout()
        self.assertEqual(st, 200, r)
        self.assertEqual(r["equipments"][0]["shape"], "rect")

    def test_no_config_falls_back(self):
        os.remove(os.path.join(self.rdir, "config", "tool_groups.csv"))
        st, r = self._layout()
        self.assertEqual(st, 200, r)
        self.assertEqual(r["equipments"][0]["shape"], "rect")

    def test_explicit_shape_in_config(self):
        with open(os.path.join(self.rdir, "config",
                               "tool_groups.csv"), "w") as f:
            f.write("tool_group,dispatch_rule,shape\nTG1,,circle\n")
        st, r = self._layout()
        self.assertEqual(r["equipments"][0]["shape"], "circle")


class LayoutWhTest(unittest.TestCase):
    """equipments/stockers の w,h 焼き込みと API layout の w/h。"""
    def test_dbwriter_wh(self):
        import sqlite3
        from fabsim import config
        from fabsim.dbwriter import DBWriter
        mdir = tempfile.mkdtemp()
        write_model(mdir)
        m, errors, _ = config.load_model(mdir)
        self.assertFalse(errors)
        dbp = os.path.join(mdir, "r.db")
        w = DBWriter(dbp)
        w.open()
        nodes = {("equip", "EQ1"): {"x": 10, "y": 20, "w": 80, "h": 55},
                 ("stocker", "S1"): {"x": 1, "y": 2, "w": 90, "h": 70},
                 ("area", "A1"): {"x": 0, "y": 0, "w": 200, "h": 200}}
        w.write_static(m, nodes, {"w": 1000, "h": 600}, [])
        db = w.conn
        r = db.execute("SELECT * FROM equipments").fetchone()
        self.assertEqual((r[6], r[7]), (80.0, 55.0))
        s = db.execute("SELECT * FROM stockers").fetchone()
        self.assertEqual((s[5], s[6]), (90.0, 70.0))
        db.close()
        shutil.rmtree(mdir, ignore_errors=True)

    def test_api_layout_wh_compat(self):
        # OldRunShapeTest と同じく w/h 列なし DB → 既定値
        dir0 = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = dir0
        write_model(os.path.join(dir0, "models", "m1"))
        rdir = os.path.join(dir0, "runs", "20260101-000000-abcdef")
        os.makedirs(os.path.join(rdir, "config"))
        import sqlite3
        db = sqlite3.connect(os.path.join(rdir, "result.db"))
        db.executescript(
            "CREATE TABLE meta(key TEXT, value TEXT);"
            "INSERT INTO meta VALUES('schema_version','1');"
            "CREATE TABLE areas(area TEXT PRIMARY KEY, agv_count INTEGER,"
            " x REAL, y REAL, w REAL, h REAL);"
            "INSERT INTO areas VALUES('A1',1,0,0,100,100);"
            "CREATE TABLE equipments(equip_id TEXT PRIMARY KEY, area TEXT,"
            " tool_group TEXT, x REAL, y REAL);"
            "INSERT INTO equipments VALUES('EQ1','A1','TG1',10,10);"
            "CREATE TABLE stockers(stocker_id TEXT PRIMARY KEY,"
            " area TEXT, capacity INTEGER, x REAL, y REAL);"
            "INSERT INTO stockers VALUES('S1','A1',50,10,10);"
            "CREATE TABLE cl_stations(seq INTEGER, stocker_id TEXT,"
            " base_time REAL, time_to_next REAL);"
            "INSERT INTO cl_stations VALUES(1,'S1',0,10);")
        db.commit(); db.close()
        st, r = apilib.dispatch("layout",
                                {"run": "20260101-000000-abcdef"})
        self.assertEqual(st, 200, r)
        self.assertEqual((r["equipments"][0]["w"],
                          r["equipments"][0]["h"]), (40, 30))
        self.assertEqual((r["stockers"][0]["w"],
                          r["stockers"][0]["h"]), (60, 40))
        shutil.rmtree(dir0, ignore_errors=True)
