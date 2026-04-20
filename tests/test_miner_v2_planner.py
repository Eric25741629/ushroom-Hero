from miner.v2.planner import (
    apply_bomb,
    apply_dig,
    apply_drill,
    build_analysis,
    classify_strategy,
    is_frontier_diggable,
    normalize_board,
    plan_v2,
    promote_after_dig,
    top_row_pit_count,
    update_exposure,
)


def _step_signature(step):
    pos = step.get("pos")
    target = step.get("target")
    if isinstance(pos, list):
        pos = tuple(pos)
    if isinstance(target, list):
        target = tuple(target)
    return (
        step.get("type"),
        step.get("item"),
        pos,
        target,
        step.get("action"),
    )


def test_normalize_board_converts_legacy_pit():
    board = [["pit", "dug_pit"], ["empty", "rock"]]
    normalized = normalize_board(board)
    assert normalized[0][0] == "reachable_pit"
    assert normalized[0][1] == "dug_pit"


def test_void_is_canonicalized_to_empty_after_exposure():
    board = [["void", "empty"], ["unreachable_empty", "rock"]]
    update_exposure(board)
    assert board[0][0] == "empty"


def test_classify_strategy_counts_unreachable_pit_as_has_pit():
    board = [
        ["unreachable_pit", "empty"],
        ["dirt", "rock"],
    ]
    assert classify_strategy(normalize_board(board)) == "has_pit"


def test_top_row_pit_count_detects_y0_pits():
    board = [["reachable_pit", "empty", "unreachable_pit"], ["empty", "empty", "empty"]]
    assert top_row_pit_count(board) == 2


def test_build_analysis_marks_exit_guard_when_pits_remain():
    board = [["reachable_pit", "empty"], ["empty", "empty"]]
    analysis = build_analysis(board)
    assert analysis["remaining_pits"] == 1
    assert analysis["top_row_pits"] == 1
    assert analysis["exit_guard_required"] is True


def test_apply_bomb_marks_offscreen_floor7_when_blast_reaches_below_board():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    cost, offscreen, hits = apply_bomb(board, (6, 2))
    assert cost == 3.0
    assert offscreen is True
    assert hits > 0


def test_apply_drill_only_affects_visible_cells():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[6][2] = "rock"
    cost = apply_drill(board, (5, 2))
    assert cost == 3.0
    assert board[6][2] == "empty"


