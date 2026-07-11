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


def run(planner, runs, seed, max_iter, inv, time_cap_s, known_rows=7):
    rows = []
    t0 = time.perf_counter()
    for i in range(runs):
        if time.perf_counter() - t0 > time_cap_s:
            break
        r = play_one_game(
            seed=seed + i, max_iter=max_iter, planner=planner,
            starting_inv=dict(inv), known_rows=known_rows,
        )
        rows.append(r)
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
    return {
        "n": len(rows),
        "score": statistics.mean(sc),
        "pits": statistics.mean(pit),
        "depth": statistics.mean(dep),
        "cost": statistics.mean(cost),
        "pits_per_shovel": statistics.mean(pps),
        "plan_ms": statistics.mean(all_times) if all_times else (statistics.mean(ms) if ms else 0.0),
        "plan_ms_p95": percentile(all_times, 0.95),
        "plan_ms_p99": percentile(all_times, 0.99),
        "plan_ms_max": max(all_times, default=0.0),
        "density": 100 * statistics.mean(dens),
        "fb_rate": fb_rate,
        "stuck": stuck,
        "clusters": sum(sum(r["stats"].clusters_completed.values()) for r in rows),
        "pits_per_item": pits / items if items else 0.0,
        "pits_per_equal_cost": (
            pits / (shovels + equal_item_weight * items) if (shovels + items) else 0.0
        ),
        "lost_pits": sum(r.get("lost_pits", 0) for r in rows),
        "unfinished_clusters": sum(r.get("unfinished_clusters", 0) for r in rows),
        "rejected": sum(r.get("rejected", 0) for r in rows),
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
    args = ap.parse_args()

    inv = ({"pickaxe": 1000, "bomb": 10, "drill": 10} if args.scarce
           else {"pickaxe": 1000, "bomb": 600, "drill": 60})
    print(f"inventory: {inv}  | seeds {args.seed}..{args.seed+args.runs-1} "
          f"| max_iter={args.max_iter} | known_rows={args.known_rows} (final_v1 only)")
    print("=" * 150)
    hdr = (f"{'planner':8s} {'n':>3s} {'score':>8s} {'pits':>6s} {'clus':>5s} {'depth':>6s} "
           f"{'cost':>7s} {'pit/sh':>7s} {'pit/it':>7s} {'pit/eq':>7s} "
           f"{'plan_ms':>8s} {'p95':>7s} {'p99':>7s} {'max_ms':>8s} "
           f"{'fb%':>6s} {'stuck':>6s} {'rej':>5s} {'lost':>5s} {'unfin':>6s}")
    print(hdr)
    print("-" * 150)
    for p in args.planners.split(","):
        rows = run(p, args.runs, args.seed, args.max_iter, inv, args.time_cap,
                   known_rows=args.known_rows)
        a = agg(rows, equal_item_weight=args.equal_item_weight)
        if a is None:
            print(f"{p:8s}  (no runs completed within time cap)")
            continue
        print(f"{p:8s} {a['n']:3d} {a['score']:8.0f} {a['pits']:6.1f} "
              f"{a['clusters']:5d} "
              f"{a['depth']:6.1f} {a['cost']:7.0f} {a['pits_per_shovel']:7.1f} "
              f"{a['pits_per_item']:7.2f} {a['pits_per_equal_cost']:7.3f} "
              f"{a['plan_ms']:8.2f} {a['plan_ms_p95']:7.2f} {a['plan_ms_p99']:7.2f} "
              f"{a['plan_ms_max']:8.2f} {a['fb_rate']:6.1f} "
              f"{a['stuck']:6d} {a['rejected']:5d} {a['lost_pits']:5d} "
              f"{a['unfinished_clusters']:6d}")


if __name__ == "__main__":
    main()
