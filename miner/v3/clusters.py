"""Identify 1×1 / 2×2 / 3×3 pit clusters on the visible board.

Used by the planner to evaluate whether a bomb/drill placement covers a
"complete" cluster — covering an N×N cluster in one shot is the high-leverage
move, and the planner should prefer such placements over scattering items.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Tuple

from .board import is_pit
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