def test_plan_v2_no_pit_uses_floor7_strategy():
    board = [
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
        ["empty", "dirt", "rock", "empty", "empty", "empty"],
    ]
    result = plan_v2(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert result["ok"] is True
    assert result["strategy_class"] == "no_pit"
    assert result["floor7_open"] is True
    assert result["exit_guard_required"] is False


def test_plan_v2_collects_simple_reachable_pit():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[3][2] = "reachable_pit"
    update_exposure(board)
    result = plan_v2(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert result["ok"] is True
    assert result["strategy_class"] == "has_pit"
    assert result["remaining_pits"] == 0
    assert result["exit_guard_required"] is False
    assert result["steps"]


def test_plan_v2_no_pit_can_use_drill_in_main_search():
    board = [["unreachable_rock" for _ in range(6)] for _ in range(7)]
    board[0][2] = "empty"
    update_exposure(board)

    result = plan_v2(board, shovels=6, items={"drill": 1, "bomb": 0})

    assert result["ok"] is True
    assert result["strategy_class"] == "no_pit"
    assert result["floor7_open"] is True
    assert any(step.get("item") == "drill" for step in result["steps"])


def test_plan_v2_can_dig_lower_then_bomb_to_open_floor7():
    board = [["unreachable_rock" for _ in range(6)] for _ in range(7)]
    board[0][2] = "empty"
    for row in range(1, 5):
        board[row][2] = "unreachable_dirt"
    for row in (5, 6):
        board[row][2] = "unreachable_rock"
    update_exposure(board)

    result = plan_v2(board, shovels=7, items={"drill": 0, "bomb": 1})

    assert result["ok"] is True
    assert result["strategy_class"] == "no_pit"
    assert result["floor7_open"] is True
    assert result["steps"][-1].get("item") == "bomb"
    assert any(step["type"] == "dig" for step in result["steps"][:-1])


def test_plan_v2_top_row_pit_beats_item_opportunity():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[0][0] = "reachable_pit"
    board[6][1] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"
    update_exposure(board)

    result = plan_v2(board, shovels=20, items={"drill": 1, "bomb": 1})

    assert result["ok"] is True
    assert result["strategy_class"] == "has_pit"
    assert result["steps"]
    first = result["steps"][0]
    assert first["type"] == "dig"
    assert tuple(first["pos"]) == (0, 0)
    assert result["remaining_pits"] == 0


def test_plan_v2_has_pit_budget_limited_returns_partial_plan():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[3][2] = "reachable_pit"
    board[4][2] = "reachable_pit"

    result = plan_v2(board, shovels=1, items={"drill": 0, "bomb": 0})

    assert result["ok"] is True
    assert result["strategy_class"] == "has_pit"
    assert result.get("partial") is True
    assert result["remaining_pits"] == 1
    assert result["exit_guard_required"] is True
    assert result["steps"]


def test_update_exposure_does_not_promote_isolated_unreachable_pockets():
    """CNN ground truth: an unreachable_empty pocket inside the board should
    stay unreachable until a dig opens it — even if other reachable empties
    exist elsewhere."""
    board = [
        ["empty", "rock", "unreachable_empty", "unreachable_pit"],
        ["empty", "rock", "unreachable_empty", "unreachable_empty"],
    ]
    update_exposure(board)
    assert board[0][2] == "unreachable_empty"
    assert board[0][3] == "unreachable_pit"
    assert board[1][2] == "unreachable_empty"


def test_update_exposure_preserves_top_row_isolated_air():
    """Top row r=0 with an `empty` next to a wall must NOT auto-expose neighbours.
    Reachability is the CNN's call, not the planner's."""
    board = [
        ["unreachable_rock", "unreachable_rock", "empty", "unreachable_rock"],
        ["unreachable_rock", "unreachable_rock", "unreachable_dirt", "unreachable_rock"],
    ]
    update_exposure(board)
    # Adjacent rocks should NOT be stripped of unreachable_ at plan start.
    assert board[0][1] == "unreachable_rock"
    assert board[0][3] == "unreachable_rock"
    # Frontier-dig allows them to be tried though.
    assert is_frontier_diggable(board, 0, 1) is True
    assert is_frontier_diggable(board, 0, 3) is True
    # But the deeper interior cell is NOT diggable.
    assert is_frontier_diggable(board, 1, 1) is False


def test_promote_after_dig_floods_unreachable_empty_chain():
    """Once a dig opens a wall, an adjacent unreachable_empty pocket becomes
    fully empty (the entire pocket flips), and adjacent hard cells lose their
    unreachable_ prefix."""
    board = [
        ["empty", "dirt", "unreachable_empty", "unreachable_empty", "unreachable_pit"],
        ["unreachable_rock", "unreachable_rock", "unreachable_empty", "unreachable_rock", "unreachable_rock"],
    ]
    apply_dig(board, (0, 1))  # dig the dirt wall
    assert board[0][1] == "empty"
    assert board[0][2] == "empty"  # pocket flipped
    assert board[0][3] == "empty"
    assert board[1][2] == "empty"
    assert board[0][4] == "reachable_pit"  # boundary pit promoted
    # But pockets disconnected from the dug cell stay unreachable.
    assert board[1][0] == "unreachable_rock"


def test_plan_v2_does_not_target_isolated_empty():
    """A pocket of `empty` cells that is NOT connected to any reachable cell
    should not lure the planner into digging the wall around it."""
    board = [["unreachable_rock" for _ in range(6)] for _ in range(7)]
    # genuine reachable empty at top
    board[0][0] = "empty"
    # an isolated empty pocket the planner used to chase
    board[3][3] = "unreachable_empty"
    board[3][4] = "unreachable_empty"

    result = plan_v2(board, shovels=10, items={"drill": 0, "bomb": 0})
    assert result["ok"] is True
    # Plan must not include any dig that targets the isolated pocket itself
    # (those cells are air and not diggable anyway), but more importantly
    # the planner must not chain into them via the wall above.
    for step in result["steps"]:
        if step["type"] != "dig":
            continue
        r, c = step["pos"]
        # Anything in row >= 2 col 2..5 is interior unreachable — should not be targeted.
        assert not (r >= 2 and 2 <= c <= 5), f"planner reached interior cell {(r,c)}"


def test_plan_v2_does_not_trigger_floor7_when_pits_remain():
    """硬約束：頂層或任何 pit 未清前，計畫不能含「打通 row 6」的步驟，
    否則一捲動 row 0 礦永久丟失。"""
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[0][0] = "reachable_pit"
    # 在 row 6 留一個容易被踩的 dirt 入口
    board[6][3] = "dirt"
    update_exposure(board)

    result = plan_v2(board, shovels=20, items={"drill": 0, "bomb": 0})
    assert result["ok"] is True
    # 任何步驟都不能是 row 6 的 dig（會打通 row 6）
    for step in result["steps"]:
        if step["type"] == "dig":
            r, _ = step["pos"]
            assert r != 6, f"plan dug row 6 while pit at (0,0) remains: {step}"


def test_plan_v2_uses_bomb_to_hit_2x2_cluster():
    """道具效益最大化：2x2 礦群應該選擇炸彈一發吃整群，而非逐格挖。"""
    board = [["empty" for _ in range(6)] for _ in range(7)]
    # 2x2 礦群在中下方
    board[4][2] = "reachable_pit"
    board[4][3] = "reachable_pit"
    board[5][2] = "reachable_pit"
    board[5][3] = "reachable_pit"
    update_exposure(board)

    result = plan_v2(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert result["ok"] is True
    assert result["remaining_pits"] == 0
    # 炸彈應該被使用，而非全部用鎬
    assert any(step.get("item") == "bomb" for step in result["steps"]), \
        f"plan did not use bomb on a 2x2 cluster: {result['steps']}"


def test_plan_v2_can_return_second_best_first_step_when_blocked():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[3][1] = "reachable_pit"
    board[3][4] = "reachable_pit"
    update_exposure(board)

    first_result = plan_v2(board, shovels=10, items={"drill": 0, "bomb": 0})
    assert first_result["ok"] is True
    assert first_result["steps"]

    blocked = {_step_signature(first_result["steps"][0])}
    second_result = plan_v2(
        board,
        shovels=10,
        items={"drill": 0, "bomb": 0},
        blocked_first_steps=blocked,
    )

    assert second_result["ok"] is True
    assert second_result["steps"]
    assert _step_signature(second_result["steps"][0]) not in blocked
