"""AGV (設計書 5.5.4, 9章): タスク所要時間・最小空走選択・搬出優先。"""
import shutil
import tempfile
import unittest

from fabsim import config
from fabsim.engine import Simulator
from fabsim.transport.agv import AgvFleet, AgvTask
from fabsim.models import Area

from tests.helpers import make_lot, write_model


class AgvUnitTest(unittest.TestCase):
    def _fleet(self, n=2, out_first=1):
        cfg = Area(name="A1", agv_count=n, agv_load_time=1.0,
                   agv_unload_time=1.0)
        return AgvFleet(cfg, "S1", out_first=bool(out_first))

    def test_task_duration(self):
        """1台・1タスクの所要時間 = 空走 + ロード + 実走 + アンロード。"""
        fleet = self._fleet(n=1)
        times = {"EQ1": 5.0}
        # AGV は S1 にいる。タスク S1→EQ2。
        empty = fleet.dist("S1", "S1", times)
        run = fleet.dist("S1", "EQ1", times)
        self.assertEqual(empty, 0.0)
        self.assertEqual(run, 5.0)
        total = empty + fleet.load_time + run + fleet.unload_time
        self.assertEqual(total, 7.0)
        # 設備→設備の空走は ハブ経由
        fleet2 = self._fleet(n=1)
        fleet2.agvs[0].pos = "EQ2"
        self.assertEqual(fleet2.dist("EQ2", "S1", {"EQ2": 3.0}), 3.0)
        self.assertEqual(fleet2.dist("EQ2", "EQ1", {"EQ2": 3.0, "EQ1": 5.0}),
                         8.0)

    def test_min_empty_agv_selected(self):
        """複数台では空走時間が最小の AGV が選ばれる。"""
        fleet = self._fleet(n=2)
        fleet.agvs[0].pos = "EQ_FAR"     # dist 10
        fleet.agvs[1].pos = "EQ_NEAR"    # dist 2
        times = {"EQ_FAR": 10.0, "EQ_NEAR": 2.0}
        task = AgvTask(make_lot("L1"), "in", "S1", "EQ1", 0.0, 0)
        picks = sorted(fleet.agvs,
                       key=lambda a: (fleet.dist(a.pos, task.from_node, times),
                                      a.agv_id))
        self.assertEqual(picks[0].agv_id, fleet.agvs[1].agv_id)

    def test_out_first(self):
        """agv_out_first=1 では搬出が搬入より先に割当される。"""
        fleet = self._fleet(n=1, out_first=1)
        fleet.request(AgvTask(make_lot("LIN"), "in", "S1", "EQ1", 0.0, -1))
        fleet.request(AgvTask(make_lot("LOUT"), "out", "EQ1", "S1", 5.0, -1))
        fleet.queue.sort(key=fleet._task_key)
        self.assertEqual(fleet.queue[0].kind, "out")
        # out_first=0 なら依頼時刻順
        fleet2 = self._fleet(n=1, out_first=0)
        fleet2.request(AgvTask(make_lot("L1"), "in", "S1", "EQ1", 0.0, -1))
        fleet2.request(AgvTask(make_lot("L2"), "out", "EQ1", "S1", 5.0, -1))
        fleet2.queue.sort(key=fleet2._task_key)
        self.assertEqual(fleet2.queue[0].lot.lot_id, "L1")


class AgvIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_task_time_in_sim(self):
        """実際のシミュレーションでも 1 タスク = 7 分を記録する。"""
        overrides = {
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
        write_model(self.dir, **overrides)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        lot = sim.lots["L1"]
        # 搬入3回+搬出2回 = 5 タスク × 7 分 = 35 (うち loaded 走行内訳=25)
        self.assertEqual(lot.n_agv, 5)
        self.assertAlmostEqual(lot.t_agv_move, 35.0, places=6)
        agv = sim.fleets["A1"].agvs[0]
        self.assertEqual(agv.tasks, 5)


class ZeroAgvAreaTest(unittest.TestCase):
    """agv_count=0 のエリアは到達不可として行き先候補から除外され、
    ロットは現在のストッカーに滞留するが、他エリアは正常に進み、
    run は例外なく duration まで進む。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_zero_agv_area_waits_but_sim_completes(self):
        write_model(self.dir, **{
            "sim.csv": """key,value
duration,120
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
            "products.csv": """product,ratio,priority,due_factor,wafers
PA,1.0,0,4,25
PB,1.0,0,4,25
""",
            "recipes.csv": """recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
R1,TG1,10,lot,fixed,,,,
R2,TG2,10,lot,fixed,,,,
""",
            "routes.csv": """product,seq,recipe,step_name
PA,10,R1,s1
PB,10,R2,s1
""",
            "tool_groups.csv": """tool_group,dispatch_rule
TG1,
TG2,
""",
            "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,1,0,0,0,0,
EQ2,TG2,A2,5,1,0,0,0,0,
""",
            "areas.csv": """area,agv_count,agv_load_time,agv_unload_time
A1,1,1,1
A2,0,1,1
""",
            "stockers.csv": """stocker_id,area,capacity,in_ports,out_ports,port_time
S1,A1,50,1,1,0
S2,A2,50,1,1,0
""",
            "cleanliner.csv": """seq,stocker_id,time_to_next
1,S1,10
2,S2,10
""",
            "release.csv": """lot_id,product,release_time,priority,due_date
LA,PA,0,0,
LB,PB,0,0,
""",
        })
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertTrue(any("AGV 0 台" in w for w in warnings),
                        str(warnings))
        # AGV 0 台エリアの設備は「到達可能な設備なし」の警告も出る
        self.assertTrue(any("到達可能な設備" in w for w in warnings),
                        str(warnings))
        sim = Simulator(model)
        sim.run()                       # 例外なく duration まで進む
        # A2 は行き先候補から除外されるため AGV タスク自体が発行されない
        self.assertEqual(sim.fleets["A2"].agvs, [])
        self.assertEqual(len(sim.fleets["A2"].queue), 0)
        # LB は A1 のストッカーでキュー滞留(未完)
        lb = sim.lots["LB"]
        self.assertIsNone(lb.complete_time)
        self.assertEqual(lb.state, "QUEUED")
        self.assertEqual(lb.cur_stocker, "S1")
        self.assertIn("LB", sim.stockers["S1"].stored)
        # A1 側の LA は正常に完了
        self.assertIsNotNone(sim.lots["LA"].complete_time)
        # 統計も 0 台エリアを除算しない(t=60 の KPI tick 実行済み)
        a2_util = [r[3] for r in sim.stats.kpi_rows
                   if r[1] == "agv_util" and r[2] == "A2"]
        self.assertTrue(a2_util and all(v == 0.0 for v in a2_util))
        sim.stats.summary(sim)


if __name__ == "__main__":
    unittest.main()


class ReturnHomeTest(unittest.TestCase):
    """agv_return_home=1 でタスク完了後ストッカーへ戻る。"""
    def _sim(self, return_home):
        import tempfile, shutil
        from fabsim import config
        from fabsim.engine import Simulator
        d = tempfile.mkdtemp()
        areas = ("area,agv_count,agv_load_time,agv_unload_time,"
                 "agv_return_home\nA1,1,1,1,%d\n" % return_home)
        write_model(d, **{
            "areas.csv": areas,
            "sim.csv": ("key,value\nduration,120\nwarmup,0\nseed,1\n"
                        "dispatch_rule,FIFO\nrelease_mode,schedule\n"
                        "lots_per_day,0\nrelease_control,fixed\nwip_limit,0\n"
                        "release_stocker,S1\narea_select,LEAST_LOADED\n"
                        "agv_out_first,1\ncl_carriers,1\ncl_slots,1\n"
                        "cl_load_time,1\ncl_unload_time,1\n"
                        "failure_policy,resume\n"),
            "release.csv":
                "lot_id,product,release_time\nL1,P,0\nL2,P,0\n"})
        m, errors, _ = config.load_model(d)
        assert not errors, errors
        sim = Simulator(m)
        sim.run()
        shutil.rmtree(d, ignore_errors=True)
        return sim

    def test_return_interval(self):
        sim = self._sim(1)
        # DB 無し実行では agv_moves は見えないため、AGV の位置で確認
        agv = sim.fleets["A1"].agvs[0]
        # 全タスク終了後、AGV はストッカーに戻っている
        self.assertEqual(agv.pos, "S1")

    def test_no_return_when_disabled(self):
        sim = self._sim(0)
        agv = sim.fleets["A1"].agvs[0]
        # 戻り無しなら直前の設備位置に残る(S1 以外の場合)
        self.assertIn(agv.pos, ("S1", "EQ1"))

    def test_return_then_new_task_assigned(self):
        """戻り中に新タスクが来ても戻り完了後に割当される(非割込み)。"""
        import tempfile, shutil
        from fabsim import config
        from fabsim.engine import Simulator
        d = tempfile.mkdtemp()
        write_model(d, **{
            "sim.csv": ("key,value\nduration,300\nwarmup,0\nseed,1\n"
                        "dispatch_rule,FIFO\nrelease_mode,schedule\n"
                        "lots_per_day,0\nrelease_control,fixed\nwip_limit,0\n"
                        "release_stocker,S1\narea_select,LEAST_LOADED\n"
                        "agv_out_first,1\ncl_carriers,1\ncl_slots,1\n"
                        "cl_load_time,1\ncl_unload_time,1\n"
                        "failure_policy,resume\n"),
            "release.csv":
                "lot_id,product,release_time\nL1,P,0\nL2,P,60\n"})
        m, e, _ = config.load_model(d)
        assert not e
        sim = Simulator(m)
        sim.run()
        # L2 も処理完了(戻り中割込みなしでも終了時刻内に割当)
        self.assertIsNotNone(sim.lots["L2"].complete_time)
        self.assertEqual(sim.fleets["A1"].agvs[0].pos, "S1")
        shutil.rmtree(d, ignore_errors=True)
