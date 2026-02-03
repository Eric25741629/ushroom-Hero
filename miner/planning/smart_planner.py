from __future__ import annotations

import heapq
import time
from typing import List, Dict, Any, Tuple, Optional, Set
from copy import deepcopy
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells

# 定義成本
COST_PICKAXE = 1.0
COST_ITEM = 2.99

def is_air(label: str) -> bool:
    if label.startswith("unreachable_"): return False
    return label in ("empty", "void", "dug_pit")

def get_hp(label: str) -> int:
    if "rock" in label: return 2
    if "dirt" in label or "pit" in label: return 1
    return 0

class SmartState:
    def __init__(self, board: List[List[str]], 
                 shovels: float, 
                 items: Dict[str, int], 
                 history: List[Dict] = None, 
                 accumulated_cost: float = 0.0):
        self.board = board
        self.shovels = shovels
        self.items = items
        self.history = history or []
        self.accumulated_cost = accumulated_cost
        
        # 統計資訊
        self.remaining_pits = self._count_pits()
        self.f7_open = self._is_f7_open()
        
        self.signature = (
            tuple(tuple(row) for row in self.board),
            tuple(sorted(self.items.items()))
        )

    def _count_pits(self) -> int:
        return sum(1 for row in self.board for cell in row if "pit" in cell and "dug" not in cell)

    def _is_f7_open(self) -> bool:
        return any(is_air(cell) for cell in self.board[-1])

    def count_pits_in_top_rows(self, rows=2) -> int:
        count = 0
        for r in range(min(rows, len(self.board))):
            count += sum(1 for cell in self.board[r] if "pit" in cell and "dug" not in cell)
        return count

    def get_priority(self) -> Tuple[int, int, float]:
        if self.remaining_pits > 0:
            return (self.remaining_pits, 1 if self.f7_open else 0, self.accumulated_cost)
        else:
            return (0, 0 if self.f7_open else 1, self.accumulated_cost)

    def __lt__(self, other: SmartState):
        return self.get_priority() < other.get_priority()

class SmartPlanner:
    def __init__(self, board: List[List[str]], shovels: float = 100, items: Dict[str, int] = None):
        self.initial_board = [list(row) for row in board]
        self.initial_shovels = shovels
        self.initial_items = items or {'drill': 0, 'bomb': 0}
        self._update_board_exposure(self.initial_board)

    def _update_board_exposure(self, board: List[List[str]]):
        """
        修正：只有連通到地表(Row 0)或已連通空氣的方塊才是可達的。
        """
        R, C = len(board), len(board[0])
        reachable_air = set()
        queue = []
        
        # 規則 1: Row 0 的所有空氣是起點
        for c in range(C):
            if is_air(board[0][c]):
                reachable_air.add((0, c))
                queue.append((0, c))
        
        # 規則 2: 已經標記為 reachable (非 unreachable_) 的空氣也應該是起點 
        # (這是為了處理前端傳來的已知可達區域)
        for r in range(R):
            for c in range(C):
                if is_air(board[r][c]) and not board[r][c].startswith("unreachable_"):
                    if (r, c) not in reachable_air:
                        reachable_air.add((r, c))
                        queue.append((r, c))

        # BFS 擴散連通空氣
        head = 0
        while head < len(queue):
            r, c = queue[head]
            head += 1
            for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < R and 0 <= nc < C:
                    label = board[nr][nc]
                    if (nr, nc) not in reachable_air:
                        # 如果是空氣（即使原本不可達），一旦與可達空氣相連，就變為可達
                        if is_air(label) or label in ("unreachable_empty", "unreachable_void"):
                            board[nr][nc] = "empty" # 揭開
                            reachable_air.add((nr, nc))
                            queue.append((nr, nc))

        # 更新實體方塊的暴露狀態：只有相鄰可達空氣的實體才是 reachable
        for r in range(R):
            for c in range(C):
                label = board[r][c]
                if label.startswith("unreachable_") and label not in ("unreachable_empty", "unreachable_void"):
                    # 檢查四周是否有可達空氣
                    exposed = False
                    for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                        nr, nc = r + dr, c + dc
                        if (nr, nc) in reachable_air:
                            exposed = True
                            break
                    if exposed:
                        board[r][c] = label.replace("unreachable_", "")

    def get_valid_actions(self, state: SmartState) -> List[Dict]:
        actions = []
        R, C = len(state.board), len(state.board[0])
        for r in range(R):
            for c in range(C):
                label = state.board[r][c]
                # 只有真正暴露且非空氣的才能挖
                if not is_air(label) and not label.startswith("unreachable_"):
                    actions.append({"type": "dig", "pos": (r, c)})

        # 道具必須放在可達空氣上
        air_cells = [(r, c) for r in range(R) for c in range(C) 
                     if is_air(state.board[r][c]) and not state.board[r][c].startswith("unreachable_")]
        
        if state.items.get('drill', 0) > 0:
            for r, c in air_cells:
                if any(not is_air(state.board[nr][c]) for nr in range(r + 1, R)):
                    actions.append({"type": "use", "item": "drill", "pos": (r, c)})

        if state.items.get('bomb', 0) > 0:
            for r, c in air_cells:
                actions.append({"type": "use", "item": "bomb", "pos": (r, c)})
        return actions

    def simulate_action(self, state: SmartState, action: Dict) -> SmartState:
        new_board = [list(row) for row in state.board]
        new_items = state.items.copy()
        R, C = len(new_board), len(new_board[0])
        cost = 0.0
        
        if action['type'] == 'dig':
            label = new_board[action['pos'][0]][action['pos'][1]]
            cost = float(get_hp(label))
            new_board[action['pos'][0]][action['pos'][1]] = "empty"
        elif action['type'] == 'use':
            item = action['item']
            new_items[item] -= 1
            cost = COST_ITEM
            targets = get_drill_affected_cells(*action['pos'], R, C) if item == 'drill' else get_bomb_affected_cells(*action['pos'], R, C)
            for nr, nc in targets:
                new_board[nr][nc] = "empty"
        
        self._update_board_exposure(new_board)
        action_with_cost = action.copy()
        action_with_cost['step_cost'] = cost
        return SmartState(new_board, state.shovels - cost, new_items, state.history + [action_with_cost], state.accumulated_cost + cost)

    def solve(self, max_nodes: int = 2000) -> Dict[str, Any]:
        start_state = SmartState(self.initial_board, self.initial_shovels, self.initial_items)
        pq = [start_state]
        seen = {start_state.signature: 0.0}
        best_finished = None
        nodes_explored = 0
        
        while pq and nodes_explored < max_nodes:
            current = heapq.heappop(pq)
            nodes_explored += 1
            if current.remaining_pits == 0 and current.f7_open:
                if best_finished is None or current.accumulated_cost < best_finished.accumulated_cost:
                    best_finished = current
                break 

            for action in self.get_valid_actions(current):
                next_state = self.simulate_action(current, action)
                if next_state.f7_open and next_state.count_pits_in_top_rows(2) > 0:
                    continue
                if next_state.signature not in seen or next_state.accumulated_cost < seen[next_state.signature]:
                    seen[next_state.signature] = next_state.accumulated_cost
                    heapq.heappush(pq, next_state)

        res = best_finished or current
        return {
            "ok": True,
            "steps": res.history,
            "total_cost": res.accumulated_cost,
            "remaining_pits": res.remaining_pits,
            "floor7_open": res.f7_open,
            "message": "A* Planning complete."
        }

def plan_smart(board: List[List[str]], shovels: float = 100, items: Dict[str, int] = None) -> Dict[str, Any]:
    return SmartPlanner(board, shovels, items).solve()