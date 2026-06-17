from __future__ import annotations

import heapq
import time
from typing import List, Dict, Any, Tuple, Optional, Set
from copy import deepcopy
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
from miner.planning.planner import is_empty as is_air

# 定義配置類別，方便 AI 自動優化
class PlannerConfig:
    def __init__(self):
        self.cost_pickaxe = 1.0
        self.cost_item = 2.99
        self.weight_h = 1.5      # 啟發式函數權重 (A* 的 epsilon)
        self.weight_chest = 10.0 # 每個未挖掘寶箱的懲罰權重
        self.weight_depth = 5.0  # 未開啟下一層的懲罰權重

DEFAULT_CONFIG = PlannerConfig()

def get_hp(label: str) -> int:
    if "rock" in label: return 2
    if "dirt" in label or "pit" in label: return 1
    return 0


def _descent_fallback_step(board: List[List[str]]) -> Optional[Dict[str, Any]]:
    """When A* finds no goal-improving plan (no pits left AND floor7 already
    open, the WS no_pit case), emit ONE downward dig so the runtime keeps
    scrolling — mirrors v4's no_pit descent. Without this v1 returns an empty
    plan and the WS supervised loop stalls ('v1 會有空的問題').

    Picks the deepest diggable cell (largest row, tie-break smallest col).
    'Diggable' matches get_valid_actions: not air and not unreachable_.
    Returns None when nothing is reachably diggable (honest empty plan)."""
    R, C = len(board), len(board[0])
    for r in range(R - 1, -1, -1):
        for c in range(C):
            label = board[r][c]
            if not is_air(label) and not label.startswith("unreachable_"):
                return {"type": "dig", "pos": (r, c),
                        "step_cost": float(get_hp(label))}
    return None

class SmartState:
    def __init__(self, board: List[List[str]],
                 shovels: float,
                 items: Dict[str, int],
                 history: List[Dict] = None,
                 accumulated_cost: float = 0.0,
                 config: PlannerConfig = DEFAULT_CONFIG):
        self.board = board
        self.shovels = shovels
        self.items = items
        self.history = history or []
        self.accumulated_cost = accumulated_cost
        self.config = config
        
        # 統計資訊
        self.remaining_pits = self._count_pits()  # 只計算 pit（寶箱）
        self.f7_open = self._is_f7_open()
        
        self.signature = (
            tuple(tuple(row) for row in self.board),
            tuple(sorted(self.items.items()))
        )

    def _count_pits(self) -> int:
        """計算剩餘需要挖掘的寶箱（pit），不包括 dirt 和 rock。"""
        count = 0
        for row in self.board:
            for cell in row:
                if "pit" in cell and "dug" not in cell:
                    count += 1
        return count

    def _is_f7_open(self) -> bool:
        """檢查第 7 層（最後一行）是否有可達空氣，表示已真正打通到底部。
        
        不可達的空氣（unreachable_empty）不算，因為需要繼續挖掘才能連通。
        """
        return any(
            is_air(cell) and not cell.startswith("unreachable_")
            for cell in self.board[-1]
        )

    def get_heuristic(self) -> float:
        """
        啟發式函數 h(n): 估計到達目標的剩餘成本
        """
        h = 0.0
        # 懲罰未挖完的寶箱 (pit)
        h += self.remaining_pits * self.config.weight_chest
        # 懲罰未打通到底部
        if not self.f7_open:
            h += self.config.weight_depth
        return h

    def get_priority(self) -> float:
        """
        f(n) = g(n) + w * h(n)
        """
        return self.accumulated_cost + self.config.weight_h * self.get_heuristic()

    def __lt__(self, other: SmartState):
        return self.get_priority() < other.get_priority()

