"""測試 void 視為 empty 的邏輯"""
import sys
import os

# 直接模擬 Mining.py 中的相關函數
from typing import Dict, Optional, List, Tuple
from heapq import heappush, heappop
from math import inf

COST_TABLE: Dict[str, Optional[int]] = {
    "empty": 0,
    "void": 0,  # void 視為 empty
    "dirt": 1,
    "rock": 2,
    "one_hit_rock": 1,
    "pit": 1,
    "dug_pit": 0,
    "reachable_pit": 1,
    "unreachable_pit": None,
    "unreachable_void": 0,
}

def base_label(lbl: str) -> str:
    return lbl.replace("unreachable_", "")

def enter_cost(lbl: str) -> Optional[int]:
    """進入該格要付的鏟子成本；None = 不能進（牆/void）。"""
    if lbl == "unreachable_void":
        return 0
    return COST_TABLE.get(base_label(lbl), None)

def is_empty(lbl: str) -> bool:
    base = base_label(lbl)
    return base == "empty" or base == "void"

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


# 測試 1: 成本表
print("=" * 60)
print("測試 1: 成本表")
print("=" * 60)
test_labels = [
    "empty", "void", "unreachable_void",
    "dirt", "unreachable_dirt",
    "rock", "unreachable_rock",
]
for lbl in test_labels:
    cost = enter_cost(lbl)
    is_emp = is_empty(lbl)
    print(f"{lbl:20} -> cost={cost}, is_empty={is_emp}")

# 測試 2: Dijkstra 路徑規劃
print("\n" + "=" * 60)
print("測試 2: Dijkstra 路徑規劃（用你的盤面）")
print("=" * 60)

board = [
    ["rock", "empty", "empty", "dirt", "unreachable_rock", "unreachable_rock"],
    ["unreachable_dirt", "rock", "empty", "empty", "dirt", "unreachable_dirt"],
    ["dirt", "rock", "dug_pit", "dug_pit", "rock", "unreachable_dirt"],
    ["empty", "empty", "dug_pit", "dug_pit", "dirt", "unreachable_dirt"],
    ["dirt", "dirt", "rock", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "dirt", "empty", "dirt", "unreachable_dirt"],
    ["unreachable_dirt", "unreachable_rock", "unreachable_void", "rock", "unreachable_dirt", "unreachable_dirt"],
]

dist, prev = dijkstra_from_all_empties(board)

# 印出到第 7 列 (6,2) unreachable_void 的距離
target = (6, 2)
print(f"\n到 {target} (unreachable_void) 的最小成本: {dist[target[0]][target[1]]}")

# 找出到第 7 列各個位置的成本
print("\n第 7 列各位置的最小成本:")
for c in range(6):
    label = board[6][c]
    cost = dist[6][c]
    print(f"  (6, {c}) {label:20} -> 成本={cost}")

# 重建到 (6,2) 的路徑
path = []
cur = target
while cur and prev[cur[0]][cur[1]]:
    path.append(cur)
    cur = prev[cur[0]][cur[1]]
if cur:
    path.append(cur)
path.reverse()

print(f"\n到 {target} 的路徑:")
for i, (r, c) in enumerate(path):
    label = board[r][c]
    cost = enter_cost(label)
    print(f"  {i}. ({r},{c}) {label:20} -> 進入成本={cost}")

total_cost = sum(enter_cost(board[r][c]) or 0 for r, c in path[1:])  # 跳過起點
print(f"\n路徑總成本: {total_cost}")
