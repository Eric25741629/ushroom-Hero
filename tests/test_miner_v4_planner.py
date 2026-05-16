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

from miner.v4.planner import plan_v4, _unseal_corridor
from miner.v3.board import canonicalize_in_place, normalize_board


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


# ---------------------------------------------------------------------------
# Deeply buried unreachable pit — must still produce a non-empty plan
# (2026-05-15: empty `steps` on this shape were causing emulator-5554 to
# abort mining via the consecutive-empty-plans guard while still holding
# full shovels).
# ---------------------------------------------------------------------------


def test_plan_v4_5560_deeply_buried_pit_still_makes_progress():
    """REGRESSION: production board where v4 used to return 0 steps.

    Captured 2026-05-02 from emulator-5560 logs (also 5556, 5554).
    Board legend (mining_service.get_visual_board):
          0  1  2  3  4  5
       0  d  d  R  .  D  d
       1  d  r  D  .  D  d
       2  d  d  D  .  D  R
       3  d  d  R  .  .  .
       4  d  _  r  R  .  .
       5  X  r  d  D  .  D    ← X = unreachable_pit at (5, 0)
       6  d  d  _  d  D  _

    The pit at (5, 0) is sealed behind unreachable rock with no diggable
    approach within Manhattan-3, and no item placement on the reachable
    column-3 air can hit it. Before 2026-05-15 this combination made
    `_filter_actions(has_pit)` return [], the DFS immediately exit, and
    `plan["steps"]` come back empty — the runtime then aborted mining via
    the consecutive-empty-plans guard.

    Fix: `_filter_actions(has_pit)` now falls through to the no_pit dig
    filter when its Manhattan filter rejects every action, and the
    anti-scroll guard only triggers when a *reachable* pit remains. The
    planner must produce at least one productive step that makes scroll
    or pit-collection progress.
    """
    board = [
        ["unreachable_dirt", "unreachable_dirt", "rock", "empty", "dirt", "unreachable_dirt"],
        ["unreachable_dirt", "unreachable_rock", "dirt", "empty", "dirt", "unreachable_dirt"],
        ["unreachable_dirt", "unreachable_dirt", "dirt", "empty", "dirt", "rock"],
        ["unreachable_dirt", "unreachable_dirt", "rock", "empty", "empty", "empty"],
        ["unreachable_dirt", "unreachable_empty", "unreachable_rock", "rock", "empty", "empty"],
        ["unreachable_pit", "unreachable_rock", "unreachable_dirt", "dirt", "empty", "dirt"],
        ["unreachable_dirt", "unreachable_dirt", "unreachable_empty", "unreachable_dirt", "dirt", "unreachable_empty"],
    ]
    plan = plan_v4(board, shovels=100, items={"drill": 694, "bomb": 694})
    assert plan["ok"] is True
    assert plan["strategy_class"] == "has_pit"
    # The cardinal rule: never return an empty action set when productive
    # digs exist on the board.
    assert plan["steps"], (
        f"planner must produce at least one step, got: {plan}"
    )
    # The plan must make some kind of progress — either collect the pit or
    # open floor 7 so the board scrolls past the unreachable pocket.
    final_f7_open = plan["floor7_open"]
    pits_collected = plan["stats"]["pits_collected"]
    assert final_f7_open or pits_collected > 0, (
        f"plan must collect a pit or open floor 7 — got "
        f"pits_collected={pits_collected}, floor7_open={final_f7_open}: {plan}"
    )


