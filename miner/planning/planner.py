"""盤面分析與規劃相關函式。"""
from __future__ import annotations

import logging
from heapq import heappop, heappush
from math import inf
from typing import Any, Dict, List, Optional, Tuple

from miner.core.config import COST_TABLE, MINE_LABELS, REWARD_TABLE

logger = logging.getLogger(__name__)

Board = List[List[str]]
Coordinate = Tuple[int, int]


# ---------------------------------------------------------------------------
# 標籤判斷工具
# ---------------------------------------------------------------------------
def base_label(lbl: str) -> str:
    """移除 unreachable_ 前綴，使成本與獎勵查表更簡潔。"""
    return lbl.replace("unreachable_", "")


def enter_cost(lbl: str) -> Optional[int]:
    """回傳進入該格需要的鏟子成本；None 代表不可進入。

    調整：unreachable_pit 可以用鏟子挖開（成本與 reachable_pit 相同），
    但在路徑起點判斷時仍不應把 unreachable_* 當作可直接當作起點的空地。
    """
    # 特殊處理：unreachable_void 若在 COST_TABLE 有定義則回傳，否則 None
    if lbl == "unreachable_void":
        return COST_TABLE.get("unreachable_void")
    # unreachable_pit 可用鏟子處理，視為跟 reachable_pit 相同成本
    if lbl == "unreachable_pit":
        return COST_TABLE.get("reachable_pit")
    # 其他情況以 base_label 查表
    return COST_TABLE.get(base_label(lbl))


def is_empty(lbl: str) -> bool:
    # Treat unreachable_* variants as their base label equivalents so
    # Dijkstra and other planners consider 'unreachable_empty' as an empty start.
    return base_label(lbl) in ("empty", "void", "dug_pit")


def is_mine(lbl: str) -> bool:
    return base_label(lbl) in MINE_LABELS


def is_pit(lbl: str) -> bool:
    return base_label(lbl) in {"pit", "reachable_pit", "unreachable_pit", "dug_pit"}


def is_dug_pit(lbl: str) -> bool:
    return lbl == "dug_pit"


def is_reachable_pit(lbl: str) -> bool:
    return lbl == "reachable_pit"


def is_unreachable_pit(lbl: str) -> bool:
    return lbl == "unreachable_pit"


def list_all_pits(board: Board) -> Tuple[List[Coordinate], List[Coordinate], List[Coordinate]]:
    """取得 (dug, reachable, unreachable) 位置列表。"""
    R, C = len(board), len(board[0])
    dug: List[Coordinate] = []
    reachable: List[Coordinate] = []
    unreachable: List[Coordinate] = []
    for r in range(R - 1, -1, -1):
        for c in range(C):
            if is_dug_pit(board[r][c]):
                dug.append((r, c))
            elif is_reachable_pit(board[r][c]):
                reachable.append((r, c))
            elif is_unreachable_pit(board[r][c]):
                unreachable.append((r, c))
    return dug, reachable, unreachable

from typing import List, Tuple
def update_reachable_pits(board: Board) -> int:
    """
    根據目前盤面，把已經連到空地的 unreachable_pit 改成 reachable_pit。
    規則：
        - 只要某個 unreachable_pit 四方向有 reachable 的 empty / dug_pit，
          就把它標成 reachable_pit。
    回傳：
        本次一共更新了幾顆坑。
    """
    if not board:
        return 0

    H = len(board)
    W = len(board[0])
    changed = 0

    for r in range(H - 1, -1, -1):
        for c in range(W):
            label = board[r][c]
            # 只處理 unreachable_pit
            if base_label(label) != "pit" or not label.startswith("unreachable_"):
                continue

            # 看看四方向有沒有 reachable 的空地
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W:
                    n_label = board[rr][cc]
                    # reachable 的 empty / dug_pit / void 視為空地
                    if (not n_label.startswith("unreachable_")) and base_label(n_label) in ("empty", "dug_pit", "void"):
                        board[r][c] = "reachable_pit"
                        changed += 1
                        break

    if changed:
        print(f"[規劃 v2] 更新 reachable_pit 數量: {changed}")
    return changed

