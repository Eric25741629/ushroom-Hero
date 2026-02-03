from miner.scripts.optimize_algo import MiningSearcher
import time

def solve_user_case():
    # 使用者提供的 board_before
    board = [
        ["empty",        "dirt",        "empty",             "unreachable_dirt", "unreachable_dirt", "unreachable_rock"], 
        ["empty",        "dirt",        "unreachable_rock",  "unreachable_dirt", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_rock",  "unreachable_rock", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_dirt",  "unreachable_dirt", "unreachable_void", "unreachable_dirt"], 
        ["empty",        "dirt",        "unreachable_void",  "unreachable_dirt", "unreachable_dirt", "unreachable_void"], 
        ["empty",        "rock",        "unreachable_dirt",  "unreachable_pit", "unreachable_dirt", "unreachable_rock"], 
        ["reachable_pit", "unreachable_rock", "unreachable_dirt", "unreachable_pit", "unreachable_void", "unreachable_dirt"]
    ]

    # 假設有 20 鏟子，沒有道具 (因為這個盤面不需要道具)
    searcher = MiningSearcher(board, initial_shovels=20, initial_items={'drill': 1, 'bomb': 1})
    
    print("正在規劃使用者案例...")
    start_time = time.time()
    # 只需要淺層搜索，因為目標很明顯
    best_actions, end_state = searcher.solve(max_depth=3, beam_width=10)
    duration = time.time() - start_time
    
    print(f"\n規劃完成 (耗時 {duration:.4f}s)")
    print(f"預期總花費: {end_state.accumulated_cost}")
    print(f"預期總收益: {end_state.collected_rewards}")
    print("建議步驟:")
    for i, act in enumerate(best_actions):
        print(f" {i+1}. {act}")

if __name__ == "__main__":
    solve_user_case()
