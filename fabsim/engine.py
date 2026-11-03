"""イベントキューとシミュレーター本体 (設計書 第3章・第5章)。"""
import heapq
import json
import math
import os
import random
import time as _time
from collections import deque

from . import events as ev
from .models import Lot
from .release import ReleasePlan
from .rules import RULES, QueueEntry
from .transport.agv import AgvFleet, AgvTask
from .transport.cleanliner import CleanLiner, Ride
from .transport.stocker import Stocker
from .stats import Stats

KPI_INTERVAL = 60.0


class EventQueue(object):
    """(time, priority, seq, event) の heapq。"""

    def __init__(self):
        self._h = []
        self._seq = 0

    def push(self, t, event):
        heapq.heappush(self._h, (t, event.priority, self._seq, event))
        self._seq += 1

    def pop(self):
        t, _, _, e = heapq.heappop(self._h)
        return t, e

    def __len__(self):
        return len(self._h)


class Simulator(object):
    def __init__(self, model, db=None, trace=False, progress_file=None):
        self.model = model
        self.p = model.params
        self.duration = float(self.p.duration)
        self.q = EventQueue()
        self.now = 0.0
        self.db = db
        self.trace_on = trace
        self.progress_file = progress_file
        self._wall_start = _time.time()

        self.stockers = {sid: Stocker(c) for sid, c in model.stockers.items()}
        self.stocker_by_area = {s.area: s for s in self.stockers.values()}
        self.fleets = {}
        for a in model.areas.values():
            st = self.stocker_by_area[a.name]
            self.fleets[a.name] = AgvFleet(a, st.stocker_id,
                                         out_first=bool(self.p.agv_out_first))
        self.equip_agv_time = {eid: e.agv_time
                               for eid, e in model.equipments.items()}
        self.cl = CleanLiner(model.cl_stations, self.p.cl_carriers,
                             self.p.cl_slots, self.p.cl_load_time,
                             self.p.cl_unload_time)
        self.station_of_stocker = dict(self.cl.station_idx)
        self.queues = {}
        self.tg_area_equips = {}
        for tg in model.tool_groups.values():
            for e in tg.equipments:
                self.tg_area_equips.setdefault((tg.name, e.area), []).append(e)
        for k in self.tg_area_equips:
            self.tg_area_equips[k].sort(key=lambda e: e.equip_id)

        self.lots = {}
        self.active_lots = set()
        self.wip = 0
        self.release_held = deque()
        self.plan = ReleasePlan(model, self.p.seed)
        self.rng_proc = random.Random(self.p.seed * 31 + 7)
        self.rng_fail = random.Random(self.p.seed * 97 + 13)
        self.stats = Stats(model, self.duration, self.p.warmup)
        self.batch_armed = {}
        self._ride_seq = 0
        self._agv_seq = 0
        self._task_seq = 0
        self._rel_rows = {r.lot_id: r for r in model.release_rows}

        for e in model.equipments.values():
            e.last_recipe = e.initial_recipe
            e.state = "IDLE"
            e.state_since = 0.0
            e.interval_lot = ""
            e.interval_recipe = ""
            e.busy_since = 0.0
            e.current_lots = []
            e.expected_lots = []
            e.fail_remaining = math.inf
            e.fail_gen = 0
            e.proc_event = None
            e.pm_pending = False
            if self.db is not None:
                self.db.equip_state_open(e.equip_id, "IDLE", "", "", 0.0)

    # ------------------------------------------------------------ utils
    def schedule(self, t, event):
        self.q.push(t, event)

    def _emit(self, kind, obj, lot, detail=""):
        if self.trace_on:
            self.stats.trace_rows.append(
                (round(self.now, 6), kind, obj, lot, detail))
        if self.db is not None and self.db.trace:
            self.db.add("events", (round(self.now, 6), kind, obj, lot, detail))

    def _set_state(self, e, state, now, lot_id="", recipe=""):
        """設備状態を遷移させ、区間を閉じて開く。"""
        if e.state == state and e.interval_lot == lot_id and \
                e.interval_recipe == recipe:
            return
        if now == e.state_since:
            # 長さ0の区間は作らず、開いている区間を書き換える
            e.state = state
            e.interval_lot = lot_id
            e.interval_recipe = recipe
            if self.db is not None:
                self.db.equip_state_rewrite(e.equip_id, state, lot_id, recipe)
            return
        self.stats.equip_interval(e.equip_id, e.state, e.interval_lot,
                                  e.interval_recipe, e.state_since, now)
        if self.db is not None:
            self.db.equip_state_close(e.equip_id, now)
        e.state = state
        e.state_since = now
        e.interval_lot = lot_id
        e.interval_recipe = recipe
        if self.db is not None:
            self.db.equip_state_open(e.equip_id, state, lot_id, recipe, now)

    def _req_dispatch(self, tg, area):
        self.schedule(self.now, ev.Dispatch(tg, area))

    def _equip_idle(self, e, now):
        if e.port_queue:                 # ポート待機ロットを即座に開始
            e.current_lots = [e.port_queue.pop(0)]
            self._setup_or_process(e, now)
            return
        if e.pm_pending:
            self._start_pm(e, now)
        else:
            self._req_dispatch(e.tool_group, e.area)

    # ------------------------------------------------------------ main loop
    def run(self):
        first = self.plan.first_event()
        if first is not None:
            self.schedule(first[0], ev.LotRelease(first[1]))
        for e in self.model.equipments.values():
            if e.pm_interval > 0:
                t = e.pm_interval
                while t <= self.duration:
                    self.schedule(t, ev.PMStart(e))
                    t += e.pm_interval
        if KPI_INTERVAL <= self.duration:
            self.schedule(KPI_INTERVAL, ev.KpiTick())
        self._progress("running", 0.0)
        while len(self.q):
            t, event = self.q.pop()
            if t > self.duration:
                break
            self.now = t
            if not event.cancelled:
                event.handle(self)
        self._finalize(self.duration)
        return self.stats

    def _progress(self, status, t):
        if not self.progress_file:
            return
        try:
            data = {"status": status, "progress_time": round(t, 3),
                    "duration": self.duration,
                    "wall_time": round(_time.time() - self._wall_start, 3)}
            tmp = self.progress_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.progress_file)
        except OSError:
            pass

    def _finalize(self, duration):
        for e in self.model.equipments.values():
            self.stats.equip_interval(e.equip_id, e.state, e.interval_lot,
                                      e.interval_recipe, e.state_since, duration)
            if self.db is not None:
                self.db.equip_state_close(e.equip_id, duration)
        for st in self.stockers.values():
            st.finalize(duration)
            if self.db is not None:
                self.db.stocker_lots_close_all(st.stocker_id, duration)
        if self.db is not None:
            self.db.lot_steps_close_all(duration)
            self.db.equip_port_lots_close_all(duration)
        self.stats.finalize(self)
        self._progress("done", duration)

    # ------------------------------------------------------------ release
    def _on_lot_release(self, event):
        spec = event.spec
        nxt = self.plan.next_event(self.now)
        if nxt is not None:
            self.schedule(nxt[0], ev.LotRelease(nxt[1]))
        if self.p.release_control == "conwip" and self.wip >= self.p.wip_limit:
            self.release_held.append(spec)
            return
        self._admit(spec, self.now)

    def _admit(self, spec, now):
        lot_id, prod_name, rel_t, prio = spec
        prod = self.model.products[prod_name]
        pr = prod.priority if prio is None else prio
        row = self._rel_rows.get(lot_id)
        due = (row.due_date if row is not None and row.due_date is not None
               else now + prod.theory_time() * prod.due_factor)
        lot = Lot(lot_id=lot_id, product=prod, priority=pr, wafers=prod.wafers,
                  release_time=now, due_date=due)
        self.lots[lot_id] = lot
        self.active_lots.add(lot_id)
        self.wip += 1
        self.stats.wip_changed(self.wip, now)
        self.stats.lot_released(lot)
        if self.db is not None:
            self.db.lot_insert(lot)
        self._emit("LotRelease", lot_id, lot_id, prod_name)
        st = self.stockers[self.p.release_stocker]
        if st.is_full():
            st.rejects += 1
            st.pending_in.append(("release", lot))
            return
        start, wait = st.request_port("in", now)
        lot.t_port_wait += (start + st.port_time) - now
        st.inbound_reserved += 1
        self.schedule(start + st.port_time, ev.StockerIn(lot, st.stocker_id))

    def _on_stocker_in(self, event):
        st = self.stockers[event.stocker_id]
        st.inbound_reserved -= 1
        self._lot_stored(event.lot, st, self.now)

    # ------------------------------------------------------------ stored / routing
    def _lot_stored(self, lot, st, now):
        st.add(lot.lot_id, now)
        lot.cur_stocker = st.stocker_id
        if self.db is not None:
            self.db.stocker_lot_open(st.stocker_id, lot.lot_id, now)
        step = lot.next_step()
        if step is None:
            return
        tg = step.recipe.tool_group
        if not lot.dest_area:
            lot.dest_area = self._select_dest(lot, st.area)
        if lot.dest_area != st.area:
            lot.state = "CL_WAIT"
            self._cl_request(lot, st, now)
            return
        self._enqueue(lot, st, tg, now)

    def _enqueue(self, lot, st, tg, now):
        lot.state = "QUEUED"
        lot.queue_in = now
        entry = QueueEntry(lot, now)
        self.queues.setdefault((tg, st.area), []).append(entry)
        step = lot.next_step()
        if self.db is not None:
            self.db.lot_step_open(lot.lot_id, step.seq, step.name,
                                  step.recipe.recipe, now)
        self._req_dispatch(tg, st.area)

    def _select_dest(self, lot, cur_area):
        step = lot.next_step()
        tg = step.recipe.tool_group
        cands = self.model.areas_with_group(tg)
        if not cands:
            return cur_area
        if self.p.area_select == "NEAREST":
            if cur_area in cands:
                return cur_area
            i0 = self.cl.station_idx[self.stocker_by_area[cur_area].stocker_id]
            return min(cands, key=lambda a: (
                self.cl.travel(i0, self.cl.station_idx[
                    self.stocker_by_area[a].stocker_id]), a))
        best, bestk = None, None
        for a in cands:
            avail = sum(1 for x in self.model.tool_groups[tg].equipments
                        if x.area == a and x.state not in ("DOWN", "PM"))
            nwait = len(self.queues.get((tg, a), []))
            score = nwait / avail if avail > 0 else math.inf
            k = (score, 0 if a == cur_area else 1, a)
            if bestk is None or k < bestk:
                bestk, best = k, a
        return best

    # ------------------------------------------------------------ dispatch
    def _on_dispatch(self, event):
        tg, area = event.tg, event.area
        q = self.queues.get((tg, area))
        if not q:
            return
        now = self.now
        tgo = self.model.tool_groups[tg]
        rule = RULES[tgo.dispatch_rule or self.p.dispatch_rule]
        # IDLE かつ占有0を優先、次に占有の少ない順、equip_id 昇順
        cands_e = [e for e in self.tg_area_equips.get((tg, area), [])
                   if e.capacity > 1
                   and e.state == "IDLE" and not e.expected_lots
                   or e.capacity == 1
                   and e.state not in ("DOWN", "PM")
                   and len(e.expected_lots) < e.ports]
        cands_e.sort(key=lambda e: (
            0 if e.state == "IDLE" and not e.expected_lots else 1,
            len(e.expected_lots), e.equip_id))
        for e in cands_e:
            if not q:
                break
            if e.state == "IDLE" and e.pm_pending:
                self._start_pm(e, now)
                continue
            excluded = set()
            while q:
                cands = [qe for qe in q
                         if qe not in excluded
                         and e.can_process(qe.lot.next_step().recipe)]
                if not cands:
                    break
                ent = rule(cands, e, now)
                if ent is None:
                    break
                lot = ent.lot
                recipe = lot.next_step().recipe
                if not recipe.is_batch:
                    q.remove(ent)
                    self._reserve(e, [lot], now)
                    # ポートに空きがあれば同じ設備に続けて予約(先行搬入)
                    if len(e.expected_lots) >= e.ports:
                        break
                    excluded = set()
                    continue
                group = [qe for qe in q
                         if qe.lot.next_step().recipe is recipe
                         and e.can_process(recipe)]
                group.sort(key=lambda ge: (ge.enqueue_time, ge.lot.lot_id))
                n_cap = min(recipe.batch_max, e.capacity)
                oldest = group[0].enqueue_time
                if len(group) >= n_cap or \
                        (now - oldest) >= recipe.batch_wait_max:
                    take = group[:n_cap]
                    for qe in take:
                        q.remove(qe)
                    self.batch_armed.pop((e.equip_id, recipe.recipe), None)
                    self._reserve(e, [qe.lot for qe in take], now)
                    break
                excluded.add(ent)
                key = (e.equip_id, recipe.recipe)
                fire = oldest + recipe.batch_wait_max
                if self.batch_armed.get(key) != fire:
                    self.batch_armed[key] = fire
                    self.schedule(fire, ev.BatchTimeout(e.equip_id,
                                                      recipe.recipe, fire))
            # 設備1台につき1割当

    def _reserve(self, e, lots, now):
        fleet = self.fleets[e.area]
        self._set_state(e, "RESERVED", now,
                        lot_id=",".join(l.lot_id for l in lots))
        e.expected_lots += list(lots)     # ポート占有を加算(既定でも既に空)
        for lot in lots:
            lot.t_stocker_wait += now - lot.queue_in
            lot.state = "AGV_IN"
            if self.db is not None:
                self.db.lot_step_update(lot.lot_id,
                                        lot.next_step().seq,
                                        equip_id=e.equip_id,
                                        t_reserved=now)
                self.db.equip_port_open(e.equip_id, lot.lot_id, now)
            self._task_seq += 1
            task = AgvTask(lot, "in", lot.cur_stocker, e.equip_id, now,
                           self._task_seq)
            lot.n_agv += 1
            fleet.request(task)
            self._emit("AgvRequest", e.equip_id, lot.lot_id, "in")
        fleet.try_assign(self, now)

    def _on_batch_timeout(self, event):
        key = (event.equip_id, event.recipe)
        if self.batch_armed.get(key) != event.armed_at:
            return
        del self.batch_armed[key]
        e = self.model.equipments[event.equip_id]
        self._req_dispatch(e.tool_group, e.area)

    # ------------------------------------------------------------ AGV
    def _agv_start_task(self, agv, task, now):
        fleet = self.fleets[agv.area]
        agv.state = "MOVING_EMPTY"
        task.depart_empty = now
        d = fleet.dist(agv.pos, task.from_node, self.equip_agv_time)
        agv.empty_time += d
        self.schedule(now + d, ev.AgvLeg(fleet, task, "at_from"))

    def _on_agv_leg(self, event):
        task = event.task
        fleet = event.fleet
        agv = task.agv
        lot = task.lot
        now = self.now
        if event.phase == "at_from":
            if lot is not None:
                lot.t_agv_wait += now - task.request_time
            task.arrive_from = now
            agv.wait_sum += now - task.request_time
            agv.wait_n += 1
            if self.db is not None:
                self.db.add("agv_moves",
                            (agv.agv_id, fleet.area, "EMPTY", agv.pos,
                             task.from_node, None,
                             round(task.depart_empty, 6), round(now, 6)))
            agv.pos = task.from_node
            if task.kind == "return":         # ストッカー帰還タスク終了
                agv.task = None
                agv.state = "IDLE"
                fleet.try_assign(self, now)
                return
            if task.from_node == fleet.stocker_id:
                st = self.stockers[task.from_node]
                start, wait = st.request_port("out", now)
                lot.t_port_wait += wait
                task.load_start = start
            else:
                task.load_start = now
            task.depart_from = task.load_start + fleet.load_time
            self.schedule(task.depart_from,
                          ev.AgvLeg(fleet, task, "loaded"))
        elif event.phase == "loaded":
            lot.t_agv_move += task.depart_from - task.load_start
            if task.from_node == fleet.stocker_id:
                st = self.stockers[task.from_node]
                self._stocker_remove(st, lot.lot_id, now)
            agv.state = "CARRYING"
            d = fleet.dist(task.from_node, task.to_node, self.equip_agv_time)
            agv.loaded_time += d
            task.arrive_to = now + d
            self.schedule(task.arrive_to, ev.AgvLeg(fleet, task, "at_to"))
        elif event.phase == "at_to":
            lot.t_agv_move += now - task.depart_from
            if self.db is not None:
                self.db.add("agv_moves",
                            (agv.agv_id, fleet.area, "LOADED",
                             task.from_node, task.to_node, lot.lot_id,
                             round(task.depart_from, 6), round(now, 6)))
            agv.pos = task.to_node
            if task.to_node == fleet.stocker_id:
                st = self.stockers[task.to_node]
                if st.is_full():
                    st.rejects += 1
                    st.pending_in.append(("agv", fleet, task))
                    return
                ustart, uwait = st.request_port("in", now)
                lot.t_port_wait += uwait
                st.inbound_reserved += 1
                task.unload_start = ustart
            else:
                task.unload_start = now
            self.schedule(task.unload_start + fleet.unload_time,
                          ev.AgvLeg(fleet, task, "unloaded"))
        elif event.phase == "unloaded":
            lot.t_agv_move += now - task.unload_start
            if task.to_node == fleet.stocker_id:
                st = self.stockers[task.to_node]
                st.inbound_reserved -= 1
            agv.task = None
            agv.state = "IDLE"
            agv.tasks += 1
            if task.kind == "in":
                e = self.model.equipments[task.to_node]
                self._lot_at_equip(lot, e, now)
            else:
                st = self.stockers[task.to_node]
                self._lot_stored(lot, st, now)
            fleet.try_assign(self, now)
            # 待ちタスクが無ければストッカーへ空走で戻る(agv_return_home=1)
            if agv.state == "IDLE" and fleet.return_home and                     agv.pos != fleet.stocker_id:
                self._task_seq += 1
                rt = AgvTask(None, "return", fleet.stocker_id,
                             fleet.stocker_id, now, self._task_seq)
                rt.agv = agv
                agv.task = rt
                self._agv_start_task(agv, rt, now)

    def _stocker_remove(self, st, lot_id, now):
        st.remove(lot_id, now)
        if self.db is not None:
            self.db.stocker_lot_close(st.stocker_id, lot_id, now)
        pend = st.pending_in
        st.pending_in = []
        for item in pend:
            if st.is_full():
                st.pending_in.append(item)
                continue
            if item[0] == "agv":
                _, fleet, task = item
                ustart, uwait = st.request_port("in", now)
                task.lot.t_port_wait += uwait
                st.inbound_reserved += 1
                task.unload_start = ustart
                self.schedule(ustart + fleet.unload_time,
                              ev.AgvLeg(fleet, task, "unloaded"))
            else:
                _, lot = item
                start, wait = st.request_port("in", now)
                lot.t_port_wait += (start + st.port_time) - now
                st.inbound_reserved += 1
                self.schedule(start + st.port_time,
                              ev.StockerIn(lot, st.stocker_id))

    # ------------------------------------------------------------ process
    def _lot_at_equip(self, lot, e, now):
        lot.step_arrive = now
        lot.state = "AT_EQUIP"
        if e.capacity == 1:
            e.port_queue.append(lot)     # 搬入完了順の待機キュー
            # IDLE/RESERVED(予約済み未到着含む)なら即座に開始
            if e.state in ("IDLE", "RESERVED"):
                self._equip_idle(e, now)
            elif len(e.expected_lots) < e.ports:
                self._req_dispatch(e.tool_group, e.area)
            return
        e.current_lots.append(lot)
        if len(e.current_lots) >= len(e.expected_lots):
            self._setup_or_process(e, now)

    def _setup_or_process(self, e, now):
        lots = e.current_lots
        recipe = lots[0].next_step().recipe
        for lot in lots:
            lot.t_equip_wait += now - lot.step_arrive
            lot.step_recipe = recipe.recipe
        setup_t = 0.0
        if e.last_recipe and e.last_recipe != recipe.recipe:
            setup_t = self.model.setups.get(
                (e.tool_group, e.last_recipe, recipe.recipe), 0.0)
        for lot in lots:
            lot.step_setup = setup_t
            lot.t_setup_sum += setup_t
        if setup_t > 0:
            self._set_state(e, "SETUP", now,
                            lot_id=",".join(l.lot_id for l in lots),
                            recipe=recipe.recipe)
            self.schedule(now + setup_t, ev.SetupEnd(e))
        else:
            self._start_process(e, now)

    def _on_setup_end(self, event):
        e = event.equip
        if e.state != "SETUP":
            return
        self._start_process(e, self.now)

    def _start_process(self, e, now):
        lots = e.current_lots
        recipe = lots[0].next_step().recipe
        wafers = sum(l.wafers for l in lots)
        dur = recipe.sample_time(wafers, self.rng_proc)
        e.recipe_in_process = recipe.recipe
        self._set_state(e, "BUSY", now,
                        lot_id=",".join(l.lot_id for l in lots),
                        recipe=recipe.recipe)
        e.busy_since = now
        e.proc_start = now
        e.proc_end = now + dur
        for lot in lots:
            lot.step_proc = dur
        event = ev.ProcessEnd(e, 0)
        e.proc_event = event
        self.schedule(e.proc_end, event)
        e.fail_gen += 1
        if math.isinf(e.fail_remaining) and e.mtbf > 0:
            e.fail_remaining = self.rng_fail.expovariate(1.0 / e.mtbf)
        if not math.isinf(e.fail_remaining):
            self.schedule(now + e.fail_remaining,
                          ev.EquipDown(e, e.fail_gen))
        self._emit("ProcessStart", e.equip_id,
                   ",".join(l.lot_id for l in lots), recipe.recipe)

    def _on_process_end(self, event):
        e = event.equip
        if e.proc_event is not event or e.state != "BUSY":
            return
        now = self.now
        lots = list(e.current_lots)
        recipe = e.recipe_in_process
        dur = now - e.proc_start
        for lot in lots:
            step = lot.product.route[lot.route_index]
            lot.t_proc_sum += dur
            lot.history.append((step.seq, step.name, recipe, e.equip_id,
                                lot.queue_in, e.proc_start, now))
            if self.db is not None:
                self.db.lot_step_update(lot.lot_id, step.seq,
                                        t_start=e.proc_start, t_end=now)
        e.last_recipe = recipe
        e.current_lots = []
        e.expected_lots = [l for l in e.expected_lots if l not in lots]
        e.proc_event = None
        if self.db is not None:
            for lot in lots:
                self.db.equip_port_close(e.equip_id, lot.lot_id, now)
        if not math.isinf(e.fail_remaining):
            e.fail_remaining -= now - e.busy_since
        self._set_state(e, "IDLE", now)
        self._emit("ProcessEnd", e.equip_id,
                   ",".join(l.lot_id for l in lots), recipe)
        fleet = self.fleets[e.area]
        for lot in lots:
            lot.route_index += 1
            if lot.next_step() is None:
                self._complete_lot(lot, now)
            else:
                lot.dest_area = self._select_dest(lot, e.area)
                lot.state = "AGV_OUT"
                st = self.stocker_by_area[e.area]
                self._task_seq += 1
                task = AgvTask(lot, "out", e.equip_id, st.stocker_id, now,
                               self._task_seq)
                lot.n_agv += 1
                fleet.request(task)
                self._emit("AgvRequest", st.stocker_id, lot.lot_id, "out")
        fleet.try_assign(self, now)
        self._equip_idle(e, now)

    def _complete_lot(self, lot, now):
        lot.complete_time = now
        lot.state = "DONE"
        self.active_lots.discard(lot.lot_id)
        self.wip -= 1
        self.stats.wip_changed(self.wip, now)
        self.stats.lot_completed(lot)
        if self.db is not None:
            self.db.lot_complete(lot.lot_id, now)
        self._emit("LotComplete", lot.lot_id, lot.lot_id, "")
        if self.p.release_control == "conwip":
            while self.release_held and self.wip < self.p.wip_limit:
                self._admit(self.release_held.popleft(), now)

    # ------------------------------------------------------------ failure / PM
    def _on_equip_down(self, event):
        e = event.equip
        if event.gen != e.fail_gen or e.state != "BUSY":
            return
        now = self.now
        elapsed = now - e.busy_since
        for lot in e.current_lots:
            lot.t_proc_sum += elapsed
        if self.p.failure_policy == "restart":
            e.down_proc_remaining = -1.0
        else:
            e.down_proc_remaining = e.proc_end - now
        e.proc_event = None
        self._set_state(e, "DOWN", now,
                        lot_id=",".join(l.lot_id for l in e.current_lots),
                        recipe=e.recipe_in_process)
        self.schedule(now + self.rng_fail.expovariate(1.0 / e.mttr),
                      ev.EquipUp(e))
        self._emit("EquipDown", e.equip_id, "", "")

    def _on_equip_up(self, event):
        e = event.equip
        if e.state != "DOWN":
            return
        now = self.now
        if e.mtbf > 0:
            e.fail_remaining = self.rng_fail.expovariate(1.0 / e.mtbf)
        self._emit("EquipUp", e.equip_id, "", "")
        if e.current_lots:
            recipe = self.model.recipes[e.recipe_in_process]
            if e.down_proc_remaining < 0:
                wafers = sum(l.wafers for l in e.current_lots)
                dur = recipe.sample_time(wafers, self.rng_proc)
            else:
                dur = e.down_proc_remaining
            self._set_state(e, "BUSY", now,
                            lot_id=",".join(l.lot_id for l in e.current_lots),
                            recipe=e.recipe_in_process)
            e.busy_since = now
            e.proc_start = now
            e.proc_end = now + dur
            event2 = ev.ProcessEnd(e, 0)
            e.proc_event = event2
            self.schedule(e.proc_end, event2)
            e.fail_gen += 1
            if not math.isinf(e.fail_remaining):
                self.schedule(now + e.fail_remaining,
                              ev.EquipDown(e, e.fail_gen))
        else:
            self._set_state(e, "IDLE", now)
            self._equip_idle(e, now)

    def _on_pm_start(self, event):
        e = event.equip
        if e.state == "IDLE":
            self._start_pm(e, self.now)
        else:
            e.pm_pending = True

    def _start_pm(self, e, now):
        e.pm_pending = False
        self._set_state(e, "PM", now)
        self.schedule(now + e.pm_duration, ev.PMEnd(e))
        self._emit("PMStart", e.equip_id, "", "")

    def _on_pm_end(self, event):
        e = event.equip
        if e.state != "PM":
            return
        self._set_state(e, "IDLE", self.now)
        self._equip_idle(e, self.now)

    # ------------------------------------------------------------ cleanliner
    def _cl_request(self, lot, st, now):
        self._ride_seq += 1
        i_from = self.cl.station_idx[st.stocker_id]
        dst_st = self.stocker_by_area[lot.dest_area]
        i_to = self.cl.station_idx[dst_st.stocker_id]
        ride = Ride(self._ride_seq, lot, i_from, i_to, now)

        def port_free_at(sid, t):
            return self.stockers[sid].port_start("out", t)[0]

        if self.cl.reserve(ride, now, port_free_at) is None:
            return
        sstart, sidx = st.port_start("out", ride.t_arr_dep)
        st.book_port("out", sidx, sstart)
        self.schedule(ride.t_load, ev.ClLoad(ride))

    def _on_cl_load(self, event):
        ride = event.ride
        lot = ride.lot
        now = self.now
        k, j = ride.carrier, ride.slot
        # 周回待ち延長でスロットが衝突した場合は予約し直す
        for r in self.cl.resv.get((k, j), []):
            if r is not ride and r.status == "riding" and \
                    r.t_load <= now < r.t_unload:
                self.cl.remove_resv(ride)
                self._cl_request(lot,
                                 self.stockers[lot.cur_stocker], now)
                return
        st = self.stockers[self.cl.stations[ride.from_idx]]
        self._stocker_remove(st, lot.lot_id, now)
        ride.status = "riding"
        lot.state = "CL_RIDE"
        lot.t_cl_wait += ride.t_load - ride.request_time
        lot.n_cl += 1
        self.cl.load_wait_sum[k] += ride.t_load - ride.request_time
        self.schedule(ride.t_arr_dst, ev.ClArrive(ride))
        self._emit("ClLoad", "carrier%d" % k, lot.lot_id, "")

    def _on_cl_arrive(self, event):
        ride = event.ride
        now = self.now
        sid = self.cl.stations[ride.to_idx]
        st = self.stockers[sid]
        if st.is_full() or st.port_start("in", now)[0] > now + 1e-9:
            # ストッカー満杯/ポート使用中: 積載したまま周回して次回到着で再試行
            st.rejects += 1
            ride.reloops += 1
            self.cl.reloop_sum[ride.carrier] += 1
            ride.t_arr_dst += self.cl.T
            ride.t_unload = ride.t_arr_dst + self.cl.unload_time
            self.schedule(ride.t_arr_dst, ev.ClArrive(ride))
            return
        ustart, _ = st.request_port("in", now)
        st.inbound_reserved += 1
        ride.t_unload = now + self.cl.unload_time
        self.schedule(ride.t_unload, ev.ClStored(ride))

    def _on_cl_stored(self, event):
        ride = event.ride
        now = self.now
        sid = self.cl.stations[ride.to_idx]
        st = self.stockers[sid]
        st.inbound_reserved -= 1
        ride.status = "done"
        self.cl.remove_resv(ride)
        k = ride.carrier
        self.cl.rides_done[k] += 1
        self.cl.slot_busy[k] += ride.t_unload - ride.t_load
        self.cl.reloop_sum[k] += ride.reloops
        ride.lot.t_cl_move += ride.t_unload - ride.t_load
        if self.db is not None:
            self.db.add("cl_rides",
                        (ride.lot.lot_id, k, ride.slot,
                         self.cl.stations[ride.from_idx], sid,
                         round(ride.request_time, 6),
                         round(ride.t_load, 6), round(ride.t_unload, 6)))
        self._emit("ClUnload", "carrier%d" % k, ride.lot.lot_id, sid)
        self._lot_stored(ride.lot, st, now)

    # ------------------------------------------------------------ kpi
    def _on_kpi_tick(self, event):
        now = self.now
        new_rows = self.stats.tick(self, now)
        if self.db is not None:
            for r in new_rows:
                self.db.add("kpi_ts", r)
            self.db.commit(now)
        self._progress("running", now)
        if now + KPI_INTERVAL <= self.duration:
            self.schedule(now + KPI_INTERVAL, ev.KpiTick())
