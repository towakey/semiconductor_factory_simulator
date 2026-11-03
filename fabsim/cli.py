"""CLI (設計書 6.2): run / compare / validate。"""
import argparse
import datetime
import json
import os
import shutil
import sys
import traceback
import uuid

from . import config, layout, report
from .dbwriter import DBWriter, SCHEMA_VERSION
from .engine import Simulator
from .rules import RULES
from .transport.cleanliner import CleanLiner


def _eprint(msg):
    sys.stderr.write(msg + "\n")


def _load_or_fail(config_dir):
    model, errors, warnings = config.load_model(config_dir)
    for w in warnings:
        _eprint("警告: " + w)
    if errors:
        for e in errors:
            _eprint("エラー: " + e)
        return None, 2
    return model, 0


def _apply_overrides(model, args):
    p = model.params
    if getattr(args, "rule", None):
        if args.rule not in RULES:
            _eprint("エラー: --rule %r が不正です %s" % (args.rule,
                                                       sorted(RULES)))
            return 2
        p.dispatch_rule = args.rule
    if getattr(args, "seed", None) is not None:
        p.seed = args.seed
    if getattr(args, "duration", None) is not None:
        if args.duration <= 0:
            _eprint("エラー: --duration は正の値が必要です")
            return 2
        p.duration = args.duration
    return 0


def _open_db(out_dir, run_id, model, trace, progress_file):
    db = DBWriter(os.path.join(out_dir, "result.db"), trace=trace)
    db.open()
    lay = layout.load_layout_for_model(model, model.source_dir)
    cl = CleanLiner(model.cl_stations, model.params.cl_carriers,
                    model.params.cl_slots, model.params.cl_load_time,
                    model.params.cl_unload_time)
    db.write_static(model, lay["nodes"], lay["world"], cl,
                    lay.get("points"))
    db.set_meta("schema_version", SCHEMA_VERSION)
    db.set_meta("run_id", run_id)
    db.set_meta("status", "running")
    db.set_meta("progress_time", 0.0)
    db.set_meta("duration", model.params.duration)
    db.set_meta("seed", model.params.seed)
    db.set_meta("rule", model.params.dispatch_rule)
    db.set_meta("warmup", model.params.warmup)
    db.set_meta("started_at", datetime.datetime.now().isoformat(
        timespec="seconds"))
    db.set_meta("cl_carriers", cl.M)
    db.set_meta("cl_slots", cl.S)
    db.set_meta("cl_period", round(cl.T, 6))
    db.set_meta("world_w", lay["world"]["w"])
    db.set_meta("world_h", lay["world"]["h"])
    db.commit(0.0)
    return db


def cmd_run(args):
    model, rc = _load_or_fail(args.config_dir)
    if model is None:
        return rc
    rc = _apply_overrides(model, args)
    if rc:
        return rc
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    run_id = args.run_id or (
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" +
        uuid.uuid4().hex[:6])
    db = None
    if not args.no_db:
        db = _open_db(out_dir, run_id, model, args.trace,
                      args.progress_file)
    sim = Simulator(model, db=db, trace=args.trace,
                    progress_file=args.progress_file)
    try:
        sim.run()
    except Exception:
        if db is not None:
            db.fail(traceback.format_exc())
        _eprint(traceback.format_exc())
        return 1
    extra = {"run_id": run_id, "seed": model.params.seed,
             "rule": model.params.dispatch_rule}
    summary = report.write_reports(sim, out_dir, extra=extra)
    if db is not None:
        db.finish(summary)
    print(json.dumps({"run_id": run_id, "status": "done",
                      "completed_lots": summary["completed_lots"],
                      "wip_avg": summary["wip_avg"],
                      "throughput_lots_per_day":
                          summary["throughput_lots_per_day"]},
                     ensure_ascii=False))
    return 0


def _parse_seeds(text):
    seeds = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            seeds.extend(range(int(a), int(b) + 1))
        else:
            seeds.append(int(tok))
    return seeds


def cmd_compare(args):
    rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    for r in rules:
        if r not in RULES:
            _eprint("エラー: ルール %r が不正です %s" % (r, sorted(RULES)))
            return 2
    try:
        seeds = _parse_seeds(args.seeds)
    except ValueError:
        _eprint("エラー: --seeds %r が不正です" % args.seeds)
        return 2
    results = []
    for rule in rules:
        for seed in seeds:
            model, rc = _load_or_fail(args.config_dir)
            if model is None:
                return rc
            model.params.dispatch_rule = rule
            model.params.seed = seed
            if getattr(args, "duration", None):
                model.params.duration = args.duration
            db = None
            out_dir = args.out
            if args.db:
                run_dir = os.path.join(out_dir, "%s_%s" % (rule, seed))
                os.makedirs(run_dir, exist_ok=True)
                db = _open_db(run_dir, "compare-%s-%s" % (rule, seed),
                              model, False, None)
            sim = Simulator(model, db=db)
            try:
                sim.run()
            except Exception:
                if db is not None:
                    db.fail(traceback.format_exc())
                _eprint(traceback.format_exc())
                return 1
            summary = sim.stats.summary(sim)
            if args.db:
                report.write_reports(sim, run_dir, copy_config=False)
                db.finish(summary)
            results.append({"rule": rule, "seed": seed,
                            "summary": summary})
            _eprint("%s seed=%s: 完了 %d ロット, 平均CT %.1f"
                    % (rule, seed, summary["completed_lots"],
                       summary["cycle_time_min"]["mean"]))
    out = report.write_compare(results, args.out)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_validate(args):
    model, errors, warnings = config.load_model(args.config_dir)
    for w in warnings:
        print("警告: " + w)
    if errors:
        for e in errors:
            print("エラー: " + e)
        return 2
    print("OK: %s" % args.config_dir)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="fabsim",
                                 description="半導体工場シミュレーター")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="シミュレーション実行")
    p.add_argument("--config-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rule")
    p.add_argument("--seed", type=int)
    p.add_argument("--duration", type=float)
    p.add_argument("--run-id")
    p.add_argument("--progress-file")
    p.add_argument("--no-db", action="store_true")
    p.add_argument("--trace", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("compare", help="複数ルール×シードの比較")
    p.add_argument("--config-dir", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--seeds", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--duration", type=float)
    p.add_argument("--db", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("validate", help="設定検証のみ")
    p.add_argument("--config-dir", required=True)
    p.set_defaults(func=cmd_validate)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
