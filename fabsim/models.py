"""ドメインモデル (設計書 第4章)。全て dataclass。"""
import math
from dataclasses import dataclass, field


EQUIP_STATES = ("IDLE", "RESERVED", "BUSY", "SETUP", "DOWN", "PM")


@dataclass
class Recipe:
    recipe: str
    tool_group: str
    process_time: float
    time_unit: str = "lot"          # lot | wafer
    dist: str = "fixed"             # fixed | triangular | normal
    dist_param: str = ""
    batch_min: int = 1
    batch_max: int = 1
    batch_wait_max: float = 0.0

    @property
    def is_batch(self):
        return self.batch_max > 1

    def _dist_params(self):
        if not self.dist_param:
            return []
        out = []
        for tok in str(self.dist_param).replace(";", ",").split(","):
            tok = tok.strip()
            if tok:
                out.append(float(tok))
        return out

    def unit_time(self, rng):
        """1単位(lot または wafer)あたりの処理時間をサンプルする。"""
        base = self.process_time
        if self.dist == "triangular":
            ps = self._dist_params()
            if len(ps) >= 2:
                lo, hi = ps[0], ps[1]
                mode = min(max(base, lo), hi)
                return rng.triangular(lo, hi, mode)
            return base
        if self.dist == "normal":
            ps = self._dist_params()
            sd = ps[0] if ps else 0.0
            if sd <= 0:
                return base
            for _ in range(20):
                v = rng.gauss(base, sd)
                if v >= 0:
                    return v
            return 0.0
        return base

    def sample_time(self, wafers, rng):
        """ロット(バッチなら合計ウェハ数)の処理時間。"""
        t = self.unit_time(rng)
        if self.time_unit == "wafer":
            t *= wafers
        return t

    def nominal_time(self, wafers):
        t = self.process_time
        if self.time_unit == "wafer":
            t *= wafers
        return t


@dataclass
class Step:
    seq: int
    name: str
    recipe: Recipe


@dataclass
class Product:
    name: str
    ratio: float = 1.0
    priority: int = 0
    due_factor: float = 4.0
    wafers: int = 25
    route: list = field(default_factory=list)  # List[Step]

    def theory_time(self):
        return sum(s.recipe.nominal_time(self.wafers) for s in self.route)


@dataclass
class Equipment:
    equip_id: str
    tool_group: str
    area: str
    agv_time: float
    capacity: int = 1
    ports: int = 1   # ポート数(既定=capacity)
    mtbf: float = 0.0
    mttr: float = 0.0
    pm_interval: float = 0.0
    pm_duration: float = 0.0
    initial_recipe: str = ""
    qualified_recipes: set = None   # None = 同一ツールグループ全レシピ可
    # ---- runtime ----
    state: str = "IDLE"
    state_since: float = 0.0
    current_lots: list = field(default_factory=list)   # 到着済みロット
    expected_lots: list = field(default_factory=list)  # 予約済み(搬送中含む)
    port_queue: list = field(default_factory=list)     # 到着済・処理待ち(搬入完了順)
    recipe_in_process: str = ""
    last_recipe: str = ""
    proc_start: float = 0.0
    proc_end: float = 0.0
    proc_event: object = None
    fail_remaining: float = math.inf   # 稼働時間ベースの残り故障間隔
    fail_gen: int = 0
    down_proc_remaining: float = 0.0   # 故障中断した処理の残り
    pm_pending: bool = False
    setup_end: float = 0.0

    def can_process(self, recipe):
        if self.qualified_recipes is None:
            return True
        return recipe.recipe in self.qualified_recipes


@dataclass
class ToolGroup:
    name: str
    dispatch_rule: str = ""
    shape: str = "rect"
    equipments: list = field(default_factory=list)


@dataclass
class Area:
    name: str
    agv_count: int
    agv_load_time: float = 0.0
    agv_unload_time: float = 0.0
    agv_return_home: int = 1


@dataclass
class StockerCfg:
    stocker_id: str
    area: str
    capacity: int
    in_ports: int = 1
    out_ports: int = 1
    port_time: float = 0.0


@dataclass
class SimParams:
    duration: float = 0.0
    warmup: float = 0.0
    seed: int = 1
    dispatch_rule: str = "FIFO"
    release_mode: str = "fixed"      # fixed | poisson | schedule
    lots_per_day: float = 0.0
    release_control: str = "fixed"   # fixed | conwip
    wip_limit: int = 0
    release_stocker: str = ""
    area_select: str = "LEAST_LOADED"  # LEAST_LOADED | NEAREST
    agv_out_first: int = 1
    cl_carriers: int = 1
    cl_slots: int = 2
    cl_load_time: float = 0.0
    cl_unload_time: float = 0.0
    failure_policy: str = "resume"   # resume | restart


@dataclass
class Lot:
    lot_id: str
    product: Product
    priority: int
    wafers: int
    release_time: float
    due_date: float
    route_index: int = 0
    complete_time: float = None
    history: list = field(default_factory=list)  # (seq, step_name, recipe, equip, queue_in, start, end)
    # runtime
    state: str = "NEW"      # NEW/STORED/QUEUED/CL_WAIT/AGV_IN/AT_EQUIP/AGV_OUT/DONE
    cur_stocker: str = ""
    dest_area: str = ""
    queue_in: float = 0.0
    step_arrive: float = 0.0   # 設備到着時刻
    step_recipe: str = ""
    step_setup: float = 0.0    # 現ステップの段取り時間
    step_proc: float = 0.0     # 現ステップの処理時間
    # 搬送時間内訳 (5.5.6) + equip_wait(バッチ組立待ち)
    t_stocker_wait: float = 0.0
    t_agv_wait: float = 0.0
    t_agv_move: float = 0.0
    t_cl_wait: float = 0.0
    t_cl_move: float = 0.0
    t_port_wait: float = 0.0
    t_equip_wait: float = 0.0
    t_proc_sum: float = 0.0
    t_setup_sum: float = 0.0
    n_agv: int = 0
    n_cl: int = 0

    def next_step(self):
        if self.route_index < len(self.product.route):
            return self.product.route[self.route_index]
        return None

    def transport_total(self):
        return (self.t_stocker_wait + self.t_agv_wait + self.t_agv_move +
                self.t_cl_wait + self.t_cl_move + self.t_port_wait +
                self.t_equip_wait)


@dataclass
class ReleaseRow:
    lot_id: str
    product: str
    release_time: float
    priority: int = None
    due_date: float = None


@dataclass
class Model:
    params: SimParams
    products: dict = field(default_factory=dict)      # name -> Product
    recipes: dict = field(default_factory=dict)       # recipe -> Recipe
    tool_groups: dict = field(default_factory=dict)   # name -> ToolGroup
    equipments: dict = field(default_factory=dict)    # equip_id -> Equipment
    areas: dict = field(default_factory=dict)         # name -> Area
    stockers: dict = field(default_factory=dict)      # stocker_id -> StockerCfg
    cl_stations: list = field(default_factory=list)   # [(seq, stocker_id, time_to_next)]
    setups: dict = field(default_factory=dict)        # (tg, from, to) -> min
    release_rows: list = field(default_factory=list)  # List[ReleaseRow]
    source_dir: str = ""
    warnings: list = field(default_factory=list)

    def stocker_of_area(self, area):
        for s in self.stockers.values():
            if s.area == area:
                return s
        return None

    def areas_with_group(self, tg):
        out = []
        for e in self.equipments.values():
            if e.tool_group == tg and e.area not in out:
                out.append(e.area)
        return sorted(out)
