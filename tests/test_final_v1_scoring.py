"""final_v1 scoring contract: flat item cost, cluster identity, action_cost, row-loss."""
from miner.final_v1.scoring import (
    ITEM_COST,
    ITEM_COST_PLAN,
    ITEM_COST_STEP,
    SHOVEL_COST,
    evaluate_state,
    item_use_cost,
    pit_clusters,
)
from miner.final_v1.types import SearchUsage


def test_item_shadow_price_splits_by_profile_but_items_stay_equal():
    """道具搜尋內部影子價依 exec_profile 分開（掃描定案）：plan=3 鏟、step=3.6 鏟；
    分級成本已廢除、與命中礦數無關，且同一 profile 下 bomb/drill 恆同價。
    對外 alias ITEM_COST = plan 值（掃描 monkeypatch / 舊呼叫端相容）。"""
    assert ITEM_COST_PLAN == 3.0 * SHOVEL_COST
    assert ITEM_COST_STEP == 3.6 * SHOVEL_COST
    assert ITEM_COST == ITEM_COST_PLAN
    for unit in (ITEM_COST_PLAN, ITEM_COST_STEP):
        assert item_use_cost("bomb", unit) == item_use_cost("drill", unit) == unit


def test_action_cost_penalizes_distance_to_next_cluster_only_under_positive_rho():
    """action_cost = rho * PIT_VALUE * (best_dist + 最近簇剩餘格)；rho=0（plan/ADB）
    恆為 0 不改排名，rho>0（step）才對「離下一簇越遠」加懲罰。"""
    board = [["empty"] * 6 for _ in range(3)]
    board[2][0] = "reachable_pit"
    clusters = pit_clusters(board)
    plan = evaluate_state(board, board, SearchUsage(), clusters=clusters,
                          best_dist=2.0, best_cluster=0, rho_action=0.0)
    step = evaluate_state(board, board, SearchUsage(), clusters=clusters,
                          best_dist=2.0, best_cluster=0, rho_action=0.15)
    assert plan.action_cost == 0.0
    assert step.action_cost > 0.0
    # best_dist == inf（尚未挖到任何格）時無資訊，不罰
    unknown = evaluate_state(board, board, SearchUsage(), clusters=clusters,
                             best_dist=float("inf"), best_cluster=-1, rho_action=0.15)
    assert unknown.action_cost == 0.0


def test_dug_pit_preserves_cluster_identity_and_blocks_false_completion():
    """半挖簇的中間格變 dug_pit 後不得被重分群：連通分量在 pit∪dug_pit 聯集上算，
    完成判定要求整簇無未挖 pit，避免半挖就誤發完成獎勵。"""
    # 一條 1x3 直簇，中間 (1,1) 已挖成 dug_pit，兩端仍是未挖 pit
    board = [["empty"] * 3 for _ in range(4)]
    board[0][1] = "reachable_pit"
    board[1][1] = "dug_pit"
    board[2][1] = "reachable_pit"
    clusters = pit_clusters(board)
    assert len(clusters) == 1
    assert clusters[0] == frozenset({(0, 1), (1, 1), (2, 1)})
    # 仍有 2 未挖 pit → 未完成，不得發完成獎勵
    partial = evaluate_state(board, board, SearchUsage(), clusters=clusters)
    assert partial.cluster_gain == 0.0
    assert partial.unfinished_cluster_penalty > 0.0
    # 端點都挖掉 → 整簇完成，完成獎勵以整簇尺寸 3 計
    done = [row[:] for row in board]
    done[0][1] = "dug_pit"
    done[2][1] = "dug_pit"
    finished = evaluate_state(board, done, SearchUsage(), clusters=clusters)
    assert finished.cluster_gain > 0.0
    assert finished.unfinished_cluster_penalty == 0.0


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
