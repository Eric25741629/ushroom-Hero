"""驗證下樓邏輯：應該優先挖泥土解鎖 void 而非挖石頭"""
import sys
import os

# 直接從 Mining.py 導入需要的函數
# 因為有相對導入問題，我們複製關鍵邏輯
from typing import Dict, Optional, List, Tuple, Any
from heapq import heappush, heappop
from math import inf

COST_TABLE: Dict[str, Optional[int]] = {
    "empty": 0,
    "void": 0,
    "dirt": 1,
    "rock": 2,
    "one_hit_rock": 1,
    "pit": 1,
    "dug_pit": 0,
    "reachable_pit": 1,
    "unreachable_pit": None,
    "unreachable_void": None,
}

def base_label(lbl: str) -> str:
    return lbl.replace("unreachable_", "")

def enter_cost(lbl: str) -> Optional[int]:
    """進入該格要付的鏟子成本；None = 不能進（牆/void）。"""
    return COST_TABLE.get(base_label(lbl), None)

def is_empty(lbl: str) -> bool:
    # 只有 empty 和 void 本身才是空的
    # unreachable_void 不算 empty
    return lbl == "empty" or lbl == "void"

def is_void(lbl: str) -> bool:
    return base_label(lbl) == "void"

def dijkstra_from_all_empties(board: List[List[str]]) -> Tuple[List[List[float]], List[List[Optional[Tuple[int,int]]]]]:
    """多源 Dijkstra：源點 = 所有 empty；代價 = 目標格 enter_cost。"""
    R, C = len(board), len(board[0])
    dist = [[inf]*C for _ in range(R)]
    prev: List[List[Optional[Tuple[int,int]]]] = [[None]*C for _ in range(R)]
    hq: List[Tuple[float,int,int]] = []
    for r in range(R):
        for c in range(C):
            if is_empty(board[r][c]):
                dist[r][c] = 0
                heappush(hq, (0, r, c))
    dirs = [(1,0),(-1,0),(0,1),(0,-1)]
    def inb(rr, cc): return 0 <= rr < R and 0 <= cc < C
    while hq:
        d, r, c = heappop(hq)
        if d != dist[r][c]:
            continue
        for dr, dc in dirs:
            rr, cc = r+dr, c+dc
            if not inb(rr, cc):
                continue
            step = enter_cost(board[rr][cc])
            if step is None:
                continue
            nd = d + step
            if nd < dist[rr][cc]:
                dist[rr][cc] = nd
                prev[rr][cc] = (r, c)
                heappush(hq, (nd, rr, cc))
    return dist, prev

def reconstruct_path(prev: List[List[Optional[Tuple[int,int]]]], end: Tuple[int,int]) -> List[Tuple[int,int]]:
    path: List[Tuple[int,int]] = []
    cur = end
    while cur:
        path.append(cur)
        cur = prev[cur[0]][cur[1]]
    path.reverse()
    return path

def summarize_path(board: List[List[str]], path: List[Tuple[int,int]]) -> Tuple[List[Tuple[int,int]], int]:
    """回傳需要實際挖的座標與總成本（忽略原本 empty 的格子）。"""
    dig_list = [(r,c) for (r,c) in path if not is_empty(board[r][c])]
    total_cost = sum(enter_cost(board[r][c]) or 0 for (r,c) in dig_list)
    return dig_list, int(total_cost)

def relax_unreachable_void(board: List[List[str]]) -> int:
    """把與 empty 相連的 unreachable_void 鬆弛成 void"""
    R, C = len(board), len(board[0])
    changed = 0
    again = True
    
    def neighbors(r, c):
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            rr, cc = r+dr, c+dc
            if 0 <= rr < R and 0 <= cc < C:
                yield rr, cc
    
    def has_empty_neighbor(r, c):
        return any(is_empty(board[rr][cc]) for rr, cc in neighbors(r, c))
    
    while again:
        again = False
        for r in range(R):
            for c in range(C):
                if board[r][c] == "unreachable_void" and has_empty_neighbor(r, c):
                    board[r][c] = "void"
                    changed += 1
                    again = True
    return changed

