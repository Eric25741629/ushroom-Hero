"""V4 planner must respect the `shovels` argument as a hard budget.

Previously `shovels` was accepted but unused — the DFS would happily
plan moves whose cumulative shovel cost exceeded the runtime's actual
pickaxe count. The mining loop's outer `while count >= 1` guard caught
the obvious case (count == 0), but with a low non-zero count the
planner could still emit a multi-step dig plan the executor couldn't
afford. These tests pin the budget contract.
"""
from __future__ import annotations

from miner.v4.planner import plan_v4


def _make_board(rows: int = 7, cols: int = 6, fill: str = "empty") -> list:
    return [[fill] * cols for _ in range(rows)]


def test_plan_v4_shovel_cost_never_exceeds_budget_small_budget():
    # Dirt-heavy board so the planner has plenty of dig options. With
    # only 2 shovels, depth-3 search would naively want 3 digs (= cost 3+).
    board = _make_board(fill="dirt")
    board[3][3] = "reachable_pit"

    plan = plan_v4(board, shovels=2, items={"drill": 0, "bomb": 0})

    assert plan["ok"] is True
    actual_shovels = plan["stats"]["shovel_cost"]
    assert actual_shovels <= 2.0, (
        f"v4 exceeded shovel budget: shovel_cost={actual_shovels}, "
        f"steps={plan['steps']}, stats={plan['stats']}"
    )


def test_plan_v4_zero_shovels_emits_no_dig_steps():
    """Budget=0 → planner cannot emit any dig step (dig minimum cost is 1)."""
    board = _make_board(fill="dirt")
    board[3][3] = "reachable_pit"

    plan = plan_v4(board, shovels=0, items={"drill": 0, "bomb": 0})

    assert plan["ok"] is True
    dig_steps = [s for s in plan["steps"] if s.get("type") == "dig"]
    assert dig_steps == [], (
        f"v4 emitted dig steps with shovels=0: {dig_steps} "
        f"(stats={plan['stats']})"
    )


def test_plan_v4_zero_shovels_still_allows_item_plays():
    """Items don't consume shovels, so budget=0 must not block item use."""
    board = _make_board()
    board[2][2] = "reachable_pit"
    board[2][3] = "reachable_pit"
    board[3][2] = "reachable_pit"
    board[3][3] = "reachable_pit"  # 2×2 cluster — bomb plays well here

    plan = plan_v4(board, shovels=0, items={"drill": 0, "bomb": 2})

    assert plan["ok"] is True
    assert plan["stats"]["shovel_cost"] == 0.0
    # The planner should consider this winnable via bomb — at least one
    # bomb step should be present.
    use_steps = [s for s in plan["steps"] if s.get("type") == "use"]
    assert use_steps, (
        f"v4 produced no item plays despite valid 2x2 cluster + 2 bombs and "
        f"zero shovel budget: steps={plan['steps']}"
    )


def test_plan_v4_high_budget_unchanged_behavior():
    """High budget (legacy default) should not regress — planner picks up pits."""
    board = _make_board()
    board[3][3] = "reachable_pit"

    plan = plan_v4(board, shovels=100, items={"drill": 0, "bomb": 0})

    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] >= 1
