"""DepthTracker — per-session scroll/depth tracking for mining.

The mining board is a 7-row viewport sliding down a longer tape. Between two
consecutive classifications the viewport may have scrolled down some rows when
the bottom row was cleared. DepthTracker compares consecutive boards, infers
how many rows scrolled, and accumulates a relative session depth. It tolerates
the expected differences from cells the bot dug itself, and refuses to guess
when alignment is ambiguous.
"""
from __future__ import annotations

from miner.depth_tracker import DepthTracker

ROWS = 7
COLS = 6


def _board(fill: str = "empty") -> list:
    return [[fill for _ in range(COLS)] for _ in range(ROWS)]


def _scroll_down(board: list, s: int, new_fill: str = "dirt") -> list:
    """Produce the next viewport after scrolling down `s` rows.

    Top `s` rows of `board` fall off; `s` fresh rows enter at the bottom.
    """
    kept = board[s:]
    fresh = [[new_fill for _ in range(COLS)] for _ in range(s)]
    return kept + fresh


# ---------------------------------------------------------------------------
# No scroll
# ---------------------------------------------------------------------------


def test_identical_board_reports_zero_scroll() -> None:
    tracker = DepthTracker()
    board = _board("dirt")
    assert tracker.update(board) == 0  # first board is baseline
    assert tracker.update(board) == 0
    assert tracker.depth == 0
    assert tracker.last_uncertain is False


# ---------------------------------------------------------------------------
# Scroll 1 / 2 rows
# ---------------------------------------------------------------------------


def test_scroll_one_row_detected() -> None:
    tracker = DepthTracker()
    # Distinctive structure so alignment is unambiguous.
    prev = _board("empty")
    prev[3] = ["rock"] * COLS
    prev[5] = ["dirt"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 1, new_fill="rock")
    assert tracker.update(cur) == 1
    assert tracker.depth == 1
    assert tracker.last_uncertain is False


def test_scroll_two_rows_detected() -> None:
    tracker = DepthTracker()
    prev = _board("empty")
    prev[2] = ["rock"] * COLS
    prev[4] = ["dirt"] * COLS
    prev[6] = ["rock"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 2, new_fill="dirt")
    assert tracker.update(cur) == 2
    assert tracker.depth == 2


def test_depth_accumulates_across_updates() -> None:
    tracker = DepthTracker()
    prev = _board("dirt")
    prev[5] = ["rock"] * COLS
    tracker.update(prev)
    cur1 = _scroll_down(prev, 1, new_fill="rock")
    tracker.update(cur1)
    cur2 = _scroll_down(cur1, 1, new_fill="dirt")
    tracker.update(cur2)
    assert tracker.depth == 2


# ---------------------------------------------------------------------------
# Tolerate self-dug differences
# ---------------------------------------------------------------------------


def test_scroll_one_with_dug_cells_tolerated() -> None:
    tracker = DepthTracker()
    prev = _board("dirt")
    prev[1] = ["rock"] * COLS
    prev[4] = ["rock"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 1, new_fill="dirt")
    # The bot dug a few cells between frames: solid -> empty/dug_pit, and a
    # pit it could not reach became reachable. These are all expected diffs
    # the matcher must ignore.
    cur[0][0] = "empty"      # was dirt, now dug
    cur[0][1] = "dug_pit"    # collected a pit
    cur[2][3] = "empty"      # was rock row cell, dug away
    assert tracker.update(cur) == 1
    assert tracker.depth == 1
    assert tracker.last_uncertain is False


def test_unreachable_to_reachable_does_not_break_match() -> None:
    tracker = DepthTracker()
    prev = _board("empty")
    prev[3] = ["unreachable_rock"] * COLS
    prev[5] = ["unreachable_dirt"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 1, new_fill="dirt")
    # Same structure, now reachable after digging opened a path.
    cur[2] = ["rock"] * COLS
    cur[4] = ["dirt"] * COLS
    assert tracker.update(cur) == 1


# ---------------------------------------------------------------------------
# Ambiguous board
# ---------------------------------------------------------------------------


def test_ambiguous_board_returns_zero_and_flags_uncertain() -> None:
    tracker = DepthTracker()
    tracker.update(_board("dirt"))
    # A totally different board with no comparable solid structure overlap
    # (all air) — alignment confidence is undefined.
    other = _board("empty")
    assert tracker.update(other) == 0
    assert tracker.last_uncertain is True
    # Depth must not move on an uncertain read.
    assert tracker.depth == 0


# ---------------------------------------------------------------------------
# Absolute-depth calibration
# ---------------------------------------------------------------------------


def test_set_absolute_depth_overrides() -> None:
    tracker = DepthTracker()
    tracker.update(_board("dirt"))
    tracker.set_absolute_depth(12)
    assert tracker.depth == 12
    # Subsequent relative scrolls continue from the calibrated baseline.
    prev = _board("dirt")
    prev[5] = ["rock"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 1, new_fill="rock")
    tracker.update(cur)
    assert tracker.depth == 13


def test_reset_clears_state() -> None:
    tracker = DepthTracker()
    prev = _board("dirt")
    prev[5] = ["rock"] * COLS
    tracker.update(prev)
    cur = _scroll_down(prev, 1, new_fill="rock")
    tracker.update(cur)
    assert tracker.depth == 1
    tracker.reset()
    assert tracker.depth == 0
    assert tracker.last_uncertain is False
    # After reset the next board is a fresh baseline (no scroll inferred).
    assert tracker.update(_board("dirt")) == 0
