"""The mining loop must abort when the board signature is identical for
_MAX_IDENTICAL_BOARDS consecutive non-empty-plan iterations (the live
deadlock signature). Pure-logic test of the guard helper."""
from __future__ import annotations

from miner.mining_service import _identical_board_exceeded, _MAX_IDENTICAL_BOARDS


def test_guard_trips_after_threshold_identical():
    sig = "AAA|BBB"
    count = 0
    tripped = False
    for _ in range(_MAX_IDENTICAL_BOARDS + 2):
        count, tripped = _identical_board_exceeded(sig, sig, count)
        if tripped:
            break
    assert tripped is True
    assert count >= _MAX_IDENTICAL_BOARDS


def test_guard_resets_when_board_changes():
    count = 0
    count, tripped = _identical_board_exceeded("A", "A", count)  # same
    assert tripped is False and count == 1
    count, tripped = _identical_board_exceeded("B", "A", count)  # changed
    assert tripped is False and count == 0
