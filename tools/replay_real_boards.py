"""Replay every planner on the REAL board snapshots captured in miner.log.

This is the gold-standard punt/timing test: it feeds each planner the exact
boards the live game produced (parsed from get_visual_board output), with no
simulator in the loop, and measures:
  - empty-plan rate (how often the planner returns 0 steps = a live ~7s waste)
  - plan timing distribution (must stay < 300ms/step)
  - first-step action mix

Symbol -> label map is the inverse of miner.mining_service.get_visual_board.
"""
from __future__ import annotations

import argparse
import glob
import re
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from miner.planning.smart_planner import plan_smart
from miner.final_v1 import plan_final_v1
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4

# v2 removed 2026-06-05; v5 removed 2026-06-18 (lowest score at real 3.6% density).
PLANNERS = {"v1": plan_smart, "v3": plan_v3, "v4": plan_v4,
            "final_v1": plan_final_v1}

SYMBOL_TO_LABEL = {
    ".": "empty",
    "_": "unreachable_empty",
    "D": "dirt",
    "d": "unreachable_dirt",
    "R": "rock",
    "r": "unreachable_rock",
    "*": "reachable_pit",
    "X": "unreachable_pit",
}

BOARD_HEADER_RE = re.compile(r"^\s*0 1 2 3 4 5\s*$")
BOARD_ROW_RE = re.compile(r"^\s*\d+\s+([.\_DdRr*X ]+)$")


def parse_boards(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    boards = []
    i, n = 0, len(lines)
    while i < n:
        content = lines[i].split("] ", 1)[-1] if "] " in lines[i] else lines[i]
        if BOARD_HEADER_RE.match(content.strip()):
            grid = []
            j = i + 1
            while j < n and len(grid) < 7:
                rc = lines[j].split("] ", 1)[-1] if "] " in lines[j] else lines[j]
                rm = BOARD_ROW_RE.match(rc)
                if not rm:
                    break
                cells = rm.group(1).split()
                if len(cells) == 6:
                    grid.append([SYMBOL_TO_LABEL.get(ch, "dirt") for ch in cells])
                j += 1
            if len(grid) == 7:
                boards.append(grid)
                i = j
                continue
        i += 1
    return boards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="logs/*/miner.log")
    ap.add_argument("--planners", default="v1,v3,v4")
    ap.add_argument("--limit", type=int, default=0, help="cap boards (0=all)")
    # Realistic live inventory (bombs abundant, drills moderate).
    ap.add_argument("--bomb", type=int, default=600)
    ap.add_argument("--drill", type=int, default=60)
    ap.add_argument("--shovels", type=float, default=200.0)
    args = ap.parse_args()

    boards = []
    for p in glob.glob(str(REPO_ROOT / args.glob)):
        boards.extend(parse_boards(Path(p)))
    if args.limit:
        boards = boards[: args.limit]
    print(f"Replaying {len(boards)} real boards | inv bomb={args.bomb} "
          f"drill={args.drill} shovels={args.shovels}")
    print("=" * 108)
    print(f"{'planner':8s} {'boards':>7s} {'empty':>7s} {'empty%':>7s} "
          f"{'ms_mean':>8s} {'ms_p95':>8s} {'ms_p99':>8s} {'ms_max':>8s} "
          f"{'>250ms':>7s} {'>300ms':>7s}")
    print("-" * 108)

    for name in args.planners.split(","):
        fn = PLANNERS[name]
        empty = 0
        times = []
        over250 = 0
        over300 = 0
        for grid in boards:
            board = [row[:] for row in grid]
            t0 = time.perf_counter()
            try:
                plan = fn(board, shovels=args.shovels,
                          items={"drill": args.drill, "bomb": args.bomb})
            except Exception as e:  # noqa: BLE001 - record planner crashes
                print(f"{name}: CRASH on a board: {type(e).__name__}: {e}")
                continue
            dt = (time.perf_counter() - t0) * 1000.0
            times.append(dt)
            if dt > 250:
                over250 += 1
            if dt > 300:
                over300 += 1
            if not (plan.get("steps") or []):
                empty += 1
        if not times:
            print(f"{name:8s}  (no boards)")
            continue
        ts = sorted(times)
        p95 = ts[min(len(ts) - 1, int(len(ts) * 0.95))]
        p99 = ts[min(len(ts) - 1, int(len(ts) * 0.99))]
        print(f"{name:8s} {len(times):7d} {empty:7d} "
              f"{100*empty/len(times):6.2f}% {statistics.mean(times):8.3f} "
              f"{p95:8.3f} {p99:8.3f} {max(times):8.3f} {over250:7d} {over300:7d}")


if __name__ == "__main__":
    main()
