"""設備ポート数(ports)の実装テスト。"""
import os
import shutil
import tempfile
import unittest

from fabsim import config, engine
from tests.helpers import write_model


def _model(dirpath, **overrides):
    write_model(dirpath, **overrides)
    m, errors, warnings = config.load_model(dirpath)
    return m, errors, warnings


class PortsConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_is_capacity(self):
        m, errors, _ = _model(self.dir)
        self.assertFalse(errors)
        self.assertEqual(m.equipments["EQ1"].ports, 1)

    def test_ports_lt_capacity_error(self):
        _, errors, _ = _model(
            self.dir, **{
                "equipments.csv":
                    "equip_id,tool_group,area,agv_time,capacity,ports\n"
                    "EQ1,TG1,A1,5,4,2\n"})
        self.assertTrue(any("ports" in e for e in errors))

    def test_batch_ports_clamped_warn(self):
        _, errors, warnings = _model(
            self.dir, **{
                "equipments.csv":
                    "equip_id,tool_group,area,agv_time,capacity,ports\n"
                    "EQ1,TG1,A1,5,4,8\n"})
        self.assertFalse(errors)
        self.assertTrue(any("ports" in w for w in warnings))
        m, _, _ = config.load_model(self.dir)
        self.assertEqual(m.equipments["EQ1"].ports, 4)


class PortsEngineTest(unittest.TestCase):
    """1設備・ports=2 で先行搬入が起きるか。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, ports_val):
        eq_csv = ("equip_id,tool_group,area,agv_time,capacity,ports\n"
                  "EQ1,TG1,A1,0,%s\n" % ports_val)
        m, errors, _ = _model(
            self.dir, **{
                "equipments.csv": eq_csv,
                "recipes.csv":
                    "recipe,tool_group,process_time,time_unit\n"
                    "R1,TG1,10,lot\n",
                "routes.csv":
                    "product,seq,recipe\nP,10,R1\n",
                "sim.csv":
                    "key,value\nduration,60\nwarmup,0\nseed,1\n"
                    "dispatch_rule,FIFO\nrelease_mode,schedule\n"
                    "lots_per_day,0\nrelease_control,fixed\nwip_limit,0\n"
                    "release_stocker,S1\narea_select,LEAST_LOADED\n"
                    "agv_out_first,1\ncl_carriers,1\ncl_slots,1\n"
                    "cl_load_time,1\ncl_unload_time,1\n"
                    "failure_policy,resume\n",
                "release.csv":
                    "lot_id,product,release_time\nL1,P,0\nL2,P,0\nL3,P,0\n"})
        self.assertFalse(errors)
        eng = engine.Simulator(m)
        eng.run()
        return eng

    def test_preload_second_lot(self):
        eng = self._run("1,2")
        lots = {lid: l for lid, l in eng.lots.items()}
        self.assertEqual(len([l for l in lots.values()
                              if l.complete_time is not None]), 3)
        # 先行搬入: L2 は L1 の ProcessEnd 直後に処理開始
        h1 = lots["L1"].history[0]
        h2 = lots["L2"].history[0]
        self.assertAlmostEqual(h2[5], h1[6], places=3)

    def test_no_preload_default(self):
        """ports=1 では L2 は L1 終了後に搬入 → 開始が遅れる。"""
        eng = self._run("1,1")
        lots = eng.lots
        h1 = lots["L1"].history[0]
        h2 = lots["L2"].history[0]
        self.assertGreater(h2[5], h1[6] + 1.0)

    def test_ports_default_unchanged(self):
        """ports 未指定(既定=1)では従来通り逐次処理。"""
        m, errors, _ = _model(
            self.dir, **{
                "recipes.csv":
                    "recipe,tool_group,process_time,time_unit\n"
                    "R1,TG1,10,lot\n",
                "routes.csv":
                    "product,seq,recipe\nP,10,R1\n",
                "sim.csv":
                    "key,value\nduration,100\nwarmup,0\nseed,1\n"
                    "dispatch_rule,FIFO\nrelease_mode,schedule\n"
                    "lots_per_day,0\nrelease_control,fixed\nwip_limit,0\n"
                    "release_stocker,S1\narea_select,LEAST_LOADED\n"
                    "agv_out_first,1\ncl_carriers,1\ncl_slots,1\n"
                    "cl_load_time,1\ncl_unload_time,1\n"
                    "failure_policy,resume\n",
                "release.csv":
                    "lot_id,product,release_time\nL1,P,0\nL2,P,0\n"})
        self.assertFalse(errors)
        eng = engine.Simulator(m)
        eng.run()
        self.assertEqual(eng.stats.total_completed, 2)


if __name__ == "__main__":
    unittest.main()
