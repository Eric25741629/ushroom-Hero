from miner.v2.planner import (
    apply_bomb,
    apply_drill,
    build_analysis,
    classify_strategy,
    normalize_board,
    plan_v2,
    top_row_pit_count,
    update_exposure,
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