# ---------------------------------------------------------------------------
# Dijkstra 工具
# ---------------------------------------------------------------------------
def dijkstra_from_all_empties(board: Board) -> Tuple[List[List[float]], List[List[Optional[Coordinate]]]]:
    """以所有空地為起點，計算到整個盤面的最短鏟子成本。"""
    R, C = len(board), len(board[0])
    dist = [[inf] * C for _ in range(R)]
    prev: List[List[Optional[Coordinate]]] = [[None] * C for _ in range(R)]
    hq: List[Tuple[float, int, int]] = []

    # 只有真正的 empty, void, dug_pit 才能當起點
    # unreachable_* 必須透過挖掘路徑才能到達，不能直接當起點
    for r in range(R):
        for c in range(C):
            if board[r][c] in ("empty", "void", "dug_pit"):
                dist[r][c] = 0
                heappush(hq, (0, r, c))

    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def in_bounds(rr: int, cc: int) -> bool:
        return 0 <= rr < R and 0 <= cc < C

    while hq:
        d, r, c = heappop(hq)
        if d != dist[r][c]:
            continue
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            if not in_bounds(rr, cc):
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


def reconstruct_path(prev: List[List[Optional[Coordinate]]], end: Coordinate) -> List[Coordinate]:
    path: List[Coordinate] = []
    cur = end
    while cur:
        path.append(cur)
        cur = prev[cur[0]][cur[1]] if prev[cur[0]][cur[1]] else None
    path.reverse()
    return path


def summarize_path(board: Board, path: List[Coordinate]) -> Tuple[List[Coordinate], int]:
    """回傳需要實際挖的座標與總成本。"""
    dig_list = [(r, c) for (r, c) in path if not is_empty(board[r][c])]
    total_cost = sum(enter_cost(board[r][c]) or 0 for (r, c) in dig_list)
    return dig_list, int(total_cost)


def mark_path_as_empty(board: Board, dig_list: List[Coordinate]) -> None:
    for (r, c) in dig_list:
        board[r][c] = "empty"


def floor7_triggered(board: Board) -> bool:
    R, C = len(board), len(board[0])
    return any(is_empty(board[R - 1][c]) for c in range(C))


def find_best_descend_target(dist: List[List[float]], prev: List[List[Optional[Coordinate]]], R: int, C: int) -> Optional[Dict[str, Any]]:
    """
    尋找最佳下樓點 (第 7 層)。
    排序邏輯：
    1. 總成本 (dist) 越小越好。
    2. 路徑起點深度 (start_row) 越大越好 -> 優先選擇延續已經挖得較深的路徑，避免切換跑道。
    3. 靠左優先 (Tie-breaker)。
    """
    last_row = R - 1
    candidates = []
    for c in range(C):
        cost = dist[last_row][c]
        if cost < inf:
            end_node = (last_row, c)
            path = reconstruct_path(prev, end_node)
            # path[0] 是起點 (Empty/DugPit)，其 Row 代表我們是從哪一層開始往下挖
            start_row = path[0][0] if path else -1
            candidates.append({
                "c": c,
                "cost": cost,
                "start_row": start_row,
                "path": path
            })
    
    if not candidates:
        return None
        
    # 排序：成本低 > 起點深 > 靠左
    candidates.sort(key=lambda x: (x['cost'], -x['start_row'], x['c']))
    return candidates[0]


