"""Action enumeration and simulation for v3.

Same physics as v1/v2 (drill = column-down + bottom-row left/right;
bomb = 3×3 + cross extension). Differences from v2:

- `dig` is allowed on any *frontier* cell — including `unreachable_*` cells
  that are 4-adjacent to a confirmed-reachable empty.
- `apply_*` uses local `promote_after_dig` (no plan-start BFS rebuild).
- Item placement only considers *confirmed reachable* empties, never an
  `unreachable_empty` pocket.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Tuple

from miner.core.mechanics import get_drill_affected_cells

from .board import (
    PIT_LABELS,
    base_label,
    is_air,
    is_frontier_diggable,
    is_reachable_air,
    normalize_label,
    open_cell,
    promote_after_dig,
)
from .types import Board, Coordinate


def dig_cost(label: str) -> int:
    label = normalize_label(label)
    base = base_label(label)
    if label in PIT_LABELS:
        return 1
    if base in {"dirt", "one_hit_rock"}:
        return 1
    if base == "rock":
        return 2
    return 0


def apply_dig(board: Board, pos: Coordinate) -> float:
    r, c = pos
    label = normalize_label(board[r][c])
    cost = float(dig_cost(label))
    open_cell(board, r, c)
    promote_after_dig(board, [(r, c)])
    return cost


def get_drill_targets(r: int, c: int, rows: int, cols: int) -> List[Coordinate]:
    return [t for t in get_drill_affected_cells(r, c, rows, cols) if 0 <= t[0] < rows and 0 <= t[1] < cols]


def get_bomb_targets(r: int, c: int, rows: int, cols: int) -> Tuple[List[Coordinate], int]:
    raw: List[Coordinate] = []
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            raw.append((r + dr, c + dc))
    for dr, dc in ((0, 2), (0, -2), (2, 0), (-2, 0)):
        raw.append((r + dr, c + dc))
    visible: List[Coordinate] = []
    offscreen_bottom = 0
    for nr, nc in raw:
        if 0 <= nr < rows and 0 <= nc < cols:
            visible.append((nr, nc))
        elif nr >= rows:
            offscreen_bottom += 1
    return visible, offscreen_bottom


def apply_drill(board: Board, pos: Coordinate) -> Tuple[float, List[Coordinate]]:
    rows = len(board)
    cols = len(board[0]) if board else 0
    affected = get_drill_targets(pos[0], pos[1], rows, cols)
    for r, c in affected:
        open_cell(board, r, c)
    promote_after_dig(board, affected)
    return 3.0, affected


def apply_bomb(board: Board, pos: Coordinate) -> Tuple[float, List[Coordinate], int]:
    rows = len(board)
    cols = len(board[0]) if board else 0
    affected, offscreen_bottom = get_bomb_targets(pos[0], pos[1], rows, cols)
    for r, c in affected:
        open_cell(board, r, c)
    promote_after_dig(board, affected)
    return 3.0, affected, offscreen_bottom


def enumerate_dig_actions(board: Board) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    rows = len(board)
    cols = len(board[0]) if board else 0
    for r in range(rows):
        for c in range(cols):
            if is_frontier_diggable(board, r, c):
                actions.append({"type": "dig", "pos": (r, c), "action": "dig"})
    return actions


def enumerate_item_actions(
    board: Board,
    items: Dict[str, int],
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    rows = len(board)
    cols = len(board[0]) if board else 0
    placements: List[Coordinate] = [
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if is_reachable_air(board[r][c])
    ]
    if items.get("drill", 0) > 0:
        for pos in placements:
            actions.append({"type": "use", "item": "drill", "pos": pos, "action": "use_drill"})
    if items.get("bomb", 0) > 0:
        for pos in placements:
            actions.append({"type": "use", "item": "bomb", "pos": pos, "action": "use_bomb"})
    return actions


def affected_pit_cells(board: Board, action: Dict[str, Any]) -> FrozenSet[Coordinate]:
    rows = len(board)
    cols = len(board[0]) if board else 0
    if action["type"] == "dig":
        r, c = action["pos"]
        if normalize_label(board[r][c]) in PIT_LABELS:
            return frozenset({(r, c)})
        return frozenset()
    if action["item"] == "drill":
        cells = get_drill_targets(action["pos"][0], action["pos"][1], rows, cols)
    else:
        cells, _ = get_bomb_targets(action["pos"][0], action["pos"][1], rows, cols)
    return frozenset(
        (r, c) for r, c in cells if normalize_label(board[r][c]) in PIT_LABELS
    )
