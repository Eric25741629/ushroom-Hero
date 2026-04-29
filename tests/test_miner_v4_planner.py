"""V4 planner — bounded depth-3 rolling-horizon search with aggressive pruning.

Design goals (revised 2026-04-29 after planner-eval skill measurements):

1. Search depth = 3; the runtime re-plans each iteration anyway. Depth-5
   used to hit the 50 000-node budget on cluster-rich boards.
2. Item costs lightly priced (drill 1.5, bomb 2.0) so they're competitive
   with shovels — empirical eval (v1's drill-spam wins) confirms hoarding
   items hurts. Cluster reward refills drills/bombs on 2×2/3×3 completion.
3. Goal = floor7_open (any row-6 reachable air), not full row-6 clear —
   the H5 game auto-scrolls on the first row-6 air cell.
4. Rich PlanStats / PlanResult contract identical to v3 so mining_service
   can swap planners without re-keying dict access.
"""
from __future__ import annotations

from miner.v4.planner import plan_v4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_board(rows: int = 7, cols: int = 6, fill: str = "empty") -> list:
    return [[fill] * cols for _ in range(rows)]


# ---------------------------------------------------------------------------
# Contract: return shape matches v3 so mining_service can dispatch uniformly
# ---------------------------------------------------------------------------


def test_plan_v4_return_shape_matches_v3_contract():
    board = _make_board()
    board[3][3] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    # Required top-level keys (mining_service reads these directly).
    for key in (
        "ok",
        "message",
        "steps",
        "strategy_class",
        "floor7_open",
        "remaining_pits",
        "total_cost",
        "explored_nodes",
        "elapsed_ms",
        "stats",
    ):
        assert key in plan, f"missing key {key!r} in plan result"
    assert plan["ok"] is True
    for step in plan["steps"]:
        assert "type" in step
        assert "pos" in step
        assert "step_cost" in step


# ---------------------------------------------------------------------------
# Item-vs-shovel: 1×3 partial cluster
# ---------------------------------------------------------------------------
# Old (depth-5 / DRILL_COST=4) tests asserted shovels win on partial clusters
# because items were "scarce". Empirical eval (planner-eval skill, 2026-04-29)
# showed v1's drill-spam strategy wins long-run because cluster completion
# refills drills/bombs. v4 now uses cheaper item costs (1.5/2.0) and items
# DO win when their pit-yield matches a multi-shovel sequence. The remaining
# assertions only require pit collection, not which tool is used.


def test_plan_v4_partial_1x3_row6_cluster_collects_pits():
    board = _make_board()
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    board[6][4] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 1, "bomb": 3})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] >= 1, (
        f"planner must dig at least one pit, got: {plan['steps']}"
    )


def test_plan_v4_partial_1x3_row0_cluster_collects_pits():
    board = _make_board()
    board[0][2] = "reachable_pit"
    board[0][3] = "reachable_pit"
    board[0][4] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 1, "bomb": 3})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] >= 1


# ---------------------------------------------------------------------------
# Core behaviour: bombs ARE used when the full 3×3 / 2×2 is visible
# ---------------------------------------------------------------------------


def test_plan_v4_full_3x3_cluster_uses_bomb():
    # 3×3 cluster fully visible in rows 2-4 cols 1-3. Bomb placed adjacent
    # covers ≥4 pits in one blast — plan must use the bomb.
    board = _make_board()
    for r in range(2, 5):
        for c in range(1, 4):
            board[r][c] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["bombs_used"] == 1, (
        f"bomb must be used on full 3×3 cluster: {plan['steps']}"
    )


def test_plan_v4_full_2x2_cluster_uses_bomb():
    board = _make_board()
    board[5][2] = "reachable_pit"
    board[5][3] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["bombs_used"] == 1, (
        f"bomb must be used on 2×2 cluster: {plan['steps']}"
    )


# ---------------------------------------------------------------------------
# Core behaviour: single pit → shovel
# ---------------------------------------------------------------------------


def test_plan_v4_single_pit_uses_shovel_not_item():
    board = _make_board()
    board[3][3] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 1, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["bombs_used"] == 0
    assert plan["stats"]["drills_used"] == 0
    assert plan["stats"]["pits_collected"] == 1


# ---------------------------------------------------------------------------
# No items available → plain digging
# ---------------------------------------------------------------------------


def test_plan_v4_no_items_row0_pit_dug_directly():
    board = _make_board()
    board[0][3] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 1
    first = plan["steps"][0]
    assert first["type"] == "dig"
    assert tuple(first["pos"]) == (0, 3)


# ---------------------------------------------------------------------------
# Depth bound: MAX_DEPTH=3 (300 ms budget per plan)
# ---------------------------------------------------------------------------


def test_plan_v4_depth_bounded_to_3_steps():
    board = _make_board()
    positions = [(r, c) for r in (1, 3, 5) for c in (0, 2, 4)][:10]
    extras = [(2, 1), (4, 3)]
    for r, c in positions + extras:
        board[r][c] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert len(plan["steps"]) <= 3, f"expected ≤3 steps, got {len(plan['steps'])}"
    assert plan["stats"]["pits_collected"] <= 3


def test_plan_v4_depth_bounded_returns_best_partial():
    # Within depth=3, planner clears the 3 highest-value pits.
    board = _make_board()
    for r, c in [(1, 0), (1, 2), (1, 4), (3, 0), (3, 2), (3, 4), (5, 0), (5, 2), (5, 4), (2, 1)]:
        board[r][c] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 3


