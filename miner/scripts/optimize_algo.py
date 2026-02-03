import heapq
import copy
import time
from typing import List, Tuple, Dict, Optional, Set
from miner.core.config import HIT_TABLE, REWARD_TABLE, COST_TABLE
from miner.planning.planner import list_all_pits, is_empty, base_label, enter_cost
from miner.scripts.simulator import GameSimulator

# ==========================================
#  State Representation for Search
# ==========================================

class State:
    def __init__(self, board, shovels, items, history=None, accumulated_cost=0, collected_rewards=0):
        self.board = board
        self.shovels = shovels
        self.items = items # Dict: {'drill': n, 'bomb': n}
        self.history = history or [] # List of actions
        self.accumulated_cost = accumulated_cost
        self.collected_rewards = collected_rewards
        
        # Cache key for deduplication (board tuple, shovels, item counts)
        self.signature = self._make_signature()

    def _make_signature(self):
        # Flatten board to tuple of strings
        b_tuple = tuple(tuple(row) for row in self.board)
        i_tuple = tuple(sorted(self.items.items()))
        return (b_tuple, i_tuple) # Shovels aren't part of signature if we only care about "min cost to reach board state"

    def __lt__(self, other):
        # Priority Queue needs this.
        # We prefer higher rewards, then lower cost.
        if self.collected_rewards != other.collected_rewards:
            return self.collected_rewards > other.collected_rewards
        return self.accumulated_cost < other.accumulated_cost

# ==========================================
#  The Searcher (Beam Search / A*)
# ==========================================

