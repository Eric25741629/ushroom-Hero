# Game simulator for miner - minimal env for RL
from typing import List, Tuple, Dict, Any, Optional
from copy import deepcopy
from miner.planning.planner import (
    base_label, enter_cost, is_empty, list_all_pits, update_reachable_pits,
    dijkstra_from_all_empties, reconstruct_path, summarize_path, mark_path_as_empty, floor7_triggered
)
from .config import REWARD_TABLE, HIT_TABLE
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells

Board = List[List[str]]

class GameSimulator:
    def __init__(self, board: Board, pickaxes: int = 20, items: Optional[Dict[str,int]] = None):
        self.init_board = deepcopy(board)
        self.pickaxes = pickaxes
        self.items = items or {"drill":0,"bomb":0}
        self.reset()

    def reset(self):
        self.board = deepcopy(self.init_board)
        self.shovels = self.pickaxes
        self.items_left = dict(self.items)
        self.total_reward = 0
        self.steps = 0
        return deepcopy(self.board)

    def step(self, action: Dict[str,Any]):
        """
        action: {"type":"dig","pos":(r,c)} or {"type":"use","item":"bomb"/"drill","pos":(r,c)}
        """
        reward = 0
        done = False
        info = {}
        self.steps += 1

        if action.get("type") == "dig":
            r,c = action["pos"]
            lbl = self.board[r][c]

            # Prevent directly digging an unreachable_pit unless it is adjacent
            # to a real reachable empty/dug/void cell. This avoids allowing the
            # agent to "dig" an unreachable pit without first making it reachable.
            if lbl == "unreachable_pit":
                H = len(self.board); W = len(self.board[0])
                has_adjacent_reachable = False
                for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
                    rr,cc = r+dr, c+dc
                    if 0 <= rr < H and 0 <= cc < W:
                        n_lbl = self.board[rr][cc]
                        if (not n_lbl.startswith("unreachable_")) and base_label(n_lbl) in ("empty","dug_pit","void","reachable_pit","pit"):
                            has_adjacent_reachable = True
                            break
                if not has_adjacent_reachable:
                    info["failed"] = "unreachable"
                    # return board unchanged, no reward, not done (let caller continue)
                    obs = deepcopy(self.board)
                    return obs, 0, False, info

            hits = HIT_TABLE.get(lbl, HIT_TABLE.get(base_label(lbl), 0))
            if hits <= 0:
                info["skipped"] = True
            else:
                cost = enter_cost(lbl) or 1
                if self.shovels < cost:
                    info["failed"] = "no_shovel"
                else:
                    self.shovels -= cost
                    # apply hits: convert pit -> dug_pit; obstacle -> empty
                    if base_label(lbl) in ("pit","reachable_pit","unreachable_pit"):
                        self.board[r][c] = "dug_pit"
                        reward += REWARD_TABLE.get("reachable_pit", REWARD_TABLE.get("pit",0))
                    else:
                        self.board[r][c] = "empty"
                    # relax & update
                    self._relax_and_update()

        elif action.get("type") == "use":
            item = action["item"]
            r,c = action["pos"]
            if self.items_left.get(item,0) <= 0:
                info["failed"] = "no_item"
            else:
                H = len(self.board); W = len(self.board[0])
                if item == "bomb":
                    affected = get_bomb_affected_cells(r, c, H, W)
                else: # drill
                    affected = get_drill_affected_cells(r, c, H, W)
                
                # apply effects
                for (rr,cc) in affected:
                    lbl = self.board[rr][cc]
                    if base_label(lbl) in ("pit","reachable_pit","unreachable_pit"):
                        self.board[rr][cc] = "dug_pit"
                        reward += REWARD_TABLE.get("reachable_pit", REWARD_TABLE.get("pit",0))
                    else:
                        b = base_label(lbl)
                        if b in ("dirt","rock","one_hit_rock"):
                            self.board[rr][cc] = "unreachable_empty" if lbl.startswith("unreachable_") else "empty"
                self.items_left[item] -= 1
                self._relax_and_update()

        else:
            info["failed"] = "unknown_action"

        self.total_reward += reward
        # done if no shovels or floor7 triggered or all pits collected
        _, rea, unrea = list_all_pits(self.board)
        if self.shovels <= 0:
            done = True; info["terminated"] = "no_shovels"
        elif floor7_triggered(self.board):
            done = True; info["terminated"] = "floor7"
        elif not rea and not unrea:
            done = True; info["terminated"] = "all_pits_collected"

        obs = deepcopy(self.board)
        return obs, reward, done, info

    def _relax_and_update(self):
        # relax unreachable_empty -> empty if adjacent to real empty
        R,C = len(self.board), len(self.board[0])
        changed = True
        while changed:
            changed = False
            for r in range(R):
                for c in range(C):
                    if self.board[r][c] == "unreachable_empty":
                        for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
                            rr,cc = r+dr, c+dc
                            if 0<=rr<R and 0<=cc<C and base_label(self.board[rr][cc]) in ("empty","dug_pit","void"):
                                self.board[r][c] = "empty"
                                changed = True
                                break
        update_reachable_pits(self.board)

    def render(self):
        # Compute column width to avoid truncation and ensure alignment
        max_label_len = 0
        for row in self.board:
            for cell in row:
                if len(cell) > max_label_len:
                    max_label_len = len(cell)
        # add padding
        col_w = max_label_len + 2
        for r in range(len(self.board)):
            print(''.join(f"{x:<{col_w}}" for x in self.board[r]))
        # Build well-formatted items string (sorted keys, consistent spacing)
        items_pairs = [f"{k}:{v}" for k, v in sorted(self.items_left.items())]
        items_str = ", ".join(items_pairs)
        # Print aligned status line (no omission)
        print(f"shovels={self.shovels:<3}  items={{ {items_str} }}  reward={self.total_reward:<5}  steps={self.steps}")

# Simple random agent for testing
def random_agent_step(sim: GameSimulator):
    import random
    # try collect reachable pits first
    R,C = len(sim.board), len(sim.board[0])
    for r in range(R):
        for c in range(C):
            if base_label(sim.board[r][c]) in ("reachable_pit","pit"):
                return {"type":"dig","pos":(r,c)}
    # else random dig on non-empty that costs <= shovels
    candidates=[]
    for r in range(R):
        for c in range(C):
            if base_label(sim.board[r][c]) in ("dirt","one_hit_rock","rock","unreachable_pit"):
                candidates.append((r,c))
    if candidates:
        return {"type":"dig","pos":random.choice(candidates)}
    # else noop
    return {"type":"noop"}

if __name__ == "__main__":
    # quick demo loader
    sample_board = [
        ["unreachable_dirt","rock","empty","rock","unreachable_empty","unreachable_dirt"],
        ["dirt","empty","empty","dirt","unreachable_dirt","unreachable_rock"],
        ["dirt","empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty"],
        ["empty","empty","rock","unreachable_rock","unreachable_dirt","unreachable_empty"],
        ["empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty","unreachable_dirt"],
        ["empty","dirt","unreachable_empty","unreachable_pit","unreachable_dirt","unreachable_empty"],
        ["rock","unreachable_rock","unreachable_dirt","unreachable_dirt","unreachable_dirt","unreachable_rock"],
    ]
    sim = GameSimulator(sample_board, pickaxes=10, items={"drill":1,"bomb":1})
    obs = sim.reset()
    sim.render()
    while True:
        act = random_agent_step(sim)
        if act["type"]=="noop":
            break
        obs, rew, done, info = sim.step(act)
        print("act:",act,"rew",rew,"info",info)
        sim.render()
        if done: break