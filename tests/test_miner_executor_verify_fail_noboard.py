"""A dig that fails verification AND leaves the board unchanged must raise
NoBoardChangeError so the mining loop blacklists it (regression: 7fe98fc6
122x identical-plan spin on row-0 unreachable pits, 2026-06-17)."""
from __future__ import annotations

import pytest

from miner.planning import executor as ex
from miner.planning.executor import NoBoardChangeError, execute_plan_steps

# 7x6 deadlock board: pits at row1 reachable only via row-0 air above.
_SYM = {".": "empty", "_": "unreachable_empty", "D": "dirt", "d": "unreachable_dirt",
        "R": "rock", "r": "unreachable_rock", "*": "reachable_pit", "X": "unreachable_pit"}
_GRID = ["_...R.", "dXXXD.", "_XXXD.", "drddR.", "d_d_D.", "dd_rR.", "dr_rdD"]
DEADLOCK = [[_SYM[ch] for ch in row] for row in _GRID]


class _FakeDevice:
    def screenshot(self, format=None):
        return object()  # opaque; classifier is stubbed to ignore it
    def click(self, *a, **k):
        pass


class _FakeClassifier:
    """Always returns the deadlock board — i.e. nothing the executor does
    changes the board (models the real 'tap does nothing' case)."""
    def classify_board(self, img, save_samples=False):
        return [row[:] for row in DEADLOCK], None


def test_dig_verify_fail_with_unchanged_board_raises_noboardchange(monkeypatch):
    # Stub the device-touching helpers so no real ADB/screenshot is needed.
    monkeypatch.setattr(ex, "tap_cell", lambda *a, **k: None)
    monkeypatch.setattr(ex, "verify_cell_empty", lambda *a, **k: False)  # never empties
    monkeypatch.setattr(ex, "check_points", lambda *a, **k: None)
    monkeypatch.setattr(ex, "wait_frame_stable", lambda d, **k: object())

    d = _FakeDevice()
    clf = _FakeClassifier()
    board = [row[:] for row in DEADLOCK]
    plan_steps = [{"type": "dig", "action": "dig", "dig_list": [(1, 2)],
                   "target": (1, 2)}]

    with pytest.raises(NoBoardChangeError):
        execute_plan_steps(d, clf, board, plan_steps)
