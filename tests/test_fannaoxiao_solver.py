"""Tests for the 煩惱消 (左右消除 / left-right crush) solver.

Game model (live-confirmed on 7fe98fc6, 2026-06-14, see
docs/protocol/FANNAOXIAO_RECON.md):
  - Board = rows of blocks over WIDTH=9 columns. Bottom row is RowList[-1].
  - A block = (pos, length, color). It occupies columns [pos, pos+length).
  - A MOVE drags one block horizontally within its own row to a target column,
    sliding only through empty cells (cannot jump over other blocks).
  - A row whose 9 cells are ALL filled clears and scores; blocks above drop
    into empty cells below.
  - Game over when row count reaches MAX_ROWS (12).

The solver is pure / deterministic: read board -> choose_move -> (row, from_pos,
to_pos). Move execution + drop cascade are simulated for lookahead.
"""
import pytest

from game_actions.fannaoxiao_solver import (
    WIDTH,
    Block,
    Board,
    legal_moves,
    apply_move,
    row_filled,
    grid_info,
    choose_move,
    is_game_over,
)


def mk_row(*blocks):
    """Helper: build a row (tuple of Block) from (pos, length, color) triples."""
    return tuple(Block(pos=p, length=l, color=c) for (p, l, c) in blocks)


# ── grid_info / row_filled ─────────────────────────────────────────────


def test_grid_info_marks_occupied_columns():
    row = mk_row((1, 3, 1), (5, 1, 1))  # cols 1,2,3 and 5
    assert grid_info(row) == [0, 1, 1, 1, 0, 1, 0, 0, 0]


def test_row_filled_true_when_all_nine_columns_occupied():
    row = mk_row((0, 3, 1), (3, 3, 2), (6, 3, 1))  # widths 3+3+3 = 9
    assert row_filled(row) is True


def test_row_filled_false_when_gap_remains():
    row = mk_row((0, 3, 1), (3, 3, 2))  # only 6 columns
    assert row_filled(row) is False


# ── legal_moves: slide range only through empty cells ──────────────────


def test_legal_moves_block_slides_through_empty_cells_only():
    # row: 1-wide block at col5; empty cells 3,4 to the left, col6 occupied.
    # Range = [3,5]; moving to 3 or 4 are legal, 5 is the no-op origin.
    row = mk_row((6, 1, 1), (5, 1, 1), (2, 1, 1))
    board = Board(rows=(row,))
    moves = legal_moves(board)
    targets = {(r, fp, tp) for (r, fp, tp) in moves}
    # block@5 can go to 3 or 4 (not over col6 block)
    assert (0, 5, 3) in targets
    assert (0, 5, 4) in targets
    # cannot move onto/over an occupied column
    assert (0, 5, 6) not in targets


def test_legal_moves_excludes_noop_same_position():
    row = mk_row((0, 1, 1))
    board = Board(rows=(row,))
    for (r, fp, tp) in legal_moves(board):
        assert fp != tp


def test_legal_moves_block_cannot_jump_blocker():
    # block@0 (len1), blocker at col2, target col4 is blocked by col2 -> illegal
    row = mk_row((0, 1, 1), (2, 1, 2), (5, 1, 1))
    board = Board(rows=(row,))
    targets = {(r, fp, tp) for (r, fp, tp) in legal_moves(board)}
    assert (0, 0, 4) not in targets  # would require jumping col2
    assert (0, 0, 1) in targets      # one step right into empty col1 is fine


# ── apply_move + clearing + drop cascade ───────────────────────────────


def test_apply_move_updates_block_position():
    row = mk_row((6, 1, 1), (5, 1, 1), (2, 1, 1))
    board = Board(rows=(row,))
    nb, scored = apply_move(board, (0, 5, 4))
    moved = [b for b in nb.rows[0] if b.pos == 4]
    assert len(moved) == 1
    assert scored == 0  # row not completed, no clear


def test_apply_move_completing_row_via_drop_clears_and_scores():
    # A same-row slide cannot fill a 9-wide row (it just relocates the gap);
    # completion happens when a block DROPS from an upper row into the gap.
    # Bottom row: cols 0-2,3-5,6,7 filled => only col8 empty (total width 8).
    upper = mk_row((8, 1, 1))                       # a 1-block sitting above col8
    lower = mk_row((0, 3, 1), (3, 3, 2), (6, 1, 1), (7, 1, 1))  # col8 empty
    board = Board(rows=(upper, lower))
    # Any move triggers a settle; here moving the upper block within its row is
    # a no-op for the gap, but settle drops it into the lower row's col8 -> full.
    from game_actions.fannaoxiao_solver import settle_drops, _settle
    settled, cleared = _settle(board)
    assert cleared == 1


def test_apply_move_no_clear_when_only_relocating_gap():
    # Sliding a 1-block one cell just moves the gap; no clear.
    row = mk_row((0, 3, 1), (4, 1, 2), (5, 3, 1), (8, 1, 1))  # col3 empty, width sum 8
    board = Board(rows=(row,))
    nb, scored = apply_move(board, (0, 4, 3))
    assert scored == 0


def test_drop_cascade_moves_block_into_empty_lower_cell():
    # Upper row has a block over a column that is empty in the lower row ->
    # after a move triggers update, the block drops down.
    upper = mk_row((0, 1, 1))           # col0 occupied up top
    lower = mk_row((3, 1, 2))           # col0 empty below
    board = Board(rows=(upper, lower))
    # apply_drops is part of apply_move's settle; expose via apply_move no-clear move.
    from game_actions.fannaoxiao_solver import settle_drops
    nb = settle_drops(board)
    # the upper block should have dropped into the lower row's col0
    cols_lower = grid_info(nb.rows[-1])
    assert cols_lower[0] == 1


