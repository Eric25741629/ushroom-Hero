"""Reconstruct TRUE mineral cluster sizes by tracking pits across time.

A single board snapshot is a 7-row viewport sliding over a tape. A 3x3 cluster
spans 3 tape rows and is collected incrementally as the viewport descends (the
top cells are dug before the bottom row scrolls into reach), so it never appears
as 9 intact pit cells in ANY single frame. Counting connected components within
isolated snapshots therefore UNDERCOUNTS large clusters.

This tool instead aligns consecutive snapshots by their scroll offset, builds a
per-session GLOBAL tape, marks every tape cell that was EVER a pit (`*`/`X`)
before being dug, and computes connected components over that reconstruction.
That recovers 3x3 (and larger) clusters that the per-frame view splits apart.

Usage:
    python tools/track_pits_replay.py
    python tools/track_pits_replay.py --glob "logs/emulator-5554/miner.log"
    python tools/track_pits_replay.py --debug-sessions 3
"""
from __future__ import annotations

import argparse
import glob
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# symbol -> coarse terrain class
NORM = {
    ".": "air", "_": "air",
    "D": "dirt", "d": "dirt",
    "R": "rock", "r": "rock",
    "*": "pit", "X": "pit",
}
PIT_CHARS = {"*", "X"}

SESSION_START_RE = re.compile(r"開始挖礦")
BOARD_HEADER_RE = re.compile(r"^\s*0 1 2 3 4 5\s*$")
BOARD_ROW_RE = re.compile(r"^\s*\d+\s+([.\_DdRr*X ]+)$")


