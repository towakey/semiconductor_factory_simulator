"""エリア専属 AGV (設計書 5.5.4)。スター型(ストッカーがハブ)の移動時間。"""


class AgvTask(object):
    __slots__ = ("lot", "kind", "from_node", "to_node", "request_time",
                 "seq", "agv", "depart_empty", "arrive_from", "load_start",
                 "depart_from", "arrive_to", "unload_start")

    def __init__(self, lot, kind, from_node, to_node, request_time, seq):
        self.lot = lot
        self.kind = kind            # "in"=ストッカー→設備, "out"=設備→ストッカー
        self.from_node = from_node
        self.to_node = to_node
        self.request_time = request_time
        self.seq = seq
        self.agv = None
        self.depart_empty = 0.0
        self.arrive_from = 0.0
        self.load_start = 0.0
        self.depart_from = 0.0
        self.arrive_to = 0.0
        self.unload_start = 0.0


class Agv(object):
    __slots__ = ("agv_id", "area", "state", "pos", "task",
                 "empty_time", "loaded_time", "tasks", "wait_sum", "wait_n")

    def __init__(self, agv_id, area, pos):
        self.agv_id = agv_id
        self.area = area
        self.state = "IDLE"         # IDLE | MOVING_EMPTY | CARRYING
        self.pos = pos              # 直近のノード (stocker_id または equip_id)
        self.task = None
        self.empty_time = 0.0
        self.loaded_time = 0.0
        self.tasks = 0
        self.wait_sum = 0.0
        self.wait_n = 0


class AgvFleet(object):
    """1 エリアの AGV 群とタスク行列。"""

    def __init__(self, area_cfg, stocker_id, out_first=True):
        self.area = area_cfg.name
        self.load_time = area_cfg.agv_load_time
        self.unload_time = area_cfg.agv_unload_time
        self.out_first = out_first
        self.stocker_id = stocker_id
        self.return_home = getattr(area_cfg, "agv_return_home", 1)
        self.agvs = [Agv("%s_AGV%d" % (self.area, i + 1), self.area, stocker_id)
                     for i in range(area_cfg.agv_count)]
        self.queue = []             # AgvTask
        self._seq = 0

    def dist(self, a, b, equip_times):
        """ノード a → b の移動時間。equip_times: equip_id -> agv_time。"""
        if a == b:
            return 0.0
        ta = 0.0 if a == self.stocker_id else equip_times[a]
        tb = 0.0 if b == self.stocker_id else equip_times[b]
        return ta + tb

    def request(self, task):
        task.seq = self._seq
        self._seq += 1
        self.queue.append(task)

    def _task_key(self, task):
        kind_rank = (0 if task.kind == "out" else 1) if self.out_first else 0
        return (kind_rank, -task.lot.priority, task.request_time, task.seq)

    def try_assign(self, sim, now):
        """空き AGV と待ちタスクを突き合わせて割当てる。"""
        idle = [a for a in self.agvs if a.state == "IDLE"]
        while idle and self.queue:
            self.queue.sort(key=self._task_key)
            task = self.queue[0]
            # 空走時間が最小の AGV (同率は agv_id 昇順)
            agv = min(idle, key=lambda a: (self.dist(a.pos, task.from_node,
                                                     sim.equip_agv_time),
                                           a.agv_id))
            self.queue.pop(0)
            idle.remove(agv)
            agv.task = task
            task.agv = agv
            sim._agv_start_task(agv, task, now)
