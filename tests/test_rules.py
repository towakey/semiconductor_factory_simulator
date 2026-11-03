"""ディスパッチルールの選択順 (設計書 9章)。"""
import unittest

from fabsim import rules
from fabsim.models import Equipment
from fabsim.rules import QueueEntry

from tests.helpers import make_lot


def _q(entries):
    return [QueueEntry(lot, t) for lot, t in entries]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.eq = Equipment(equip_id="E", tool_group="TG", area="A",
                            agv_time=1.0)

    def test_fifo(self):
        q = _q([(make_lot("L1"), 5.0), (make_lot("L2"), 1.0),
                (make_lot("L3"), 3.0)])
        e = rules.fifo(q, self.eq, 0.0)
        self.assertEqual(e.lot.lot_id, "L2")

    def test_priority(self):
        q = _q([(make_lot("L1", priority=1), 1.0),
                (make_lot("L2", priority=9), 5.0),
                (make_lot("L3", priority=9), 2.0)])
        e = rules.priority(q, self.eq, 0.0)
        self.assertEqual(e.lot.lot_id, "L3")   # 同優先度は到着順

    def test_edd(self):
        q = _q([(make_lot("L1", due=100.0), 1.0),
                (make_lot("L2", due=50.0), 5.0)])
        e = rules.edd(q, self.eq, 0.0)
        self.assertEqual(e.lot.lot_id, "L2")

    def test_spt(self):
        q = _q([(make_lot("L1", proc_times=(30.0,)), 1.0),
                (make_lot("L2", proc_times=(5.0,)), 5.0)])
        e = rules.spt(q, self.eq, 0.0)
        self.assertEqual(e.lot.lot_id, "L2")

    def test_min_setup(self):
        self.eq.last_recipe = "RB"
        q = _q([(make_lot("L1", recipes=["RA"]), 1.0),
                (make_lot("L2", recipes=["RB"]), 5.0)])
        e = rules.min_setup(q, self.eq, 0.0)
        self.assertEqual(e.lot.lot_id, "L2")

    def test_cr(self):
        # CR = (納期-現在)/残処理時間 が小さい順
        q = _q([(make_lot("L1", due=100.0, proc_times=(10.0,)), 1.0),
                (make_lot("L2", due=100.0, proc_times=(50.0,)), 5.0)])
        e = rules.cr(q, self.eq, 0.0)
        # L1: (100-0)/10=10, L2: 100/50=2 → L2
        self.assertEqual(e.lot.lot_id, "L2")

    def test_empty(self):
        self.assertIsNone(rules.fifo([], self.eq, 0.0))


if __name__ == "__main__":
    unittest.main()
