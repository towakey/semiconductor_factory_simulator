"""統計: Little の法則・百分位・搬送時間内訳の整合 (設計書 9章)。"""
import shutil
import tempfile
import unittest

from fabsim import config
from fabsim.engine import Simulator
from fabsim.stats import percentile

from tests.helpers import write_model


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_percentile(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(percentile([10], 95), 10)
        self.assertEqual(percentile([], 95), 0.0)
        self.assertAlmostEqual(percentile(list(range(1, 101)), 95), 95.05)

    def test_littles_law(self):
        """故障なし・単純ケースで WIP ≒ スループット × CT が成立する。"""
        overrides = {
            "sim.csv": """key,value
duration,20160
warmup,0
seed,1
dispatch_rule,FIFO
release_mode,fixed
lots_per_day,8
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
""",
        }
        write_model(self.dir, **overrides)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        s = sim.stats.summary(sim)
        wip = s["wip_avg"]
        ct = s["cycle_time_min"]["mean"]
        lam = s["throughput_lots_per_day"] / 1440.0
        self.assertGreater(wip, 0)
        self.assertAlmostEqual(wip, lam * ct, delta=wip * 0.20)

    def test_transport_breakdown_consistency(self):
        """搬送時間内訳の合計が、処理+段取りを除いた滞留時間と一致する。"""
        overrides = {
            "sim.csv": """key,value
duration,2880
warmup,0
seed,1
dispatch_rule,FIFO
release_mode,fixed
lots_per_day,24
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
""",
        }
        write_model(self.dir, **overrides)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        n = 0
        for lot in sim.lots.values():
            if lot.complete_time is None:
                continue
            n += 1
            ct = lot.complete_time - lot.release_time
            dwell = ct - lot.t_proc_sum - lot.t_setup_sum
            self.assertAlmostEqual(lot.transport_total(), dwell, places=4,
                                   msg=lot.lot_id)
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()
