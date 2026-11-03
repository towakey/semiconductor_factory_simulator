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
import modelstore  # noqa: E402

from tests.helpers import write_model

CID = "a1b2c3d4e5f60718"
CID2 = "ffffffffffffffff"


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
