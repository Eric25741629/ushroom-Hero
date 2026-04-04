from typing import List, Tuple, Optional
from heapq import heappush, heappop
from math import inf

# Generic path planning utilities decoupled from game-specific label logic.
# Callers must provide is_empty(cell_label) and enter_cost(cell_label) functions.

def dijkstra_from_all_empties(board: List[List[str]], is_empty, enter_cost) -> Tuple[List[List[float]], List[List[Optional[Tuple[int,int]]]]]:
    """Multi-source Dijkstra: sources are all empty cells (is_empty(lbl) True).
    Distances accumulate enter_cost of target cells. Non-enterable cells return None.
    Returns (dist, prev) matrices.
    """
    R, C = len(board), len(board[0])
    dist = [[inf]*C for _ in range(R)]
    prev: List[List[Optional[Tuple[int,int]]]] = [[None]*C for _ in range(R)]
    hq: List[Tuple[float,int,int]] = []
    for r in range(R):
        for c in range(C):
            if is_empty(board[r][c]):
                dist[r][c] = 0
                heappush(hq, (0, r, c))
    dirs = ((1,0),(-1,0),(0,1),(0,-1))
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

def dijkstra_from_all_enterables(board: List[List[str]], is_empty, enter_cost) -> Tuple[List[List[float]], List[List[Optional[Tuple[int,int]]]]]:
    """Multi-source Dijkstra with broader sources:
    - Sources include ALL cells that do NOT start with 'unreachable_' and have enter_cost != None.
    - For empty/dug_pit sources, initial dist = 0.
    - For other enterable sources (e.g., dirt/rock/pit), initial dist = enter_cost(cell).

    Middle of paths may still traverse cells with labels starting with 'unreachable_' IF enter_cost allows it,
    enabling the planner to choose to dig through unreachable_dirt/rock as part of the path.
    """
    R, C = len(board), len(board[0])
    dist = [[inf]*C for _ in range(R)]
    prev: List[List[Optional[Tuple[int,int]]]] = [[None]*C for _ in range(R)]
    hq: List[Tuple[float,int,int]] = []

    for r in range(R):
        for c in range(C):
            lbl = board[r][c]
            # 起始點不得是 unreachable_*
            if isinstance(lbl, str) and lbl.startswith('unreachable_'):
                continue
            step = enter_cost(lbl)
            if step is None:
                continue
            if is_empty(lbl):
                d0 = 0.0
            else:
                d0 = float(step)
            dist[r][c] = d0
            heappush(hq, (d0, r, c))

    dirs = ((1,0),(-1,0),(0,1),(0,-1))
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

def summarize_path(board: List[List[str]], path: List[Tuple[int,int]], is_empty, enter_cost) -> Tuple[List[Tuple[int,int]], int]:
    """Return dig_list (cells that are not already empty) and total shovel cost."""
    dig_list = [(r,c) for (r,c) in path if not is_empty(board[r][c])]
    total_cost = sum(enter_cost(board[r][c]) or 0 for (r,c) in dig_list)
    return dig_list, int(total_cost)

def mark_path_as_empty(board: List[List[str]], dig_list: List[Tuple[int,int]]) -> None:
    for (r,c) in dig_list:
        board[r][c] = "empty"

def floor7_triggered(board: List[List[str]], is_empty) -> bool:
    R, C = len(board), len(board[0])
    return any(is_empty(board[R-1][c]) for c in range(C))
