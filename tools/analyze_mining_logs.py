"""Empirical analysis of real-game mining logs.

Parses every `logs/<device>/miner.log`, extracting board snapshots
(rendered by `get_visual_board`) and the per-plan stats lines, to measure
the REAL distribution of cell types, pit density, plan timing, item
accumulation, and stuck/empty-plan incidence.

This grounds the simulator's `_extend_tape` appearance rates against what the
live game actually produces, so `tools/mining_sim_eval.py` benchmarks planners
on a realistic board distribution rather than an invented one.

Board legend (from miner.mining_service.get_visual_board):
    .  empty / void / dug_pit   (reachable air)
    _  unreachable_empty/void   (air pocket, not yet reachable)
    D  dirt (reachable)         d  unreachable_dirt
    R  rock (reachable)         r  unreachable_rock
    *  reachable_pit            X  unreachable_pit   <- MINERAL / ore target

Usage:
    python tools/analyze_mining_logs.py
    python tools/analyze_mining_logs.py --glob "logs/emulator-5554/miner.log"
"""
from __future__ import annotations

import argparse
import glob
import re
import statistics
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LEGEND = {
    ".": "air_reachable",
    "_": "air_unreachable",
    "D": "dirt_reachable",
    "d": "dirt_unreachable",
    "R": "rock_reachable",
    "r": "rock_unreachable",
    "*": "pit_reachable",
    "X": "pit_unreachable",
}
PIT_CHARS = {"*", "X"}

BOARD_HEADER_RE = re.compile(r"^\s*0 1 2 3 4 5\s*$")
BOARD_ROW_RE = re.compile(r"^\s*\d+\s+([.\_DdRr*X ]+)$")
CALC_RE = re.compile(
    r"planner=(\w+)\s+calc_ms=([\d.]+).*?nodes=(\d+)\s+steps=(\d+)\s+strategy=(\w+)"
)
STATS_RE = re.compile(r"planner=\w+ stats=(\{.*\})")
ITEM_RE = re.compile(r"items_available=\{'drill': (\d+), 'bomb': (\d+)\}")
PLANNER_VER_RE = re.compile(r"planner_version=(\w+)")


