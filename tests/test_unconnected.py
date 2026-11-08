"""構造的な未接続(ストッカー無しエリア・CL 駅無しストッカー・到達不可の
設備)はエラーではなく警告になり、モデルは保存・実行できる。
実行時は到達不可な設備・工程にロットが送られず、現在のストッカーで
滞留するだけで例外なく duration まで進むことの検証 (設計書 6.1)。"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "web", "cgi-bin"))
import modelstore  # noqa: E402

from fabsim import config
from fabsim.engine import Simulator

from tests.helpers import write_model

SIM_SCHEDULE = """key,value
duration,300
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

SIM_SCHEDULE_S2 = SIM_SCHEDULE.replace("release_stocker,S1",
                                       "release_stocker,S2")

PRODUCTS_2 = """product,ratio,priority,due_factor,wafers
PA,1.0,0,4,25
PB,1.0,0,4,25
"""

RECIPES_2 = """recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
R1,TG1,10,lot,fixed,,,,
R2,TG2,10,lot,fixed,,,,
"""

ROUTES_PA_R1_PB_R2 = """product,seq,recipe,step_name
PA,10,R1,s1
PB,10,R2,s1
"""

ROUTES_PB_R2_R1 = """product,seq,recipe,step_name
PA,10,R1,s1
PB,10,R2,s1
PB,20,R1,s2
"""

TOOL_GROUPS_2 = """tool_group,dispatch_rule
TG1,
TG2,
"""

AREAS_2 = """area,agv_count,agv_load_time,agv_unload_time
A1,1,1,1
A2,1,1,1
"""