class SmartPlanner:
    def __init__(self, board: List[List[str]], shovels: float = 100, items: Dict[str, int] = None, config: PlannerConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.initial_board = [list(row) for row in board]
        self.initial_shovels = shovels
        self.initial_items = items or {'drill': 0, 'bomb': 0}
        self._update_board_exposure(self.initial_board)

    def _update_board_exposure(self, board: List[List[str]]):
        """
        修正：只有連通到「已可達空氣」的方塊才是可達的。
        注意：unreachable_* 空氣不能作為起始可達空氣，否則會把封閉空腔誤判為可達。
        """
        R, C = len(board), len(board[0])
        reachable_air = set()
        queue = []
        
        # 規則 1: 只把「非 unreachable_」空氣當作 BFS 起點
        # （例如 empty / void / dug_pit）。
        # unreachable_empty / unreachable_void 不能直接當起點。
        for r in range(R):
            for c in range(C):
                label = board[r][c]
                if is_air(label) and not label.startswith("unreachable_"):
                    board[r][c] = "empty"  # 統一標記為 "empty"
                    reachable_air.add((r, c))
                    queue.append((r, c))
        
        # 如果沒有任何空氣被找到，這表示分類有問題
        if not queue:
            # 嘗試診斷：看棋盤中有哪些標籤
            import collections
            label_counts = collections.Counter()
            for row in board:
                for cell in row:
                    label_counts[cell] += 1
            # 記錄供調試
            pass

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
                        # 若鄰格為空氣（含 unreachable_*），且與可達空氣相連，才可轉為可達
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
        """H9 fix: 過濾掉成本超過剩餘 shovels 的動作，避免 simulate_action
        產生 state.shovels < 0 的非法節點。"""
        actions = []
        R, C = len(state.board), len(state.board[0])
        cost_pickaxe = self.config.cost_pickaxe
        cost_item = self.config.cost_item
        for r in range(R):
            for c in range(C):
                label = state.board[r][c]
                # 只有真正暴露且非空氣的才能挖
                if not is_air(label) and not label.startswith("unreachable_"):
                    dig_cost = float(get_hp(label)) * cost_pickaxe
                    if dig_cost > state.shovels:
                        continue  # H9: 預算不足
                    actions.append({"type": "dig", "pos": (r, c)})

        # 道具必須放在可達空氣上
        air_cells = [(r, c) for r in range(R) for c in range(C) 
                     if is_air(state.board[r][c]) and not state.board[r][c].startswith("unreachable_")]

        # H9: 使用道具的 shovel 成本固定為 cost_item；預算不足就整批跳過
        item_affordable = cost_item <= state.shovels

        if state.items.get('drill', 0) > 0 and item_affordable:
            for r, c in air_cells:
                if any(not is_air(state.board[nr][c]) for nr in range(r + 1, R)):
                    actions.append({"type": "use", "item": "drill", "pos": (r, c)})

        if state.items.get('bomb', 0) > 0 and item_affordable:
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
            cost = float(get_hp(label)) * self.config.cost_pickaxe
            new_board[action['pos'][0]][action['pos'][1]] = "empty"
        elif action['type'] == 'use':
            item = action['item']
            new_items[item] -= 1
            cost = self.config.cost_item
            targets = get_drill_affected_cells(*action['pos'], R, C) if item == 'drill' else get_bomb_affected_cells(*action['pos'], R, C)
            for nr, nc in targets:
                new_board[nr][nc] = "empty"

        self._update_board_exposure(new_board)
        action_with_cost = action.copy()
        action_with_cost['step_cost'] = cost
        return SmartState(new_board, state.shovels - cost, new_items, state.history + [action_with_cost], state.accumulated_cost + cost, config=self.config)

    def solve(self, max_nodes: int = 2000) -> Dict[str, Any]:
        start_state = SmartState(self.initial_board, self.initial_shovels, self.initial_items, config=self.config)
        pq = [start_state]
        seen = {start_state.signature: 0.0}
        best_finished = None
        nodes_explored = 0

        while pq and nodes_explored < max_nodes:
            current = heapq.heappop(pq)
            nodes_explored += 1

            # 目標：挖完所有寶箱 (pit) 且打通底部
            # 當沒有寶箱時，仍需確保底部可達
            if current.remaining_pits == 0 and current.f7_open:
                if best_finished is None or current.accumulated_cost < best_finished.accumulated_cost:
                    best_finished = current
                break

            valid_actions = self.get_valid_actions(current)
            for action in valid_actions:
                next_state = self.simulate_action(current, action)
                # 簡單過濾：如果這一動沒意義就跳過 (這部分邏輯維持原樣)
                if next_state.signature not in seen or next_state.accumulated_cost < seen[next_state.signature]:
                    seen[next_state.signature] = next_state.accumulated_cost
                    heapq.heappush(pq, next_state)

        res = best_finished or current
        steps = res.history
        if not steps:
            fallback = _descent_fallback_step(res.board)
            if fallback is not None:
                steps = [fallback]
        return {
            "ok": True,
            "steps": steps,
            "total_cost": res.accumulated_cost,
            "remaining_pits": res.remaining_pits,
            "floor7_open": res.f7_open,
            "message": f"A* Planning complete (nodes={nodes_explored})."
        }

def plan_smart(board: List[List[str]], shovels: float = 100, items: Dict[str, int] = None, config: PlannerConfig = None) -> Dict[str, Any]:
    return SmartPlanner(board, shovels, items, config).solve()