class MiningSearcher:
    def __init__(self, initial_board, initial_shovels, initial_items):
        self.initial_state = State(initial_board, initial_shovels, initial_items)
        self.simulator = GameSimulator(initial_board, initial_shovels, initial_items)
        
    def get_valid_actions(self, state: State) -> List[Dict]:
        """
        Generate reasonable actions. 
        Optimization: Don't try to dig every cell. Only 'frontier' cells.
        Don't bomb empty space.
        """
        actions = []
        R, C = len(state.board), len(state.board[0])
        
        # 1. Digging Actions (Only cells reachable via BFS or adjacent to empty)
        # Actually, simpler: Dig any cell that isn't empty, but cost calculation handles reachability.
        # To reduce search space: Only dig cells adjacent to 'empty' or 'dug_pit'.
        
        frontier_candidates = set()
        for r in range(R):
            for c in range(C):
                if base_label(state.board[r][c]) in ("empty", "dug_pit", "void"):
                    # Check neighbors
                    for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < R and 0 <= cc < C:
                            lbl = base_label(state.board[rr][cc])
                            if lbl not in ("empty", "dug_pit", "void"):
                                frontier_candidates.add((rr, cc))
        
        for r, c in frontier_candidates:
             actions.append({"type": "dig", "pos": (r, c)})

        # 2. Item Actions
        # Limit drill to columns (0..C-1) at the highest non-empty row?
        # Actually user can drill anywhere. But typically you drill from top of a column.
        # Or bomb clusters.
        
        if state.items.get('drill', 0) > 0:
            # Try drilling every column
            for c in range(C):
                # Find highest non-empty row in this col to target?
                # Actually simulator allows targeting any cell.
                # Heuristic: Target row 0-3 is usually enough.
                for r in range(4):
                    actions.append({"type": "use", "item": "drill", "pos": (r, c)})

        if state.items.get('bomb', 0) > 0:
            # Try bombing relevant areas (where blocks exist)
            # Heuristic: Stride 2 to reduce count?
            for r in range(1, R, 2):
                for c in range(1, C, 2):
                    actions.append({"type": "use", "item": "bomb", "pos": (r, c)})
                    
        return actions

    def evaluate_state(self, state: State) -> float:
        """
        Heuristic function for A* / Beam Search.
        Lower is better.
        """
        # Goal: Maximize Reward, Minimize Cost.
        # Cost = Shovels spent.
        # Reward = Value of pits collected.
        
        # We want to minimize: Cost - (Reward * Weight)
        # Weight depends on how much we value rewards vs saving shovels.
        # Assuming 1 pit ~ 5-10 shovels worth?
        
        score = state.accumulated_cost - (state.collected_rewards * 100) # Big weight for rewards
        
        # Heuristic: Estimated cost to collect REMAINING rewards?
        # This is hard. For now, just use current state.
        
        return score

    def solve(self, max_depth=5, beam_width=20):
        """
        Run Beam Search.
        max_depth: How many actions lookahead.
        beam_width: Keep top K states per depth.
        """
        current_beam = [self.initial_state]
        best_end_state = self.initial_state
        
        for depth in range(max_depth):
            print(f"Depth {depth}: Analyzing {len(current_beam)} states...")
            next_beam_candidates = []
            
            seen_signatures = set()
            
            for state in current_beam:
                # Check if done (all pits collected)
                _, reachable, unreachable = list_all_pits(state.board)
                if not reachable and not unreachable:
                    if state.collected_rewards > best_end_state.collected_rewards or \
                       (state.collected_rewards == best_end_state.collected_rewards and state.accumulated_cost < best_end_state.accumulated_cost):
                        best_end_state = state
                    continue # No need to search further from this state
                
                valid_actions = self.get_valid_actions(state)
                
                for action in valid_actions:
                    # SIMULATE ACTION
                    # We need a lightweight simulation. 
                    # The GameSimulator class is a bit heavy, let's copy state manually or adapt.
                    # For accuracy, we use the GameSimulator logic but locally.
                    
                    sim = GameSimulator(state.board, state.shovels, state.items)
                    obs, reward, done, info = sim.step(action)
                    
                    if info.get("failed"):
                        continue
                        
                    # Calculate cost of this specific step
                    # Shovels spent = old_shovels - new_shovels
                    step_cost = state.shovels - sim.shovels
                    
                    # Create new state
                    new_state = State(
                        board=obs,
                        shovels=sim.shovels,
                        items=sim.items_left,
                        history=state.history + [action],
                        accumulated_cost=state.accumulated_cost + step_cost,
                        collected_rewards=state.collected_rewards + reward
                    )
                    
                    if new_state.signature in seen_signatures:
                        continue
                    seen_signatures.add(new_state.signature)
                    
                    # Heuristic Score
                    score = self.evaluate_state(new_state)
                    next_beam_candidates.append((score, new_state))
            
            # Select top K
            next_beam_candidates.sort(key=lambda x: x[0]) # Sort by score (lower better)
            current_beam = [s for _, s in next_beam_candidates[:beam_width]]
            
            if not current_beam:
                break
                
            # Update best found so far
            for state in current_beam:
                 if state.collected_rewards > best_end_state.collected_rewards or \
                   (state.collected_rewards == best_end_state.collected_rewards and state.accumulated_cost < best_end_state.accumulated_cost):
                    best_end_state = state

        return best_end_state.history, best_end_state

def test_scenario():
    # A tricky scenario: 
    # Top layer is rock (hard to dig).
    # Below is a cluster of diamonds (pits).
    # Using a bomb/drill at the top opens the way cheaply.
    
    board = [
        ["rock", "rock", "rock", "rock", "rock", "rock"],
        ["rock", "unreachable_pit", "unreachable_pit", "unreachable_pit", "rock", "rock"],
        ["rock", "unreachable_pit", "unreachable_pit", "unreachable_pit", "rock", "rock"],
        ["rock", "rock", "rock", "rock", "rock", "rock"],
        ["empty", "empty", "empty", "empty", "empty", "empty"],
        ["empty", "empty", "empty", "empty", "empty", "empty"],
        ["empty", "empty", "empty", "empty", "empty", "empty"],
    ]
    
    # We have few shovels, but 1 drill and 1 bomb.
    searcher = MiningSearcher(board, initial_shovels=100, initial_items={'drill': 1, 'bomb': 1})
    
    print("Starting Search...")
    start_time = time.time()
    best_actions, end_state = searcher.solve(max_depth=4, beam_width=100)
    duration = time.time() - start_time
    
    print(f"\nSearch Complete in {duration:.2f}s")
    print(f"Total Cost: {end_state.accumulated_cost}")
    print(f"Rewards: {end_state.collected_rewards}")
    print("Actions:")
    for i, act in enumerate(best_actions):
        print(f" {i+1}. {act}")

if __name__ == "__main__":
    test_scenario()