class UnconnectedValidateTest(unittest.TestCase):
    """検証: 構造的な未接続は警告(エラー 0)、参照切れはエラーのまま。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_area_without_stocker_warns(self):
        """エリアにストッカーが 0 基は警告(保存・実行可)。"""
        write_model(self.dir, **{
            "areas.csv": AREAS_2,          # A2 はストッカー無し
        })
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertTrue(
            any("A2" in w and "ストッカーがありません" in w
                for w in warnings), str(warnings))

    def test_area_without_stocker_with_equipment_warns(self):
        """ストッカー無しエリアに設備があると、設備名入りの警告が出る。"""
        write_model(self.dir, **{
            "areas.csv": AREAS_2,
            "equipments.csv":
                "equip_id,tool_group,area,agv_time,capacity\n"
                "EQ1,TG1,A2,5,1\n",
        })
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertTrue(
            any("EQ1" in w and "ストッカーがありません" in w
                for w in warnings), str(warnings))
        # その設備は到達不可なので工程側にも警告が出る
        self.assertTrue(
            any("到達可能な設備" in w for w in warnings), str(warnings))

    def test_two_stockers_per_area_still_error(self):
        """エリアにストッカー 2 基以上は一意に決まらないのでエラーのまま。"""
        write_model(self.dir, **{
            "areas.csv": AREAS_2,
            "stockers.csv":
                "stocker_id,area,capacity,in_ports,out_ports,port_time\n"
                "S1,A1,50,1,1,0\nS2,A2,50,1,1,0\nS3,A1,50,1,1,0\n",
            "cleanliner.csv":
                "seq,stocker_id,time_to_next\n1,S1,10\n2,S2,10\n3,S3,10\n",
        })
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(any("A1" in e and "2 基" in e for e in errors),
                        str(errors))

    def test_stocker_without_cl_station_warns(self):
        """CL 駅の無いストッカーは警告(そのストッカーは CL で到達不可)。"""
        write_model(self.dir, **{
            "areas.csv": AREAS_2,
            "stockers.csv":
                "stocker_id,area,capacity,in_ports,out_ports,port_time\n"
                "S1,A1,50,1,1,0\nS2,A2,50,1,1,0\n",
            "cleanliner.csv": "seq,stocker_id,time_to_next\n1,S1,10\n",
        })
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertTrue(
            any("S2" in w and "駅がありません" in w for w in warnings),
            str(warnings))

    def test_cl_station_unknown_stocker_is_error(self):
        """cleanliner.csv が存在しない stocker_id を指すのは参照切れ=エラー。"""
        write_model(self.dir, **{
            "cleanliner.csv":
                "seq,stocker_id,time_to_next\n1,S1,10\n2,SX,10\n",
        })
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(
            any("cleanliner.csv" in e and "SX" in e for e in errors),
            str(errors))

    def test_cl_station_duplicate_is_error(self):
        """同じストッカーの駅が 2 回登場するのはエラー。"""
        write_model(self.dir, **{
            "cleanliner.csv":
                "seq,stocker_id,time_to_next\n1,S1,10\n2,S1,10\n",
        })
        model, errors, _ = config.load_model(self.dir)
        self.assertTrue(any("重複" in e for e in errors), str(errors))

    def test_unreachable_equipment_warns(self):
        """ツールグループの設備が到達可能なエリアに無い工程は警告。"""
        write_model(self.dir, **{
            # EQ1 だけが TG1 の設備で、EQ1 は AGV 0 台の A2 にある
            "areas.csv":
                "area,agv_count,agv_load_time,agv_unload_time\n"
                "A1,1,1,1\nA2,0,1,1\n",
            "stockers.csv":
                "stocker_id,area,capacity,in_ports,out_ports,port_time\n"
                "S1,A1,50,1,1,0\nS2,A2,50,1,1,0\n",
            "cleanliner.csv":
                "seq,stocker_id,time_to_next\n1,S1,10\n2,S2,10\n",
            "equipments.csv":
                "equip_id,tool_group,area,agv_time,capacity\n"
                "EQ1,TG1,A2,5,1\n",
        })
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.assertTrue(
            any("到達可能な設備" in w for w in warnings), str(warnings))

    def test_save_model_with_warnings_succeeds(self):
        """警告のみのモデルは model_save が 200 で保存できる。"""
        write_model(self.dir, **{"areas.csv": AREAS_2})
        cid = "a1b2c3d4e5f60718"
        ok, st = modelstore.acquire_lock(self.dir, cid, "u1")
        assert ok, st
        files = modelstore.read_files(self.dir)
        status, r = modelstore.save_model(
            self.dir, files, cid, modelstore.revision(self.dir),
            "u1", True)
        self.assertEqual(status, 200, r)
        self.assertTrue(
            any("ストッカーがありません" in w for w in r["warnings"]),
            str(r))


class UnconnectedRunTest(unittest.TestCase):
    """実行: 未接続モデルでも run は例外なく duration まで進み、
    到達不可の工程のロットは現在のストッカーで滞留するだけ。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sim(self, **overrides):
        write_model(self.dir, **overrides)
        model, errors, warnings = config.load_model(self.dir)
        self.assertEqual(errors, [])
        sim = Simulator(model)
        sim.run()
        return sim

    def test_equipment_in_stockerless_area(self):
        """ストッカー無しエリアの設備は行き先候補から除外され、
        その工程のロットは現在のストッカーで滞留する。"""
        sim = self._sim(**{
            "sim.csv": SIM_SCHEDULE,
            "products.csv": PRODUCTS_2,
            "recipes.csv": RECIPES_2,
            "routes.csv": ROUTES_PA_R1_PB_R2,
            "tool_groups.csv": TOOL_GROUPS_2,
            "equipments.csv":
                "equip_id,tool_group,area,agv_time,capacity\n"
                "EQ1,TG1,A1,5,1\nEQ2,TG2,A2,5,1\n",
            "areas.csv": AREAS_2,       # A2 にストッカー無し
            # stockers/cleanliner は既定(S1/A1 のみ)
            "release.csv":
                "lot_id,product,release_time\nLA,PA,0\nLB,PB,0\n",
        })
        # PB の工程は TG2 → A2 の EQ2 だが A2 へは到達不可 → S1 で滞留
        lb = sim.lots["LB"]
        self.assertIsNone(lb.complete_time)
        self.assertEqual(lb.state, "QUEUED")
        self.assertEqual(lb.cur_stocker, "S1")
        self.assertIn("LB", sim.stockers["S1"].stored)
        # A2 の設備 EQ2 は一度も使われない(AGV タスクも無い)
        self.assertEqual(len(sim.fleets["A2"].queue), 0)
        eq2_states = {s for eid, s, _l, _r, _t0, _t1
                      in sim.stats.equip_intervals if eid == "EQ2"}
        self.assertEqual(eq2_states, {"IDLE"})
        # A1 側の LA は正常に完了
        self.assertIsNotNone(sim.lots["LA"].complete_time)
        sim.stats.summary(sim)

    def test_release_into_non_station_stocker(self):
        """投入ストッカーが CL 駅でない場合、ロットはそのエリア内だけ
        処理でき、他エリアの工程には進めずストッカーで滞留する。"""
        sim = self._sim(**{
            "sim.csv": SIM_SCHEDULE_S2,   # release_stocker=S2
            "products.csv": PRODUCTS_2,
            "recipes.csv": RECIPES_2,
            "routes.csv": ROUTES_PB_R2_R1,
            "tool_groups.csv": TOOL_GROUPS_2,
            "equipments.csv":
                "equip_id,tool_group,area,agv_time,capacity\n"
                "EQ1,TG1,A1,5,1\nEQ2,TG2,A2,5,1\n",
            "areas.csv": AREAS_2,
            "stockers.csv":
                "stocker_id,area,capacity,in_ports,out_ports,port_time\n"
                "S1,A1,50,1,1,0\nS2,A2,50,1,1,0\n",
            # S2 は CL 駅ではない
            "cleanliner.csv": "seq,stocker_id,time_to_next\n1,S1,10\n",
            "release.csv":
                "lot_id,product,release_time\nLA,PA,0\nLB,PB,0\n",
        })
        lb = sim.lots["LB"]
        # 工程1(TG2)は A2 の EQ2 で処理できたが、工程2(TG1→A1)へは
        # CL 駅の無い S2 から出られないので S2 で滞留
        self.assertIsNone(lb.complete_time)
        self.assertEqual(lb.state, "QUEUED")
        self.assertEqual(lb.cur_stocker, "S2")
        self.assertIn("LB", sim.stockers["S2"].stored)
        self.assertEqual(len(lb.history), 1)       # R2 は処理済み
        self.assertEqual(lb.history[0][3], "EQ2")
        # CL は一度も走らない
        self.assertEqual(sum(sim.cl.rides_done), 0)
        # LA(PA→TG1→A1)は S2 から出られないので同じく滞留
        la = sim.lots["LA"]
        self.assertIsNone(la.complete_time)
        self.assertEqual(la.cur_stocker, "S2")
        sim.stats.summary(sim)

    def test_dest_selection_skips_unreachable_area(self):
        """同一 TG の設備が到達可能/不可の両エリアにある場合、
        ロットは到達可能な側の設備だけで処理される。"""
        sim = self._sim(**{
            "sim.csv": SIM_SCHEDULE,
            "equipments.csv":
                "equip_id,tool_group,area,agv_time,capacity\n"
                "EQ1,TG1,A1,5,1\nEQ3,TG1,A3,5,1\n",
            "areas.csv":
                "area,agv_count,agv_load_time,agv_unload_time\n"
                "A1,1,1,1\nA3,1,1,1\n",
            # A3 はストッカー無し → EQ3 は到達不可
            "release.csv":
                "lot_id,product,release_time\nL1,P,0\nL2,P,10\nL3,P,20\n",
        })
        for lid in ("L1", "L2", "L3"):
            self.assertIsNotNone(sim.lots[lid].complete_time)
            for h in sim.lots[lid].history:
                self.assertEqual(h[3], "EQ1")      # EQ3 は使われない
        eq3_states = {s for eid, s, _l, _r, _t0, _t1
                      in sim.stats.equip_intervals if eid == "EQ3"}
        self.assertEqual(eq3_states, {"IDLE"})
        sim.stats.summary(sim)


if __name__ == "__main__":
    unittest.main()
