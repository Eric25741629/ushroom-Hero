from __future__ import annotations

from .types import Board

BOARD_SYMBOLS = {
    "empty": ".",
    "void": ".",
    "dug_pit": ".",
    "unreachable_empty": "_",
    "unreachable_void": "_",
    "dirt": "D",
    "unreachable_dirt": "d",
    "one_hit_rock": "1",
    "rock": "R",
    "unreachable_rock": "r",
    "reachable_pit": "*",
    "unreachable_pit": "X",
}


def format_board(board: Board) -> str:
    if not board:
        return "(empty board)"

    col_count = len(board[0])
    header = "   " + " ".join(str(index) for index in range(col_count))
    rows = [header]

    for row_index, row in enumerate(board):
        cells = " ".join(BOARD_SYMBOLS.get(cell, cell[:1]) for cell in row)
        rows.append(f"{row_index:2d} {cells}")
    return "\n".join(rows)