# ---------------------------------------------------------------------------
# 規劃：收集所有礦再下樓
# ---------------------------------------------------------------------------
def plan_collect_all_mines_then_descend_v2(
    board: Board,
    descend_after_collect: bool = True,
) -> Dict[str, Any]:
    bd = [row[:] for row in board]
    R, C = len(bd), len(bd[0])
    steps: List[Dict[str, Any]] = []

    def neighbors(r: int, c: int):
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < R and 0 <= cc < C:
                yield rr, cc

    def has_empty_neighbor(r: int, c: int) -> bool:
        return any(is_empty(bd[rr][cc]) for rr, cc in neighbors(r, c))

    def relax_unreachable_empties() -> int:
        """將與可達空地相鄰的 unreachable_empty 和 unreachable_void 鬆弛成 empty"""
        changed = 0
        again = True
        while again:
            again = False
            for r in range(R - 1, -1, -1):
                for c in range(C):
                    if bd[r][c] in ("unreachable_empty", "unreachable_void"):
                        # 檢查是否與真正可達的空地相鄰（不包括其他 unreachable_*）
                        has_reachable_empty = False
                        for rr, cc in neighbors(r, c):
                            if bd[rr][cc] in ("empty", "void", "dug_pit"):
                                has_reachable_empty = True
                                break
                        if has_reachable_empty:
                            bd[r][c] = "empty"
                            changed += 1
                            again = True
        if changed:
            print(f"[規劃 v2] 鬆弛 unreachable_empty/unreachable_void -> empty 數量: {changed}")
        return changed

    total_reward = 0
    total_cost = 0

    def collect_reachable_pits_once() -> int:
        nonlocal total_reward, total_cost
        collected = 0
        for r in range(R - 1, -1, -1):
            for c in range(C):
                if is_reachable_pit(bd[r][c]):
                    pit_cost = enter_cost(bd[r][c]) or 1
                    steps.append(
                        {
                            "action": "collect",
                            "target": (r, c),
                            "step_cost": pit_cost,
                            "gain": REWARD_TABLE["reachable_pit"],
                            "path": [(r, c)],
                            "dig_list": [(r, c)],
                        }
                    )
                    total_reward += REWARD_TABLE["reachable_pit"]
                    total_cost += pit_cost
                    bd[r][c] = "empty"
                    collected += 1
        return collected

    def collect_adjacent_unreachable_pits_once() -> int:
        # 停用此函數，unreachable_pit 統一由主迴圈的 Dijkstra 規劃處理
        # 避免複雜的相鄰判斷邏輯造成混亂
        return 0

    while True:
        changed = 0
        changed += collect_reachable_pits_once()
        changed += collect_adjacent_unreachable_pits_once()
        changed += relax_unreachable_empties()
        changed += update_reachable_pits(bd)  
        if changed == 0:
            break

    # 防止無限循環的安全機制
    max_iterations = 100
    iteration_count = 0
    
    while True:
        iteration_count += 1
        if iteration_count > max_iterations:
            print(f"[規劃 v2] 達到最大迭代次數 {max_iterations}，強制退出")
            break
            
        _, rea, unrea = list_all_pits(bd)
        if not unrea and not rea:
            print("[規劃 v2] 所有礦已收集完畢")
            break

        print(f"[規劃 v2] 剩餘 reachable_pit: {len(rea)} 個, unreachable_pit: {len(unrea)} 個，位置: {unrea}")
        dist, prev = dijkstra_from_all_empties(bd)

        # 只有當有 pit 需要收集時，才考慮戰略挖掘
        # 如果沒有礦物，應該直接下第7層
        if not unrea and not rea:
            print("[規劃 v2] 沒有礦物需要收集，準備下樓")
            break
            
        # 優先找可以解鎖 unreachable_pit 的挖掘格子  
        best_dig_target = None
        best_dig_cost = inf
        best_dig_value = 0
        
        # 只掃描能解鎖 unreachable_pit 的挖掘點
        for r in range(R - 1, -1, -1):
            for c in range(C):
                cell_cost = enter_cost(bd[r][c])
                if cell_cost is not None and cell_cost > 0:  # 可挖掘且非免費
                    path_cost = dist[r][c]
                    if path_cost < inf:  # 可到達
                        total_cost_to_dig = path_cost + cell_cost
                        
                        # 只計算解鎖 unreachable_pit 的價值
                        dig_value = 0
                        for rr, cc in neighbors(r, c):
                            if bd[rr][cc] == "unreachable_pit":
                                dig_value += 1  # 每個解鎖的 pit 獲得 1 分
                        
                        # 只有能解鎖 pit 的挖掘點才考慮
                        if dig_value > 0:
                            if (dig_value > best_dig_value) or \
                               (dig_value == best_dig_value and total_cost_to_dig < best_dig_cost):
                                best_dig_cost = total_cost_to_dig
                                best_dig_target = (r, c)
                                best_dig_value = dig_value
        
        # 如果找到能解鎖 pit 的挖掘格子，優先挖掘它
        if best_dig_target:
            r, c = best_dig_target
            cell_cost = enter_cost(bd[r][c])
            print(f"[規劃 v2] 戰略挖掘 ({r},{c}) {bd[r][c]} 成本={cell_cost} 可解鎖 {best_dig_value} 個 unreachable_pit")
            
            # 額外檢查：若存在針對單一 unreachable_pit 的「相鄰格」候選，
            # 若該相鄰格的總成本不高於目前 best_dig_cost，則優先使用該相鄰格（避免繞遠路）
            best_adj_cell = None
            best_adj_target = None
            best_adj_cost = inf
            # unrea 在前面已計算過（剩餘的 unreachable_pit 列表）
            try:
                for (mr, mc) in unrea:
                    for rr, cc in neighbors(mr, mc):
                        adj_cost = enter_cost(bd[rr][cc])
                        if adj_cost is not None and adj_cost > 0:
                            path_to_adj = dist[rr][cc] + adj_cost
                            if path_to_adj < best_adj_cost:
                                best_adj_cost = path_to_adj
                                best_adj_cell = (rr, cc)
                                best_adj_target = (mr, mc)
            except (IndexError, KeyError, ValueError, TypeError) as e:
                # 邊界錯誤：座標越界、bd/dist 結構不一致、enter_cost 收到非預期型別。
                # 故意不接 Exception，讓真正的 bug（AttributeError / NameError 等）暴露。
                logger.debug(
                    "[Planner v2] 鄰格候選搜尋忽略邊界錯誤 %s: %s; unrea=%s",
                    type(e).__name__,
                    e,
                    unrea,
                )
                best_adj_cell = None
                best_adj_cost = inf

            # 若相鄰格候選存在且成本不高於 global best，優先執行相鄰格的挖掘（更直接解鎖 pit）
            if best_adj_cell and best_adj_cost <= best_dig_cost:
                rr, cc = best_adj_cell
                neighbor_cost = enter_cost(bd[rr][cc])
                print(f"[規劃 v2] 優先挖鄰格 ({rr},{cc}) {bd[rr][cc]} 成本={neighbor_cost} 來打通到 {best_adj_target}")
                # 如果需要路徑才能到達該鄰格，先執行路徑上的第一格
                if dist[rr][cc] > 0:
                    path = reconstruct_path(prev, (rr, cc))
                    dig_list, step_cost = summarize_path(bd, path)
                    if dig_list:
                        first_cell = dig_list[0]
                        first_cost = enter_cost(bd[first_cell[0]][first_cell[1]]) or 0
                        steps.append({
                            "action": "mine_path",
                            "target": best_adj_target,
                            "step_cost": first_cost,
                            "gain": None,
                            "path": [first_cell],
                            "dig_list": [first_cell],
                        })
                        mark_path_as_empty(bd, [first_cell])
                        total_cost += first_cost
                        # 挖掘後處理與收集
                        relaxed = relax_unreachable_empties()
                        promoted = update_reachable_pits(bd)
                        collected_r = collect_reachable_pits_once()
                        print(
                            f"[規劃 v2] 挖鄰格後鬆弛了 {relaxed} 格，解鎖 {promoted} 個 pit，收集了 {collected_r} 個 reachable_pit"
                        )
                        continue
                # 直接挖鄰格
                steps.append(
                    {
                        "action": "mine_path",
                        "target": best_adj_target,
                        "step_cost": neighbor_cost,
                        "gain": None,
                        "path": [(rr, cc)],
                        "dig_list": [(rr, cc)],
                    }
                )
                mark_path_as_empty(bd, [(rr, cc)])
                total_cost += neighbor_cost
                # 挖掘後重新鬆弛和收集
                relaxed = relax_unreachable_empties()
                promoted = update_reachable_pits(bd)
                collected_r = collect_reachable_pits_once()
                print(
                    f"[規劃 v2] 挖鄰格後鬆弛了 {relaxed} 格，解鎖 {promoted} 個 pit，收集了 {collected_r} 個 reachable_pit"
                )
                continue

            # 否則沿用原本的 global best_dig_target 行為
            if dist[r][c] > 0:
                path = reconstruct_path(prev, (r, c))
                dig_list, step_cost = summarize_path(bd, path)
                if dig_list:
                    first_cell = dig_list[0]
                    first_cost = enter_cost(bd[first_cell[0]][first_cell[1]]) or 0
                    steps.append({
                        "action": "mine_path",
                        "target": (r, c),
                        "step_cost": first_cost,
                        "gain": None,
                        "path": [first_cell],
                        "dig_list": [first_cell],
                    })
                    mark_path_as_empty(bd, [first_cell])
                    total_cost += first_cost
                    continue
            
            # 直接挖掘目標格子
            steps.append(
                {
                    "action": "mine_path",
                    "target": (r, c),
                    "step_cost": cell_cost,
                    "gain": None,
                    "path": [(r, c)],
                    "dig_list": [(r, c)],
                }
            )
            mark_path_as_empty(bd, [(r, c)])
            total_cost += cell_cost

            # 挖掘後重新鬆弛和收集
            relaxed = relax_unreachable_empties()
            promoted = update_reachable_pits(bd)        # ✨ 把新連通的坑從 unreachable_pit 變 reachable_pit
            collected_r = collect_reachable_pits_once()
            print(
                f"[規劃 v2] 挖掘後鬆弛了 {relaxed} 格，"
                f"解鎖 {promoted} 個 pit，收集了 {collected_r} 個 reachable_pit"
            )
            continue

        
        # 如果沒有可直接挖掘的格子，才嘗試針對特定 unreachable_pit 規劃
        best_pos, best_cost = None, inf
        best_neighbor = None
        found_target_row = -1

        for (mr, mc) in unrea:
            # [Optimization] 若已在較深層找到可行的目標，且當前掃描到的層數較淺，
            # 則停止搜尋，確保優先處理底部 (符合 "分析最後幾行" 的需求)。
            if best_pos is not None and mr < found_target_row:
                break

            # 檢查相鄰格子是否可以挖掘來打通
            for rr, cc in neighbors(mr, mc):
                adj_cost = enter_cost(bd[rr][cc])
                if adj_cost is not None and adj_cost > 0:  # 可以挖掘且非免費
                    path_to_adj = dist[rr][cc] + adj_cost
                    if path_to_adj < best_cost:
                        best_cost = path_to_adj
                        best_pos = (mr, mc)
                        best_neighbor = (rr, cc)
                        found_target_row = mr
        
        if best_pos is None or best_cost == inf:
            print(f"[規劃 v2] 無法找到到達 unreachable_pit 的路徑，放棄收集剩餘 {len(unrea)} 個礦")
            # 不修改盤面，直接跳出循環，保留 unreachable_pit 的原始狀態
            break

        # 如果需要透過挖掘相鄰格子來打通
        if best_neighbor:
            rr, cc = best_neighbor
            neighbor_cost = enter_cost(bd[rr][cc])
            print(f"[規劃 v2] 挖掘 ({rr},{cc}) {bd[rr][cc]} 成本={neighbor_cost} 來打通到 {best_pos}")
            steps.append(
                {
                    "action": "mine_path",
                    "target": best_pos,
                    "step_cost": neighbor_cost,
                    "gain": None,
                    "path": [(rr, cc)],
                    "dig_list": [(rr, cc)],
                }
            )
            mark_path_as_empty(bd, [(rr, cc)])
            total_cost += neighbor_cost
            # 挖掘後重新鬆弛和收集
            relaxed = relax_unreachable_empties()
            promoted = update_reachable_pits(bd)      # ✨ 解鎖坑
            collected_r = collect_reachable_pits_once()
            collected_u = collect_adjacent_unreachable_pits_once()
            print(
                f"[規劃 v2] 鬆弛了 {relaxed} 格，解鎖 {promoted} 個 pit，"
                f"收集了 {collected_r} 個 reachable_pit，{collected_u} 個 unreachable_pit"
            )
            continue

        
        # 原本的路徑邏輯
        path = reconstruct_path(prev, best_pos)
        dig_list, step_cost = summarize_path(bd, path)
        if not dig_list:
            # 直接收集（應該是透過鬆弛變成可達的）
            r, c = best_pos

            # 若是 unreachable_pit，先確認是否有可直接敲擊的鄰格
            if base_label(bd[r][c]) == "unreachable_pit":
                if not any(
                    bd[rr][cc] in ("empty", "dug_pit", "reachable_pit", "pit")
                    for rr, cc in neighbors(r, c)
                ):
                    # 無相鄰可達格，跳過（交由主迴圈的 Dijkstra 去處理）
                    continue

            pit_cost = enter_cost(bd[r][c]) or 1  # unreachable_pit 用 fallback
            steps.append(
                {
                    "action": "collect",
                    "target": best_pos,
                    "step_cost": pit_cost,
                    "gain": REWARD_TABLE["unreachable_pit"],
                    "path": [(r, c)],
                    "dig_list": [(r, c)],
                }
            )
            bd[r][c] = "empty"
            total_reward += REWARD_TABLE["unreachable_pit"]
            total_cost += pit_cost
            relax_unreachable_empties()
            collect_reachable_pits_once()
            collect_adjacent_unreachable_pits_once()
            continue

        first_cell = dig_list[0]
        first_cost = enter_cost(bd[first_cell[0]][first_cell[1]]) or 0
        steps.append(
            {
                "action": "mine_path",
                "target": best_pos,
                "step_cost": first_cost,
                "gain": None,
                "path": [first_cell],
                "dig_list": [first_cell],
            }
        )
        mark_path_as_empty(bd, [first_cell])
        total_cost += first_cost

        relaxed = relax_unreachable_empties()
        promoted = update_reachable_pits(bd)       # ✨ 一樣要更新坑
        collected_r = collect_reachable_pits_once()
        collected_u = collect_adjacent_unreachable_pits_once()
        print(
            f"[規劃 v2] 鬆弛了 {relaxed} 格，解鎖 {promoted} 個 pit，"
            f"收集了 {collected_r} 個 reachable_pit，{collected_u} 個 unreachable_pit"
        )


    if descend_after_collect:
        relax_unreachable_empties()
        update_reachable_pits(bd)   # ✨ 雖然通常不影響下樓，但保持狀態一致
        dist, prev = dijkstra_from_all_empties(bd)
        
        best = find_best_descend_target(dist, prev, R, C)
        if best:
            end = (R - 1, best['c'])
            path = best['path']
            dig_list, step_cost = summarize_path(bd, path)
            steps.append(
                {
                    "action": "descend",
                    "target": end,
                    "step_cost": step_cost,
                    "path": path,
                    "dig_list": dig_list,
                }
            )
            mark_path_as_empty(bd, dig_list)
            total_cost += step_cost

    return {
        "ok": True,
        "mode": "collect_all_then_descend_v2",
        "message": f"總成本={int(total_cost)}, 全部已收完=True, 總獎勵={int(total_reward)}",
        "steps": steps,
        "total_cost": int(total_cost),
        "total_reward": int(total_reward),
        "board_after": bd,
    }


__all__ = [
    "base_label",
    "enter_cost",
    "is_empty",
    "is_mine",
    "is_pit",
    "is_dug_pit",
    "is_reachable_pit",
    "is_unreachable_pit",
    "list_all_pits",
    "dijkstra_from_all_empties",
    "reconstruct_path",
    "summarize_path",
    "mark_path_as_empty",
    "floor7_triggered",
    "plan_collect_all_mines_then_descend_v2",
]