def test_plan_v4_isolated_unreachable_pit_2026_05_15_makes_progress():
    """REGRESSION: 2026-05-15 emulator-5554 board where v4 returned 0
    steps for ~3 iterations until the consecutive-empty-plans guard
    aborted mining while still holding 46 shovels.

    Board (mining_service.get_visual_board):
          0  1  2  3  4  5
       0  D  .  D  _  d  d
       1  D  .  .  R  r  _
       2  R  .  R  d  r  d
       3  .  .  D  d  r  r
       4  .  D  r  _  d  d
       5  .  D  d  _  d  _
       6  D  r  d  d  r  X    ← X = unreachable_pit at (6, 5)

    Same failure mode as the 5560 board: pit sealed behind unreachable
    rock with no diggable Manhattan-3 approach and no item hit possible.
    Must produce a non-empty plan after the 2026-05-15 fix.
    """
    board = [
        ["dirt", "empty", "dirt", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["dirt", "empty", "empty", "rock", "unreachable_rock", "unreachable_empty"],
        ["rock", "empty", "rock", "unreachable_dirt", "unreachable_rock", "unreachable_dirt"],
        ["empty", "empty", "dirt", "unreachable_dirt", "unreachable_rock", "unreachable_rock"],
        ["empty", "dirt", "unreachable_rock", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["empty", "dirt", "unreachable_dirt", "unreachable_empty", "unreachable_dirt", "unreachable_empty"],
        ["dirt", "unreachable_rock", "unreachable_dirt", "unreachable_dirt", "unreachable_rock", "unreachable_pit"],
    ]
    plan = plan_v4(board, shovels=46, items={"drill": 185, "bomb": 889})
    assert plan["ok"] is True
    assert plan["steps"], f"planner must produce a non-empty plan, got: {plan}"
    pits_collected = plan["stats"]["pits_collected"]
    assert plan["floor7_open"] or pits_collected > 0, (
        f"plan must collect a pit or open floor 7 — got "
        f"pits_collected={pits_collected}, floor7_open={plan['floor7_open']}: {plan}"
    )


# ---------------------------------------------------------------------------
# Reverse-search corridor: when has_pit Manhattan filter is empty, the
# planner builds an "unseal corridor" via reverse Dijkstra from each
# unreachable pit and lets the DFS pick the optimal mix of digs and
# drill / bomb shortcuts. These tests pin both the corridor builder and
# the optimality of the resulting plan.
# ---------------------------------------------------------------------------


def _prep(board):
    work = normalize_board(board)
    canonicalize_in_place(work)
    return work


def test_unseal_corridor_empty_when_no_unreachable_pit():
    board = _make_board()
    board[3][3] = "reachable_pit"
    corridor = _unseal_corridor(_prep(board), shovels_budget=100)
    assert corridor == frozenset()


def test_unseal_corridor_empty_when_no_reachable_air_destination():
    """Without any reachable air on the board there's nothing for the
    reverse search to anchor to — corridor must be empty (and the
    runtime falls back to no_pit scroll progress)."""
    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[3][3] = "unreachable_pit"
    corridor = _unseal_corridor(_prep(board), shovels_budget=100)
    assert corridor == frozenset()


def test_unseal_corridor_finds_simplest_one_dig_path():
    """Wall of one dirt between reachable air and an unreachable_pit.
    Corridor must contain the wall cell (so DFS can dig it) and the
    pit itself."""
    board = [["empty"] * 6 for _ in range(7)]
    # Reachable air at (3,0). Wall at (3,1)=dirt. Pit at (3,2).
    board[3] = ["empty", "dirt", "unreachable_pit", "empty", "empty", "empty"]
    # Surround the pit with non-air so the only path is through (3,1).
    for r in (2, 4):
        for c in (1, 2, 3):
            board[r][c] = "unreachable_rock"
    board[3][3] = "unreachable_rock"
    corridor = _unseal_corridor(_prep(board), shovels_budget=10)
    assert (3, 1) in corridor, f"corridor missing wall dig cell: {sorted(corridor)}"
    assert (3, 2) in corridor, "corridor must include the pit itself"


def test_unseal_corridor_respects_shovel_budget():
    """Path costs more than the shovel budget → corridor must be empty
    (Dijkstra prunes at budget). Without this, the DFS would propose
    a plan that the executor will run out of shovels on."""
    board = [["unreachable_rock"] * 6 for _ in range(7)]
    board[0][0] = "empty"           # reachable air
    board[6][5] = "unreachable_pit"  # pit on the far corner
    # Every cell between is rock (cost 2). Min path cost ≫ 3.
    corridor = _unseal_corridor(_prep(board), shovels_budget=3)
    assert corridor == frozenset()


def test_plan_v4_corridor_drill_beats_pure_dig_when_items_available():
    """Optimality check: on the 2026-05-15 board, the cheapest plan that
    collects the pit is `drill (column 2) + 2 digs`, not 4–5 shovel digs.
    The corridor exposes (5,4)/(5,5)/(6,3)/(6,5) etc, the DFS finds a
    drill placement whose footprint overlaps the corridor, and the
    cluster scoring picks it because shovel cost beats raw dig count."""
    board = [
        ["dirt", "empty", "dirt", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["dirt", "empty", "empty", "rock", "unreachable_rock", "unreachable_empty"],
        ["rock", "empty", "rock", "unreachable_dirt", "unreachable_rock", "unreachable_dirt"],
        ["empty", "empty", "dirt", "unreachable_dirt", "unreachable_rock", "unreachable_rock"],
        ["empty", "dirt", "unreachable_rock", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["empty", "dirt", "unreachable_dirt", "unreachable_empty", "unreachable_dirt", "unreachable_empty"],
        ["dirt", "unreachable_rock", "unreachable_dirt", "unreachable_dirt", "unreachable_rock", "unreachable_pit"],
    ]
    plan = plan_v4(board, shovels=46, items={"drill": 185, "bomb": 889})
    assert plan["stats"]["pits_collected"] == 1, (
        f"corridor plan must collect the buried pit: {plan}"
    )
    # The optimal plan uses at most ~5 shovels — without items, raw shovel
    # paths to the pit cost 6+ (rock walls). A non-trivial improvement
    # proves the DFS actually picked an item shortcut.
    assert plan["stats"]["shovel_cost"] <= 5.0, (
        f"plan should be shovel-efficient (≤5), got {plan['stats']['shovel_cost']}: {plan}"
    )
    assert (
        plan["stats"]["drills_used"] >= 1 or plan["stats"]["bombs_used"] >= 1
    ), f"plan should use an item shortcut on this board: {plan}"


def test_plan_v4_corridor_pure_shovel_path_when_no_items():
    """Same isolated-pit shape but no items available — DFS must still
    collect the pit, just via a longer sequence of digs. Pin that the
    corridor mechanism doesn't quietly assume items exist."""
    board = [
        ["dirt", "empty", "dirt", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["dirt", "empty", "empty", "rock", "unreachable_rock", "unreachable_empty"],
        ["rock", "empty", "rock", "unreachable_dirt", "unreachable_rock", "unreachable_dirt"],
        ["empty", "empty", "dirt", "unreachable_dirt", "unreachable_rock", "unreachable_rock"],
        ["empty", "dirt", "unreachable_rock", "unreachable_empty", "unreachable_dirt", "unreachable_dirt"],
        ["empty", "dirt", "unreachable_dirt", "unreachable_empty", "unreachable_dirt", "unreachable_empty"],
        ["dirt", "unreachable_rock", "unreachable_dirt", "unreachable_dirt", "unreachable_rock", "unreachable_pit"],
    ]
    plan = plan_v4(board, shovels=46, items={"drill": 0, "bomb": 0})
    assert plan["steps"], f"non-empty plan required even without items: {plan}"
    # Either we make scroll progress (open floor 7 to push past) or we
    # actually carve through to the pit within the depth budget. The
    # rolling re-plan in mining_service finishes the job across
    # iterations; this single-pass call doesn't have to.
    assert (
        plan["floor7_open"] or plan["stats"]["pits_collected"] > 0
    ), f"plan must collect pit or open floor 7: {plan}"


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
