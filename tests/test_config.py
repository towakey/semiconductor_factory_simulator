"""CSV 設定検証 (設計書 6.1, 9章): エラー検出・行番号・BOM/コメント・終了コード。"""
import os
import shutil
import tempfile
import unittest

from fabsim import cli, config

from tests.helpers import write_model


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_valid_model(self):
        write_model(self.dir)
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertIn("EQ1", model.equipments)

    def test_missing_required_column(self):
        write_model(self.dir, **{
            "equipments.csv": """equip_id,tool_group,area,capacity
EQ1,TG1,A1,1
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(errors)
        self.assertTrue(any("equipments.csv" in e and "agv_time" in e
                            for e in errors), str(errors))

    def test_error_has_file_and_line(self):
        write_model(self.dir, **{
            "routes.csv": """product,seq,recipe,step_name
P,10,R1,s1
P,20,NOPE,s2
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(errors)
        e = [x for x in errors if "recipe" in x.lower() or "レシピ" in x][0]
        self.assertIn("routes.csv", e)
        self.assertIn("3", e)   # 行番号(1行目=ヘッダ)
        self.assertIn("NOPE", e)

    def test_duplicate_key(self):
        write_model(self.dir, **{
            "equipments.csv": """equip_id,tool_group,area,agv_time,capacity
EQ1,TG1,A1,5,1
EQ1,TG1,A1,5,1
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(any("重複" in e for e in errors), str(errors))

    def test_bom_comment_blank_lines(self):
        """BOM 付き・コメント行・空行が正しく処理される。"""
        write_model(self.dir, **{
            "equipments.csv": "# コメント\n\n"
                              "equip_id,tool_group,area,agv_time,capacity\n"
                              "EQ1,TG1,A1,5,1\n"
        })
        # BOM を付けて書き直す
        path = os.path.join(self.dir, "equipments.csv")
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + data)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertIn("EQ1", model.equipments)

    def test_bad_enum_and_negative(self):
        write_model(self.dir, **{
            "recipes.csv": """recipe,tool_group,process_time,time_unit,dist
R1,TG1,-5,bad,worse
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertGreaterEqual(len(errors), 2)

    def test_no_capable_equipment(self):
        """どの設備も処理できないレシピはエラー。"""
        write_model(self.dir, **{
            "qualifications.csv": """equip_id,recipe
EQ1,R_OTHER
""",
            "recipes.csv": """recipe,tool_group,process_time,time_unit,dist
R1,TG1,10,lot,fixed
R_OTHER,TG1,10,lot,fixed
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(any("処理できる設備" in e for e in errors),
                        str(errors))

    def test_cleanliner_stations_mismatch(self):
        write_model(self.dir, **{
            "cleanliner.csv": """seq,stocker_id,time_to_next
1,S1,10
2,SX,10
"""})
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(any("cleanliner" in e for e in errors), str(errors))

    def test_validate_exit_codes(self):
        """正常=0、不正=2。"""
        write_model(self.dir)
        self.assertEqual(cli.main(["validate", "--config-dir", self.dir]), 0)
        write_model(self.dir, **{
            "equipments.csv": "equip_id,tool_group\nEQ1,TG1\n"})
        self.assertEqual(cli.main(["validate", "--config-dir", self.dir]), 2)


if __name__ == "__main__":
    unittest.main()
