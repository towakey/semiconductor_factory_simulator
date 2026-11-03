"""ストッカー (設計書 5.5.3, 9章): 満杯拒否・ポート・周回再試行・AGV待機。"""
import shutil
import tempfile
import unittest

from fabsim import config
from fabsim.engine import Simulator
from fabsim.models import StockerCfg
from fabsim.transport.cleanliner import Ride
from fabsim.transport.stocker import Stocker

from tests.helpers import write_model


class StockerUnitTest(unittest.TestCase):
    def test_capacity_and_reject(self):
        st = Stocker(StockerCfg("S1", "A1", capacity=2))
        st.add("L1", 0.0)
        st.add("L2", 0.0)
        self.assertTrue(st.is_full())
        st.remove("L1", 10.0)
        self.assertFalse(st.is_full())
        self.assertEqual(st.count, 1)
        self.assertEqual(st.max_occ, 2)
        st.finalize(100.0)
        self.assertAlmostEqual(st.avg_stored(100.0), (2 * 10 + 1 * 90) / 100.0)
        # 満杯時間: [0,10) の 10 分
        self.assertAlmostEqual(st.full_time, 10.0)

    def test_inbound_reserved_counts_as_full(self):
        st = Stocker(StockerCfg("S1", "A1", capacity=1))
        st.inbound_reserved = 1
        self.assertTrue(st.is_full())

    def test_port_booking(self):
        st = Stocker(StockerCfg("S1", "A1", capacity=10, in_ports=1,
                                port_time=5.0))
        s1, w1 = st.request_port("in", 0.0)
        self.assertEqual((s1, w1), (0.0, 0.0))
        s2, w2 = st.request_port("in", 1.0)
        self.assertEqual((s2, w2), (5.0, 4.0))   # ポート占有中は待つ


class StockerEngineTest(unittest.TestCase):
    """満杯時の CL 周回再試行・AGV 設備側待機(ホワイトボックス)。"""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        write_model(self.dir)
        model, errors, _ = config.load_model(self.dir)
        self.assertEqual(errors, [])
        self.sim = Simulator(model)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cl_reloop_on_full(self):
        """到着駅のストッカーが満杯ならキャリアは +T 周回して再試行する。"""
        from fabsim import events as ev
        sim = self.sim
        st = sim.stockers["S1"]
        st.add("DUMMY", 0.0)
        st.capacity = 1                       # 強制的に満杯にする
        ride = Ride(1, object(), 0, 0, 0.0)
        ride.carrier, ride.slot = 0, 0
        ride.t_load, ride.t_arr_dst = 0.0, 10.0
        ride.t_unload = 11.0
        sim.cl.resv[(0, 0)].append(ride)
        sim.now = 10.0
        sim._on_cl_arrive(ev.ClArrive(ride))
        self.assertEqual(st.rejects, 1)
        self.assertEqual(ride.reloops, 1)
        self.assertEqual(ride.t_arr_dst, 10.0 + sim.cl.T)
        self.assertTrue(sim.q._h)             # 再試行イベントが予約された

    def test_agv_waits_on_full(self):
        """AGV 搬出先が満杯なら AGV はロットを載せたまま待機する。"""
        from fabsim import events as ev
        from fabsim.transport.agv import AgvTask
        sim = self.sim
        st = sim.stockers["S1"]
        st.add("DUMMY", 0.0)
        st.capacity = 1
        fleet = sim.fleets["A1"]
        agv = fleet.agvs[0]
        lot = type("L", (), {"lot_id": "LX", "t_port_wait": 0.0,
                             "t_agv_move": 0.0, "priority": 0})()
        task = AgvTask(lot, "out", "EQ1", "S1", 0.0, 0)
        task.agv = agv
        agv.task = task
        agv.state = "CARRYING"
        task.depart_from = 0.0
        sim.now = 10.0
        sim._on_agv_leg(ev.AgvLeg(fleet, task, "at_to"))
        self.assertEqual(st.rejects, 1)
        self.assertEqual(len(st.pending_in), 1)
        self.assertEqual(agv.state, "CARRYING")   # まだ搬送中のまま
        # 空きが出ると pending が処理される
        sim.now = 20.0
        sim._stocker_remove(st, "DUMMY", 20.0)
        self.assertEqual(len(st.pending_in), 0)
        self.assertTrue(any(e.phase == "unloaded" for _, _, _, e in sim.q._h
                            if isinstance(e, ev.AgvLeg)))


if __name__ == "__main__":
    unittest.main()
