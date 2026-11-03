"""直列ラインの理論値・再現性・故障/PM/投入制御 (設計書 9章)。"""
import os
import shutil
import tempfile
import unittest

from fabsim import config
from fabsim.engine import Simulator

from tests.helpers import write_model


class SerialLineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, **overrides):
        write_model(self.dir, **overrides)
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        return sim

    def test_serial_cycle_time(self):
        """1設備・確定処理時間の直列ラインで CT が理論値と一致する。
        1ロット: 各工程は 搬入7(ロード1+走行5+アンロード1) + 処理10。
        最終工程以外は搬出も 7。CT = 3*(7+10) + 2*7 = 65。
        """
        overrides = {
            "areas.csv": """area,agv_count,agv_load_time,agv_unload_time,agv_return_home
A1,1,1,1,0
""",
            "release.csv": """lot_id,product,release_time,priority,due_date
L1,P,0,0,
""",
            "sim.csv": """key,value
duration,600
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
""",
        }
        sim = self._run(**overrides)
        lot = sim.lots["L1"]
        self.assertIsNotNone(lot.complete_time)
        self.assertAlmostEqual(lot.complete_time - lot.release_time,
                               65.0, places=6)
        # 搬入内訳の整合: 合計 = CT - 処理 - 段取り
        self.assertAlmostEqual(
            lot.transport_total(),
            65.0 - lot.t_proc_sum - lot.t_setup_sum, places=6)

    def test_reproducibility_same_seed(self):
        """同一シード・同一入力で出力が一致する。"""
        write_model(self.dir)
        m1, e1, _ = config.load_model(self.dir)
        m2, e2, _ = config.load_model(self.dir)
        sim1 = Simulator(m1)
        sim1.run()
        sim2 = Simulator(m2)
        sim2.run()
        s1 = sim1.stats.summary(sim1)
        s2 = sim2.stats.summary(sim2)
        self.assertEqual(s1, s2)
        for lot_id in sim1.lots:
            l1, l2 = sim1.lots[lot_id], sim2.lots[lot_id]
            self.assertEqual(l1.complete_time, l2.complete_time)
            self.assertEqual(l1.history, l2.history)

    def test_schedule_release_times(self):
        """schedule モードでは release.csv の時刻に投入される。"""
        overrides = {
            "release.csv": """lot_id,product,release_time,priority,due_date
L1,P,100,0,
L2,P,500,0,
""",
            "sim.csv": """key,value
duration,1440
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
""",
        }
        sim = self._run(**overrides)
        self.assertEqual(sim.lots["L1"].release_time, 100.0)
        self.assertEqual(sim.lots["L2"].release_time, 500.0)
        self.assertEqual(len(sim.lots), 2)

    def test_conwip_limits_wip(self):
        """CONWIP で WIP が wip_limit を超えない。"""
        overrides = {
            "sim.csv": """key,value
duration,2880
warmup,0
seed,1
dispatch_rule,FIFO
release_mode,fixed
lots_per_day,144
release_control,conwip
wip_limit,3
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
        sim = self._run(**overrides)
        max_wip = max(w for _, w in sim.stats.wip_hist)
        self.assertLessEqual(max_wip, 3)
        # 完了した分だけ後続が投入される
        self.assertGreater(sim.stats.total_completed, 0)

    def test_failure_resume(self):
        """故障すると DOWN 区間ができ、復旧後に処理が継続する。"""
        overrides = {
            "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,1,5,3,0,0,
""",
            "sim.csv": """key,value
duration,1440
warmup,0
seed,3
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
        sim = self._run(**overrides)
        states = [s for _, s, _, _, _, _ in sim.stats.equip_intervals]
        self.assertIn("DOWN", states)
        self.assertGreater(sim.stats.total_completed, 0)

    def test_pm(self):
        """PM で PM 区間ができる。"""
        overrides = {
            "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,1,0,0,100,30,0,
""",
        }
        sim = self._run(**overrides)
        states = [s for _, s, _, _, _, _ in sim.stats.equip_intervals]
        self.assertIn("PM", states)

    def test_setup(self):
        """レシピが変わると SETUP 区間ができる。"""
        overrides = {
            "recipes.csv": """recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
R1,TG1,10,lot,fixed,,,,
R2,TG1,10,lot,fixed,,,,
""",
            "routes.csv": """product,seq,recipe,step_name
P,10,R1,s1
P,20,R2,s2
""",
            "setups.csv": """tool_group,from_recipe,to_recipe,setup_time
TG1,R1,R2,8
""",
            "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,1,0,0,0,0,R1
""",
            "release.csv": """lot_id,product,release_time,priority,due_date
L1,P,0,0,
""",
            "sim.csv": """key,value
duration,600
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
""",
        }
        sim = self._run(**overrides)
        states = [s for _, s, _, _, _, _ in sim.stats.equip_intervals]
        self.assertIn("SETUP", states)
        lot = sim.lots["L1"]
        self.assertEqual(lot.t_setup_sum, 8.0)


if __name__ == "__main__":
    unittest.main()
