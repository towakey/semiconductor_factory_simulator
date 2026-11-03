"""クリーンライナー (設計書 5.5.5)。
円形一方通行ループを複数キャリアが等速周回する。キャリア位置は解析的なので
移動自体はイベント化せず、予約(貪欲・FIFO)で積込み/荷降ろし時刻を決める。
"""
import math


class Ride(object):
    __slots__ = ("ride_id", "lot", "from_idx", "to_idx", "request_time",
                 "carrier", "slot", "t_arr_dep", "t_load", "t_arr_dst",
                 "t_unload", "status", "reloops")

    def __init__(self, ride_id, lot, from_idx, to_idx, request_time):
        self.ride_id = ride_id
        self.lot = lot
        self.from_idx = from_idx
        self.to_idx = to_idx
        self.request_time = request_time
        self.carrier = -1
        self.slot = -1
        self.t_arr_dep = 0.0
        self.t_load = 0.0
        self.t_arr_dst = 0.0
        self.t_unload = 0.0
        self.status = "waiting"     # waiting | riding | done
        self.reloops = 0


class CleanLiner(object):
    def __init__(self, cl_stations, carriers, slots, load_time, unload_time):
        # cl_stations: [(seq, stocker_id, time_to_next)] seq 昇順
        self.stations = [sid for _, sid, _ in cl_stations]
        self.time_to_next = [t for _, _, t in cl_stations]
        self.station_idx = {sid: i for i, sid in enumerate(self.stations)}
        self.n_st = len(self.stations)
        self.base = [0.0] * self.n_st   # 位相0キャリアの駅到着時刻
        acc = 0.0
        for i in range(self.n_st):
            self.base[i] = acc
            acc += self.time_to_next[i]
        self.T = acc
        self.M = max(1, carriers)
        self.S = max(1, slots)
        self.load_time = load_time
        self.unload_time = unload_time
        self.phase = [k * self.T / self.M for k in range(self.M)]
        self.resv = {}            # (carrier, slot) -> [Ride]
        for k in range(self.M):
            for j in range(self.S):
                self.resv[(k, j)] = []
        self.boarded = {}         # (carrier, station_idx, pass) -> 積込み予定数
        # stats
        self.rides_done = [0] * self.M
        self.slot_busy = [0.0] * self.M
        self.load_wait_sum = [0.0] * self.M
        self.reloop_sum = [0] * self.M

    def travel(self, i_from, i_to):
        """駅 i_from → i_to の順方向所要時間(1周以内)。"""
        t = 0.0
        i = i_from
        while i != i_to:
            t += self.time_to_next[i]
            i = (i + 1) % self.n_st
        return t

    def dest_pass(self, i_from, i_to, n):
        """出発駅への第 n 回到着で積んだ場合、到着駅に着くのは第何回目か。"""
        return n if i_to > i_from else n + 1

    def arrival(self, carrier, station_idx, n):
        """キャリア k が駅 s に n 回目(0起算)に到着する時刻。"""
        return self.base[station_idx] + self.phase[carrier] + n * self.T

    def next_arrival_n(self, carrier, station_idx, t):
        x = (t - self.base[station_idx] - self.phase[carrier]) / self.T
        return max(0, int(math.ceil(x - 1e-9)))

    def _fits(self, k, j, t0, t1):
        for r in self.resv[(k, j)]:
            if r.t_load < t1 and t0 < r.t_unload:
                return False
        return True

    def reserve(self, ride, now, port_free_at):
        """最も早く積めるキャリア/スロットを貪欲に予約する。
        port_free_at(stocker_id, t): 出発ストッカーの out ポートが空く時刻。
        ポートが到着時刻に空いていないキャリア到着は使えない。
        戻り値: ride(確定) または None(到着駅=出発駅など)。"""
        sid_from = self.stations[ride.from_idx]
        best = None
        for k in range(self.M):
            n = self.next_arrival_n(k, ride.from_idx, now)
            own = None
            for _ in range(64):     # スロットが埋まれば次の周回(+T)を探す
                t_arr = self.arrival(k, ride.from_idx, n)
                if best is not None and t_arr >= best[0]:
                    break           # このキャリアは best より遅い
                port_at = port_free_at(sid_from, t_arr)
                if port_at > t_arr + 1e-9:
                    n += 1
                    continue
                extra = self.boarded.get((k, ride.from_idx, n), 0) \
                    * self.load_time
                t_load = t_arr + self.load_time + extra
                m = self.dest_pass(ride.from_idx, ride.to_idx, n)
                t_arr_dst = self.arrival(k, ride.to_idx, m)
                t_unload = t_arr_dst + self.unload_time
                for j in range(self.S):
                    if self._fits(k, j, t_load, t_unload):
                        own = (t_arr, k, j, n, t_load, t_arr_dst, t_unload)
                        break
                if own is not None:
                    break           # このキャリアの最早が確定
                n += 1
            if own is not None and (best is None or own[:2] < best[:2]):
                best = own
        if best is None:
            return None
        t_arr, k, j, n, t_load, t_arr_dst, t_unload = best
        ride.carrier, ride.slot = k, j
        ride.t_arr_dep, ride.t_load = t_arr, t_load
        ride.t_arr_dst, ride.t_unload = t_arr_dst, t_unload
        self.resv[(k, j)].append(ride)
        self.boarded[(k, ride.from_idx, n)] = \
            self.boarded.get((k, ride.from_idx, n), 0) + 1
        return ride

    def remove_resv(self, ride):
        lst = self.resv.get((ride.carrier, ride.slot))
        if lst and ride in lst:
            lst.remove(ride)

    def slot_busy_time(self, k, duration):
        return self.slot_busy[k] / (self.S * duration) if duration > 0 else 0.0
