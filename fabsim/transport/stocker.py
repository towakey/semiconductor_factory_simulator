"""ストッカー (設計書 5.5.3)。格納数・入出庫ポート・満杯拒否を管理する。"""


class Stocker(object):
    def __init__(self, cfg):
        self.stocker_id = cfg.stocker_id
        self.area = cfg.area
        self.capacity = cfg.capacity
        self.port_time = cfg.port_time
        self.in_port_free = [0.0] * cfg.in_ports
        self.out_port_free = [0.0] * cfg.out_ports
        self.stored = {}            # lot_id -> 格納時刻
        self.inbound_reserved = 0   # 格納予約済み(搬送中)のスロット数
        self.pending_in = []        # 満杯で待機中の格納要求
        # stats
        self.occ_integral = 0.0
        self.full_time = 0.0
        self.last_t = 0.0
        self.max_occ = 0
        self.rejects = 0

    @property
    def count(self):
        return len(self.stored)

    def is_full(self):
        return self.count + self.inbound_reserved >= self.capacity

    def _acc(self, now):
        dt = now - self.last_t
        if dt > 0:
            self.occ_integral += self.count * dt
            if self.count >= self.capacity:
                self.full_time += dt
        self.last_t = now

    def add(self, lot_id, now):
        self._acc(now)
        self.stored[lot_id] = now
        if self.count > self.max_occ:
            self.max_occ = self.count

    def remove(self, lot_id, now):
        self._acc(now)
        self.stored.pop(lot_id, None)

    def port_start(self, kind, t):
        """時刻 t にポート要求した場合の開始時刻とポート番号(予約しない)。"""
        free = self.in_port_free if kind == "in" else self.out_port_free
        idx = min(range(len(free)), key=lambda i: (free[i], i))
        return max(t, free[idx]), idx

    def book_port(self, kind, idx, start):
        free = self.in_port_free if kind == "in" else self.out_port_free
        free[idx] = start + self.port_time

    def request_port(self, kind, t):
        """開始時刻を決めてポートを予約する。戻り値 (start, wait)。"""
        start, idx = self.port_start(kind, t)
        self.book_port(kind, idx, start)
        return start, start - t

    def finalize(self, duration):
        self._acc(duration)

    def avg_stored(self, duration):
        return self.occ_integral / duration if duration > 0 else 0.0

    def full_ratio(self, duration):
        return self.full_time / duration if duration > 0 else 0.0
