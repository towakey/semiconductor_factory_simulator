"""CSV/JSON 出力 (設計書 6.3)。"""
import csv
import json
import os
import shutil

def _w(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def write_reports(sim, out_dir, copy_config=True, extra=None):
    stats = sim.stats
    summary = stats.summary(sim)
    if extra:
        summary.update(extra)
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    _w(os.path.join(out_dir, "lots.csv"),
       ["lot_id", "product", "priority", "wafers", "release_time",
        "due_date", "complete_time", "cycle_time", "wait_total"],
       [[r["lot_id"], r["product"], r["priority"], r["wafers"],
         r["release_time"], r["due_date"], r["complete_time"],
         r["cycle_time"], r["wait_total"]] for r in stats.lot_rows])

    eq_rows = []
    for e in sorted(sim.model.equipments.values(), key=lambda x: x.equip_id):
        st = stats.state_times_window(e.equip_id, sim.p.warmup, sim.duration)
        total = sim.duration - sim.p.warmup
        denom = total - st.get("DOWN", 0.0) - st.get("PM", 0.0)
        busy = st.get("BUSY", 0.0)
        eq_rows.append([
            e.equip_id, e.tool_group, e.area,
            round(busy, 3), round(st.get("IDLE", 0.0), 3),
            round(st.get("RESERVED", 0.0), 3),
            round(st.get("SETUP", 0.0), 3),
            round(st.get("DOWN", 0.0), 3), round(st.get("PM", 0.0), 3),
            round(busy / denom, 6) if denom > 0 else 0.0,
            round(busy / total, 6) if total > 0 else 0.0])
    _w(os.path.join(out_dir, "equipment.csv"),
       ["equip_id", "tool_group", "area", "busy", "idle", "reserved",
        "setup", "down", "pm", "util_op", "util_total"], eq_rows)

    _w(os.path.join(out_dir, "wip_timeseries.csv"),
       ["t", "tool_group", "wip"], stats.wip_ts)

    tr_rows = []
    for lot in sorted(sim.lots.values(), key=lambda l: l.lot_id):
        tr_rows.append([
            lot.lot_id, round(lot.t_stocker_wait, 3),
            round(lot.t_agv_wait, 3), round(lot.t_agv_move, 3),
            round(lot.t_cl_wait, 3), round(lot.t_cl_move, 3),
            round(lot.t_port_wait, 3), round(lot.t_equip_wait, 3),
            lot.n_agv, lot.n_cl, round(lot.transport_total(), 3)])
    _w(os.path.join(out_dir, "transport.csv"),
       ["lot_id", "stocker_wait", "agv_wait", "agv_move", "cl_wait",
        "cl_move", "port_wait", "equip_wait", "agv_tasks", "cl_rides",
        "transport_total"], tr_rows)

    agv_rows = []
    for area in sorted(sim.fleets):
        for a in sim.fleets[area].agvs:
            busy = a.empty_time + a.loaded_time
            agv_rows.append([
                a.agv_id, area, round(a.empty_time, 3),
                round(a.loaded_time, 3),
                round(busy / sim.duration, 6) if sim.duration else 0.0,
                a.tasks,
                round(a.wait_sum / a.wait_n, 3) if a.wait_n else 0.0])
    _w(os.path.join(out_dir, "agv.csv"),
       ["agv_id", "area", "empty_time", "loaded_time", "util", "tasks",
        "avg_task_wait"], agv_rows)

    st_rows = []
    for s in sorted(sim.stockers.values(), key=lambda x: x.stocker_id):
        st_rows.append([
            s.stocker_id, s.area, s.capacity,
            round(s.avg_stored(sim.duration), 4), s.max_occ,
            round(s.full_ratio(sim.duration), 6), s.rejects])
    _w(os.path.join(out_dir, "stocker.csv"),
       ["stocker_id", "area", "capacity", "avg_stored", "max_stored",
        "full_ratio", "rejects"], st_rows)

    cl = sim.cl
    cl_rows = []
    for k in range(cl.M):
        n = cl.rides_done[k]
        cl_rows.append([
            k, round(cl.slot_busy[k], 3),
            round(cl.slot_busy[k] / (sim.duration * cl.S), 6)
            if sim.duration else 0.0,
            n, round(cl.load_wait_sum[k] / n, 3) if n else 0.0,
            cl.reloop_sum[k]])
    _w(os.path.join(out_dir, "cleanliner_stats.csv"),
       ["carrier_id", "slot_busy", "util", "rides", "avg_load_wait",
        "reloops"], cl_rows)

    _w(os.path.join(out_dir, "stocker_timeseries.csv"),
       ["t", "stocker_id", "stored"], stats.stocker_ts)

    if sim.trace_on:
        with open(os.path.join(out_dir, "events.log"), "w",
                  encoding="utf-8") as f:
            for t, kind, obj, lot, detail in stats.trace_rows:
                f.write("%s\t%s\t%s\t%s\t%s\n" % (t, kind, obj, lot, detail))

    if copy_config and sim.model.source_dir:
        dst = os.path.join(out_dir, "config")
        same = os.path.realpath(sim.model.source_dir) == \
            os.path.realpath(dst)
        if not same:
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(sim.model.source_dir, dst)

    return summary


def write_compare(results, out_dir):
    """results: [{"rule","seed","summary"}] → compare.csv / compare.json。"""
    os.makedirs(out_dir, exist_ok=True)
    metrics = ["throughput_lots_per_day", "cycle_time_min.mean",
             "cycle_time_min.p95", "wip_avg", "completed_lots",
             "due_adherence"]

    def get(s, m):
        v = s
        for k in m.split("."):
            v = v[k]
        return v

    rows = []
    for r in results:
        row = [r["rule"], r["seed"]]
        row += [round(get(r["summary"], m), 6) for m in metrics]
        rows.append(row)
    _w(os.path.join(out_dir, "compare.csv"),
       ["rule", "seed"] + metrics, rows)

    agg = {}
    for r in results:
        agg.setdefault(r["rule"], []).append(r["summary"])
    out = {}
    import statistics
    for rule, sums in agg.items():
        entry = {}
        for m in metrics:
            xs = [get(s, m) for s in sums]
            entry[m] = {"mean": round(statistics.mean(xs), 6) if xs else 0.0,
                        "stdev": round(statistics.stdev(xs), 6)
                        if len(xs) > 1 else 0.0,
                        "n": len(xs)}
        out[rule] = entry
    with open(os.path.join(out_dir, "compare.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out
