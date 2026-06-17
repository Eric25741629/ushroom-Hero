"""Behaviour tests for the v5 probabilistic mining planner.

Written test-first (TDD). The v5 planner layers the real-session priors
(docs/MINING_V5_PRIORS.md) on top of the v4 DFS skeleton:

- bomb delay on incomplete bottom-edge square clusters
- row-0 pit rescue (never scroll away a collectable row-0 pit)
- expected-cost column choice when opening floor 7 (descend air columns)
- wall-clock deadline (<300 ms)
- empty-plan guard (always return a scroll-progress step, never surrender)
- priors loader with JSON + hard-coded fallback
"""
from __future__ import annotations

import time

import pytest

from miner.v5.planner import plan_v5
from miner.v5.priors import Priors, get_priors, load_priors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _board(rows):
    """Build a 7x6 board from a compact symbol grid.

    . empty   D dirt   R rock   * reachable_pit   X unreachable_pit
    _ unreachable_empty   d unreachable_dirt   r unreachable_rock
    """
    sym = {
        ".": "empty",
        "_": "unreachable_empty",
        "D": "dirt",
        "d": "unreachable_dirt",
        "R": "rock",
        "r": "unreachable_rock",
        "*": "reachable_pit",
        "X": "unreachable_pit",
    }
    grid = [[sym[ch] for ch in row] for row in rows]
    assert len(grid) == 7 and all(len(r) == 6 for r in grid), "expect 7x6"
    return grid


def _first_step(plan):
    steps = plan.get("steps") or []
    return steps[0] if steps else None


# ---------------------------------------------------------------------------
# Priors loader
# ---------------------------------------------------------------------------
def test_priors_load_from_json_has_real_numbers():
    p = load_priors()
    assert p.source == "priors.json"
    # marginal pit ~3.1%
    assert 0.02 < p.marginal["pit"] < 0.05
    # P(air|air) ~27.9%
    assert 0.25 < p.below_given_above["air"]["air"] < 0.31
    # P(pit|pit) ~42.5%
    assert 0.38 < p.pit_below_pit < 0.47


def test_priors_fallback_when_json_missing(monkeypatch, tmp_path):
    import miner.v5.priors as priors_mod

    monkeypatch.setattr(priors_mod, "_PRIORS_PATH", tmp_path / "nope.json")
    p = priors_mod.load_priors()
    assert p.source == "fallback"
    # fallback still has sane pit-below-by-width ladder
    assert p.pit_below_prob(1) < p.pit_below_prob(2) < p.pit_below_prob(3)
    assert p.pit_below_prob(3) > 0.7


def test_priors_pit_below_width_ladder():
    p = get_priors()
    assert p.pit_below_prob(1) < 0.1
    assert 0.35 < p.pit_below_prob(2) < 0.55
    assert p.pit_below_prob(3) > 0.7
    # clamps above 3
    assert p.pit_below_prob(5) == p.pit_below_prob(3)


def test_expected_dig_cost_below_air_cheaper_than_rock():
    p = get_priors()
    # Descending through an air column is cheaper in expectation than through
    # a rock column (air begets air; rock begets dirt/rock).
    assert p.expected_dig_cost_below("air") < p.expected_dig_cost_below("rock")


