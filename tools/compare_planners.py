"""Side-by-side planner comparison on the (recalibrated) realistic sim.

Runs each planner over the SAME seed set and prints one comparison table so
the realistic-regime ranking is directly visible. Item inventory defaults to
the live-realistic abundance (bombs plentiful, drills moderate) but can be set
to the legacy scarce inventory with --scarce.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mining_sim_eval import PLANNERS, play_one_game


def run_paired(planners, runs, seed, max_iter, inv, time_cap_s, known_rows=7,
               exec_mode="plan"):
    """Seed-外圈 / planner-內圈：time-cap 只截斷「整輪 seed」，
    所有 planner 永遠比同一組 seed。舊版逐 planner 各自截斷會毀掉配對
    （2026-07-11 驗收 v1 跑滿 100 局、final_v1 只跑 38 局還用總和統計）。"""
    rows = {p: [] for p in planners}
    t0 = time.perf_counter()
    completed = 0
    for i in range(runs):
        if time.perf_counter() - t0 > time_cap_s:
            break
        for p in planners:
            r = play_one_game(
                seed=seed + i, max_iter=max_iter, planner=p,
                starting_inv=dict(inv), known_rows=known_rows,
                exec_mode=exec_mode,
            )
            rows[p].append(r)
        completed = i + 1
    if completed < runs:
        print(f"  !! time-cap: only {completed}/{runs} paired seed-rounds completed")
    return rows


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))
    return ordered[index]


def agg(rows, equal_item_weight=3.0):
    if not rows:
        return None
    sc = [r["stats"].score for r in rows]
    pit = [r["stats"].pits for r in rows]
    dep = [r["stats"].depth for r in rows]
    cost = [r["stats"].cost for r in rows]
    pps = [(r["stats"].pits / r["stats"].cost) if r["stats"].cost else 0 for r in rows]
    all_times = [ms for r in rows for ms in r.get("plan_times_ms", [])]
    ms = [r.get("plan_avg_ms", 0.0) for r in rows]
    dens = [r.get("standing_pit_density", 0.0) for r in rows]
    stuck = sum(1 for r in rows if r.get("empty_plan"))
    # fallback rate = punts / total actions (how often the planner gave no step)
    fb = sum(r.get("fallbacks", 0) for r in rows)
    acts = sum(r.get("actions", 0) for r in rows)
    fb_rate = 100 * fb / acts if acts else 0.0
    pits = sum(r["stats"].pits for r in rows)
    shovels = sum(r["stats"].cost for r in rows)
    items = sum(r["stats"].bombs_used + r["stats"].drills_used for r in rows)
    n = len(rows)
    # 計數指標一律「每局平均」：總和在 n 不同時完全不可比
    return {
        "n": n,
        "score": statistics.mean(sc),
        "pits": statistics.mean(pit),
        "depth": statistics.mean(dep),
        "cost": statistics.mean(cost),
        "actions": acts / n,
        "pits_per_shovel": statistics.mean(pps),
        "plan_ms": statistics.mean(all_times) if all_times else (statistics.mean(ms) if ms else 0.0),
        "plan_ms_p95": percentile(all_times, 0.95),
        "plan_ms_p99": percentile(all_times, 0.99),
        "plan_ms_max": max(all_times, default=0.0),
        "density": 100 * statistics.mean(dens),
        "fb_rate": fb_rate,
        "stuck": stuck,
        "clusters": sum(sum(r["stats"].clusters_completed.values()) for r in rows) / n,
        "pits_per_item": pits / items if items else 0.0,
        "pits_per_equal_cost": (
            pits / (shovels + equal_item_weight * items) if (shovels + items) else 0.0
        ),
        "lost_pits": sum(r.get("lost_pits", 0) for r in rows) / n,
        "unfinished_clusters": sum(r.get("unfinished_clusters", 0) for r in rows) / n,
        "rejected": sum(r.get("rejected", 0) for r in rows) / n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--max-iter", type=int, default=120)
    ap.add_argument("--planners", default="v1,v4,final_v1")
    ap.add_argument("--scarce", action="store_true",
                    help="use legacy scarce inventory (bomb=10, drill=10)")
    ap.add_argument("--time-cap", type=float, default=90.0,
                    help="per-planner wall-clock cap (s) to bound stuck planners")
    ap.add_argument("--known-rows", type=int, choices=[7, 21], default=7,
                    help="known board rows fed to final_v1 only (v1/v3/v4 stay on 7)")
    ap.add_argument("--equal-item-weight", type=float, default=3.0,
                    help="item weight in pits/(shovel + w*items)")
    ap.add_argument("--exec-mode", choices=["plan", "step"], default="plan",
                    help="plan=整份 plan 連發 (CNN 語意); step=一次一步再重規劃 (WS 語意)")
    ap.add_argument("--pickaxe", type=int, default=None,
                    help="starting pickaxes (真實 session ~100-350; 預設 1000)")
    args = ap.parse_args()

    inv = ({"pickaxe": 1000, "bomb": 10, "drill": 10} if args.scarce
           else {"pickaxe": 1000, "bomb": 600, "drill": 60})
    if args.pickaxe is not None:
        inv["pickaxe"] = args.pickaxe
    planners = args.planners.split(",")
    print(f"inventory: {inv}  | seeds {args.seed}..{args.seed+args.runs-1} "
          f"| max_iter={args.max_iter} | known_rows={args.known_rows} (final_v1 only) "
          f"| exec_mode={args.exec_mode}")
    print("=" * 160)
    hdr = (f"{'planner':8s} {'n':>3s} {'score':>8s} {'pits':>6s} {'clus/g':>6s} {'depth':>6s} "
           f"{'cost':>7s} {'acts':>6s} {'pit/sh':>7s} {'pit/it':>7s} {'pit/eq':>7s} "
           f"{'plan_ms':>8s} {'p95':>7s} {'p99':>7s} {'max_ms':>8s} "
           f"{'fb%':>6s} {'stuck':>6s} {'rej/g':>6s} {'lost/g':>7s} {'unf/g':>6s}")
    print(hdr)
    print("-" * 160)
    all_rows = run_paired(planners, args.runs, args.seed, args.max_iter, inv,
                          args.time_cap, known_rows=args.known_rows,
                          exec_mode=args.exec_mode)
    for p in planners:
        a = agg(all_rows[p], equal_item_weight=args.equal_item_weight)
        if a is None:
            print(f"{p:8s}  (no runs completed within time cap)")
            continue
        print(f"{p:8s} {a['n']:3d} {a['score']:8.0f} {a['pits']:6.1f} "
              f"{a['clusters']:6.1f} "
              f"{a['depth']:6.1f} {a['cost']:7.0f} {a['actions']:6.1f} "
              f"{a['pits_per_shovel']:7.2f} "
              f"{a['pits_per_item']:7.2f} {a['pits_per_equal_cost']:7.3f} "
              f"{a['plan_ms']:8.2f} {a['plan_ms_p95']:7.2f} {a['plan_ms_p99']:7.2f} "
              f"{a['plan_ms_max']:8.2f} {a['fb_rate']:6.1f} "
              f"{a['stuck']:6d} {a['rejected']:6.1f} {a['lost_pits']:7.1f} "
              f"{a['unfinished_clusters']:6.1f}")


if __name__ == "__main__":
    main()
