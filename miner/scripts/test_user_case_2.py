from miner.scripts.optimize_algo import MiningSearcher
import time

def solve_user_case_2():
    # 使用者提供的 board #2
    board = [
        ["empty",        "dirt",        "empty",             "unreachable_dirt", "unreachable_dirt", "unreachable_rock"], 
        ["empty",        "dirt",        "unreachable_rock",  "unreachable_dirt", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_rock",  "unreachable_rock", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_dirt",  "unreachable_dirt", "unreachable_void", "unreachable_dirt"], 
        ["empty",        "dirt",        "unreachable_void",  "unreachable_dirt", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_dirt",  "unreachable_pit", "unreachable_dirt", "unreachable_rock"], 
        ["reachable_pit", "unreachable_rock", "unreachable_dirt", "unreachable_rock", "unreachable_void", "unreachable_dirt"]
    ]

    # 情境 A: 鎬子充足，無道具 -> 測試是否值得硬挖
    print("--- 情境 A: 僅有鎬子 (20) ---")
    searcher_a = MiningSearcher(board, initial_shovels=20, initial_items={'drill': 0, 'bomb': 0})
    best_actions_a, end_state_a = searcher_a.solve(max_depth=6, beam_width=20) # 深度加大，因為路徑可能很長
    print(f"總花費: {end_state_a.accumulated_cost}, 收益: {end_state_a.collected_rewards}")
    for i, act in enumerate(best_actions_a):
        print(f" {i+1}. {act}")

    # 情境 B: 有鑽頭 -> 測試是否值得用鑽頭
    print("\n--- 情境 B: 有鑽頭 (1) ---")
    searcher_b = MiningSearcher(board, initial_shovels=20, initial_items={'drill': 1, 'bomb': 0})
    best_actions_b, end_state_b = searcher_b.solve(max_depth=4, beam_width=20)
    print(f"總花費: {end_state_b.accumulated_cost}, 收益: {end_state_b.collected_rewards}")
    for i, act in enumerate(best_actions_b):
        print(f" {i+1}. {act}")

if __name__ == "__main__":
    solve_user_case_2()