# ---------------------------------------------------------------------------
# Plan output shape
# ---------------------------------------------------------------------------
def test_plan_v5_returns_v4_compatible_shape():
    board = _board([
        "..DDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0})
    assert isinstance(plan, dict)
    assert "steps" in plan and "stats" in plan and "ok" in plan
    assert plan["ok"] is True
    for step in plan["steps"]:
        assert step["type"] in ("dig", "use")
        assert "pos" in step or "target" in step


def test_plan_v5_records_depth_in_stats():
    board = _board([
        "..DDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0}, depth=4)
    assert plan["stats"].get("requested_depth") == 4


# ---------------------------------------------------------------------------
# Row-0 rescue: a collectable pit in row 0 must be dug before scrolling.
# ---------------------------------------------------------------------------
def test_plan_v5_rescues_row0_pit_first():
    # Row 0 has a reachable pit (foothold air beside it). Floor 7 already has
    # air, so scrolling is tempting -- but that would lose the row-0 pit.
    board = _board([
        ".*DDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "..DDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0})
    step = _first_step(plan)
    assert step is not None
    pos = tuple(step.get("pos") or step.get("target"))
    assert pos == (0, 1), f"row-0 pit must be rescued first, got {pos}"


# ---------------------------------------------------------------------------
# Bomb delay: an incomplete bottom-edge square cluster should NOT be bombed
# now -- it is likely to extend downward (w=2 -> 43%, w=3 -> 77%).
# ---------------------------------------------------------------------------
def test_plan_v5_delays_bomb_on_incomplete_bottom_square():
    # A width-2 pit run on the bottom row (row 6), only 1 row visible.
    # Square-cluster prior says ~43% chance of more pit below once scrolled.
    # v5 should NOT spend a bomb here; prefer digging the other reachable pit
    # or making scroll progress instead.
    board = _board([
        "..DDDD",
        "DDDDDD",
        "DDDD*D",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "**DDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 5})
    first = _first_step(plan)
    assert first is not None
    # The very first action must not be a bomb landing on the bottom-edge
    # width-2 cluster (cols 0-1, row 6).
    if first["type"] == "use" and first.get("item") == "bomb":
        bombed = tuple(first["pos"])
        assert bombed[0] < 6 or bombed[1] > 2, (
            f"should not bomb incomplete bottom square, bombed {bombed}"
        )


def test_plan_v5_bombs_complete_square_cluster():
    # A full visible 3x3 pit block (cols 1-3, rows 2-4), reachable from above.
    # This is a complete square -> bombing it (clearing 9 pits) must be a move
    # the planner is willing to make.
    board = _board([
        "..DDDD",
        "DDDDDD",
        "D***DD",
        "D***DD",
        "D***DD",
        "DDDDDD",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 5})
    steps = plan.get("steps") or []
    used_bomb = any(s["type"] == "use" and s.get("item") == "bomb" for s in steps)
    # Either it bombs, or it digs into the cluster -- but with a bomb available
    # and a full 3x3, a bomb that clears the whole cluster should be chosen.
    assert used_bomb, "a full 3x3 with a bomb in inventory should be bombed"


# ---------------------------------------------------------------------------
# Empty-plan guard: never surrender. Even a board with no pits must return a
# scroll-progress dig.
# ---------------------------------------------------------------------------
def test_plan_v5_never_empty_on_no_pit_board():
    board = _board([
        "..DDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0})
    assert plan["steps"], "no-pit board must still yield a scroll-progress step"


def test_plan_v5_never_empty_on_buried_pit_board():
    # A pit fully sealed behind rock, plus open frontier elsewhere.
    board = _board([
        "..DDDD",
        "DDDDDD",
        "DDDDDD",
        "rrXrrD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0})
    assert plan["steps"], "buried-pit board must still make progress"


# ---------------------------------------------------------------------------
# Deadline: worst-case plan time stays under the wall-clock budget.
# ---------------------------------------------------------------------------
def test_plan_v5_respects_deadline():
    # Cluster-rich board (many pits + items) is the worst case for branching.
    board = _board([
        "..*D*D",
        "D*D*DD",
        "*D*D*D",
        "D*D*DD",
        "*D*D*D",
        "D*D*DD",
        "*D*D*D",
    ])
    t0 = time.perf_counter()
    plan = plan_v5(board, shovels=200.0, items={"drill": 10, "bomb": 10})
    dt_ms = (time.perf_counter() - t0) * 1000.0
    assert dt_ms < 300.0, f"plan took {dt_ms:.0f}ms (budget 300ms)"
    assert plan["ok"] is True


# ---------------------------------------------------------------------------
# Expected-cost column choice: when only scroll progress is possible, v5
# should prefer descending a column that sits below known air (cheaper future
# digging) over one below rock.
# ---------------------------------------------------------------------------
def test_plan_v5_prefers_air_column_for_descent():
    # No pits. Row 6 is all dirt. Column 0 has air above it (cheap future
    # descent: P(air|air) lift); column 5 sits under rock. Both row-6 cells
    # cost the same to dig (dirt=1), so the tie-break is the expected cost of
    # the cell BELOW once we scroll -- which favours the air column.
    board = _board([
        "....RR",
        "....RR",
        "....RR",
        "....RR",
        "....RR",
        "....RR",
        "DDDDDD",
    ])
    plan = plan_v5(board, shovels=100.0, items={"drill": 0, "bomb": 0})
    step = _first_step(plan)
    assert step is not None
    pos = tuple(step.get("pos") or step.get("target"))
    # Should open row 6 under the air columns (0-3), not the rock columns (4-5).
    assert pos[0] == 6 and pos[1] <= 3, (
        f"expected descent under an air column, got {pos}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
