"""final_v1 planner behavior: inventory, visibility, legality, determinism, timeout."""
import time

from miner.final_v1 import plan_final_v1


def _board(rows=7):
    out = [["unreachable_dirt"] * 6 for _ in range(rows)]
    out[0][2] = "empty"
    out[1][2] = "dirt"
    return out


def test_zero_pickaxes_can_still_return_a_valuable_item_action():
    board = _board()
    board[1][1] = "reachable_pit"
    result = plan_final_v1(board, 0, {"bomb": 1, "drill": 0})
    assert result["steps"]
    assert result["steps"][0]["type"] == "use"


def test_known_offscreen_pit_can_raise_bomb_value_but_not_drill_or_pickaxe_value():
    board = _board(rows=10)
    board[6][2] = "empty"
    board[7][2] = "unreachable_pit"
    valid = {("use", "bomb", 6, 2), ("use", "drill", 6, 2), ("dig", "pickaxe", 1, 2)}
    bomb = plan_final_v1(board, 20, {"bomb": 1, "drill": 0}, visible_rows=7, valid_targets=valid)
    drill = plan_final_v1(board, 20, {"bomb": 0, "drill": 1}, visible_rows=7, valid_targets=valid)
    assert bomb["score_breakdown"]["pit_gain"] > drill["score_breakdown"]["pit_gain"]


def test_first_step_is_in_action_aware_valid_targets():
    board = _board()
    valid = {("dig", "pickaxe", 1, 2)}
    result = plan_final_v1(board, 10, {"bomb": 5, "drill": 5}, valid_targets=valid)
    step = result["steps"][0]
    assert (step["type"], step.get("item", "pickaxe"), *step["target"]) in valid


def test_deeper_preview_may_leave_visible_rows_but_emitted_step_may_not():
    board = _board(rows=12)
    board[9][2] = "unreachable_pit"
    result = plan_final_v1(board, 40, {"bomb": 2, "drill": 2}, visible_rows=7)
    assert all(step["target"][0] < 7 for step in result["steps"])


def test_item_counts_are_independent_from_shovel_budget():
    result = plan_final_v1(_board(), 0, {"bomb": 1, "drill": 1})
    assert result["stats"]["ending_inventory"]["pickaxe"] == 0
    assert result["stats"]["ending_inventory"]["bomb"] >= 0
    assert result["stats"]["ending_inventory"]["drill"] >= 0


def test_time_budget_returns_best_so_far_under_250ms():
    board = _board(rows=21)
    started = time.perf_counter()
    result = plan_final_v1(board, 1000, {"bomb": 100, "drill": 100}, time_budget_ms=250.0)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 300
    assert result["elapsed_ms"] <= elapsed_ms
    assert isinstance(result["budget_hit"], bool)


def test_item_choice_does_not_change_when_only_inventory_surplus_changes():
    board = _board()
    board[1][1] = "reachable_pit"
    scarce = plan_final_v1(board, 50, {"bomb": 1, "drill": 1})["steps"][0]
    surplus = plan_final_v1(board, 50, {"bomb": 999, "drill": 1})["steps"][0]
    assert (scarce["type"], scarce.get("item"), scarce["target"]) == (
        surplus["type"], surplus.get("item"), surplus["target"]
    )


def test_equal_effect_bomb_and_drill_receive_equal_objective_value():
    board = [["empty"] * 6 for _ in range(7)]
    board[1][2] = "reachable_pit"
    bomb = plan_final_v1(
        board, 0, {"bomb": 1, "drill": 0},
        valid_targets={("use", "bomb", 0, 2)},
    )
    drill = plan_final_v1(
        board, 0, {"bomb": 0, "drill": 1},
        valid_targets={("use", "drill", 0, 2)},
    )
    assert bomb["objective_score"] == drill["objective_score"]
    assert bomb["score_breakdown"]["item_cost"] == drill["score_breakdown"]["item_cost"]


def test_row_zero_pit_is_collected_before_scroll_progress():
    board = _board()
    board[0][1] = "reachable_pit"
    board[6][2] = "dirt"
    result = plan_final_v1(board, 50, {"bomb": 0, "drill": 0})
    assert result["steps"][0]["target"] == (0, 1)
