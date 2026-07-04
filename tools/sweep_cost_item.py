"""Sweep v1 (smart_planner) cost_item against the canonical mining_sim.html.

cost_item is the shovel-equivalent price A* pays to use a bomb/drill. Lower =
use items more freely. Reuses sim_html_eval's HTML oracle + play_one so the
numbers are directly comparable to the main eval.

Usage: python tools/sweep_cost_item.py --values 1.0,2.0,2.99,4.0 --runs 5
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))  # tools/ isn't a package

from playwright.sync_api import sync_playwright

from miner.planning.smart_planner import PlannerConfig, plan_smart
from sim_html_eval import _APPLY_HELPER, _SEED_SCRIPT, play_one


def _make_plan_fn(cost_item: float):
    cfg = PlannerConfig()
    cfg.cost_item = cost_item
    return lambda board, shovels, items: plan_smart(board, shovels, items, config=cfg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--values", default="1.0,2.0,2.99,4.0,6.0")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--max-iters", type=int, default=600)
    ap.add_argument("--density", type=float, default=None,
                    help="override mining_sim.html PIT_DENSITY (e.g. 0.024)")
    args = ap.parse_args()

    url = "file:///" + str((REPO_ROOT / "tools" / "mining_sim.html").resolve()).replace("\\", "/")
    values = [float(v) for v in args.values.split(",")]

    print(f"v1 cost_item sweep | values={values} runs={args.runs} "
          f"seeds={args.seed}..{args.seed + args.runs - 1}")
    print(f"{'cost_item':>9s} {'score':>8s} {'pits':>6s} {'cost':>7s} {'pit/sh':>7s} "
          f"{'bomb':>5s} {'drill':>6s} {'stuck':>6s}")
    print("-" * 64)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for ci in values:
            plan_fn = _make_plan_fn(ci)
            rows = []
            for i in range(args.runs):
                seed = args.seed + i
                page = browser.new_page()
                page.add_init_script(_SEED_SCRIPT % seed)
                if args.density is not None:
                    page.add_init_script(f"window.__PIT_DENSITY = {args.density};")
                page.goto(url)
                page.evaluate(_APPLY_HELPER)
                rows.append(play_one(page, plan_fn, args.max_iters))
                page.close()
            sc = statistics.mean(r["score"] for r in rows)
            pit = statistics.mean(r["pits"] for r in rows)
            cost = statistics.mean(r["cost"] for r in rows)
            pps = statistics.mean((r["pits"] / r["cost"]) if r["cost"] else 0 for r in rows)
            bombs = statistics.mean(r["bombs_used"] for r in rows)
            drills = statistics.mean(r["drills_used"] for r in rows)
            stuck = sum(1 for r in rows if r["stuck"])
            print(f"{ci:9.2f} {sc:8.0f} {pit:6.1f} {cost:7.0f} {pps:7.2f} "
                  f"{bombs:5.1f} {drills:6.1f} {stuck:6d}")
        browser.close()


if __name__ == "__main__":
    main()
