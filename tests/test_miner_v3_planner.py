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


def test_plan_v3_allows_row6_pit_dig_when_only_row6_pits_remain():
    # Regression: emulator-5558 log 2026-04-24 01:11:13. The planner used to
    # burn 21 shovels clearing every rock/dirt in rows 1-5 before allowing a
    # row-6 pit dig, because `triggers_floor7_loss` filtered productive pit
    # actions whenever any non-scroll alternative existed. Rocks/dirt yield
    # nothing, so that tour was pure waste.
    board = [
        [".", ".", ".", ".", ".", "."],
        ["R", "R", ".", ".", ".", "R"],
        [".", "R", ".", "R", ".", "."],
        [".", ".", ".", "R", ".", "."],
        [".", "D", ".", ".", "D", "D"],
        ["one_hit_rock", "unreachable_dirt", "R", ".", "D", "unreachable_dirt"],
        ["unreachable_rock", "unreachable_dirt", "unreachable_pit",
         "reachable_pit", "unreachable_rock", "unreachable_dirt"],
    ]
    label_map = {".": "empty", "R": "rock", "D": "dirt"}
    board = [[label_map.get(cell, cell) for cell in row] for row in board]

    plan = plan_v3(board, shovels=40, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    stats = plan["stats"]
    # Optimal is 2 shovels: dig(6,3) promotes (6,2) → dig(6,2).
    # Anything up to ~6 shovels is acceptable; >10 means the old waste-tour bug.
    assert stats["pits_collected"] >= 1, plan
    assert stats["shovel_cost"] <= 6.0, (
        f"expected ≤6 shovels for 2 row-6 pits, got {stats['shovel_cost']}; "
        f"rocks/dirt give 0 reward so burning them is pure waste"
    )


def test_plan_v3_still_blocks_row6_non_pit_dig_when_upper_pit_remains():
    # Counterpart to the relaxation: dig(6,x) on a dirt cell must still be
    # filtered when a reachable pit lives above row 6 — scrolling would
    # trash the upper pit for no pit reward.
    board = [["empty"] * 6 for _ in range(7)]
    board[0][0] = "reachable_pit"
    board[6][3] = "dirt"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    for step in plan["steps"]:
        if step["type"] == "dig":
            r, _ = step["pos"]
            assert r != 6, f"non-pit row-6 dig allowed with upper pit remaining: {step}"


def test_plan_v3_row0_connected_pit_dug_directly():
    # r=0 connected pit: CNN label is `reachable_pit`, directly adjacent to
    # reachable air. Plan should dig it for cost=1, no wall-removal needed.
    board = [["empty"] * 6 for _ in range(7)]
    board[0][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 1
    assert plan["stats"]["shovel_cost"] == 1.0, f"direct row-0 pit dig should cost 1, got {plan['stats']['shovel_cost']}"
    first = plan["steps"][0]
    assert first["type"] == "dig"
    assert tuple(first["pos"]) == (0, 3)


def test_plan_v3_row0_unconnected_pit_requires_wall_dig():
    # r=0 UNCONNECTED pit: CNN label is `unreachable_pit`, walled in by
    # unreachable_rocks (CNN thinks those rocks are also unreachable). The pit
    # is only diggable after the wall is dug from row 2 (where reachable air
    # exists). Using "rock" (reachable) would short-circuit — the planner
    # would just dig the top-row rock directly since reachable rocks are
    # always frontier_diggable.
    board = [["unreachable_rock"] * 6, ["unreachable_rock"] * 6] + [["empty"] * 6 for _ in range(5)]
    board[0][3] = "unreachable_pit"

    # (0,3) has no reachable_air neighbor in row 0 (all unreachable_rock) and
    # (1,3) is also unreachable → (0,3) is NOT frontier_diggable.
    assert is_frontier_diggable(board, 0, 3) is False
    # But (1,3) IS frontier_diggable because (2,3) is reachable_air.
    assert is_frontier_diggable(board, 1, 3) is True

    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 1, plan
    # Wall dig (1,3)=unreachable_rock cost 2 → promote (0,3) to reachable_pit.
    # Then dig (0,3)=* cost 1. Total 3.
    assert plan["stats"]["shovel_cost"] == 3.0, (
        f"wall-dig then pit-dig should cost 3, got {plan['stats']['shovel_cost']}"
    )
    dug_positions = [tuple(step["pos"]) for step in plan["steps"] if step["type"] == "dig"]
    assert (1, 3) in dug_positions, f"expected wall dig at (1,3); got {dug_positions}"
    assert (0, 3) in dug_positions
    # Wall must be dug BEFORE the pit — order matters for promote_after_dig.
    assert dug_positions.index((1, 3)) < dug_positions.index((0, 3))


def test_plan_v3_row0_pit_collected_before_row6_pit_to_avoid_scroll_loss():
    # Mixed: r=0 reachable pit + r=6 reachable pit. Digging (6,x) scrolls and
    # would lose the row-0 pit. Planner must dig row 0 first.
    board = [["empty"] * 6 for _ in range(7)]
    board[0][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 2
    dug_order = [tuple(step["pos"]) for step in plan["steps"] if step["type"] == "dig"]
    assert dug_order.index((0, 2)) < dug_order.index((6, 3)), (
        f"row-0 pit must be dug before row-6 pit; got {dug_order}"
    )


def test_plan_v3_row0_unconnected_pit_still_dug_when_row6_pit_exists():
    # Harder: (0,3)=X is walled in, (6,3)=* is reachable. The cheapest plan
    # could just dig(6,3) alone (cost 1) and let (0,3) be lost to scroll. But
    # the planner's design is "never give up a pit" (unreachable_pit_max_extra
    # _cost=inf), so it must dig the wall + pit + row-6 pit = 4 shovels.
    board = [["unreachable_rock"] * 6, ["unreachable_rock"] * 6] + [["empty"] * 6 for _ in range(5)]
    board[0][3] = "unreachable_pit"
    board[6][3] = "reachable_pit"

    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    # Both pits should be targeted by the plan (design: never abandon pits).
    assert plan["stats"]["pits_collected"] == 2, plan
    dug_positions = {tuple(step["pos"]) for step in plan["steps"] if step["type"] == "dig"}
    assert (0, 3) in dug_positions, f"buried row-0 pit must be collected, got {dug_positions}"
    assert (6, 3) in dug_positions


def test_plan_v3_multi_row6_pits_single_dig_when_no_item():
    # 3 pits in row 6, shovel only. With my fix, planner dives into row-6 pit
    # directly (priority beats any rock dig). Executor will scroll after the
    # first row-6 dig anyway, so any plan is "1 pit for 1 shovel" in reality.
    board = [["empty"] * 6 for _ in range(7)]
    board[6][1] = "reachable_pit"
    board[6][3] = "reachable_pit"
    board[6][4] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    # Planner should collect all 3 in abstract simulation for cost=3.
    assert plan["stats"]["pits_collected"] == 3
    assert plan["stats"]["shovel_cost"] == 3.0
    # All dig actions must be on pit cells (no wasted rock/dirt digging).
    for step in plan["steps"]:
        if step["type"] == "dig":
            r, c = step["pos"]
            assert board[r][c] == "reachable_pit", f"non-pit dig wastes shovels: {step}"


def test_plan_v3_bomb_prefers_2x2_pit_cluster_in_row5_6():
    # 2×2 pit cluster straddling row 5-6. Bomb (cost 3) collects all 4 in one
    # placement vs 4 shovels for individual digs. Must pick bomb.
    board = [["empty"] * 6 for _ in range(7)]
    board[5][2] = "reachable_pit"
    board[5][3] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    assert any(step.get("item") == "bomb" for step in plan["steps"]), (
        f"must use bomb on 2×2 row5-6 cluster, got {plan['steps']}"
    )
    assert plan["stats"]["bombs_used"] == 1


def test_plan_v3_bomb_used_on_3x3_cluster_to_save_shovels():
    # 3×3 cluster = 9 pits. Bomb can't be placed ON a pit (must be on
    # reachable_air), so it can't cover all 9 with one blast. Best placement
    # (e.g., bomb at (1,2) adjacent to the cluster top) hits 4 pits. Planner
    # should still use bomb + cleanup digs, saving shovels vs pure shovel plan.
    board = [["empty"] * 6 for _ in range(7)]
    for r in range(2, 5):
        for c in range(1, 4):
            board[r][c] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    assert plan["stats"]["bombs_used"] == 1, f"should use bomb on 3×3 cluster, got {plan['stats']}"
    # Bomb (cost 3) + remaining digs < pure shovel (9) — at least save 1.
    assert plan["stats"]["shovel_cost"] < 9.0, (
        f"bomb plan should beat 9-shovel baseline, got {plan['stats']['shovel_cost']}"
    )


def test_plan_v3_single_pit_prefers_shovel_over_bomb():
    # One isolated pit. Don't waste a bomb (cost 3) when shovel (cost 1) does.
    board = [["empty"] * 6 for _ in range(7)]
    board[3][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    assert plan["stats"]["bombs_used"] == 0, f"bomb wasted on single pit: {plan}"
    assert plan["stats"]["shovel_cost"] == 1.0


def test_plan_v3_no_pit_strategy_digs_row6_to_scroll():
    # No pits. Goal switches to `no_pit` mode: open floor7 with minimum cost.
    # Row 6 is fully hard cells so the planner HAS to dig one to open floor7.
    board = [["empty"] * 6 for _ in range(6)] + [["rock"] * 6]
    board[6][2] = "dirt"  # cheapest floor7 opener
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    # Floor7 should end up open.
    assert plan["floor7_open"] is True
    # Minimum cost path uses the dirt cell (cost 1), not a rock (cost 2).
    assert plan["stats"]["shovel_cost"] == 1.0, plan["stats"]
    first = plan["steps"][0]
    assert first["type"] == "dig"
    assert tuple(first["pos"]) == (6, 2), f"should pick cheapest dirt, got {first}"


def test_plan_v3_already_open_floor7_no_pit_no_digging():
    # Floor7 already open and no pits → goal met at start, empty plan.
    board = [["empty"] * 6 for _ in range(7)]
    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "no_pit"
    assert plan["stats"]["pits_collected"] == 0
    assert plan["stats"]["shovel_cost"] == 0.0
    assert plan["steps"] == []


def test_plan_v3_row0_promoted_pit_from_row1_dig_propagates_through_cluster():
    # Row-0 unreachable pit chain: (0,2)(0,3)(0,4) all = unreachable_pit,
    # walled in by unreachable_empty pocket, with row 1 also walled. Digging
    # (1,3)=rock from row-2 air should flood the unreachable_empty and expose
    # all 3 row-0 pits via promote_after_dig.
    board = [["unreachable_empty"] * 6, ["rock"] * 6] + [["empty"] * 6 for _ in range(5)]
    board[0][2] = "unreachable_pit"
    board[0][3] = "unreachable_pit"
    board[0][4] = "unreachable_pit"

    plan = plan_v3(board, shovels=30, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 3, plan
    # Wall dig at row 1 must happen before any row-0 pit dig.
    dug_order = [tuple(step["pos"]) for step in plan["steps"] if step["type"] == "dig"]
    first_wall_dig = next((i for i, p in enumerate(dug_order) if p[0] == 1), None)
    first_pit_dig = next((i for i, p in enumerate(dug_order) if p[0] == 0), None)
    assert first_wall_dig is not None and first_pit_dig is not None
    assert first_wall_dig < first_pit_dig


def test_plan_v3_drill_option_available_for_vertical_pits():
    # Drill affects column-down + bottom row. For a 1×3 vertical pit stack,
    # drill and 3 shovels both cost 3. Either choice is acceptable — just
    # assert the plan collects all pits without absurd overspend.
    board = [["empty"] * 6 for _ in range(7)]
    board[3][3] = "reachable_pit"
    board[4][3] = "reachable_pit"
    board[5][3] = "reachable_pit"
    plan = plan_v3(board, shovels=20, items={"drill": 1, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_remaining"] == 0
    # shovel_cost already includes drill's 3.0 per use (apply_drill returns 3.0).
    # 3 pits should cost at most ~3 total, plus zero wasted steps.
    assert plan["stats"]["shovel_cost"] <= 3.0, plan["stats"]


def test_plan_v3_cost_budget_truncates_deep_plans():
    # Cost budget = max(shovels*2, shovels+6). With shovels=5, budget=11.
    # Put 8 reachable pits — optimal plan would cost 8 (>11? No, 8<11, so full
    # plan). Drop shovels to 3 (budget=9) and add 10 pits → plan should still
    # try (partial is allowed) but not exceed budget by >2x.
    board = [["empty"] * 6 for _ in range(7)]
    # 10 pits to force > budget search
    positions = [(r, c) for r in range(1, 6) for c in range(0, 6, 3)]  # 10 cells
    for r, c in positions:
        board[r][c] = "reachable_pit"
    plan = plan_v3(board, shovels=3, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    # budget = max(6, 9) = 9. Plan cost should not exceed it by much.
    assert plan["stats"]["shovel_cost"] <= 9.0, (
        f"plan cost {plan['stats']['shovel_cost']} exceeded budget"
    )


def test_plan_v3_all_pits_unreachable_still_produces_plan():
    # All 3 pits are `unreachable_pit`, forming an interior pocket walled off
    # by unreachable_rock. Planner should still produce a plan that at least
    # tries to dig toward them (even if `partial`).
    board = [["unreachable_rock"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    board[0][1] = "empty"
    board[3][3] = "unreachable_pit"
    board[3][4] = "unreachable_pit"
    board[4][3] = "unreachable_pit"
    plan = plan_v3(board, shovels=30, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["steps"], "must produce at least one step even for buried pits"
    # Progress should be tracked in stats.
    stats = plan["stats"]
    assert "pits_unreachable_remaining" in stats


def test_plan_v3_blocked_action_signatures_are_respected():
    # Simulates mining_service blacklisting a specific action after a
    # `NoBoardChangeError`. The planner must avoid repeating it.
    board = [["empty"] * 6 for _ in range(7)]
    board[3][3] = "reachable_pit"
    blocked = {("dig", None, (3, 3))}
    plan = plan_v3(
        board,
        shovels=20,
        items={"drill": 0, "bomb": 0},
        blocked_actions=blocked,
    )
    # With the only pit action blocked, planner can't collect — partial at best.
    for step in plan.get("steps", []):
        if step["type"] == "dig":
            assert tuple(step["pos"]) != (3, 3), (
                f"planner used blocked action: {step}"
            )


def test_plan_v3_row6_pit_with_unreachable_pit_neighbor():
    # Edge case from user's 01:11:13 log: (6,2)=X unreachable, (6,3)=*
    # reachable. Both have reachable_air neighbors in row 5, so both are
    # directly frontier_diggable even though (6,2) carries the `unreachable_`
    # prefix. Plan should collect both for cost 2 regardless of order.
    board = [["empty"] * 6 for _ in range(7)]
    board[6][2] = "unreachable_pit"
    board[6][3] = "reachable_pit"

    # Both diggable via row-5 air neighbors.
    assert is_frontier_diggable(board, 6, 2) is True
    assert is_frontier_diggable(board, 6, 3) is True

    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 2
    assert plan["stats"]["shovel_cost"] == 2.0
    dig_positions = [tuple(s["pos"]) for s in plan["steps"] if s["type"] == "dig"]
    assert sorted(dig_positions) == [(6, 2), (6, 3)], dig_positions


def test_plan_v3_row6_pit_when_only_promoted_via_neighbor_dig():
    # Stricter variant: (6,2)=unreachable_pit has NO direct reachable_air
    # neighbor — (5,2)=rock blocks it, (6,1) and (6,3) are hard cells too.
    # Only after digging some neighbor does (6,2) become accessible.
    board = [["empty"] * 6 for _ in range(5)] + [["rock"] * 6, ["dirt"] * 6]
    board[6][2] = "unreachable_pit"
    # (6,2)'s neighbors: (5,2)=rock, (6,1)=dirt, (6,3)=dirt — none reachable_air.
    assert is_frontier_diggable(board, 6, 2) is False
    # But (5,2)=rock IS frontier_diggable via (4,2)=empty.
    assert is_frontier_diggable(board, 5, 2) is True

    plan = plan_v3(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert plan["ok"] is True
    assert plan["stats"]["pits_collected"] == 1
    dug_order = [tuple(s["pos"]) for s in plan["steps"] if s["type"] == "dig"]
    # Must dig a wall (row 5 or row 6 non-pit) before the pit itself.
    pit_idx = dug_order.index((6, 2))
    assert pit_idx > 0, f"(6,2) can't be first — needs wall-dig first: {dug_order}"


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