def parse_events(path: Path):
    """Yield ('session', None) and ('board', grid) events in file order."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if SESSION_START_RE.search(line):
            events.append(("session", None))
        content = line.split("] ", 1)[-1] if "] " in line else line
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
                    grid.append(cells)
                j += 1
            if len(grid) == 7:
                events.append(("board", grid))
                i = j
                continue
        i += 1
    return events


def best_scroll(prev, cur, max_s=6):
    """Best downward scroll s (0..max_s) aligning cur under prev.

    Compares only solid (non-air) structure on the overlap. Returns
    (s, ratio). A dug cell (now air) is ignored, not counted as mismatch.
    """
    best = (0, -1.0)
    for s in range(0, max_s + 1):
        comparable = 0
        matches = 0
        for i in range(0, 7 - s):
            for j in range(6):
                a = NORM[prev[s + i][j]]
                b = NORM[cur[i][j]]
                if a == "air":
                    continue  # prev already open here -> no structural signal
                if b == "air":
                    # cur dug/opened this cell: compatible, weak signal
                    continue
                comparable += 1
                if a == b:
                    matches += 1
        ratio = (matches / comparable) if comparable >= 6 else -1.0
        # Prefer higher ratio; tie -> smaller scroll (scroll is usually 0/1).
        if ratio > best[1] + 1e-9:
            best = (s, ratio)
    return best


def components(pit_set):
    """4-connected components over a set of (row, col)."""
    seen = set()
    comps = []
    for cell in pit_set:
        if cell in seen:
            continue
        stack = [cell]
        seen.add(cell)
        comp = []
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (r + dr, c + dc)
                if nb in pit_set and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    return comps


def reconstruct_session(boards, ratio_floor=0.55):
    """Track pits across a session's snapshots into a global tape.

    Returns a list of pit components (each a list of (global_row, col)),
    splitting into a new segment whenever alignment confidence drops (a
    re-scan / discontinuity), so we never merge unrelated tapes.
    """
    segments = []  # each: (pit_set, viewport_max_row)
    cur_pits = set()
    cur_max_row = 6  # first viewport spans global rows 0..6
    offset = 0
    prev = None
    for grid in boards:
        if prev is None:
            offset = 0
        else:
            s, ratio = best_scroll(prev, grid)
            if ratio < ratio_floor:
                # discontinuity: close current segment, start fresh
                if cur_pits:
                    segments.append((cur_pits, cur_max_row))
                cur_pits = set()
                cur_max_row = 6
                offset = 0
            else:
                offset += s
        cur_max_row = max(cur_max_row, offset + 6)
        for i in range(7):
            for j in range(6):
                if grid[i][j] in PIT_CHARS:
                    cur_pits.add((offset + i, j))
        prev = grid
    if cur_pits:
        segments.append((cur_pits, cur_max_row))
    comps = []
    area = 0
    for seg, max_row in segments:
        comps.extend(components(seg))
        area += (max_row + 1) * 6  # full viewport extent, not just pit extent
    return comps, area


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="logs/*/miner.log")
    ap.add_argument("--debug-sessions", type=int, default=0)
    args = ap.parse_args()

    paths = [Path(p) for p in glob.glob(str(REPO_ROOT / args.glob))]
    size_dist = Counter()
    bbox_dist = Counter()
    raw_pit_cells = 0          # pit cells summed over all raw snapshots
    tracked_pit_cells = 0      # distinct global pit positions reconstructed
    tape_area = 0              # reconstructed global tape cells (for density)
    sessions_total = 0

    for path in paths:
        events = parse_events(path)
        # split events into sessions on 'session' markers
        sessions = []
        cur = []
        for kind, payload in events:
            if kind == "session":
                if cur:
                    sessions.append(cur)
                cur = []
            else:
                cur.append(payload)
        if cur:
            sessions.append(cur)

        for si, boards in enumerate(sessions):
            if len(boards) < 1:
                continue
            sessions_total += 1
            for grid in boards:
                raw_pit_cells += sum(
                    1 for row in grid for ch in row if ch in PIT_CHARS
                )
            comps, area = reconstruct_session(boards)
            tape_area += area
            for comp in comps:
                tracked_pit_cells += len(comp)
                size_dist[len(comp)] += 1
                rs = [p[0] for p in comp]
                cs = [p[1] for p in comp]
                bbox_dist[(max(rs) - min(rs) + 1, max(cs) - min(cs) + 1)] += 1
            if args.debug_sessions and sessions_total <= args.debug_sessions:
                print(f"[{path.name} session {si}] {len(boards)} frames -> "
                      f"{len(comps)} pit clusters, sizes "
                      f"{sorted((len(c) for c in comps), reverse=True)[:12]}")

    total = sum(size_dist.values())
    print("=" * 60)
    print(f"Sessions: {sessions_total} | reconstructed pit clusters: {total}")
    print(f"raw pit-cell observations (sum over frames): {raw_pit_cells}")
    print(f"distinct tracked pit positions: {tracked_pit_cells}")
    print(f"reconstructed tape area (cells): {tape_area}")
    print(f"TRUE spawn pit density (tracked / tape area): "
          f"{100*tracked_pit_cells/tape_area if tape_area else 0:.2f}%")
    # cluster mix collapsed to square classes (size-8 = 3x3 caught mid-collect)
    c1 = size_dist.get(1, 0)
    c4 = size_dist.get(2, 0) + size_dist.get(3, 0) + size_dist.get(4, 0)
    c9 = sum(c for s, c in size_dist.items() if s >= 6)
    sq_total = c1 + c4 + c9
    if sq_total:
        print(f"square-class mix: 1x1={100*c1/sq_total:.0f}%  "
              f"2x2={100*c4/sq_total:.0f}%  3x3={100*c9/sq_total:.0f}%")
    print("=" * 60)
    print("\n--- TRUE cluster size distribution (time-tracked) ---")
    for size in sorted(size_dist):
        cnt = size_dist[size]
        print(f"  size {size:2d}: {cnt:5d}  ({100*cnt/total if total else 0:5.1f}%)")
    print(f"\n  clusters >= 9 cells (3x3+): "
          f"{sum(c for s, c in size_dist.items() if s >= 9)}")
    print(f"  max cluster size: {max(size_dist) if size_dist else 0}")

    print("\n--- bounding-box shapes (top 15) ---")
    for shape in sorted(bbox_dist, key=lambda s: -bbox_dist[s])[:15]:
        cnt = bbox_dist[shape]
        print(f"    {shape[0]}x{shape[1]}: {cnt:5d}  "
              f"({100*cnt/total if total else 0:5.1f}%)")
    # 3x3+ bounding boxes specifically
    big = sum(c for (h, w), c in bbox_dist.items() if h >= 3 and w >= 3)
    print(f"\n  bounding boxes with both dims >= 3 (3x3 capable): {big} "
          f"({100*big/total if total else 0:.1f}%)")


if __name__ == "__main__":
    main()
