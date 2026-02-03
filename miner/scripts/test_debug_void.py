"""更詳細的調試：檢查 (6,2) 周圍的格子"""
board = [
    ["rock", "empty", "empty", "dirt", "unreachable_rock", "unreachable_rock"],
    ["unreachable_dirt", "rock", "empty", "empty", "dirt", "unreachable_dirt"],
    ["dirt", "rock", "dug_pit", "dug_pit", "rock", "unreachable_dirt"],
    ["empty", "empty", "dug_pit", "dug_pit", "dirt", "unreachable_dirt"],
    ["dirt", "dirt", "rock", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "dirt", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "unreachable_void", "rock", "unreachable_dirt", "unreachable_dirt"],
]

target = (6, 2)  # unreachable_void
print(f"檢查 {target} 的鄰居:")
print(f"  上方 (5,2): {board[5][2]}")
print(f"  左側 (6,1): {board[6][1]}")
print(f"  右側 (6,3): {board[6][3]}")
print(f"  (沒有下方，已經是最下層)")

print("\n問題: (5,2) 是 'dirt'，不是 'empty'，所以鬆弛不會觸發")
print("正確邏輯應該是:")
print("  1. Dijkstra 從所有 empty 出發")
print("  2. 到達 (5,2) dirt 需要成本 1")
print("  3. 從 (5,2) 挖開後變成 empty")
print("  4. (6,2) unreachable_void 才能被鬆弛成 void")
print("  5. 然後成本 0 進入")
print("\n但現在 Dijkstra 顯示成本=0 是因為 unreachable_void 被錯誤地當成 empty 了！")

# 驗證 is_empty 和 enter_cost
def base_label(lbl: str) -> str:
    return lbl.replace("unreachable_", "")

def is_empty_test(lbl: str) -> bool:
    base = base_label(lbl)
    return base == "empty" or base == "void"

def enter_cost_test(lbl: str):
    COST_TABLE = {
        "empty": 0,
        "void": 0,
        "dirt": 1,
        "rock": 2,
        "unreachable_pit": None,
        "unreachable_void": None,
    }
    return COST_TABLE.get(base_label(lbl), None)

test_label = "unreachable_void"
print(f"\n測試 '{test_label}':")
print(f"  base_label: {base_label(test_label)}")
print(f"  is_empty: {is_empty_test(test_label)}")
print(f"  enter_cost: {enter_cost_test(test_label)}")

print("\n❌ 發現 BUG: is_empty('unreachable_void') 返回 True!")
print("因為 base_label('unreachable_void') = 'void'")
print("而 is_empty 檢查 base == 'void' → True")
print("\n這導致 Dijkstra 把 unreachable_void 當作起點（成本 0）！")
