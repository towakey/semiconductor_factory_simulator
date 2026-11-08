"""モデル編集ストア/API (設計書 12.6)。"""
import json
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
CID2 = "ffffffffffffffff"

LAYOUT = """node_type,node_id,x,y,w,h
world,world,0,0,1000.00,600.00
area,A1,10,10,100,100
equip,EQ1,20,20,40,30
stocker,S1,30,30,60,40
cl_point,S1:0,1,2,0,0
cl_point,S1:1,3,4,0,0
agv_point,EQ1:0,5,6,0,0
"""


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mdir = os.path.join(self.dir, "m1")
        write_model(self.mdir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _lock(self, cid=CID):
        ok, st = modelstore.acquire_lock(self.mdir, cid, "u1")
        assert ok, st

    def _save(self, files=None, cid=CID, base=None):
        return modelstore.save_model(
            self.mdir, files if files is not None else
            modelstore.read_files(self.mdir), cid,
            base if base is not None else modelstore.revision(self.mdir),
            "u1", True)

    def test_roundtrip(self):
        before = {fn: open(os.path.join(self.mdir, fn), "rb").read()
                  for fn in os.listdir(self.mdir) if fn.endswith(".csv")}
        self._lock()
        st, r = self._save()
        self.assertEqual(st, 200)
        after = {fn: open(os.path.join(self.mdir, fn), "rb").read()
                 for fn in os.listdir(self.mdir) if fn.endswith(".csv")}
        # 往復で内容が失われない(値・行数は同一)
        files = modelstore.read_files(self.mdir)
        for fn, tbl in files.items():
            if fn in before:
                orig = before[fn].decode("utf-8-sig")
                n_orig = sum(1 for l in orig.splitlines()
                             if l.strip() and not l.strip().startswith("#"))
                self.assertEqual(len(tbl["rows"]) + 1, n_orig, fn)

    def test_validation_errors_400_and_unchanged(self):
        self._lock()
        rev = modelstore.revision(self.mdir)
        files = modelstore.read_files(self.mdir)
        files["routes.csv"]["rows"][0][
            files["routes.csv"]["cols"].index("recipe")] = "NOPE"
        st, r = self._save(files)
        self.assertEqual(st, 400)
        self.assertTrue(r["detail"])
        self.assertEqual(modelstore.revision(self.mdir), rev)

    def test_lock_403_409_expire(self):
        st, r = self._save()
        self.assertEqual(st, 403)                    # ロック無し
        self._lock(CID2)
        st, r = self._save(cid=CID)
        self.assertEqual(st, 409)                    # 他者ロック中
        ok, st2 = modelstore.acquire_lock(self.mdir, CID, "u2", now=0)
        self.assertFalse(ok)
        ok, st2 = modelstore.acquire_lock(self.mdir, CID, "u2",
                                        now=1e12)    # 期限切れ
        self.assertTrue(ok)

    def test_revision_409(self):
        self._lock()
        st, r = self._save(base="0" * 16)
        self.assertEqual(st, 409)

    def test_history_20(self):
        self._lock()
        files = modelstore.read_files(self.mdir)
        for i in range(21):
            files["products.csv"]["rows"][0][1] = str(1 + i * 0.01)
            st, r = self._save(files)
            self.assertEqual(st, 200)
        hist = os.path.join(self.mdir, "model_history")
        self.assertEqual(len(os.listdir(hist)), 20)

    def test_clone(self):
        dst = os.path.join(self.dir, "m2")
        errors, warnings = modelstore.clone_model(self.mdir, dst)
        self.assertFalse(errors)
        self.assertTrue(os.path.exists(os.path.join(dst, "sim.csv")))
        # ロック・履歴は複製しない
        self._lock()
        dst2 = os.path.join(self.dir, "m3")
        modelstore.clone_model(self.mdir, dst2)
        self.assertFalse(os.path.exists(os.path.join(dst2, ".model.lock")))
        self.assertFalse(os.path.exists(os.path.join(dst2, "model_history")))

    def test_optional_empty_deleted(self):
        self._lock()
        files = modelstore.read_files(self.mdir)
        files["qualifications.csv"]["rows"] = []
        st, r = self._save(files)
        self.assertEqual(st, 200)
        self.assertFalse(os.path.exists(
            os.path.join(self.mdir, "qualifications.csv")))


class RenameTest(unittest.TestCase):
    """主キーリネームのカスケード(12.6)。renames → layout.csv 置換。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mdir = os.path.join(self.dir, "m1")
        write_model(self.mdir, **{
            "qualifications.csv": "equip_id,recipe\nEQ1,R1\n",
            "setups.csv": "tool_group,from_recipe,to_recipe,setup_time\n"
                          "TG1,R1,R1,5\n",
            "release.csv": "lot_id,product,release_time\nL1,P,0\n",
            "layout.csv": LAYOUT})
        ok, st = modelstore.acquire_lock(self.mdir, CID, "u1")
        assert ok, st

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _save(self, files, renames):
        return modelstore.save_model(
            self.mdir, files, CID, modelstore.revision(self.mdir),
            "u1", True, renames=renames)

    def _replace(self, files, fn, col, old, new):
        """クライアント側カスケード(modeledit.js)相当の参照置換。"""
        t = files[fn]
        ci = t["cols"].index(col)
        n = 0
        for r in t["rows"]:
            if r[ci] == old:
                r[ci] = new
                n += 1
        return n

    def _read(self, fn):
        with open(os.path.join(self.mdir, fn), encoding="utf-8") as f:
            return f.read()

    def test_rename_equip(self):
        files = modelstore.read_files(self.mdir)
        self.assertEqual(
            self._replace(files, "equipments.csv", "equip_id", "EQ1", "EQ9"), 1)
        self.assertEqual(
            self._replace(files, "qualifications.csv", "equip_id", "EQ1", "EQ9"),
            1)
        st, r = self._save(files, [{"kind": "equipments.csv",
                                    "col": "equip_id",
                                    "old": "EQ1", "new": "EQ9"}])
        self.assertEqual(st, 200, r)
        self.assertEqual(r["layout_renamed"], 2)
        self.assertTrue(r["layout_updated"])
        self.assertIn("EQ9,R1", self._read("qualifications.csv"))
        lay = self._read("layout.csv")
        self.assertIn("equip,EQ9,", lay)
        self.assertIn("agv_point,EQ9:0,", lay)
        self.assertNotIn("EQ1", lay)

    def test_rename_stocker(self):
        files = modelstore.read_files(self.mdir)
        self._replace(files, "stockers.csv", "stocker_id", "S1", "S9")
        self._replace(files, "cleanliner.csv", "stocker_id", "S1", "S9")
        # sim.csv release_stocker の value も対象
        t = files["sim.csv"]
        ki, vi = t["cols"].index("key"), t["cols"].index("value")
        for row in t["rows"]:
            if row[ki] == "release_stocker" and row[vi] == "S1":
                row[vi] = "S9"
        st, r = self._save(files, [{"kind": "stockers.csv",
                                    "col": "stocker_id",
                                    "old": "S1", "new": "S9"}])
        self.assertEqual(st, 200, r)
        self.assertEqual(r["layout_renamed"], 3)   # stocker + cl_point x2
        self.assertIn(",S9,", self._read("cleanliner.csv"))
        self.assertIn("release_stocker,S9", self._read("sim.csv"))
        lay = self._read("layout.csv")
        self.assertIn("stocker,S9,", lay)
        self.assertIn("cl_point,S9:0,", lay)
        self.assertIn("cl_point,S9:1,", lay)
        self.assertNotIn("S1", lay)

    def test_rename_area(self):
        files = modelstore.read_files(self.mdir)
        self._replace(files, "areas.csv", "area", "A1", "AX")
        self._replace(files, "stockers.csv", "area", "A1", "AX")
        self._replace(files, "equipments.csv", "area", "A1", "AX")
        st, r = self._save(files, [{"kind": "areas.csv", "col": "area",
                                    "old": "A1", "new": "AX"}])
        self.assertEqual(st, 200, r)
        self.assertEqual(r["layout_renamed"], 1)
        self.assertIn("area,AX,", self._read("layout.csv"))

    def test_rename_no_layout_file(self):
        # layout.csv が無ければ置換 0 件・更新なしで成功する
        os.remove(os.path.join(self.mdir, "layout.csv"))
        files = modelstore.read_files(self.mdir)
        self._replace(files, "areas.csv", "area", "A1", "AX")
        self._replace(files, "stockers.csv", "area", "A1", "AX")
        self._replace(files, "equipments.csv", "area", "A1", "AX")
        st, r = self._save(files, [{"kind": "areas.csv", "col": "area",
                                    "old": "A1", "new": "AX"}])
        self.assertEqual(st, 200, r)
        self.assertEqual(r["layout_renamed"], 0)
        self.assertFalse(r["layout_updated"])

    def test_rename_kind_without_layout_effect(self):
        files = modelstore.read_files(self.mdir)
        self._replace(files, "recipes.csv", "recipe", "R1", "R9")
        self._replace(files, "routes.csv", "recipe", "R1", "R9")
        self._replace(files, "qualifications.csv", "recipe", "R1", "R9")
        self._replace(files, "setups.csv", "from_recipe", "R1", "R9")
        self._replace(files, "setups.csv", "to_recipe", "R1", "R9")
        before = self._read("layout.csv")
        st, r = self._save(files, [{"kind": "recipes.csv", "col": "recipe",
                                    "old": "R1", "new": "R9"}])
        self.assertEqual(st, 200, r)
        self.assertEqual(r["layout_renamed"], 0)
        self.assertFalse(r["layout_updated"])
        self.assertEqual(self._read("layout.csv"), before)

    def test_rename_chain(self):
        files = modelstore.read_files(self.mdir)
        self._replace(files, "equipments.csv", "equip_id", "EQ1", "EQ9")
        self._replace(files, "qualifications.csv", "equip_id", "EQ1", "EQ9")
        st, r = self._save(files, [
            {"kind": "equipments.csv", "col": "equip_id",
             "old": "EQ1", "new": "EQX"},
            {"kind": "equipments.csv", "col": "equip_id",
             "old": "EQX", "new": "EQ9"}])
        self.assertEqual(st, 200, r)
        lay = self._read("layout.csv")
        self.assertIn("equip,EQ9,", lay)
        self.assertIn("agv_point,EQ9:0,", lay)

    def test_rename_layout_locked_by_other(self):
        ok, _ = layoutstore.acquire_lock(self.mdir, CID2, "other")
        self.assertTrue(ok)
        files = modelstore.read_files(self.mdir)
        self._replace(files, "areas.csv", "area", "A1", "AX")
        self._replace(files, "stockers.csv", "area", "A1", "AX")
        self._replace(files, "equipments.csv", "area", "A1", "AX")
        st, r = self._save(files, [{"kind": "areas.csv", "col": "area",
                                    "old": "A1", "new": "AX"}])
        self.assertEqual(st, 200, r)
        self.assertTrue(r["layout_updated"])
        self.assertIn("area,AX,", self._read("layout.csv"))

    def test_bad_renames(self):
        files = modelstore.read_files(self.mdir)
        for bad in ("x", 1, ["x"],
                    [{"kind": "equipments.csv", "col": "area",
                      "old": "A1", "new": "AX"}],          # col は主キーでない
                    [{"kind": "layout.csv", "col": "node_id",
                      "old": "A", "new": "B"}],            # 対象外ファイル
                    [{"kind": "equipments.csv", "col": "equip_id",
                      "old": "EQ/1", "new": "EQ9"}],       # 不正文字
                    [{"kind": "equipments.csv", "col": "equip_id",
                      "old": "EQ1", "new": ""}],           # 空
                    [{"kind": "equipments.csv", "col": "equip_id",
                      "old": "EQ1", "new": "X" * 65}],     # 長すぎ
                    [{"kind": "equipments.csv", "col": "equip_id",
                      "old": "EQ1"}],                      # new 無し
                    ):
            st, r = self._save(files, bad)
            self.assertEqual(st, 400, (bad, r))
            self.assertEqual(r["error"], "invalid")
        st, r = self._save(files, [{"kind": "equipments.csv",
                                    "col": "equip_id",
                                    "old": "EQ1", "new": "EQ9"}] * 201)
        self.assertEqual(st, 400)

    def test_rename_dup_pk_validation_400(self):
        # 主キー重複は UI が差し戻すが、サーバー側でも既存検証で 400
        files = modelstore.read_files(self.mdir)
        files["equipments.csv"]["rows"].append(
            list(files["equipments.csv"]["rows"][0]))
        lay_before = self._read("layout.csv")
        rev = modelstore.revision(self.mdir)
        st, r = self._save(files, [{"kind": "equipments.csv",
                                    "col": "equip_id",
                                    "old": "EQ1", "new": "EQ9"}])
        self.assertEqual(st, 400)
        self.assertEqual(r["error"], "validation")
        self.assertEqual(modelstore.revision(self.mdir), rev)
        self.assertEqual(self._read("layout.csv"), lay_before)

    def test_save_without_renames_unchanged(self):
        files = modelstore.read_files(self.mdir)
        st, r = self._save(files, None)
        self.assertEqual(st, 200, r)
        self.assertNotIn("layout_renamed", r)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = cls.data
        with open(os.path.join(cls.data, "web_config.json"), "w") as f:
            f.write('{"allow_anonymous_edit": true}')
        cls.mdir = os.path.join(cls.data, "models", "m1")
        write_model(cls.mdir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data, ignore_errors=True)
        os.environ.pop("FABSIM_DATA_DIR", None)

    def d(self, action, **kw):
        return apilib.dispatch(action, kw.pop("params", {}), **kw)

    def test_files_and_spec(self):
        st, r = self.d("model_files", params={"model": "m1",
                                            "client_id": CID})
        self.assertTrue(r["ok"])
        self.assertIn("recipes.csv", r["files"])
        self.assertIn("spec", r)
        self.assertIn("sim_spec", r)

    def test_path_rejects(self):
        st, _ = self.d("model_files", params={"model": "../x"})
        self.assertEqual(st, 400)
        st, _ = self.d("model_save", method="POST",
                       params={"model": "m1", "client_id": CID,
                               "files": {"../x.csv": {"cols": [], "rows": []}}},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 403)          # ロック無しが先
        st, _ = self.d("model_lock", method="GET",
                       params={"model": "m1", "client_id": CID})
        self.assertEqual(st, 400)          # GET での POST API 拒否

    def test_lock_save_cycle(self):
        st, r = self.d("model_lock", method="POST",
                       params={"model": "m1", "client_id": CID},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 200)
        st, r = self.d("model_files", params={"model": "m1",
                                            "client_id": CID})
        files = r["files"]
        st, r = self.d("model_save", method="POST",
                       params={"model": "m1", "client_id": CID,
                               "base_revision": r["revision"],
                               "files": files},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 200, r)
        st, r = self.d("model_unlock", method="POST",
                       params={"model": "m1", "client_id": CID},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 200)

    def test_clone_api(self):
        st, r = self.d("model_clone", method="POST",
                       params={"model": "m1", "new_name": "bad/name"},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 400)
        st, r = self.d("model_clone", method="POST",
                       params={"model": "m1", "new_name": "m1"},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertEqual(st, 409)
        st, r = self.d("model_clone", method="POST",
                       params={"model": "m1", "new_name": "m2"},
                       x_requested_with="fabsim",
                       content_type="application/json")
        self.assertTrue(r["ok"], r)


if __name__ == "__main__":
    unittest.main()
