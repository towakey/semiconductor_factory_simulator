"""バッチ処理 (設計書 5.2, 9章)。"""
import shutil
import tempfile
import unittest

from fabsim import config
from fabsim.engine import Simulator

from tests.helpers import write_model


BATCH_SIM = """key,value
duration,{duration}
warmup,0
seed,1
dispatch_rule,FIFO
release_mode,schedule
lots_per_day,0
release_control,fixed
wip_limit,0
release_stocker,S1
area_select,LEAST_LOADED
agv_out_first,1
cl_carriers,1
cl_slots,1
cl_load_time,1
cl_unload_time,1
failure_policy,resume
"""

BATCH_FILES = {
    "recipes.csv": """recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
RB,TG1,240,lot,fixed,,2,4,10
""",
    "routes.csv": """product,seq,recipe,step_name
P,10,RB,bake
""",
    "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,4,0,0,0,0,
""",
}


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, release_csv, duration):
        files = dict(BATCH_FILES)
        files["release.csv"] = release_csv
        files["sim.csv"] = BATCH_SIM.format(duration=duration)
        write_model(self.dir, **files)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        return sim

    def test_waits_below_min_then_timeout_starts(self):
        """batch_min 未満(1ロット)ではすぐ開始せず、
        タイムアウト(batch_wait_max=10)で開始する。"""
        sim = self._run("""lot_id,product,release_time,priority,due_date
L1,P,0,0,
""", 400)
        lot = sim.lots["L1"]
        self.assertEqual(len(lot.history), 1)
        seq, name, recipe, eid, queue_in, start, end = lot.history[0]
        # ストッカー格納 t=0 → タイムアウト t=10 で予約 → 搬入7 で処理開始 17
        self.assertAlmostEqual(start, 17.0, places=6)
        self.assertAlmostEqual(end, 257.0, places=6)
        self.assertIsNotNone(lot.complete_time)

    def test_batch_max_starts_immediately(self):
        """batch_max(4)集まればタイムアウト前に開始する。"""
        sim = self._run("""lot_id,product,release_time,priority,due_date
L1,P,0,0,
L2,P,0,0,
L3,P,0,0,
L4,P,0,0,
""", 400)
        # 4ロットとも t=0 に同じ設備へ予約され、全員到着後に処理開始。
        # AGV1台が4往復: 到着は 7, 19, 31, 43 (2往復目以降は空走5が加わる)。
        starts = [sim.lots["L%d" % i].history[0][5] for i in range(1, 5)]
        ends = [sim.lots["L%d" % i].history[0][6] for i in range(1, 5)]
        equips = [sim.lots["L%d" % i].history[0][3] for i in range(1, 5)]
        self.assertEqual(set(equips), {"EQ1"})
        self.assertEqual(len(set(starts)), 1)     # 全員同時に開始
        self.assertEqual(len(set(ends)), 1)       # 全員同時に終了
        # 最後のロット到着 = 43 → 処理 [43, 283]
        self.assertAlmostEqual(starts[0], 43.0, places=6)
        self.assertAlmostEqual(ends[0], 283.0, places=6)


if __name__ == "__main__":
    unittest.main()
