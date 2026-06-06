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


def run(planner, runs, seed, max_iter, inv, time_cap_s):
    rows = []
    t0 = time.perf_counter()
    for i in range(runs):
        if time.perf_counter() - t0 > time_cap_s:
            break
        r = play_one_game(
            seed=seed + i, max_iter=max_iter, planner=planner,
            starting_inv=dict(inv),
        )
        rows.append(r)
    return rows


def agg(rows):
    if not rows:
        return None
    sc = [r["stats"].score for r in rows]
    pit = [r["stats"].pits for r in rows]
    dep = [r["stats"].depth for r in rows]
    cost = [r["stats"].cost for r in rows]
    pps = [(r["stats"].pits / r["stats"].cost) if r["stats"].cost else 0 for r in rows]
    ms = [r["plan_avg_ms"] for r in rows]
    maxms = max((r["plan_avg_ms"] for r in rows), default=0)
    dens = [r["standing_pit_density"] for r in rows]
    stuck = sum(1 for r in rows if r["empty_plan"])
    # fallback rate = punts / total actions (how often the planner gave no step)
    fb = sum(r.get("fallbacks", 0) for r in rows)
    acts = sum(r["actions"] for r in rows)
    fb_rate = 100 * fb / acts if acts else 0.0
    return {
        "n": len(rows),
        "score": statistics.mean(sc),
        "pits": statistics.mean(pit),
        "depth": statistics.mean(dep),
        "cost": statistics.mean(cost),
        "pits_per_shovel": statistics.mean(pps),
        "plan_ms": statistics.mean(ms),
        "plan_ms_max": maxms,
        "density": 100 * statistics.mean(dens),
        "fb_rate": fb_rate,
        "stuck": stuck,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--max-iter", type=int, default=120)
    ap.add_argument("--planners", default="v1,v3,v4")
    ap.add_argument("--scarce", action="store_true",
                    help="use legacy scarce inventory (bomb=10, drill=10)")
    ap.add_argument("--time-cap", type=float, default=90.0,
                    help="per-planner wall-clock cap (s) to bound stuck planners")
    args = ap.parse_args()

    inv = ({"pickaxe": 1000, "bomb": 10, "drill": 10} if args.scarce
           else {"pickaxe": 1000, "bomb": 600, "drill": 60})
    print(f"inventory: {inv}  | seeds {args.seed}..{args.seed+args.runs-1} "
          f"| max_iter={args.max_iter}")
    print("=" * 104)
    hdr = (f"{'planner':8s} {'n':>3s} {'score':>8s} {'pits':>6s} {'depth':>6s} "
           f"{'cost':>7s} {'pit/sh':>7s} {'plan_ms':>8s} {'max_ms':>8s} "
           f"{'fb%':>6s} {'stuck':>6s}")
    print(hdr)
    print("-" * 104)
    for p in args.planners.split(","):
        rows = run(p, args.runs, args.seed, args.max_iter, inv, args.time_cap)
        a = agg(rows)
        if a is None:
            print(f"{p:8s}  (no runs completed within time cap)")
            continue
        print(f"{p:8s} {a['n']:3d} {a['score']:8.0f} {a['pits']:6.1f} "
              f"{a['depth']:6.1f} {a['cost']:7.0f} {a['pits_per_shovel']:7.1f} "
              f"{a['plan_ms']:8.2f} {a['plan_ms_max']:8.2f} {a['fb_rate']:6.1f} "
              f"{a['stuck']:6d}")


if __name__ == "__main__":
    main()