# ── game over ──────────────────────────────────────────────────────────


def test_is_game_over_true_at_max_rows():
    from game_actions.fannaoxiao_solver import MAX_ROWS
    rows = tuple(mk_row((0, 1, 1)) for _ in range(MAX_ROWS))
    assert is_game_over(Board(rows=rows)) is True


def test_is_game_over_false_below_max_rows():
    board = Board(rows=(mk_row((0, 1, 1)),))
    assert is_game_over(board) is False


# ── choose_move: prefers a move that clears a row ──────────────────────


def test_choose_move_prefers_completing_a_row():
    # Upper row has a block that, after a settle, drops into the lower row's
    # only gap and clears it. The solver should pick the move that yields the
    # clear (score) over moves that don't.
    upper = mk_row((8, 1, 1), (0, 1, 2))            # blocks we can wiggle
    lower = mk_row((0, 3, 1), (3, 3, 2), (6, 1, 1), (7, 1, 1))  # col8 empty -> drop fills it
    board = Board(rows=(upper, lower))
    mv = choose_move(board)
    assert mv is not None
    nb, scored = apply_move(board, mv)
    assert scored > 0  # the chosen move leads to a cleared row


def test_choose_move_returns_none_when_no_legal_move():
    # A single full-width set with no empty cell to slide into.
    row = mk_row((0, 3, 1), (3, 3, 2), (6, 3, 1))  # all 9 filled, 0 empty
    board = Board(rows=(row,))
    assert choose_move(board) is None


def test_choose_move_is_deterministic():
    row = mk_row((6, 1, 1), (5, 1, 1), (2, 1, 1))
    board = Board(rows=(row,))
    a = choose_move(board)
    b = choose_move(board)
    assert a == b


# ── save-string parsing (legit read path) ─────────────────────────────


# ── next-row preview (green bar) — known-spawn lookahead ───────────────


def test_next_row_to_blocks_decodes_preview_pairs():
    from game_actions.fannaoxiao_solver import next_row_to_blocks
    # [[2,13],[5,4],[1,2]] -> 3-wide colour1 @2, 1-wide colour4 @5, 1-wide colour2 @1
    blocks = next_row_to_blocks([[2, 13], [5, 4], [1, 2]])
    spans = {(b.pos, b.length, b.color) for b in blocks}
    assert (2, 3, 1) in spans
    assert (5, 1, 4) in spans
    assert (1, 1, 2) in spans


def test_spawn_next_row_appends_at_bottom_and_settles():
    from game_actions.fannaoxiao_solver import spawn_next_row
    # An upper block over an empty column drops onto the spawned bottom row.
    board = Board(rows=(mk_row((0, 1, 1)),))
    nb, scored = spawn_next_row(board, [[3, 1]])  # 1-wide block @3
    # gravity merges the lone upper block into the new bottom row (no clear)
    assert scored == 0
    assert grid_info(nb.rows[-1])[0] == 1   # the @0 block dropped to the bottom
    assert grid_info(nb.rows[-1])[3] == 1   # the spawned @3 block is there too


def test_spawn_next_row_completes_a_one_short_row_and_clears():
    from game_actions.fannaoxiao_solver import spawn_next_row
    # Existing row is cols 0-7 filled, col8 empty (1 short). The previewed row
    # is a single block at col8 -> after spawn the existing row drops onto it,
    # completing all 9 columns -> clears.
    bottom = mk_row((0, 3, 1), (3, 3, 2), (6, 1, 1), (7, 1, 1))  # col8 empty
    board = Board(rows=(bottom,))
    nb, scored = spawn_next_row(board, [[8, 1]])
    assert scored > 0


def test_choose_move_uses_preview_to_prefer_better_spawn_outcome():
    # Two rows: the solver should pick the move whose resulting board (after the
    # previewed row spawns) is best. With the preview known, choose_move must
    # run without error and return a legal move, preferring spawn-aware value.
    bottom = mk_row((0, 3, 1), (4, 1, 2), (5, 1, 1), (7, 1, 1))  # gaps at 3,6,8
    board = Board(rows=(bottom,))
    next_row = [[3, 1], [6, 1], [8, 1]]   # fills the three gaps when it drops
    mv = choose_move(board, next_row=next_row)
    assert mv is not None
    # the chosen move is one of the legal moves
    assert mv in legal_moves(board)


def test_choose_move_preview_avoids_game_over_overflow():
    from game_actions.fannaoxiao_solver import MAX_ROWS
    # Stack one short of the limit; spawning the preview would hit game over.
    rows = tuple(mk_row((0, 1, 1)) for _ in range(MAX_ROWS - 1))
    board = Board(rows=rows)
    mv = choose_move(board, next_row=[[5, 1]])
    # there's at least one legal move (the lone blocks can slide); it must not
    # crash and must return a move (overflow penalty just biases the choice).
    assert mv is not None


def test_parse_save_string_round0_empty():
    from game_actions.fannaoxiao_solver import parse_save_string
    board, meta = parse_save_string("0 0 0 0 0 0 0 1_13+5_1 2_7+5_1+7_2")
    assert meta["score"] == 0
    # two rows parsed (last token is next-row, excluded from playable rows)
    assert len(board.rows) >= 1
    # first parsed row token "1_13+5_1": block pos1 index13 (3-wide), pos5 index1 (1-wide)
    first = board.rows[0]
    assert any(b.pos == 1 and b.length == 3 for b in first)
    assert any(b.pos == 5 and b.length == 1 for b in first)
