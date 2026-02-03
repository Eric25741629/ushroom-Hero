"""測試 unreachable_pit 的成本與規劃行為"""
from miner import planner


def test_unreachable_pit_cost_and_planning():
    # unreachable_pit 應該由 COST_TABLE 中查到 None（代表不可直接進入）
    assert planner.enter_cost("unreachable_pit") is None

    # 建一個簡單盤面：第 7 列 (6,4) 是 unreachable_pit，旁邊 (5,4) 是 dirt
    board = [
        ["empty"] * 6 for _ in range(6)
    ]
    board.append(["empty", "empty", "empty", "empty", "unreachable_pit", "empty"])  # row 6
    board[5][4] = "dirt"

    result = planner.plan_collect_all_mines_then_descend_v2(board)

    # 檢查第一個動作是 collect (6,4)
    assert result.get("ok", False)
    steps = result.get("steps", [])
    assert steps, "應該規劃出步驟"
    first = steps[0]
    assert first["action"] == "collect"
    assert first["target"] == (6, 4)
    # collect 對 unreachable_pit 應該使用 fallback，步驟成本為 1
    assert first["step_cost"] == 1


if __name__ == "__main__":
    print("執行測試: test_unreachable_pit_cost_and_planning")
    test_unreachable_pit_cost_and_planning()
    print("通過")