def parse_log(path: Path):
    """Yield structured records from one miner.log."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    boards = []  # list of 7x6 char grids
    calc_rows = []  # (planner, calc_ms, nodes, steps, strategy)
    items = []  # (drill, bomb)
    planner_versions = Counter()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        m = PLANNER_VER_RE.search(line)
        if m:
            planner_versions[m.group(1)] += 1

        m = ITEM_RE.search(line)
        if m:
            items.append((int(m.group(1)), int(m.group(2))))

        m = CALC_RE.search(line)
        if m:
            calc_rows.append(
                (m.group(1), float(m.group(2)), int(m.group(3)),
                 int(m.group(4)), m.group(5))
            )

        # Board block: a header row "0 1 2 3 4 5" then up to 7 rows.
        # The header itself is on a line ending with the column indices;
        # rows look like " 0 D . D d d d".
        content = line.split("] ", 1)[-1] if "] " in line else line
        if BOARD_HEADER_RE.match(content.strip()):
            grid = []
            j = i + 1
            while j < n and len(grid) < 7:
                row_content = lines[j]
                row_content = row_content.split("] ", 1)[-1] if "] " in row_content else row_content
                rm = BOARD_ROW_RE.match(row_content)
                if not rm:
                    break
                cells = rm.group(1).split()
                if len(cells) == 6:
                    grid.append(cells)
                j += 1
            if len(grid) == 7:
                boards.append(grid)
                i = j
                continue
        i += 1

    return {
        "boards": boards,
        "calc_rows": calc_rows,
        "items": items,
        "planner_versions": planner_versions,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="logs/*/miner.log")
    args = ap.parse_args()

    paths = [Path(p) for p in glob.glob(str(REPO_ROOT / args.glob))]
    if not paths:
        print(f"No logs matched {args.glob}")
        return

    all_cells = Counter()
    boards_total = 0
    boards_with_pit = 0
    pit_counts_per_board = []
    reachable_air_per_board = []
    component_sizes = Counter()  # connected-component size among pit cells
    component_shapes = Counter()  # bounding-box (h x w) of each pit component
    calc_all = []
    calc_by_planner = {}
    strategy_counter = Counter()
    nodes_all = []
    items_all = []
    planner_versions = Counter()

    for path in paths:
        rec = parse_log(path)
        planner_versions.update(rec["planner_versions"])
        for grid in rec["boards"]:
            boards_total += 1
            pit_n = 0
            air_n = 0
            for row in grid:
                for ch in row:
                    all_cells[ch] += 1
                    if ch in PIT_CHARS:
                        pit_n += 1
                    if ch == ".":
                        air_n += 1
            pit_counts_per_board.append(pit_n)
            reachable_air_per_board.append(air_n)
            if pit_n:
                boards_with_pit += 1
            # Connected components of pit cells (4-adjacency) within this snapshot.
            pit_set = {
                (r, c)
                for r in range(len(grid))
                for c in range(len(grid[r]))
                if grid[r][c] in PIT_CHARS
            }
            seen_pit = set()
            for cell in pit_set:
                if cell in seen_pit:
                    continue
                stack = [cell]
                seen_pit.add(cell)
                comp = []
                while stack:
                    cr, cc = stack.pop()
                    comp.append((cr, cc))
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nb = (cr + dr, cc + dc)
                        if nb in pit_set and nb not in seen_pit:
                            seen_pit.add(nb)
                            stack.append(nb)
                component_sizes[len(comp)] += 1
                rs = [p[0] for p in comp]
                cs = [p[1] for p in comp]
                h = max(rs) - min(rs) + 1
                w = max(cs) - min(cs) + 1
                component_shapes[(h, w)] += 1
        for (planner, calc_ms, nodes, steps, strategy) in rec["calc_rows"]:
            calc_all.append(calc_ms)
            calc_by_planner.setdefault(planner, []).append(calc_ms)
            strategy_counter[strategy] += 1
            nodes_all.append(nodes)
        items_all.extend(rec["items"])

    total_cells = sum(all_cells.values())
    print("=" * 64)
    print(f"Parsed {len(paths)} logs | {boards_total} board snapshots | "
          f"{total_cells} cells")
    print(f"planner_versions seen: {dict(planner_versions)}")
    print("=" * 64)

    print("\n--- CELL TYPE DISTRIBUTION (all snapshots) ---")
    grouped = Counter()
    for ch, cnt in all_cells.items():
        name = LEGEND.get(ch, f"?{ch!r}")
        grouped[name] += cnt
    for name in ["air_reachable", "air_unreachable", "dirt_reachable",
                 "dirt_unreachable", "rock_reachable", "rock_unreachable",
                 "pit_reachable", "pit_unreachable"]:
        cnt = grouped.get(name, 0)
        pct = 100 * cnt / total_cells if total_cells else 0
        print(f"  {name:20s} {cnt:7d}  {pct:6.2f}%")

    # Merge reachable+unreachable for the "terrain mix" view.
    print("\n--- TERRAIN MIX (reachable + unreachable merged) ---")
    air = grouped["air_reachable"] + grouped["air_unreachable"]
    dirt = grouped["dirt_reachable"] + grouped["dirt_unreachable"]
    rock = grouped["rock_reachable"] + grouped["rock_unreachable"]
    pit = grouped["pit_reachable"] + grouped["pit_unreachable"]
    for name, cnt in [("air", air), ("dirt", dirt), ("rock", rock), ("pit", pit)]:
        pct = 100 * cnt / total_cells if total_cells else 0
        print(f"  {name:6s} {cnt:7d}  {pct:6.2f}%")

    # Solid-only mix (exclude air) -- this is what _roll_cell models.
    solid = dirt + rock + pit
    print("\n--- SOLID-CELL MIX (air excluded; matches _roll_cell output) ---")
    for name, cnt in [("dirt", dirt), ("rock", rock), ("pit", pit)]:
        pct = 100 * cnt / solid if solid else 0
        print(f"  {name:6s} {cnt:7d}  {pct:6.2f}%")

    print("\n--- PIT / MINERAL DENSITY ---")
    print(f"  boards with >=1 pit: {boards_with_pit}/{boards_total} "
          f"({100*boards_with_pit/boards_total if boards_total else 0:.2f}%)")
    if pit_counts_per_board:
        print(f"  pits per board: mean={statistics.mean(pit_counts_per_board):.3f} "
              f"max={max(pit_counts_per_board)}")
    print(f"  pit cells as fraction of all cells: "
          f"{100*pit/total_cells if total_cells else 0:.3f}%")

    print("\n--- PIT CLUSTER SHAPES (4-connected components per snapshot) ---")
    total_comp = sum(component_sizes.values())
    print(f"  total pit components observed: {total_comp}")
    for size in sorted(component_sizes):
        cnt = component_sizes[size]
        print(f"    size {size:2d} cells: {cnt:5d}  "
              f"({100*cnt/total_comp if total_comp else 0:5.1f}%)")
    print("  bounding-box shapes (h x w):")
    for shape in sorted(component_shapes, key=lambda s: -component_shapes[s]):
        cnt = component_shapes[shape]
        print(f"    {shape[0]}x{shape[1]}: {cnt:5d}  "
              f"({100*cnt/total_comp if total_comp else 0:5.1f}%)")

    print("\n--- PLAN TIMING (calc_ms, from live logs) ---")
    if calc_all:
        calc_sorted = sorted(calc_all)
        p50 = calc_sorted[len(calc_sorted)//2]
        p95 = calc_sorted[int(len(calc_sorted)*0.95)]
        p99 = calc_sorted[int(len(calc_sorted)*0.99)]
        print(f"  n={len(calc_all)} mean={statistics.mean(calc_all):.3f}ms "
              f"p50={p50:.3f} p95={p95:.3f} p99={p99:.3f} max={max(calc_all):.3f}")
        over_300 = sum(1 for x in calc_all if x > 300)
        print(f"  calls > 300ms: {over_300} "
              f"({100*over_300/len(calc_all):.3f}%)")
        for planner, vals in sorted(calc_by_planner.items()):
            print(f"    {planner}: n={len(vals)} mean={statistics.mean(vals):.3f}ms "
                  f"max={max(vals):.3f}ms")
    print(f"  strategy mix: {dict(strategy_counter)}")
    if nodes_all:
        print(f"  nodes explored: mean={statistics.mean(nodes_all):.1f} "
              f"max={max(nodes_all)}")

    print("\n--- ITEM ACCUMULATION (drill, bomb over session) ---")
    if items_all:
        drills = [d for d, b in items_all]
        bombs = [b for d, b in items_all]
        print(f"  drill: min={min(drills)} max={max(drills)} "
              f"mean={statistics.mean(drills):.0f}")
        print(f"  bomb:  min={min(bombs)} max={max(bombs)} "
              f"mean={statistics.mean(bombs):.0f}")


if __name__ == "__main__":
    main()
