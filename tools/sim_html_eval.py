"""Drive the REAL tools/mining_sim.html as the canonical game oracle and score
each planner against it.

Rationale (user directive 2026-06-17): mining_sim.html IS the authoritative
game model. Instead of trusting a Python re-port of its rules, we run the actual
HTML in a headless browser so there is ZERO divergence: the page's own JS
enforces every rule (cluster placement, reachability, frontier-dig, scroll,
reward). The Python planner only ever sees the 7x6 viewport the page exposes;
anything below the viewport is hidden (the planner must extrapolate), exactly
like the live game.

What it measures, per planner, over N seeded rounds:
  - score / pits / depth (read from the page's own stats)
  - STUCK detection: the planner produced steps but the board signature did not
    change for `STUCK_LIMIT` consecutive planner iterations (the live deadlock
    signature), or it returned no usable step.

A seeded RNG is injected before the page boots (overrides Math.random) so the
same seed reproduces the same mine across planners — fair head-to-head.

Usage:
    python tools/sim_html_eval.py --planners v1,v3,v4 --runs 5
    python tools/sim_html_eval.py --planners v4 --runs 5 --max-iters 400 --headed
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

from miner.planning.smart_planner import plan_smart
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4

PLANNERS = {"v1": plan_smart, "v3": plan_v3, "v4": plan_v4}

STUCK_LIMIT = 4  # identical board sig for this many planner iters == deadlock

# Top-pileup instrumentation: pit labels (mirror mining_sim.html isPit()).
_PIT_LABELS = {"pit", "reachable_pit", "unreachable_pit"}

# Seeded Math.random override (mulberry32). Injected before the page scripts run
# so every Math.random() call inside mining_sim.html is deterministic per seed.
_SEED_SCRIPT = """
(function(){
  let s = %d >>> 0;
  Math.random = function(){
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
})();
"""

# JS helper to apply one planner step using the page's own action functions,
# then run the page's own reachability + scroll, mirroring onCellClick exactly.
_APPLY_HELPER = """
window.__applyStep = function(kind, item, r, c){
  let ok = false;
  if (kind === 'dig') ok = doPickaxe(r, c);
  else if (kind === 'use' && item === 'bomb') ok = doBomb(r, c);
  else if (kind === 'use' && item === 'drill') ok = doDrill(r, c);
  if (ok) { recomputeReachability(); checkAndMaybeScroll(); }
  return ok;
};
window.__snapshot = function(){
  return { board: board.map(row => row.slice()),
           inv: Object.assign({}, inv),
           score: stats.score, pits: stats.pits, depth: stats.depth,
           digs: stats.digs, cost: stats.cost };
};
"""


def _board_sig(board):
    return "|".join("".join(row) for row in board)


def _game_over(inv):
    return inv["pickaxe"] <= 0 and inv["bomb"] <= 0 and inv["drill"] <= 0


def play_one(page, plan_fn, max_iters):
    """Run one full game on the already-loaded page. Returns a result dict."""
    snap = page.evaluate("__snapshot()")
    last_sig = None
    same_count = 0
    stuck = False
    iters = 0
    # Top-pileup metrics: how often / how long a pit lingers in row 0 (the
    # planner refusing to collect top ore -> it scrolls off uncollected).
    row0_pit_iters = 0
    row0_run = 0
    row0_run_max = 0
    for iters in range(1, max_iters + 1):
        inv = snap["inv"]
        if _game_over(inv):
            break
        board = snap["board"]
        if any(cell in _PIT_LABELS for cell in board[0]):
            row0_pit_iters += 1
            row0_run += 1
            row0_run_max = max(row0_run_max, row0_run)
        else:
            row0_run = 0
        sig = _board_sig(board)
        if sig == last_sig:
            same_count += 1
            if same_count >= STUCK_LIMIT:
                stuck = True
                break
        else:
            same_count = 0
        last_sig = sig

        plan = plan_fn(
            [row[:] for row in board],
            shovels=float(inv["pickaxe"]),
            items={"drill": inv["drill"], "bomb": inv["bomb"]},
        )
        steps = plan.get("steps") or []
        if not steps:
            # No usable plan and the board has not changed -> stuck (matches the
            # live "empty plan" path; we let the loop re-evaluate once then bail).
            same_count += 1
            if same_count >= STUCK_LIMIT:
                stuck = True
                break
            continue
        for step in steps:
            kind = step.get("type")
            item = step.get("item")
            pos = step.get("pos") or step.get("target")
            if pos is None:
                continue
            r, c = int(pos[0]), int(pos[1])
            ok = page.evaluate("(a) => __applyStep(a[0], a[1], a[2], a[3])",
                               [kind, item, r, c])
            snap = page.evaluate("__snapshot()")
            if not ok:
                break  # illegal/no-op step: re-plan from fresh board
    return {
        "score": snap["score"], "pits": snap["pits"], "depth": snap["depth"],
        "cost": snap["cost"], "digs": snap["digs"], "stuck": stuck, "iters": iters,
        "row0_pit_iters": row0_pit_iters, "row0_run_max": row0_run_max,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--planners", default="v1,v3,v4")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--max-iters", type=int, default=600)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    url = "file:///" + str((REPO_ROOT / "tools" / "mining_sim.html").resolve()).replace("\\", "/")
    names = args.planners.split(",")

    print(f"Driving real mining_sim.html | planners={names} runs={args.runs} "
          f"seeds={args.seed}..{args.seed + args.runs - 1} stuck_limit={STUCK_LIMIT}")
    print("=" * 86)
    print(f"{'planner':8s} {'score':>8s} {'pits':>6s} {'depth':>6s} {'cost':>7s} "
          f"{'pit/sh':>7s} {'stuck':>6s} {'r0pit%':>7s} {'r0run':>6s} {'rounds':>6s}")
    print("-" * 100)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        for name in names:
            plan_fn = PLANNERS[name]
            rows = []
            for i in range(args.runs):
                seed = args.seed + i
                page = browser.new_page()
                page.add_init_script(_SEED_SCRIPT % seed)
                page.goto(url)
                # boot reset() already ran under the seeded RNG (add_init_script
                # runs before page scripts), so the mine is seeded and identical
                # across planners for a given seed. Just install the helpers.
                page.evaluate(_APPLY_HELPER)
                rows.append(play_one(page, plan_fn, args.max_iters))
                page.close()
            sc = statistics.mean(r["score"] for r in rows)
            pit = statistics.mean(r["pits"] for r in rows)
            dep = statistics.mean(r["depth"] for r in rows)
            cost = statistics.mean(r["cost"] for r in rows)
            pps = statistics.mean((r["pits"] / r["cost"]) if r["cost"] else 0 for r in rows)
            stuck = sum(1 for r in rows if r["stuck"])
            # row-0 pit pileup: % of planner iters with a pit stuck in row 0,
            # and the longest consecutive run (averaged across rounds).
            r0pct = statistics.mean(
                (r["row0_pit_iters"] / r["iters"] * 100) if r["iters"] else 0 for r in rows
            )
            r0run = statistics.mean(r["row0_run_max"] for r in rows)
            print(f"{name:8s} {sc:8.0f} {pit:6.1f} {dep:6.1f} {cost:7.0f} "
                  f"{pps:7.2f} {stuck:6d} {r0pct:7.1f} {r0run:6.1f} {len(rows):6d}")
        browser.close()


if __name__ == "__main__":
    main()
