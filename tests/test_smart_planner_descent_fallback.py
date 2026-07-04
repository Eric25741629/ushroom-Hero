"""v1 (smart_planner) empty-plan regression: when A* has no goal-improving
move (no pits left AND floor7 already open, the WS no_pit case), the planner
must still emit ONE downward dig so the runtime keeps scrolling, instead of
returning an empty step list (the historical 'v1 會有空的問題')."""
from miner.planning.smart_planner import plan_smart


def _board(rows):
    return [list(r) for r in rows]


def test_no_pit_floor_open_returns_descent_dig():
    # col 0 dug to the bottom -> floor7 already open, no pits. Plain A* goal is
    # already satisfied -> would return []. Fallback must emit a descent dig.
    board = _board([
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["empty", "dirt", "dirt", "dirt", "dirt", "dirt"],
    ])
    plan = plan_smart(board, shovels=1000, items={"drill": 10, "bomb": 10})
    steps = plan.get("steps") or []
    assert steps, "expected a descent fallback dig, got empty plan"
    assert steps[0]["type"] == "dig"
    # descent: must target the deepest diggable row, not a shallow one.
    assert steps[0]["pos"][0] == 6


def test_truly_nothing_diggable_returns_empty():
    # All reachable air -> honestly nothing to dig -> empty (no fake step).
    board = _board([["empty"] * 6 for _ in range(7)])
    plan = plan_smart(board, shovels=1000, items={"drill": 0, "bomb": 0})
    assert (plan.get("steps") or []) == []


def test_normal_board_still_plans_to_dig_pit():
    # A reachable pit -> real A* still produces a non-empty plan that digs it.
    board = _board([
        ["empty", "empty", "empty", "empty", "empty", "empty"],
        ["empty", "pit", "dirt", "dirt", "dirt", "dirt"],
        ["dirt", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["dirt", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["dirt", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["dirt", "dirt", "dirt", "dirt", "dirt", "dirt"],
        ["dirt", "dirt", "dirt", "dirt", "dirt", "dirt"],
    ])
    plan = plan_smart(board, shovels=1000, items={"drill": 0, "bomb": 0})
    steps = plan.get("steps") or []
    assert steps, "real board should yield a non-empty plan"
    assert any(s["type"] == "dig" and tuple(s["pos"]) == (1, 1) for s in steps)
