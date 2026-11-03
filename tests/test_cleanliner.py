"""クリーンライナー (設計書 5.5.5, 9章): 到着時刻・スロット予約・周回待ち。"""
import unittest

from fabsim.transport.cleanliner import CleanLiner, Ride

from tests.helpers import make_lot


STATIONS = [(1, "S1", 10.0), (2, "S2", 20.0), (3, "S3", 30.0)]  # T=60


def _cl(carriers=1, slots=1, load=2.0, unload=3.0):
    return CleanLiner(STATIONS, carriers, slots, load, unload)


def _port_free(sid, t):
    return t    # 常にポート空き


class CleanLinerTest(unittest.TestCase):
    def test_arrival_time(self):
        """周回時間 T と位相から、キャリア k の駅 s 到着時刻が手計算と一致。"""
        cl = _cl(carriers=2)
        self.assertEqual(cl.T, 60.0)
        self.assertEqual(cl.base, [0.0, 10.0, 30.0])
        # 位相: k=0 → 0, k=1 → 30
        self.assertEqual(cl.phase, [0.0, 30.0])
        # k=0 が S2 に n=0 回到着: base[1] + 0 + 0 = 10
        self.assertEqual(cl.arrival(0, 1, 0), 10.0)
        # k=1 が S3 に n=2 回到着: base[2] + 30 + 2*60 = 180
        self.assertEqual(cl.arrival(1, 2, 2), 180.0)

    def test_forward_only(self):
        """逆走しない: 所要時間は常に順方向の区間和。"""
        cl = _cl()
        self.assertEqual(cl.travel(0, 1), 10.0)
        self.assertEqual(cl.travel(0, 2), 30.0)
        self.assertEqual(cl.travel(2, 0), 30.0)   # S3→S1 はラップして前方へ
        self.assertEqual(cl.travel(1, 0), 50.0)   # S2→S1 は一周近い
        # 1周はちょうど T
        self.assertEqual(sum(cl.time_to_next), cl.T)

    def test_next_arrival(self):
        cl = _cl()
        # t=25 の次の S1(idx0) 到着: 60
        self.assertEqual(cl.next_arrival_n(0, 0, 25.0), 1)
        self.assertEqual(cl.arrival(0, 0, cl.next_arrival_n(0, 0, 25.0)), 60.0)

    def test_reserve_earliest(self):
        """最早到着のキャリア・スロットに予約される。"""
        cl = _cl(carriers=1, slots=1)
        r = Ride(1, make_lot("L1"), 0, 2, 0.0)   # S1→S3
        got = cl.reserve(r, 0.0, _port_free)
        self.assertIs(got, r)
        self.assertEqual(r.carrier, 0)
        self.assertEqual(r.t_arr_dep, 0.0)
        self.assertEqual(r.t_load, 2.0)          # 到着+積込み時間
        self.assertEqual(r.t_arr_dst, 30.0)      # travel(0→2)=30
        self.assertEqual(r.t_unload, 33.0)       # +荷降ろし3

    def test_slot_full_goes_next_loop(self):
        """1台・1スロットで2件同時要求は2件目が +T 後になる。"""
        cl = _cl(carriers=1, slots=1)
        r1 = Ride(1, make_lot("L1"), 0, 1, 0.0)
        r2 = Ride(2, make_lot("L2"), 0, 1, 0.0)
        cl.reserve(r1, 0.0, _port_free)
        cl.reserve(r2, 0.0, _port_free)
        self.assertEqual(r1.t_arr_dep, 0.0)
        self.assertEqual(r2.t_arr_dep, cl.T)     # 次の周回

    def test_no_slot_overlap(self):
        """同一スロットに予約区間が重ならない。"""
        cl = _cl(carriers=2, slots=2)
        rides = []
        for i in range(6):
            r = Ride(i, make_lot("L%d" % i), 0, 2, 0.0)
            cl.reserve(r, 0.0, _port_free)
            rides.append(r)
        for (k, j), rs in cl.resv.items():
            rs = sorted(rs, key=lambda r: r.t_load)
            for a, b in zip(rs, rs[1:]):
                self.assertLessEqual(a.t_unload, b.t_load,
                                     "スロット %s/%s が重複" % (k, j))

    def test_load_time_adds(self):
        """同じ駅・キャリアへの同時積込みはロットごとに積込み時間が加算される。"""
        cl = _cl(carriers=1, slots=2)
        r1 = Ride(1, make_lot("L1"), 0, 1, 0.0)
        r2 = Ride(2, make_lot("L2"), 0, 1, 0.0)
        cl.reserve(r1, 0.0, _port_free)
        cl.reserve(r2, 0.0, _port_free)
        # 2件目は別スロットだが積込みは直列 → t_load が +load_time
        self.assertEqual(r1.t_load, 2.0)
        self.assertEqual(r2.t_load, 4.0)
        self.assertEqual(r1.t_arr_dep, r2.t_arr_dep)

    def test_port_busy_skips_arrival(self):
        """出発駅のポートが埋まっているキャリア到着は使えない。"""
        cl = _cl(carriers=1, slots=1)
        r = Ride(1, make_lot("L1"), 0, 1, 0.0)
        # t=0 の到着はポート使用中、+T 後は空く
        calls = []

        def port_free(sid, t):
            calls.append(t)
            return t + 5.0 if t < 10.0 else t

        cl.reserve(r, 0.0, port_free)
        self.assertEqual(r.t_arr_dep, cl.T)


if __name__ == "__main__":
    unittest.main()
