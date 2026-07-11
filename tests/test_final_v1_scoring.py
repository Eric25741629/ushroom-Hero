"""final_v1 scoring contract: equal item cost, cluster priority, row-loss, descent."""
from miner.final_v1.scoring import ITEM_COST, evaluate_state
from miner.final_v1.types import SearchUsage


def test_bomb_and_drill_have_the_same_base_cost():
    assert ITEM_COST["bomb"] == ITEM_COST["drill"]


def test_item_cost_tiers_penalize_low_yield_use():
    """道具成本依「該次使用實際命中的礦坑格數」分級：
    0 礦（純挖掘用途）>> 1 礦 >= 2+ 礦（基礎成本）。兩種道具同價。"""
    from miner.final_v1.scoring import item_use_cost

    assert item_use_cost("bomb", 0) > item_use_cost("bomb", 1) >= item_use_cost("bomb", 2)
    assert item_use_cost("bomb", 2) == ITEM_COST["bomb"]
    assert item_use_cost("bomb", 5) == ITEM_COST["bomb"]
    for hits in (0, 1, 2, 4):
        assert item_use_cost("bomb", hits) == item_use_cost("drill", hits)


def test_evaluate_state_charges_accumulated_item_cost_units():
    """item_cost 來自逐次使用累計的加權單位，不再用「顆數 x 固定價」。"""
    board = [["empty"] * 6]
    usage = SearchUsage(shovels=0.0, bombs=2, drills=0, item_cost_units=15.0)
    breakdown = evaluate_state(board, board, usage)
    assert breakdown.item_cost == 15.0


def test_completed_cluster_beats_equal_number_of_scattered_pits():
    original = [
        ["empty", "empty", "empty", "empty", "empty", "empty"],
        ["reachable_pit", "reachable_pit", "dirt", "reachable_pit", "dirt", "dirt"],
        ["reachable_pit", "reachable_pit", "dirt", "reachable_pit", "dirt", "dirt"],
    ]
    complete_square = [row[:] for row in original]
    scattered = [row[:] for row in original]
    for pos in ((1, 0), (1, 1), (2, 0), (2, 1)):
        complete_square[pos[0]][pos[1]] = "empty"
    for pos in ((1, 0), (1, 3), (2, 0), (2, 3)):
        scattered[pos[0]][pos[1]] = "empty"

    completed_score = evaluate_state(original, complete_square, SearchUsage()).total
    scattered_score = evaluate_state(original, scattered, SearchUsage()).total
    assert completed_score > scattered_score


def test_scrolling_an_uncollected_row_zero_pit_is_strongly_penalized():
    original = [["reachable_pit"] + ["dirt"] * 5] + [["dirt"] * 6 for _ in range(6)]
    kept = evaluate_state(original, original, SearchUsage(), scrolled=False)
    lost = evaluate_state(original, original, SearchUsage(), scrolled=True)
    assert lost.lost_pit_penalty > kept.lost_pit_penalty
    assert lost.total < kept.total


def test_descent_bonus_never_outweighs_one_pit():
    original = [["empty"] * 6 for _ in range(7)]
    pit_board = [row[:] for row in original]
    pit_board[5][2] = "reachable_pit"
    collected = [row[:] for row in pit_board]
    collected[5][2] = "empty"
    pit_score = evaluate_state(pit_board, collected, SearchUsage()).pit_gain
    descent_score = evaluate_state(original, original, SearchUsage(), descent_rows=1).descent_bonus
    assert 0 < descent_score < pit_score
