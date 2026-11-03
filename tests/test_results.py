"""結果集計 API を小さな手組みDBで検証。"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web", "cgi-bin"))
import apilib  # noqa: E402

RUN = "20261103-120000-a1b2c3"


class ResultsApiTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        os.environ["FABSIM_DATA_DIR"] = self.data
        self.rdir = os.path.join(self.data, "runs", RUN)
        os.makedirs(os.path.join(self.rdir, "config"))
        with open(os.path.join(self.rdir, "job.json"), "w") as f:
            json.dump({"status": "done", "duration": 300}, f)
        self.db = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        self.db.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO meta VALUES('status','done');
            INSERT INTO meta VALUES('duration','300');
            INSERT INTO meta VALUES('progress_time','300');
            CREATE TABLE equipments(equip_id TEXT,area TEXT,tool_group TEXT,ports INTEGER);
            INSERT INTO equipments VALUES('E1','A1','TG1',2);
            INSERT INTO equipments VALUES('E2','A2','TG2',1);
            CREATE TABLE recipes(recipe TEXT,tool_group TEXT);
            INSERT INTO recipes VALUES('R1','TG1');
            INSERT INTO recipes VALUES('R2','TG2');
            CREATE TABLE lots(lot_id TEXT,product TEXT,release_time REAL,
                              due_date REAL,complete_time REAL,wafers INTEGER);
            INSERT INTO lots VALUES('L1','P',0,200,70,25);
            INSERT INTO lots VALUES('L2','P',20,300,NULL,25);
            INSERT INTO lots VALUES('L3','P',50,400,NULL,25);
            CREATE TABLE lot_steps(lot_id TEXT,seq INTEGER,recipe TEXT,equip_id TEXT,
                t_stocker_in REAL,t_reserved REAL,t_start REAL,t_end REAL);
            INSERT INTO lot_steps VALUES('L1',10,'R1','E1',0,10,20,70);
            INSERT INTO lot_steps VALUES('L2',10,'R1','E1',20,80,90,NULL);
            INSERT INTO lot_steps VALUES('L3',10,'R2','E2',50,NULL,NULL,NULL);
            CREATE TABLE equip_state(equip_id TEXT,state TEXT,lot_id TEXT,
                recipe TEXT,t_start REAL,t_end REAL);
            INSERT INTO equip_state VALUES('E1','IDLE','', '',0,20);
            INSERT INTO equip_state VALUES('E1','BUSY','L1','R1',20,70);
            INSERT INTO equip_state VALUES('E1','SETUP','', '',70,80);
            INSERT INTO equip_state VALUES('E1','RESERVED','L2','R1',80,90);
            INSERT INTO equip_state VALUES('E1','BUSY','L2','R1',90,NULL);
            INSERT INTO equip_state VALUES('E2','IDLE','', '',0,50);
            INSERT INTO equip_state VALUES('E2','RESERVED','L3','R2',50,NULL);
        """)
        self.db.commit()
        self.db.close()

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)
        os.environ.pop("FABSIM_DATA_DIR", None)

    def get(self, **params):
        return apilib.dispatch("results", {"run": RUN, **params})

    def test_wip_boundaries_and_metrics(self):
        status, r = self.get(t="80", mode="cum")
        self.assertEqual(status, 200, r)
        # t_reserved <= t を設備上として数える。L3 はグループ待ち。
        self.assertEqual(r["total"]["wip"], 2)
        self.assertEqual(r["total"]["wip_wait"], 1)
        self.assertEqual(r["total"]["wip_equip"], 1)
        self.assertEqual(r["total"]["wip_other"], 0)
        e1 = next(x for x in r["equips"] if x["equip_id"] == "E1")
        self.assertEqual(e1["wip_equip"], 1)
        self.assertEqual(e1["wip_processing"], 0)  # start=90 > t

    def test_cumulative_and_window_period_boundaries(self):
        _, cum = self.get(t="100", mode="cum")
        self.assertEqual(cum["total"]["completed_steps"], 1)
        self.assertEqual(cum["total"]["avg_process_time"], 50)
        self.assertEqual(cum["total"]["dispatch_wait_avg"], 35)
        self.assertEqual(cum["total"]["dispatch_wait_max"], 60)
        self.assertEqual(cum["total"]["reserved_start_wait_avg"], 10)
        # [70,100]: end==70 は除外、reserved=80 の待ちだけ含む。
        _, win = self.get(t="100", mode="window", window="30")
        self.assertEqual(win["p_start"], 70)
        self.assertEqual(win["total"]["completed_steps"], 0)
        self.assertEqual(win["total"]["dispatch_wait_avg"], 60)
        e1 = next(x for x in win["equips"] if x["equip_id"] == "E1")
        self.assertEqual(e1["state_minutes"]["BUSY"], 10)
        self.assertEqual(e1["state_minutes"]["SETUP"], 10)
        self.assertEqual(e1["utilization"], 10 / 30)
        self.assertEqual(e1["utilization_gross"], 10 / 30)

    def test_null_metrics_and_invalid_parameters(self):
        _, r = self.get(t="0", mode="cum")
        self.assertIsNone(r["total"]["utilization"])
        self.assertIsNone(r["total"]["dispatch_wait_avg"])
        self.assertEqual(self.get(t="301")[0], 400)
        self.assertEqual(self.get(t="-1")[0], 400)
        self.assertEqual(self.get(t="10", mode="window", window="0")[0], 400)
        self.assertEqual(self.get(t="10", mode="window", window="999")[0], 400)
        self.assertEqual(apilib.dispatch(
            "results", {"run": RUN, "t": 1}, method="POST")[0], 400)

    def test_running_time_is_limited_to_progress(self):
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        c.execute("UPDATE meta SET value='running' WHERE key='status'")
        c.execute("UPDATE meta SET value='50' WHERE key='progress_time'")
        c.commit()
        c.close()
        with open(os.path.join(self.rdir, "job.json"), "w") as f:
            json.dump({"status": "running", "duration": 300}, f)
        with open(os.path.join(self.rdir, "progress.json"), "w") as f:
            json.dump({"progress_time": 60}, f)
        status, r = self.get(t="60")
        self.assertEqual(status, 200)
        self.assertEqual(r["progress_time"], 60)
        self.assertEqual(self.get(t="60.1")[0], 400)
        _, series = self.series("wip", step=10)
        self.assertEqual(series["times"][-1], 60)

    def test_wip_group_rollup_and_completion(self):
        _, r = self.get(t="100")
        gs = {x["tool_group"]: x for x in r["groups"]}
        self.assertEqual(gs["TG1"]["wip_equip"], 1)
        self.assertEqual(gs["TG2"]["wip_wait"], 1)
        self.assertEqual(sum(x["wip"] for x in gs.values()), 2)
        self.assertEqual(r["total"]["completed_lots"], 1)
        self.assertEqual(r["total"]["completed_lots_period"], 1)

    def test_finalized_open_step_is_still_wip_not_completed(self):
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        c.execute("UPDATE lots SET complete_time=NULL WHERE lot_id='L1'")
        c.execute("UPDATE lot_steps SET t_stocker_in=250,t_reserved=NULL,"
                  "t_start=NULL,t_end=300 WHERE lot_id='L1'")
        c.commit()
        c.close()
        _, r = self.get(t="300")
        self.assertEqual(r["total"]["wip_wait"], 2)
        self.assertEqual(r["total"]["completed_steps"], 0)

    def test_old_run_config_fallback_and_warning(self):
        # config fallback
        self.db = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        self.db.execute("DROP TABLE recipes")
        self.db.commit()
        self.db.close()
        with open(os.path.join(self.rdir, "config", "recipes.csv"), "w") as f:
            f.write("recipe,tool_group,process_time\nR1,TG1,1\nR2,TG2,1\n")
        _, r = self.get(t="80")
        self.assertEqual(next(x for x in r["groups"]
                              if x["tool_group"] == "TG1")["wip_equip"], 1)
        os.remove(os.path.join(self.rdir, "config", "recipes.csv"))
        _, r = self.get(t="80")
        self.assertTrue(r["warnings"])

    def test_old_lots_without_wafers(self):
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        c.executescript("""
            ALTER TABLE lots RENAME TO lots_with_wafers;
            CREATE TABLE lots(lot_id TEXT,product TEXT,release_time REAL,
                              due_date REAL,complete_time REAL);
            INSERT INTO lots SELECT lot_id,product,release_time,due_date,
                              complete_time FROM lots_with_wafers;
            DROP TABLE lots_with_wafers;
        """)
        c.commit()
        c.close()
        _, r = self.get(t="100")
        self.assertFalse(r["has_wafers"])
        self.assertIsNone(r["total"]["wafer_count"])

    def series(self, metric, scope="total", ids="", step=50, mode="bucket"):
        return apilib.dispatch("results_series", {
            "run": RUN, "metric": metric, "scope": scope, "ids": ids,
            "step": str(step), "mode": mode})

    def test_series_wip_and_bucket_cumulative_metrics(self):
        status, r = self.series("wip")
        self.assertEqual(status, 200, r)
        self.assertEqual(r["times"][:3], [0, 50.0, 100.0])
        self.assertEqual(r["series"][0]["label"], "全体")
        self.assertEqual(r["series"][0]["values"][:3], [1, 3, 2])
        _, wip_cum = self.series("wip", mode="cum")
        self.assertEqual(r["series"][0]["values"],
                         wip_cum["series"][0]["values"])

        _, waited = self.series("wip_wait", scope="group", ids="TG1")
        self.assertEqual(waited["series"][0]["values"][:3], [1, 1, 0])
        _, equipment = self.series("wip_equip", scope="equip", ids="E1")
        self.assertEqual(equipment["series"][0]["values"][:3], [0, 1, 1])

        _, steps = self.series("completed_steps")
        self.assertEqual(steps["series"][0]["values"][:3], [0, 0, 1])
        _, avg_proc = self.series("avg_process_time")
        self.assertIsNone(avg_proc["series"][0]["values"][1])
        self.assertEqual(avg_proc["series"][0]["values"][2], 50)
        _, avg_wait = self.series("avg_wait")
        self.assertEqual(avg_wait["series"][0]["values"][:3], [None, 10, 60])
        _, cum_wait = self.series("avg_wait", mode="cum")
        self.assertEqual(cum_wait["series"][0]["values"][2], 35)

    def test_series_state_time_and_utilization(self):
        _, r = self.series("state_time", scope="equip", ids="E1",
                           mode="cum")
        by_state = {x["id"]: x["values"] for x in r["series"]}
        self.assertEqual(by_state["BUSY"][:3], [0, 30, 60])
        self.assertEqual(by_state["SETUP"][:3], [0, 0, 10])
        _, util = self.series("utilization")
        self.assertEqual(util["series"][0]["values"][2], 0.3)
        _, gross = self.series("utilization_gross", mode="cum")
        self.assertAlmostEqual(gross["series"][0]["values"][2], 60 / 200)

    def test_series_results_consistency(self):
        _, s = self.series("completed_steps", mode="cum")
        _, r = self.get(t="100", mode="cum")
        self.assertEqual(s["times"][2], 100)
        self.assertEqual(s["series"][0]["values"][2],
                         r["total"]["completed_steps"])
        _, s = self.series("avg_wait", mode="bucket", step=50)
        _, r = self.get(t="100", mode="window", window="50")
        self.assertEqual(s["series"][0]["values"][2],
                         r["total"]["dispatch_wait_avg"])
        _, s = self.series("utilization", mode="bucket", step=50)
        _, r = self.get(t="100", mode="window", window="50")
        self.assertAlmostEqual(s["series"][0]["values"][2],
                               r["total"]["utilization"])

    def test_series_point_cap_and_input_validation(self):
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        c.execute("UPDATE meta SET value='10000' WHERE key='duration'")
        c.commit()
        c.close()
        _, r = self.series("wip", step=1)
        self.assertLessEqual(len(r["times"]), 500)
        self.assertGreater(r["step"], 1)
        self.assertEqual(r["times"][-1], 10000)
        self.assertEqual(self.series("wip", step=0)[0], 400)
        self.assertEqual(self.series("wip", step="NaN")[0], 400)
        self.assertEqual(self.series("unknown")[0], 400)
        self.assertEqual(self.series("wip", scope="group",
                                     ids="NO_GROUP")[0], 400)
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        for i in range(3, 9):
            c.execute("INSERT INTO equipments VALUES(?,?,?,?)",
                      ("E%d" % i, "A1", "TG1", 1))
        c.commit()
        c.close()
        status, r = self.series(
            "wip", scope="equip", ids="E1,E2,E3,E4,E5,E6")
        self.assertEqual(status, 200)
        self.assertEqual(len(r["series"]), 6)
        self.assertEqual(self.series(
            "wip", scope="equip",
            ids="E1,E2,E3,E4,E5,E6,E7")[0], 400)
        self.assertEqual(apilib.dispatch(
            "results_series", {"run": RUN, "metric": "wip"},
            method="POST")[0], 400)

    def test_series_missing_group_map_hides_wait(self):
        c = sqlite3.connect(os.path.join(self.rdir, "result.db"))
        c.execute("DROP TABLE recipes")
        c.commit()
        c.close()
        os.remove(os.path.join(self.rdir, "config", "recipes.csv")) \
            if os.path.exists(os.path.join(self.rdir, "config",
                                            "recipes.csv")) else None
        _, r = self.series("wip_wait", scope="group", ids="TG1")
        self.assertTrue(r["warnings"])
        self.assertEqual(r["series"], [])


if __name__ == "__main__":
    unittest.main()
