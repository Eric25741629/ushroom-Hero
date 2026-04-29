"""Drive `tools/mining_sim.html` with the v4 planner via Playwright.

Loads the HTML simulator in a real browser, reads the board state out of the
page's globals, runs `plan_v4` in Python on it, then plays the steps back via
`page.evaluate` until the game ends. Useful for end-to-end validation of the
planner against the same game logic the human player uses.

Usage:
    # Watch the AI play one game (default).
    python tools/run_planner_in_sim.py

    # Run 10 games headless and report aggregate stats.
    python tools/run_planner_in_sim.py --runs 10 --headless

    # Slow it down for easier viewing (default 50 ms between steps).
    python tools/run_planner_in_sim.py --sleep 0.2
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from miner.v4.planner import plan_v4

SIM_HTML = REPO_ROOT / "tools" / "mining_sim.html"

READ_STATE_JS = """() => ({
    board: board.map(row => [...row]),
    inv: { ...inv },
    stats: { ...stats },
    viewportRow: viewportRow,
})"""

# Apply one plan step and report whether the simulator scrolled (= viewport
# advanced). When it scrolls the rest of the plan is for a stale board, so the
# driver re-plans on the next iteration instead of pressing on.
APPLY_STEP_JS = """({ type, item, r, c }) => {
    const vpBefore = viewportRow;
    let ok = false;
    if (type === 'dig') ok = doPickaxe(r, c);
    else if (type === 'use' && item === 'bomb') ok = doBomb(r, c);
    else if (type === 'use' && item === 'drill') ok = doDrill(r, c);
    if (ok) {
        recomputeReachability();
        checkAndMaybeScroll();
        render();
    }
    return { ok, scrolled: viewportRow !== vpBefore };
}"""


def read_state(page):
    return page.evaluate(READ_STATE_JS)


def apply_step(page, step):
    pos = step["pos"]
    return page.evaluate(
        APPLY_STEP_JS,
        {
            "type": step["type"],
            "item": step.get("item"),
            "r": int(pos[0]),
            "c": int(pos[1]),
        },
    )


def play_one_game(page, max_iterations: int, sleep_sec: float, log_every: int):
    page.evaluate("reset()")
    iteration = 0
    last_state = read_state(page)
    while iteration < max_iterations:
        state = read_state(page)
        last_state = state
        inv = state["inv"]
        if inv["pickaxe"] <= 0 and inv["bomb"] <= 0 and inv["drill"] <= 0:
            print(f"  [iter {iteration}] inventory exhausted")
            break

        plan = plan_v4(
            state["board"],
            shovels=float(inv["pickaxe"]),
            items={"drill": int(inv["drill"]), "bomb": int(inv["bomb"])},
        )
        steps = plan.get("steps") or []
        if not steps:
            print(
                f"  [iter {iteration}] planner returned 0 steps "
                f"(strategy={plan.get('strategy_class')}, msg={plan.get('message','')})"
            )
            break

        for step in steps:
            res = apply_step(page, step)
            if not res["ok"]:
                # Action rejected by sim (e.g. cell wasn't diggable in current
                # state). Re-plan from the new state.
                break
            if sleep_sec:
                time.sleep(sleep_sec)
            if res["scrolled"]:
                # Viewport advanced — remaining steps are for stale rows.
                break

        iteration += 1
        if log_every and iteration % log_every == 0:
            stats = state["stats"]
            print(
                f"  [iter {iteration}] depth={stats['depth']} score={stats['score']} "
                f"pits={stats['pits']} digs={stats['digs']} inv={inv}"
            )

    return last_state["stats"], last_state["inv"]


def aggregate(results, key):
    values = [r["stats"][key] for r in results]
    return {
        "avg": statistics.mean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="how many games to play")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="seconds between steps (visualisation pacing; ignored in headless)",
    )
    parser.add_argument("--max-iterations", type=int, default=2000)
    parser.add_argument("--log-every", type=int, default=20)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    sleep = 0.0 if args.headless else args.sleep
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1100, "height": 900})
        page = context.new_page()
        page.goto(SIM_HTML.absolute().as_uri())
        page.wait_for_function("typeof doPickaxe === 'function' && Array.isArray(board)")

        for run_idx in range(args.runs):
            print(f"=== Run {run_idx + 1}/{args.runs} ===")
            stats, inv = play_one_game(
                page,
                max_iterations=args.max_iterations,
                sleep_sec=sleep,
                log_every=args.log_every,
            )
            print(f"  final stats:    {stats}")
            print(f"  remaining inv:  {inv}")
            results.append({"stats": stats, "inv": inv})

        browser.close()

    if len(results) > 1:
        print("\n=== Aggregate over runs ===")
        for key in ("score", "pits", "depth", "digs", "cost"):
            agg = aggregate(results, key)
            print(
                f"  {key:6s}  avg={agg['avg']:8.1f}  min={agg['min']:6.0f}  "
                f"max={agg['max']:6.0f}  stdev={agg['stdev']:.1f}"
            )
        cpp = [
            (r["stats"]["cost"] / r["stats"]["pits"]) if r["stats"]["pits"] else 0.0
            for r in results
        ]
        avg_cpp = statistics.mean(cpp)
        print(f"  cost/pit  avg={avg_cpp:.2f}")


if __name__ == "__main__":
    main()
