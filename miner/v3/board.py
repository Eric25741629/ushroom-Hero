from __future__ import annotations

from typing import Iterable, List, Set, Tuple

from .types import Board, Coordinate

EMPTY_BASE = {"empty", "void", "dug_pit"}
PIT_LABELS = {"reachable_pit", "unreachable_pit"}
HARD_BASE = {"dirt", "rock", "one_hit_rock"}


def normalize_label(label: str) -> str:
    if label == "pit":
        return "reachable_pit"
    return label


def base_label(label: str) -> str:
    return normalize_label(label).replace("unreachable_", "")


def is_unreachable(label: str) -> bool:
    return normalize_label(label).startswith("unreachable_")


def is_air(label: str) -> bool:
    return base_label(label) in EMPTY_BASE


def is_pit(label: str) -> bool:
    return normalize_label(label) in PIT_LABELS


def is_reachable_air(label: str) -> bool:
    return is_air(label) and not is_unreachable(label)


def normalize_board(board: Board) -> Board:
    return [[normalize_label(cell) for cell in row] for row in board]


def canonicalize_in_place(board: Board) -> None:
    """Replace the legacy `void` alias with `empty` and `pit` with `reachable_pit`.

    Trusts CNN labels otherwise — does NOT propagate reachability.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    for r in range(rows):
        for c in range(cols):
            label = normalize_label(board[r][c])
            if label == "void":
                board[r][c] = "empty"
            elif label != board[r][c]:
                board[r][c] = label


def neighbors4(r: int, c: int, rows: int, cols: int) -> Iterable[Coordinate]:
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            yield (nr, nc)


def reachable_air_set(board: Board) -> Set[Coordinate]:
    rows = len(board)
    cols = len(board[0]) if board else 0
    return {
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if is_reachable_air(board[r][c])
    }


def is_frontier_diggable(board: Board, r: int, c: int) -> bool:
    """A cell is diggable when:
    - it is a confirmed-reachable hard cell (CNN label without unreachable_), or
    - it is `unreachable_*` but 4-adjacent to a confirmed-reachable empty.

    Interior `unreachable_*` pockets stay untouchable until something opens
    a path. CNN labels are the source of truth.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    label = normalize_label(board[r][c])
    if is_air(label):
        return False
    if not is_unreachable(label):
        return True
    for nr, nc in neighbors4(r, c, rows, cols):
        if is_reachable_air(board[nr][nc]):
            return True
    return False


def open_cell(board: Board, r: int, c: int) -> None:
    """Replace a hard cell with the appropriate post-dig label."""
    label = normalize_label(board[r][c])
    if label in PIT_LABELS:
        board[r][c] = "dug_pit"
    elif not is_air(label):
        board[r][c] = "empty"


def promote_after_dig(board: Board, dug: List[Coordinate]) -> None:
    """Update labels around just-emptied cells.

    Flood-fills through 4-adjacent `unreachable_empty` (an opened pocket
    really is empty) and one-pass strips `unreachable_` from adjacent hard
    cells (now diggable). Stops at hard-cell boundaries — pockets unrelated
    to the dug cells stay CNN-truth.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    queue: List[Coordinate] = list(dug)
    seen: Set[Coordinate] = set(dug)
    while queue:
        r, c = queue.pop()
        for nr, nc in neighbors4(r, c, rows, cols):
            if (nr, nc) in seen:
                continue
            label = normalize_label(board[nr][nc])
            if label == "unreachable_empty":
                board[nr][nc] = "empty"
                seen.add((nr, nc))
                queue.append((nr, nc))
            elif is_unreachable(label):
                board[nr][nc] = normalize_label(label.replace("unreachable_", "", 1))
                seen.add((nr, nc))


def count_pits(board: Board) -> Tuple[int, int]:
    """Return (reachable_pits, unreachable_pits) — both still on board."""
    reachable = 0
    unreachable = 0
    for row in board:
        for cell in row:
            label = normalize_label(cell)
            if label == "reachable_pit":
                reachable += 1
            elif label == "unreachable_pit":
                unreachable += 1
    return reachable, unreachable


def count_remaining_pits(board: Board) -> int:
    r, u = count_pits(board)
    return r + u


def floor7_open(board: Board) -> bool:
    if not board:
        return False
    last_row = len(board) - 1
    return any(is_reachable_air(cell) for cell in board[last_row])


def top_row_pit_count(board: Board) -> int:
    if not board:
        return 0
    return sum(1 for cell in board[0] if normalize_label(cell) in PIT_LABELS)
