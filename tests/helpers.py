"""テスト用: 最小モデルの CSV ディレクトリを作るヘルパ。"""
import os

DEFAULTS = {
    "sim.csv": """key,value
duration,1440
warmup,0
seed,1
dispatch_rule,FIFO
release_mode,fixed
lots_per_day,24
release_control,fixed
wip_limit,0
release_stocker,S1
area_select,LEAST_LOADED
agv_out_first,1
cl_carriers,1
cl_slots,1
cl_load_time,1
cl_unload_time,1
failure_policy,resume
""",
    "products.csv": """product,ratio,priority,due_factor,wafers
P,1.0,0,4,25
""",
    "recipes.csv": """recipe,tool_group,process_time,time_unit,dist,dist_param,batch_min,batch_max,batch_wait_max
R1,TG1,10,lot,fixed,,,,
""",
    "routes.csv": """product,seq,recipe,step_name
P,10,R1,s1
P,20,R1,s2
P,30,R1,s3
""",
    "tool_groups.csv": """tool_group,dispatch_rule
TG1,
""",
    "equipments.csv": """equip_id,tool_group,area,agv_time,capacity,mtbf,mttr,pm_interval,pm_duration,initial_recipe
EQ1,TG1,A1,5,1,0,0,0,0,
""",
    "areas.csv": """area,agv_count,agv_load_time,agv_unload_time
A1,1,1,1
""",
    "stockers.csv": """stocker_id,area,capacity,in_ports,out_ports,port_time
S1,A1,50,1,1,0
""",
    "cleanliner.csv": """seq,stocker_id,time_to_next
1,S1,10
""",
}


def write_model(dirpath, **overrides):
    """最小モデルを dirpath に書く。overrides でファイル内容を差し替え。
    値が None のファイルは書かない。"""
    os.makedirs(dirpath, exist_ok=True)
    for name, text in DEFAULTS.items():
        if name in overrides:
            text = overrides[name]
        if text is None:
            continue
        with open(os.path.join(dirpath, name), "w",
                  encoding="utf-8", newline="") as f:
            f.write(text)
    for name, text in overrides.items():
        if name in DEFAULTS or text is None:
            continue
        with open(os.path.join(dirpath, name), "w",
                  encoding="utf-8", newline="") as f:
            f.write(text)
    return dirpath


def make_lot(lot_id, enqueue_time=0.0, priority=0, due=1000.0,
             proc_times=(10.0,), wafers=25, recipes=None):
    """ルールテスト用の最小ロット。proc_times は残工程の処理時間列。"""
    from fabsim.models import Lot, Product, Recipe, Step
    if recipes is None:
        recipes = ["R%d" % i for i in range(len(proc_times))]
    prod = Product(name="P", wafers=wafers)
    prod.route = [Step(i + 1, "s%d" % i,
                       Recipe(recipes[i], "TG", proc_times[i]))
                  for i in range(len(proc_times))]
    lot = Lot(lot_id=lot_id, product=prod, priority=priority,
              wafers=wafers, release_time=0.0, due_date=due)
    return lot
