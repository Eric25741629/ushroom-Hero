"""Identify 1×1 / 2×2 / 3×3 pit clusters on the visible board.

Used by the planner to evaluate whether a bomb/drill placement covers a
"complete" cluster — covering an N×N cluster in one shot is the high-leverage
move, and the planner should prefer such placements over scattering items.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Set, Tuple

from .board import is_air, is_pit
from .types import Board, Coordinate


@dataclass(frozen=True)
class PitCluster:
    size: int  # 1, 2, or 3 (square edge length)
    cells: FrozenSet[Coordinate]
    top_left: Coordinate

    def overlaps(self, target_cells: FrozenSet[Coordinate]) -> int:
        return len(self.cells & target_cells)

    def fully_covered_by(self, target_cells: FrozenSet[Coordinate]) -> bool:
        return self.cells.issubset(target_cells)


def find_clusters(board: Board) -> List[PitCluster]:
    """Find all maximal N×N pit-only blocks for N in {3, 2}.

    1×1 "clusters" (single isolated pits) are NOT enumerated here — they get
    counted naturally by the per-cell pit count. This avoids combinatorial
    blow-up while keeping the high-value cluster signal.

    Larger clusters are listed first so the planner can prefer covering them.
    """
    if not board:
        return []
    rows = len(board)
    cols = len(board[0])
    found: List[PitCluster] = []
    for size in (3, 2):
        if size > min(rows, cols):
            continue
        for r in range(rows - size + 1):
            for c in range(cols - size + 1):
                cells = [(r + dr, c + dc) for dr in range(size) for dc in range(size)]
                if all(is_pit(board[rr][cc]) for rr, cc in cells):
                    found.append(
                        PitCluster(
                            size=size,
                            cells=frozenset(cells),
                            top_left=(r, c),
                        )
                    )
    return found


def cluster_value(size: int) -> float:
    """Heuristic value of fully covering an N×N cluster in one shot.

    Square-superlinear so a 3×3 single-shot pays much better than nine 1×1s.
    """
    if size <= 1:
        return 1.0
    return float(size * size) * 1.4


def find_prospective_pits(board: Board) -> Set[Coordinate]:
    """Return in-viewport unexcavated cells that are certain to be pits.

    A horizontal run of N >= 2 adjacent pits in a row is guaranteed to be the
    top portion of an N×N square cluster (domain rule: isolated adjacent pits
    do not occur). The unexcavated cells in the rows directly below (within
    the viewport) are prospective pits — bombs/drills can reach them and the
    full N×N cluster bonus becomes plannable.

    Only returns cells within the viewport (row index < len(board)).
    Out-of-viewport prospective pits cannot be hit and are handled separately
    by _incomplete_bottom_squares in the v5 planner.
    """
    if not board:
        return set()
    rows = len(board)
    cols = len(board[0])
    prospective: Set[Coordinate] = set()

    for r in range(rows):
        c = 0
        while c < cols:
            if not is_pit(board[r][c]):
                c += 1
                continue
            start = c
            while c < cols and is_pit(board[r][c]):
                c += 1
            width = c - start
            if width < 2:
                continue
            # Only process from the topmost row of the expected square.
            # If the row above is all-pits at these cols, we are not at the top.
            # Domain guarantee: clusters are 1×1/2×2/3×3 squares with a 1-cell
            # isolation ring, so vertically-adjacent pits ALWAYS belong to the
            # same cluster. Hence "row above is all-pit at my columns" is exactly
            # equivalent to "I am not the top row" — a wider unrelated run above
            # cannot occur (it would require two clusters with no gap).
            if r > 0 and all(
                is_pit(board[r - 1][cc]) for cc in range(start, start + width)
            ):
                continue
            # Count consecutive all-pit rows already confirmed below the run.
            extra_confirmed = 0
            for rb in range(r + 1, r + width):
                if rb >= rows:
                    break
                if all(is_pit(board[rb][cc]) for cc in range(start, start + width)):
                    extra_confirmed += 1
                else:
                    break
            total_confirmed = 1 + extra_confirmed
            if total_confirmed >= width:
                continue  # complete cluster — find_clusters handles it
            # Collect prospective cells from the remaining unconfirmed rows.
            run_valid = True
            candidates: Set[Coordinate] = set()
            for rb in range(r + total_confirmed, r + width):
                if rb >= rows:
                    break  # out of viewport — bombs cannot reach
                for cc in range(start, start + width):
                    cell = board[rb][cc]
                    if is_air(cell):
                        run_valid = False
                        break
                    if not is_pit(cell):
                        candidates.add((rb, cc))
                if not run_valid:
                    break
            if run_valid:
                prospective.update(candidates)

    return prospective
