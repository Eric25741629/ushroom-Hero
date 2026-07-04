"""Pure, deterministic solver for 煩惱消 (act_type 224) — the 左右消除
(left-right crush) minigame skinned as ActivityNewYear24Match.

LIVE-confirmed game model (7fe98fc6, 2026-06-14; full recon in
docs/protocol/FANNAOXIAO_RECON.md):

  - Board = a stack of rows over WIDTH=9 columns. The BOTTOM row is
    ``rows[-1]`` (closest to the player); new rows spawn at the bottom and
    push the stack up. Game over when the row count reaches MAX_ROWS (12).
  - A block occupies a contiguous span of columns ``[pos, pos+length)`` and
    has a colour class (1..6). Width (``length``) is 1..4.
  - A MOVE drags ONE block horizontally inside its own row to a target
    column. The block may only slide through EMPTY cells — it cannot jump
    over another block. (Mirrors ``getGridMoveRange`` / ``moveGrid``.)
  - After any move the board SETTLES: blocks fall from upper rows into empty
    cells in the rows below (``checkGridDrop``), then any fully-filled row
    CLEARS and scores while fully-empty rows are removed and the stack
    shifts down (``checkCompleteRow``). Settling repeats until stable.

This module only models the deterministic part the client computes locally.
The next-row spawn (random) is NOT modelled here — the live driver re-reads
the real board after each move, so lookahead never needs the RNG.

The block ``index`` -> (colour, width) mapping comes from
``configLeft_right_crush_block`` (live-dumped):
    index 1..6   -> width 1, colours 1..6
    index 7..12  -> width 2, colours 1..6
    index 13..18 -> width 3, colours 1..6
    index 19..24 -> width 4, colours 1..6
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

WIDTH = 9           # columns per row (game const y=9)
MAX_ROWS = 12       # game-over threshold (game const R=12)
SCORE_PER_ROW = 1   # configLeft_right_crush level-1 score; relative ordering only

# A move is (row_index, from_pos, to_pos). row_index indexes ``Board.rows``.
Move = Tuple[int, int, int]


@dataclass(frozen=True)
class Block:
    """A coloured block spanning columns [pos, pos+length)."""

    pos: int
    length: int
    color: int

    @property
    def end(self) -> int:
        return self.pos + self.length


@dataclass(frozen=True)
class Board:
    """Immutable board: an ordered tuple of rows, each row a tuple of Blocks.

    rows[0] is the TOP row, rows[-1] is the BOTTOM row.
    """

    rows: Tuple[Tuple[Block, ...], ...]


# ── block index helpers ────────────────────────────────────────────────


def block_from_index(pos: int, index: int) -> Block:
    """Build a Block from a save-string ``pos_index`` pair.

    width = (index-1)//6 + 1 ; color = (index-1)%6 + 1.
    """
    i = int(index)
    width = (i - 1) // 6 + 1
    color = (i - 1) % 6 + 1
    return Block(pos=int(pos), length=width, color=color)


def index_from_block(block: Block) -> int:
    """Inverse of block_from_index (used for save serialisation/tests)."""
    return (block.length - 1) * 6 + (block.color - 1) + 1


# ── grid occupancy ─────────────────────────────────────────────────────


def grid_info(row: Sequence[Block]) -> List[int]:
    """0/1 occupancy vector of length WIDTH for a row."""
    cells = [0] * WIDTH
    for b in row:
        for c in range(b.pos, min(b.end, WIDTH)):
            cells[c] = 1
    return cells


def row_filled(row: Sequence[Block]) -> bool:
    return all(grid_info(row))


def row_empty(row: Sequence[Block]) -> bool:
    return not any(grid_info(row))


# ── legal move generation ──────────────────────────────────────────────


def _move_range(row: Sequence[Block], block: Block) -> Tuple[int, int]:
    """Leftmost/rightmost ``pos`` the block can slide to, sliding only
    through empty cells (mirrors N.getGridMoveRange).

    Returns (left, right) inclusive bounds on the block's new ``pos``.
    """
    cells = grid_info(row)
    # clear this block's own cells so they count as passable
    for c in range(block.pos, block.end):
        cells[c] = 0

    left = block.pos
    c = block.pos - 1
    while c >= 0 and cells[c] == 0:
        left = c
        c -= 1

    right = block.pos
    c = block.pos + 1
    while c + block.length - 1 < WIDTH:
        # the block can occupy [c, c+length) only if all those cells empty
        if all(cells[k] == 0 for k in range(c, c + block.length)):
            right = c
            c += 1
        else:
            break
    return left, right


def legal_moves(board: Board) -> List[Move]:
    """All legal (row, from_pos, to_pos) moves, excluding no-ops.

    Deterministic ordering: by row index, then block left-to-right, then
    target column ascending.
    """
    out: List[Move] = []
    for ri, row in enumerate(board.rows):
        for block in row:
            left, right = _move_range(row, block)
            for tp in range(left, right + 1):
                if tp != block.pos:
                    out.append((ri, block.pos, tp))
    return out


# ── move application + settle (drop + clear) ───────────────────────────


def _move_block_in_row(row: Tuple[Block, ...], from_pos: int, to_pos: int) -> Tuple[Block, ...]:
    new_row = []
    moved = False
    for b in row:
        if not moved and b.pos == from_pos:
            new_row.append(replace(b, pos=to_pos))
            moved = True
        else:
            new_row.append(b)
    return tuple(new_row)


def _can_place(cells: List[int], pos: int, length: int) -> bool:
    if pos < 0 or pos + length > WIDTH:
        return False
    return all(cells[c] == 0 for c in range(pos, pos + length))


def settle_drops(board: Board) -> Board:
    """Let blocks fall from upper rows into empty cells of the rows below,
    repeatedly until stable. Mirrors the engine's checkGridDrop cascade.

    Gravity is straight down (same columns). A block falls into the row
    DIRECTLY below it only if that row has room for its [pos, pos+length)
    span — it cannot pass THROUGH a row that is occupied at its columns.
    Falling repeats one row at a time until no block can drop further.
    """
    rows = [list(r) for r in board.rows]
    changed = True
    while changed:
        changed = False
        # iterate from the second-bottom row upward; drop each block one row
        for upper_i in range(len(rows) - 2, -1, -1):
            below_i = upper_i + 1
            for b in list(rows[upper_i]):
                cells = grid_info(rows[below_i])
                if _can_place(cells, b.pos, b.length):
                    rows[upper_i].remove(b)
                    rows[below_i].append(b)
                    changed = True
    return Board(rows=tuple(tuple(r) for r in rows))


def _clear_filled_rows(board: Board) -> Tuple[Board, int]:
    """Remove fully-filled rows (scoring) and fully-empty rows. Returns the
    new board and the number of rows cleared (for scoring)."""
    cleared = 0
    kept: List[Tuple[Block, ...]] = []
    for row in board.rows:
        if row_filled(row):
            cleared += 1
            continue
        if row_empty(row):
            continue
        kept.append(row)
    return Board(rows=tuple(kept)), cleared


def _settle(board: Board) -> Tuple[Board, int]:
    """Full settle loop: drop, clear, repeat until stable. Returns
    (board, rows_cleared_total)."""
    total_cleared = 0
    while True:
        dropped = settle_drops(board)
        cleared_board, cleared = _clear_filled_rows(dropped)
        total_cleared += cleared
        if cleared == 0:
            return cleared_board, total_cleared
        board = cleared_board


def apply_move(board: Board, move: Move) -> Tuple[Board, int]:
    """Apply a move and settle the board. Returns (new_board, score_gained).

    Score is ``rows_cleared * SCORE_PER_ROW`` (relative ordering only — the
    live game's exact per-level score is read from the real client).
    """
    ri, from_pos, to_pos = move
    rows = list(board.rows)
    rows[ri] = _move_block_in_row(rows[ri], from_pos, to_pos)
    moved_board = Board(rows=tuple(rows))
    settled, cleared = _settle(moved_board)
    return settled, cleared * SCORE_PER_ROW


def next_row_to_blocks(next_row: Sequence[Sequence[int]]) -> Tuple[Block, ...]:
    """Convert the engine's ``nextRowGridInfo`` (the green-bar preview, a list
    of ``[pos, index]`` pairs) into a row of Blocks."""
    return tuple(block_from_index(p, idx) for (p, idx) in next_row)


def spawn_next_row(board: Board, next_row: Sequence[Sequence[int]]) -> Tuple[Board, int]:
    """Append the previewed next row at the BOTTOM (engine ``createRow``), then
    settle (drop + clear). Returns (new_board, score_gained).

    Mirrors the engine: a relocating move sets ``needUpdate`` and the spawn of
    the *previewed* row happens after the move's own settle. Because the
    preview is shown ON SCREEN before you move, the solver can look one spawn
    ahead deterministically (no RNG needed for the immediate next row).
    """
    spawned = Board(rows=board.rows + (next_row_to_blocks(next_row),))
    return _settle(spawned)


# ── game over ──────────────────────────────────────────────────────────


def is_game_over(board: Board) -> bool:
    return len(board.rows) >= MAX_ROWS


# ── heuristic scoring of a resulting board ─────────────────────────────


def _board_potential(board: Board) -> float:
    """Heuristic: higher is better. Rewards consolidating blocks toward the
    bottom and packing rows tightly so they can complete.

    Components:
      - fewer rows is better (closer to the bottom, further from game over)
      - rows that are nearly full are valuable (close to a clear)
      - flat, well-packed columns beat scattered ones
    """
    if not board.rows:
        return 1000.0

    n_rows = len(board.rows)
    # row-count penalty: each extra row toward MAX_ROWS hurts a lot
    score = -8.0 * n_rows

    for row in board.rows:
        filled = sum(grid_info(row))
        # quadratic reward for fullness -> strongly favours near-complete rows
        score += (filled * filled) / WIDTH
        # mild bonus for fewer, wider blocks (easier to pack)
        score -= 0.25 * len(row)

    return score


# ── move selection (1-ply greedy + heuristic, deterministic) ───────────


def choose_move(
    board: Board,
    next_row: Optional[Sequence[Sequence[int]]] = None,
) -> Optional[Move]:
    """Pick the best legal move. Deterministic.

    The green-bar ``nextRowGridInfo`` preview makes the immediate next spawn
    KNOWN (not RNG), so when ``next_row`` is supplied we look one spawn ahead:

      score(move) = immediate clears
                  + clears caused by the previewed row spawning afterwards
        tie-break = potential of the board AFTER the previewed row spawns
                    (so we set up the incoming row to land well), then the
                    pre-spawn potential, then move ordering.

    Without ``next_row`` it degrades to the robust 1-ply: prefer immediate
    clears, then maximise the settled board's packing potential.

    Returns None if there are no legal moves.
    """
    moves = legal_moves(board)
    if not moves:
        return None

    best: Optional[Move] = None
    best_key: Optional[tuple] = None
    for mv in moves:
        after_move, scored = apply_move(board, mv)
        if next_row:
            after_spawn, spawn_scored = spawn_next_row(after_move, next_row)
            # heavily penalise a move that pushes us to game over after spawn
            overflow = -1000.0 if is_game_over(after_spawn) else 0.0
            key = (
                scored + spawn_scored,
                overflow + _board_potential(after_spawn),
                _board_potential(after_move),
            )
        else:
            key = (scored, _board_potential(after_move))
        if best_key is None or key > best_key:
            best_key = key
            best = mv
    return best


# ── save-string parsing (legit read path via 6464 act_clear_game_info) ──


def parse_save_string(save: str) -> Tuple[Board, dict]:
    """Parse the game's serialized save blob into a Board + metadata.

    Format (from syncMapInfo / initLevel):
        "<score> <adNum1> <adNum2> <buyNum1> <buyNum2> <useNum1> <useNum2>
         <row0> <row1> ... <rowN> <nextRow>"
    where each row token is ``pos_index+pos_index+...`` (or "0" if absent),
    and the FINAL token is the next-row info (excluded from playable rows).

    Row order in the save: rows[0] in the save is the BOTTOM-most playable
    row (closest to the next-row). initLevel pushes them then reverses, so
    the resulting RowList has rows[0] = TOP. We return rows top-first to
    match Board's convention.
    """
    tokens = save.strip().split(" ")
    meta = {"score": 0, "next_row": []}
    if not tokens or tokens == [""]:
        return Board(rows=()), meta

    nums = tokens[:7]
    if nums:
        try:
            meta["score"] = int(nums[0])
        except ValueError:
            meta["score"] = 0

    body = tokens[7:]
    if not body:
        return Board(rows=()), meta

    # last token is next-row info
    next_token = body[-1]
    row_tokens = body[:-1]
    meta["next_row"] = _parse_next_row(next_token)

    parsed_rows: List[Tuple[Block, ...]] = []
    for tok in row_tokens:
        if tok == "0" or not tok:
            parsed_rows.append(tuple())
            continue
        blocks = []
        for pair in tok.split("+"):
            if "_" not in pair:
                continue
            p, idx = pair.split("_")
            blocks.append(block_from_index(int(p), int(idx)))
        parsed_rows.append(tuple(blocks))

    # save lists bottom-first; Board wants top-first -> reverse
    parsed_rows.reverse()
    return Board(rows=tuple(parsed_rows)), meta


def _parse_next_row(token: str) -> List[Tuple[int, int]]:
    if not token or token == "0":
        return []
    out = []
    for pair in token.split("+"):
        if "_" not in pair:
            continue
        p, idx = pair.split("_")
        out.append((int(p), int(idx)))
    return out
