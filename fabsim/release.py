"""投入計画 (設計書 5.4)。固定間隔 / ポアソン / スケジュール + CONWIP。"""
import random


class ReleasePlan(object):
    """次のロット投入仕様を供給する。 spec = (lot_id, product, release_time, priority)"""

    def __init__(self, model, seed):
        self.model = model
        self.p = model.params
        self.rng = random.Random(seed * 7919 + 11)
        self.rng_prod = random.Random(seed * 104729 + 3)
        self.counter = 0
        self.schedule_idx = 0
        total = sum(pr.ratio for pr in model.products.values())
        self.mix = [(pr, pr.ratio / total) for pr in
                    sorted(model.products.values(), key=lambda x: x.name)] \
            if total > 0 else []

    def _pick_product(self):
        x = self.rng_prod.random()
        acc = 0.0
        for pr, w in self.mix:
            acc += w
            if x <= acc:
                return pr
        return self.mix[-1][0] if self.mix else None

    def _make_spec(self, t):
        pr = self._pick_product()
        if pr is None:
            return None
        self.counter += 1
        return ("LOT%05d" % self.counter, pr.name, t, pr.priority)

    def next_release_time(self, t):
        """時刻 t の投入後の次回投入時刻。"""
        if self.p.release_mode == "fixed":
            return t + 1440.0 / self.p.lots_per_day
        if self.p.release_mode == "poisson":
            return t + self.rng.expovariate(self.p.lots_per_day / 1440.0)
        return None

    def first_event(self):
        """(時刻, spec) を返す。無ければ None。"""
        p = self.p
        if p.release_mode == "schedule":
            if self.schedule_idx < len(self._sched()):
                r = self._sched()[self.schedule_idx]
                self.schedule_idx += 1
                return (r.release_time, (r.lot_id, r.product, r.release_time,
                                         r.priority))
            return None
        if p.release_mode == "fixed":
            t = 0.0
        else:
            t = self.rng.expovariate(p.lots_per_day / 1440.0)
        if t > p.duration:
            return None
        return (t, self._make_spec(t))

    def next_event(self, t):
        """現在の投入の次。(時刻, spec) or None。"""
        if self.p.release_mode == "schedule":
            return self.first_event()
        nt = self.next_release_time(t)
        if nt is None or nt > self.p.duration:
            return None
        return (nt, self._make_spec(nt))

    _sched_cache = None

    def _sched(self):
        if self._sched_cache is None:
            self._sched_cache = sorted(self.model.release_rows,
                                       key=lambda r: (r.release_time, r.lot_id))
        return self._sched_cache
