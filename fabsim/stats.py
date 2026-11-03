"""統計収集と指標算出 (設計書 第7章)。"""
import math
import statistics


def percentile(xs, p):
    """線形補間つき百分位数 (statistics.quantiles は 3.8 以降なので自前)。"""
    if not xs:
        return 0.0
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


class Stats(object):
    def __init__(self, model, duration, warmup):
        self.model = model
        self.duration = duration
        self.warmup = warmup
        self.wip_hist = []              # (t, wip)
        self.completed_ct = []          # warmup 以降に完了したロットの CT
        self.completed_due_ok = 0
        self.completed_count = 0        # warmup 以降の完了数
        self.total_completed = 0
        self.equip_intervals = []       # (equip_id, state, lot, recipe, t0, t1)
        self.equip_cum = {}             # eid -> {state: time}
        self.wip_ts = []                # (t, tool_group, wip)
        self.stocker_ts = []            # (t, stocker_id, count)
        self.kpi_rows = []              # (t, metric, key, value)
        self.trace_rows = []            # (t, type, obj, lot, detail)
        self.lot_rows = []              # lots.csv 用

    # ---- 累積系 ----
    def wip_changed(self, wip, t):
        self.wip_hist.append((t, wip))

    def equip_interval(self, eid, state, lot, recipe, t0, t1):
        if t1 <= t0:
            return
        self.equip_intervals.append((eid, state, lot, recipe,
                                     round(t0, 6), round(t1, 6)))
        cum = self.equip_cum.setdefault(eid, {})
        cum[state] = cum.get(state, 0.0) + (t1 - t0)

    def lot_released(self, lot):
        pass

    def lot_completed(self, lot):
        self.total_completed += 1

    # ---- tick ----
    def tick(self, sim, t):
        start = len(self.kpi_rows)
        by_tg = {}
        for lot_id in sim.active_lots:
            lot = sim.lots[lot_id]
            step = lot.next_step()
            tg = step.recipe.tool_group if step is not None else "-"
            by_tg[tg] = by_tg.get(tg, 0) + 1
        for tg in sorted(by_tg):
            self.wip_ts.append((round(t, 3), tg, by_tg[tg]))
        self.wip_ts.append((round(t, 3), "TOTAL", sim.wip))
        for sid in sorted(sim.stockers):
            self.stocker_ts.append((round(t, 3), sid, sim.stockers[sid].count))
        # kpi_ts
        self.kpi_rows.append((round(t, 3), "wip", "total", float(sim.wip)))
        self.kpi_rows.append((round(t, 3), "completed", "total",
                              float(self.total_completed)))
        for area, fleet in sorted(sim.fleets.items()):
            busy = sum(a.empty_time + a.loaded_time for a in fleet.agvs)
            util = busy / (t * len(fleet.agvs)) if t > 0 else 0.0
            self.kpi_rows.append((round(t, 3), "agv_util", area,
                                  round(util, 6)))
        tot_e = sum(a.empty_time + a.loaded_time
                    for f in sim.fleets.values() for a in f.agvs)
        tot_n = sum(len(f.agvs) for f in sim.fleets.values())
        self.kpi_rows.append((round(t, 3), "agv_util", "total",
                              round(tot_e / (t * tot_n), 6)
                              if t > 0 and tot_n else 0.0))
        cl = sim.cl
        cl_busy = sum(cl.slot_busy)
        cl_util = cl_busy / (t * cl.M * cl.S) if t > 0 else 0.0
        self.kpi_rows.append((round(t, 3), "cl_util", "total",
                              round(cl_util, 6)))
        for tg in sorted(sim.model.tool_groups):
            busy = down = pm = 0.0
            for e in sim.model.tool_groups[tg].equipments:
                cum = self.equip_cum.get(e.equip_id, {})
                busy += cum.get("BUSY", 0.0)
                down += cum.get("DOWN", 0.0)
                pm += cum.get("PM", 0.0)
                if e.state == "BUSY":
                    busy += t - e.state_since
                elif e.state == "DOWN":
                    down += t - e.state_since
                elif e.state == "PM":
                    pm += t - e.state_since
            n = len(sim.model.tool_groups[tg].equipments)
            denom = t * n - down - pm
            util = busy / denom if denom > 0 else 0.0
            self.kpi_rows.append((round(t, 3), "util", tg, round(util, 6)))
        return self.kpi_rows[start:]

    # ---- finalize ----
    def finalize(self, sim):
        warm = self.warmup
        self.completed_ct = []
        self.completed_due_ok = 0
        self.completed_count = 0
        for lot in sim.lots.values():
            if lot.complete_time is None:
                continue
            ct = lot.complete_time - lot.release_time
            if lot.complete_time >= warm:
                self.completed_ct.append(ct)
                self.completed_count += 1
                if lot.complete_time <= lot.due_date + 1e-9:
                    self.completed_due_ok += 1
        for lot in sorted(sim.lots.values(), key=lambda l: l.lot_id):
            ct = (lot.complete_time - lot.release_time
                  if lot.complete_time is not None else None)
            wait = lot.transport_total()
            self.lot_rows.append({
                "lot_id": lot.lot_id, "product": lot.product.name,
                "priority": lot.priority, "wafers": lot.wafers,
                "release_time": round(lot.release_time, 3),
                "due_date": round(lot.due_date, 3),
                "complete_time": (round(lot.complete_time, 3)
                                  if lot.complete_time is not None else ""),
                "cycle_time": round(ct, 3) if ct is not None else "",
                "wait_total": round(wait, 3),
            })

    def wip_avg(self):
        return self._wip_avg_over(self.warmup, self.duration)

    def _wip_avg_over(self, t0, t1):
        if t1 <= t0:
            return 0.0
        hist = self.wip_hist or [(0.0, 0)]
        total = 0.0
        prev_t, prev_w = hist[0]
        for t, w in hist[1:]:
            lo, hi = max(prev_t, t0), min(t, t1)
            if hi > lo:
                total += prev_w * (hi - lo)
            prev_t, prev_w = t, w
        if prev_t < t1:
            total += prev_w * (t1 - max(prev_t, t0))
        return total / (t1 - t0)

    def state_times_window(self, eid, t0, t1):
        """[t0, t1] に重なる設備状態区間の状態別合計時間。"""
        out = {}
        for e2, state, _l, _r, s0, s1 in self.equip_intervals:
            if e2 != eid:
                continue
            lo, hi = max(s0, t0), min(s1, t1)
            if hi > lo:
                out[state] = out.get(state, 0.0) + (hi - lo)
        return out

    def summary(self, sim):
        warm = self.warmup
        span = self.duration - warm
        days = span / 1440.0 if span > 0 else 0.0
        cts = self.completed_ct
        tp = (self.completed_count / days) if days > 0 else 0.0
        util = {}
        for tg in sorted(self.model.tool_groups):
            busy = idle = down = pm = setup = resv = 0.0
            n = 0
            for e in self.model.tool_groups[tg].equipments:
                n += 1
                st = self.state_times_window(e.equip_id, warm, self.duration)
                busy += st.get("BUSY", 0.0)
                idle += st.get("IDLE", 0.0)
                down += st.get("DOWN", 0.0)
                pm += st.get("PM", 0.0)
                setup += st.get("SETUP", 0.0)
                resv += st.get("RESERVED", 0.0)
            total = span * n
            denom = total - down - pm
            util[tg] = {
                "equipments": n,
                "busy": round(busy, 3), "idle": round(idle, 3),
                "down": round(down, 3), "pm": round(pm, 3),
                "setup": round(setup, 3), "reserved": round(resv, 3),
                "util_op": round(busy / denom, 6) if denom > 0 else 0.0,
                "util_total": round(busy / total, 6) if total > 0 else 0.0,
            }
        # 搬送指標
        tr_sum = 0.0
        tr_n = 0
        for lot in sim.lots.values():
            if lot.complete_time is not None and lot.complete_time >= warm:
                ct = lot.complete_time - lot.release_time
                if ct > 0:
                    tr_sum += (lot.t_agv_wait + lot.t_agv_move + lot.t_cl_wait
                               + lot.t_cl_move + lot.t_port_wait) / ct
                    tr_n += 1
        agv_busy = 0.0
        agv_n = 0
        for f in sim.fleets.values():
            for a in f.agvs:
                agv_busy += a.empty_time + a.loaded_time
                agv_n += 1
        agv_util = agv_busy / (span * agv_n) if agv_n and span > 0 else 0.0
        cl = sim.cl
        cl_util = sum(cl.slot_busy) / (span * cl.M * cl.S) \
            if span > 0 else 0.0
        full_sum = 0.0
        for st in sim.stockers.values():
            full_sum += st.full_ratio(self.duration)
        stocker_full = full_sum / len(sim.stockers) if sim.stockers else 0.0
        wip = self.wip_avg()
        return {
            "duration": self.duration, "warmup": warm,
            "released_lots": len(sim.lots),
            "completed_lots": self.completed_count,
            "wip_avg": round(wip, 4),
            "throughput_lots_per_day": round(tp, 4),
            "cycle_time_min": {
                "mean": round(statistics.mean(cts), 3) if cts else 0.0,
                "median": round(statistics.median(cts), 3) if cts else 0.0,
                "p95": round(percentile(cts, 95), 3) if cts else 0.0,
                "min": round(min(cts), 3) if cts else 0.0,
                "max": round(max(cts), 3) if cts else 0.0,
            },
            "due_adherence": round(self.completed_due_ok / len(cts), 6)
            if cts else 0.0,
            "utilization": util,
            "transport": {
                "avg_transport_ratio": round(tr_sum / tr_n, 6)
                if tr_n else 0.0,
                "agv_util": round(agv_util, 6),
                "cl_util": round(cl_util, 6),
                "stocker_full_ratio": round(stocker_full, 6),
            },
        }
