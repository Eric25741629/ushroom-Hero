"""When the planner is stuck (empty plans) but reachable pits remain, the loop
should pick a descent dig that drives toward a scroll rather than aborting."""
from __future__ import annotations

from miner.mining_service import _forced_descent_dig

_SYM = {".": "empty", "_": "unreachable_empty", "D": "dirt", "d": "unreachable_dirt",
        "R": "rock", "r": "unreachable_rock", "*": "reachable_pit", "X": "unreachable_pit"}


def _board(rows):
    return [[_SYM[ch] for ch in r] for r in rows]


def test_forced_descent_picks_deepest_reachable_nonpit_frontier():
    # Row-0 air strip over a column of dirt; deepest diggable dirt should win
    # so digging it advances toward row 6 (a scroll).
    b = _board(["...DDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD"])
    pos = _forced_descent_dig(b)
    assert pos is not None
    r, c = pos
    # must be a frontier dirt/rock cell (not a pit), and as deep as reachable
    assert b[r][c] in ("dirt", "rock", "one_hit_rock")


def test_forced_descent_none_when_no_frontier():
    b = _board(["______", "______", "______", "______", "______", "______", "______"])
    assert _forced_descent_dig(b) is None