# ---------------------------------------------------------------------------
# Row-0 preservation (anti-scroll) — same invariant as v3
# ---------------------------------------------------------------------------


def test_plan_v4_prefers_row0_pit_before_row6_scroll():
    board = _make_board()
    board[0][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    # Row-0 pit must be dug first — digging (6,3) scrolls and loses (0,2).
    dig_positions = [tuple(s["pos"]) for s in plan["steps"] if s["type"] == "dig"]
    assert (0, 2) in dig_positions
    if (6, 3) in dig_positions:
        assert dig_positions.index((0, 2)) < dig_positions.index((6, 3))


# ---------------------------------------------------------------------------
# no_pit descent: row 6 partially reachable must still produce a plan
# ---------------------------------------------------------------------------


def test_plan_v4_buried_unreachable_pit_through_rock_wall():
    """Reproduces a broken case from the 2026-04-28 follow-up: an
    `unreachable_pit` at (6,3) is sealed behind two rows of unreachable_rock,
    with one cost-1 dirt at (4,3) the only path. The previous `near_pit` filter
    excluded (4,3) (not 4-adjacent to the pit) so the planner returned no
    steps. The Manhattan-distance filter must include it.
    """
    board = [["empty"] * 6 for _ in range(7)]
    board[4] = ["empty", "empty", "empty", "dirt", "empty", "empty"]
    board[5] = ["unreachable_rock"] * 6
    board[6] = ["unreachable_rock"] * 3 + ["unreachable_pit"] + ["unreachable_rock"] * 2
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "has_pit"
    assert plan["stats"]["pits_collected"] == 1
    dig_positions = [tuple(s["pos"]) for s in plan["steps"] if s["type"] == "dig"]
    # Plan must approach via (4,3) → (5,3) → (6,3).
    assert (4, 3) in dig_positions
    assert (6, 3) in dig_positions


def test_plan_v4_no_pit_already_floor7_open_returns_no_steps():
    """If row 6 has any reachable air the H5 game auto-scrolls — planner
    returns zero steps (nothing left to dig). Earlier versions tried to
    fully clear row 6 first, which wasted shovels."""
    board = [["empty"] * 6 for _ in range(7)]
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    assert len(plan["steps"]) == 0


def test_plan_v4_no_pit_picks_row5_dirt_when_it_floods_row6():
    """Cross-row cascade: row 6 starts unreachable, only path to opening it
    is digging a row-5 dirt cell whose flood promotes a row-6 cell. One
    cost-1 dig opens floor7 — beats any direct row-6 rock dig (cost 2)."""
    board = [["empty"] * 6 for _ in range(5)]
    board.append(["empty", "empty", "empty", "dirt", "empty", "empty"])
    board.append(["unreachable_empty"] * 6)
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    assert len(plan["steps"]) == 1, f"expected 1-step cascade, got: {plan['steps']}"
    assert tuple(plan["steps"][0]["pos"]) == (5, 3)
    assert plan["stats"]["shovel_cost"] == 1.0


def test_plan_v4_no_pit_picks_row4_dirt_when_it_cascades_two_rows():
    """Two-row cascade: row-4 dirt above two rows of unreachable_empty. The
    flood promotes everything below, opening floor7 for cost 1."""
    board = [["empty"] * 6 for _ in range(4)]
    board.append(["empty", "empty", "empty", "dirt", "empty", "empty"])
    board.append(["unreachable_empty"] * 6)
    board.append(["unreachable_empty"] * 6)
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    assert len(plan["steps"]) == 1
    assert tuple(plan["steps"][0]["pos"]) == (4, 3)
    assert plan["stats"]["shovel_cost"] == 1.0


def test_plan_v4_no_pit_one_rock_dig_opens_floor7():
    """Row 6 = 6 reachable rocks (no air). One dig is enough — opening any
    one rock makes floor7_open True, which is the auto-scroll trigger. The
    optimal plan is exactly 1 step; anything more is wasted shovels."""
    board = [["empty"] * 6 for _ in range(7)]
    board[6] = ["rock"] * 6
    plan = plan_v4(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    assert len(plan["steps"]) == 1, (
        f"expected 1 dig (any row-6 rock opens floor7), got {len(plan['steps'])}: {plan['steps']}"
    )
    r, c = plan["steps"][0]["pos"]
    assert r == 6, f"dig must target row 6, got ({r}, {c})"


# ---------------------------------------------------------------------------
# Efficiency: pruning should keep explored_nodes low
# ---------------------------------------------------------------------------


def test_plan_v4_explored_nodes_bounded_by_pruning():
    # Busy board — lots of pits + items available. Without pruning, 5-layer
    # search would explode. With B&B + dominance + action filter, we want
    # well under 100k nodes (ample room; real target ~10k).
    board = _make_board()
    for r in range(2, 5):
        for c in range(1, 4):
            board[r][c] = "reachable_pit"
    board[0][0] = "reachable_pit"
    board[6][5] = "reachable_pit"
    plan = plan_v4(board, shovels=30, items={"drill": 2, "bomb": 2})
    assert plan["ok"] is True
    assert plan["explored_nodes"] < 100_000, (
        f"pruning too weak: {plan['explored_nodes']} nodes explored"
    )
