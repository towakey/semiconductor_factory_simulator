"""イベント定義 (設計書 3.1)。handle(sim) が engine.Simulator の処理を呼ぶ。
同時刻の優先度(小さいほど先):
処理完了 < 搬送完了 < 故障/復旧 < PM < ロット投入 < バッチ待ち < ディスパッチ < KPI
"""

P_PROC = 0       # ProcessEnd, SetupEnd
P_TRANSPORT = 1  # AgvLeg, ClLoad/ClArrive/ClStored, StockerIn
P_FAILURE = 2    # EquipDown, EquipUp
P_PM = 3         # PMStart, PMEnd
P_RELEASE = 4    # LotRelease
P_BATCH = 5      # BatchTimeout
P_DISPATCH = 6   # Dispatch
P_TICK = 7       # KpiTick


class Ev(object):
    priority = 99
    kind = "?"

    def __init__(self):
        self.cancelled = False

    def handle(self, sim):
        raise NotImplementedError


class LotRelease(Ev):
    priority = P_RELEASE
    kind = "LotRelease"

    def __init__(self, spec):
        Ev.__init__(self)
        self.spec = spec            # (lot_id, product, release_time, priority)

    def handle(self, sim):
        sim._on_lot_release(self)


class StockerIn(Ev):
    priority = P_TRANSPORT
    kind = "StockerIn"

    def __init__(self, lot, stocker_id):
        Ev.__init__(self)
        self.lot = lot
        self.stocker_id = stocker_id

    def handle(self, sim):
        sim._on_stocker_in(self)


class SetupEnd(Ev):
    priority = P_PROC
    kind = "SetupEnd"

    def __init__(self, equip):
        Ev.__init__(self)
        self.equip = equip

    def handle(self, sim):
        sim._on_setup_end(self)


class ProcessEnd(Ev):
    priority = P_PROC
    kind = "ProcessEnd"

    def __init__(self, equip, gen):
        Ev.__init__(self)
        self.equip = equip
        self.gen = gen

    def handle(self, sim):
        sim._on_process_end(self)


class AgvLeg(Ev):
    """AGV タスクの節目: at_from / loaded / at_to / unloaded"""
    priority = P_TRANSPORT
    kind = "AgvLeg"

    def __init__(self, fleet, task, phase):
        Ev.__init__(self)
        self.fleet = fleet
        self.task = task
        self.phase = phase

    def handle(self, sim):
        sim._on_agv_leg(self)


class ClLoad(Ev):
    priority = P_TRANSPORT
    kind = "ClLoad"

    def __init__(self, ride):
        Ev.__init__(self)
        self.ride = ride

    def handle(self, sim):
        sim._on_cl_load(self)


class ClArrive(Ev):
    priority = P_TRANSPORT
    kind = "ClArrive"

    def __init__(self, ride):
        Ev.__init__(self)
        self.ride = ride

    def handle(self, sim):
        sim._on_cl_arrive(self)


class ClStored(Ev):
    priority = P_TRANSPORT
    kind = "ClStored"

    def __init__(self, ride):
        Ev.__init__(self)
        self.ride = ride

    def handle(self, sim):
        sim._on_cl_stored(self)


class EquipDown(Ev):
    priority = P_FAILURE
    kind = "EquipDown"

    def __init__(self, equip, gen):
        Ev.__init__(self)
        self.equip = equip
        self.gen = gen

    def handle(self, sim):
        sim._on_equip_down(self)


class EquipUp(Ev):
    priority = P_FAILURE
    kind = "EquipUp"

    def __init__(self, equip):
        Ev.__init__(self)
        self.equip = equip

    def handle(self, sim):
        sim._on_equip_up(self)


class PMStart(Ev):
    priority = P_PM
    kind = "PMStart"

    def __init__(self, equip):
        Ev.__init__(self)
        self.equip = equip

    def handle(self, sim):
        sim._on_pm_start(self)


class PMEnd(Ev):
    priority = P_PM
    kind = "PMEnd"

    def __init__(self, equip):
        Ev.__init__(self)
        self.equip = equip

    def handle(self, sim):
        sim._on_pm_end(self)


class BatchTimeout(Ev):
    priority = P_BATCH
    kind = "BatchTimeout"

    def __init__(self, equip_id, recipe, armed_at):
        Ev.__init__(self)
        self.equip_id = equip_id
        self.recipe = recipe
        self.armed_at = armed_at

    def handle(self, sim):
        sim._on_batch_timeout(self)


class Dispatch(Ev):
    priority = P_DISPATCH
    kind = "Dispatch"

    def __init__(self, tg, area):
        Ev.__init__(self)
        self.tg = tg
        self.area = area

    def handle(self, sim):
        sim._on_dispatch(self)


class KpiTick(Ev):
    priority = P_TICK
    kind = "KpiTick"

    def handle(self, sim):
        sim._on_kpi_tick(self)