def mark_path_as_empty(board: List[List[str]], dig_list: List[Tuple[int,int]]) -> None:
    for (r,c) in dig_list:
        board[r][c] = "empty"

# ========== 測試案例 ==========
print("=" * 70)
print("測試：你的盤面 - 應該優先挖 dirt 解鎖 unreachable_void")
print("=" * 70)

board = [
    ["rock", "empty", "empty", "dirt", "unreachable_rock", "unreachable_rock"],
    ["unreachable_dirt", "rock", "empty", "empty", "dirt", "unreachable_dirt"],
    ["dirt", "rock", "dug_pit", "dug_pit", "rock", "unreachable_dirt"],
    ["empty", "empty", "dug_pit", "dug_pit", "dirt", "unreachable_dirt"],
    ["dirt", "dirt", "rock", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "dirt", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "unreachable_void", "rock", "unreachable_dirt", "unreachable_dirt"],
]

print("\n初始盤面第 6 列（下樓層）:")
for c in range(6):
    print(f"  (6,{c}): {board[6][c]}")

print("\n=== 步驟 1: 執行鬆弛（relax_unreachable_void）===")
bd_copy = [row[:] for row in board]
relaxed = relax_unreachable_void(bd_copy)
print(f"鬆弛了 {relaxed} 個 unreachable_void")

if relaxed > 0:
    print("\n鬆弛後第 6 列:")
    for c in range(6):
        if bd_copy[6][c] != board[6][c]:
            print(f"  (6,{c}): {board[6][c]} → {bd_copy[6][c]} ✓")
        else:
            print(f"  (6,{c}): {bd_copy[6][c]}")

print("\n=== 步驟 2: 執行 Dijkstra 找最便宜的下樓路徑 ===")
dist, prev = dijkstra_from_all_empties(bd_copy)

print("\n第 6 列各位置的最小成本:")
best_c, best_cost = None, inf
for c in range(6):
    label = bd_copy[6][c]
    cost = dist[6][c]
    marker = ""
    if cost < inf and (best_c is None or cost < best_cost):
        best_cost = cost
        best_c = c
        marker = " ← 最便宜"
    print(f"  (6,{c}) {label:20} → 成本={cost}{marker}")

if best_c is not None:
    print(f"\n✅ 選擇的下樓點: (6,{best_c}) {bd_copy[6][best_c]}, 總成本={best_cost}")
    
    # 重建路徑
    path = reconstruct_path(prev, (6, best_c))
    dig_list, total_cost = summarize_path(bd_copy, path)
    
    print(f"\n路徑詳情:")
    for i, (r, c) in enumerate(path):
        label = board[r][c]  # 用原始盤面的標籤
        cost = enter_cost(label) if label else 0
        print(f"  {i}. ({r},{c}) {label:20} → 進入成本={cost}")
    
    print(f"\n需要挖的格子:")
    if dig_list:
        for (r, c) in dig_list:
            label = board[r][c]
            cost = enter_cost(label)
            print(f"  ({r},{c}) {label:20} → 成本={cost}")
        print(f"\n總挖掘成本: {total_cost}")
    else:
        print("  無需挖掘（直接可達）")
    
    # 驗證結果
    print("\n" + "=" * 70)
    print("驗證結果:")
    print("=" * 70)
    if best_c == 2 and total_cost == 1:
        print("✅ 正確！選擇了 (6,2) unreachable_void，總成本=1（挖 dirt）")
    elif best_c == 3 and total_cost == 2:
        print("❌ 錯誤！選擇了 (6,3) rock，總成本=2（應該選 void）")
    else:
        print(f"⚠️  結果: (6,{best_c})，成本={total_cost}")

print("\n=== 預期行為 ===")
print("應該挖 (5,2) 的 dirt (成本1)")
print("→ 解鎖 (6,2) 的 unreachable_void 變成 void")
print("→ 走到 (6,2) void (成本0)")
print("總成本 = 1 ✓")
