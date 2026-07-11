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


def test_scroll_after_collecting_row0_pit_is_not_penalized():
    """漏礦懲罰只算捲動當下 row0 仍未採集的礦：先收礦再捲動 = 0 損失。"""
    from miner.final_v1.planner import _State, _apply
    from miner.final_v1.types import PlannerAction, SearchUsage

    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[0][1] = "reachable_pit"
    for r in range(6):
        board[r][2] = "empty"
    board[6][2] = "dirt"
    initial = _State(tuple(tuple(r) for r in board), 10.0, 0, 0,
                     SearchUsage(), tuple(), False, 0)

    collected = _apply(initial, PlannerAction("dig", "pickaxe", 0, 1), 7)
    then_scrolled = _apply(collected, PlannerAction("dig", "pickaxe", 6, 2), 7)
    assert then_scrolled.scrolled is True
    assert then_scrolled.lost_pits == 0

    scrolled_without_collect = _apply(initial, PlannerAction("dig", "pickaxe", 6, 2), 7)
    assert scrolled_without_collect.scrolled is True
    assert scrolled_without_collect.lost_pits == 1


def test_digging_steers_toward_known_below_viewport_pit():
    """位能導向：已知畫面外礦坑在 col4，第一步應沿 col4 往下挖，而非亂逛。"""
    board = [["unreachable_dirt"] * 6 for _ in range(21)]
    board[0][4] = "empty"
    board[10][4] = "unreachable_pit"
    result = plan_final_v1(board, 30, {"bomb": 0, "drill": 0}, visible_rows=7)
    assert result["steps"][0]["target"] == (1, 4)


def test_no_pit_board_steers_downward_not_sideways():
    """無礦盤面：以最低成本往下推進（開 floor7），不做水平閒逛。"""
    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    result = plan_final_v1(board, 30, {"bomb": 0, "drill": 0})
    assert result["steps"][0]["target"] == (1, 0)


def test_plan_returns_the_full_search_path_not_just_first_step():
    """多步輸出：ADB 後端整批執行需要完整路徑，不能每步付一次截圖成本。"""
    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    result = plan_final_v1(board, 50, {"bomb": 0, "drill": 0})
    assert len(result["steps"]) > 1
    assert result["preview_steps"] == result["steps"]


def test_no_pit_board_never_spends_items_on_excavation():
    """無礦盤面道具命中 0 礦 = 純挖掘用途，成本分級後必須全用鎬子下潛。"""
    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    result = plan_final_v1(board, 50, {"bomb": 5, "drill": 5})
    assert result["steps"]
    assert all(step["type"] == "dig" for step in result["steps"])


def test_bomb_hitting_multiple_pits_through_rock_still_beats_digging():
    """高收益道具使用必須保留：一發命中 2 礦 + 破 rock（逐挖 1+2+1=4 鏟 >
    bomb 3.0），不能被分級成本誤殺。"""
    board = [["unreachable_dirt"] * 6 for _ in range(7)]
    board[0][2] = "empty"
    board[1][1] = "unreachable_pit"
    board[1][2] = "unreachable_rock"
    board[1][3] = "unreachable_pit"
    result = plan_final_v1(board, 50, {"bomb": 1, "drill": 0})
    first = result["steps"][0]
    assert first["type"] == "use"
    assert first["item"] == "bomb"


def test_equal_effect_tie_break_is_coordinate_stable_not_item_fixed():
    """效果完全相同時以座標（parity）決定道具，不得固定偏好 bomb；且可重現。"""
    def _first_item(col):
        board = [["empty"] * 6 for _ in range(7)]
        board[1][col] = "reachable_pit"
        result = plan_final_v1(
            board, 0, {"bomb": 1, "drill": 1},
            valid_targets={("use", "bomb", 0, col), ("use", "drill", 0, col)},
        )
        return result["steps"][0]["item"]

    even = _first_item(2)  # row+col = 2
    odd = _first_item(3)   # row+col = 3
    assert {even, odd} == {"bomb", "drill"}
    assert _first_item(2) == even
    assert _first_item(3) == odd


def test_step_costs_reflect_the_cell_state_when_each_step_executes():
    """第 N 步的 step_cost 必須是該步當下格材質的成本（rock=更貴）。"""
    from miner.v3.actions import dig_cost
    # 全 rock 盤只留 col0 一條 dirt 通道、途中 (2,0) 是 rock：最省路徑
    # 沿 col0 下挖，rock 那步的 step_cost 必須反映 rock 而非初始盤面推算
    board = [["unreachable_rock"] * 6 for _ in range(7)]
    board[0][0] = "empty"
    for r in range(1, 7):
        board[r][0] = "unreachable_dirt"
    board[2][0] = "unreachable_rock"
    result = plan_final_v1(board, 50, {"bomb": 0, "drill": 0})
    steps = result["steps"]
    assert len(steps) >= 2
    by_target = {tuple(s["target"]): s["step_cost"] for s in steps}
    assert by_target[(1, 0)] == float(dig_cost("dirt"))
    assert by_target.get((2, 0), float(dig_cost("rock"))) == float(dig_cost("rock"))
