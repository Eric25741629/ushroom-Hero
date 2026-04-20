"""V3 planner — verifies the three structural promises:

1. Reachability is taken from CNN labels; no plan-start BFS rebuild.
2. While any pit remains, no action is allowed to scroll the board (open
   floor7) and dump row 0 / row 1 pits.
3. Cluster-aware item placement prefers covering full N×N pit groups
   (1×1 / 2×2 / 3×3).

Plus the basic stats contract.
"""
from __future__ import annotations

from miner.v3.actions import apply_dig
from miner.v3.board import (
    canonicalize_in_place,
    count_pits,
    is_frontier_diggable,
    promote_after_dig,
)
from miner.v3.clusters import cluster_value, find_clusters
from miner.v3.planner import plan_v3


# -------------------------- reachability semantics ---------------------------


def test_canonicalize_only_normalizes_void_and_legacy_pit():
    board = [["void", "pit", "unreachable_empty"]]
    canonicalize_in_place(board)
    assert board[0][0] == "empty"
    assert board[0][1] == "reachable_pit"
    assert board[0][2] == "unreachable_empty"


def test_isolated_air_pocket_stays_unreachable_at_plan_start():
    board = [["unreachable_rock"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    # An interior air pocket NOT connected to (0,0) — CNN says unreachable.
    board[3][3] = "unreachable_empty"
    board[3][4] = "unreachable_empty"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    for step in plan["steps"]:
        if step["type"] != "dig":
            continue
        r, c = step["pos"]
        # Plan must not chase cells deep inside the unreachable region.
        assert not (r >= 2 and c >= 2 and (r, c) != (3, 3) and (r, c) != (3, 4)), step


def test_frontier_dig_allows_unreachable_neighbours_of_reachable_air():
    board = [
        ["empty", "unreachable_dirt", "unreachable_rock"],
        ["unreachable_rock", "unreachable_rock", "unreachable_rock"],
    ]
    assert is_frontier_diggable(board, 0, 1) is True  # adjacent to (0,0) empty
    assert is_frontier_diggable(board, 1, 0) is True  # adjacent to (0,0) empty
    # Interior cell, not adjacent to any reachable air → cannot dig.
    assert is_frontier_diggable(board, 1, 2) is False


def test_promote_after_dig_floods_unreachable_empty_chain():
    board = [
        ["empty", "dirt", "unreachable_empty", "unreachable_empty", "unreachable_pit"],
        ["unreachable_rock"] * 5,
    ]
    apply_dig(board, (0, 1))
    assert board[0][1] == "empty"
    assert board[0][2] == "empty"
    assert board[0][3] == "empty"
    assert board[0][4] == "reachable_pit"
    # Disconnected wall stays put.
    assert board[1][4] == "unreachable_rock"


# -------------------------- pit-loss safety ---------------------------------


def test_plan_v3_refuses_to_open_floor7_while_pit_remains():
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "reachable_pit"
    board[6][3] = "dirt"  # tempting one-shot to open floor 7
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    for step in plan["steps"]:
        if step["type"] == "dig":
            r, _ = step["pos"]
            assert r != 6, f"dug row 6 while top-row pit remains: {step}"


def test_plan_v3_collects_every_reachable_pit():
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "reachable_pit"
    board[2][3] = "reachable_pit"
    board[4][5] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 3
    assert plan["stats"]["pits_remaining"] == 0


def test_plan_v3_keeps_searching_for_unreachable_pits_no_extra_cost_cap():
    # User requirement: unreachable_pit_max_extra_cost = INF — never give up.
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    # Wall a pit behind several layers of unreachable_rock.
    board[3][3] = "unreachable_pit"
    for r in range(0, 3):
        for c in range(1, 6):
            board[r][c] = "unreachable_rock"
    board[3][1] = "unreachable_rock"
    board[3][2] = "unreachable_rock"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    # The plan should at least START digging toward the buried pit.
    assert plan["steps"], "expected non-empty plan even for buried pit"


# -------------------------- cluster-aware item use ---------------------------


def test_find_clusters_detects_2x2_and_3x3():
    board = [["empty"] * 6 for _ in range(6)]
    # 3×3 in upper-left
    for r in range(3):
        for c in range(3):
            board[r][c] = "reachable_pit"
    # 2×2 in lower-right
    for r in range(4, 6):
        for c in range(4, 6):
            board[r][c] = "reachable_pit"
    clusters = find_clusters(board)
    sizes = sorted(c.size for c in clusters)
    assert 3 in sizes  # 3×3 detected
    assert 2 in sizes  # 2×2 detected (and several 2×2 sub-blocks of the 3×3)


def test_cluster_value_superlinear_in_size():
    assert cluster_value(3) > cluster_value(2) * 2  # 3×3 > two 2×2s combined
    assert cluster_value(2) > cluster_value(1) * 4  # 2×2 > four 1×1s combined


def test_plan_v3_prefers_bomb_on_2x2_cluster():
    board = [["empty"] * 6 for _ in range(7)]
    board[3][2] = "reachable_pit"
    board[3][3] = "reachable_pit"
    board[4][2] = "reachable_pit"
    board[4][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    assert any(step.get("item") == "bomb" for step in plan["steps"]), \
        f"expected bomb on 2×2 cluster, got {plan['steps']}"


def test_plan_v3_does_not_waste_item_on_zero_pits():
    """In has_pit mode, an item placement that hits no pit should not be
    chosen when a regular dig will do."""
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 1, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    # First step should be a dig at (0, 0) not a wasted drill/bomb elsewhere.
    first = plan["steps"][0]
    assert first["type"] == "dig"
    assert tuple(first["pos"]) == (0, 0)


# -------------------------- stats / contract --------------------------------


def test_plan_v3_stats_contract():
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    stats = plan["stats"]
    for key in (
        "explored_nodes",
        "elapsed_ms",
        "pits_at_start",
        "pits_collected",
        "pits_remaining",
        "shovel_cost",
        "drills_used",
        "bombs_used",
        "cost_per_pit",
    ):
        assert key in stats, f"missing stats key: {key}"
    # Legacy compatibility keys for mining_service.
    assert "remaining_pits" in plan
    assert "total_cost" in plan
