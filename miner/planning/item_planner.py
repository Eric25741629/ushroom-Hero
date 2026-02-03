from typing import Any, Dict, List, Optional, Set, Tuple

from miner.core.config import HIT_TABLE
from .planner import (
    base_label,
    dijkstra_from_all_empties,
    enter_cost,
)

TOOL_VALUE_LABELS: Set[str] = {"reachable_pit", "unreachable_pit"}
TOOL_MIN_COST_SAVINGS: float = 2.0
TOOL_DEBUG: bool = True

def material_of(label: str) -> str:
    return base_label(label)

def required_hits(label: str) -> int:
    if label in HIT_TABLE:
        return HIT_TABLE[label]
    return HIT_TABLE.get(material_of(label), 0)

def is_tool_target(label: str) -> bool:
    return base_label(label) in TOOL_VALUE_LABELS

def board_has_tool_targets(board: List[List[str]]) -> bool:
    for row in board:
        for cell in row:
            if is_tool_target(cell):
                return True
    return False

def get_bomb_affected_cells(r: int, c: int, H: int, W: int) -> List[tuple[int, int]]:
    rel = [
        (0, 0), (0, -1), (0, 1), (-1, 0), (1, 0),
        (-1, -1), (-1, 1), (1, -1), (1, 1),
        (-2, 0), (2, 0),
    ]
    return [(r + dr, c + dc) for dr, dc in rel if 0 <= r + dr < H and 0 <= c + dc < W]

def get_drill_affected_cells(r: int, c: int, H: int, W: int) -> List[tuple[int, int]]:
    cells = [(rr, c) for rr in range(r, H)]
    if H > 0:
        if c > 0:
            cells.append((H - 1, c - 1))
        if c < W - 1:
            cells.append((H - 1, c + 1))
    return cells

def collect_square_clusters(board: List[List[str]], sizes: Tuple[int, ...] = (3, 2, 1)) -> List[Dict[str, Any]]:
    if not board:
        return []
    H = len(board)
    W = len(board[0])
    clusters: List[Dict[str, Any]] = []
    for size in sizes:
        if size <= 0 or size > min(H, W):
            continue
        for r in range(H - size, -1, -1):
            for c in range(W - size + 1):
                cells: List[Tuple[int, int]] = [(r + dr, c + dc) for dr in range(size) for dc in range(size)]
                if all(is_tool_target(board[rr][cc]) for rr, cc in cells):
                    clusters.append(
                        {
                            "size": size,
                            "cells": cells,
                            "cells_set": frozenset(cells),
                            "top_left": (r, c),
                        }
                    )
    return clusters

def plan_with_items_ev(
    board: List[List[str]],
    items_available: Dict[str, int],
    drill_threshold: float = TOOL_MIN_COST_SAVINGS,
    bomb_threshold: float = TOOL_MIN_COST_SAVINGS,
) -> Dict[str, Any]:
    if not board:
        return {"ok": False, "mode": "item_ev_plan", "message": "empty board"}

    if items_available.get("drill", 0) <= 0 and items_available.get("bomb", 0) <= 0:
        return {"ok": False, "mode": "item_ev_plan", "message": "no items available"}

    H = len(board)
    W = len(board[0]) if H > 0 else 0

    def is_reachable_tile(label: str) -> bool:
        return not label.startswith("unreachable_")

    best_plan: Optional[Dict[str, Any]] = None
    best_score: Tuple[float, int, int] = (-1.0, 0, 0)
    
    dist, _ = dijkstra_from_all_empties(board)

    for r in range(H - 1, -1, -1):
        for c in range(W):
            label = board[r][c]

            if not is_reachable_tile(label):
                continue
            if base_label(label) not in ("empty", "dug_pit"):
                continue
            
            placement_cost = dist[r][c] if dist[r][c] != float('inf') else 999
            if placement_cost >= 999:
                continue

            for tool_type, threshold in (
                ("drill", drill_threshold),
                ("bomb", bomb_threshold),
            ):
                if items_available.get(tool_type, 0) <= 0:
                    continue

                if tool_type == "drill":
                    affected = get_drill_affected_cells(r, c, H, W)
                else:
                    affected = get_bomb_affected_cells(r, c, H, W)
                
                hit_cells: List[Tuple[int, int]] = []
                pits: List[Tuple[int, int]] = []
                
                obstacle_dig_cost_sum = 0.0
                no_tool_pit_cost_sum = 0.0
                
                has_pits = False

                for (rr, cc) in affected:
                    cell_label = board[rr][cc]
                    hits = required_hits(cell_label)
                    
                    if hits > 0:
                        hit_cells.append((rr, cc))
                        
                    if is_tool_target(cell_label):
                        pits.append((rr, cc))
                        has_pits = True
                        path_to_pit = placement_cost
                        pit_dig_cost = enter_cost(cell_label) or 0
                        no_tool_pit_cost_sum += path_to_pit + pit_dig_cost

                    if base_label(cell_label) in ("dirt", "one_hit_rock"):
                        obstacle_dig_cost_sum += 1
                    elif base_label(cell_label) == "rock":
                        obstacle_dig_cost_sum += 2
                
                savings = 0.0
                
                if has_pits:
                    current_savings = no_tool_pit_cost_sum - placement_cost
                    savings = max(savings, current_savings)
                
                if not pits:
                     potential_obstacle_gain = 0.0
                else:
                     potential_obstacle_gain = obstacle_dig_cost_sum - placement_cost
                     
                savings = max(savings, potential_obstacle_gain)

                if savings <= threshold:
                    continue
                
                current_score = (savings, len(pits), len(hit_cells))

                if best_plan is None or current_score > best_score:
                    best_score = current_score
                    best_plan = {
                        "ok": True,
                        "mode": "item_ev_plan",
                        "total_cost": 0.0,
                        "steps": [
                            {
                                "action": f"use_{tool_type}",
                                "target": (r, c),
                                "step_cost": 0.0,
                                "gain": len(pits),
                                "savings": savings,
                                "dig_list": hit_cells,
                            }
                        ],
                    }

    if best_plan is None:
        if TOOL_DEBUG:
            print("[ITEM_EV] no beneficial item placement")
        return {
            "ok": False,
            "mode": "item_ev_plan",
            "message": "no beneficial item placement",
        }

    step = best_plan["steps"][0]
    if step['gain'] <= 1 and step['savings'] < 3.0:
         if TOOL_DEBUG:
             print(f"[ITEM_EV] Skip marginal tool usage: gain={step['gain']}, savings={step['savings']} (Threshold 3.0 for single pit)")
         return {
            "ok": False,
            "mode": "item_ev_plan",
            "message": "marginal gain, save item for later",
        }

    if TOOL_DEBUG:
        step = best_plan["steps"][0]
        print(
            f"[ITEM_EV] choose {step['action']} at {step['target']} "
            f"savings={step['gain']} hit_cells={len(step['dig_list'])}"
        )

    return best_plan

def find_tool_candidate(board: List[List[str]]) -> Optional[Dict[str, Any]]:
    if not board:
        return None
    if not board_has_tool_targets(board):
        pass 
        
    H = len(board)
    W = len(board[0])
    clusters = collect_square_clusters(board)
    
    dist, _ = dijkstra_from_all_empties(board)
    
    best_tool: Optional[Dict[str, Any]] = None
    best_score: Tuple[float, int] = (-1.0, -1)

    for r in range(H - 1, -1, -1):
        for c in range(W):
            if base_label(board[r][c]) not in ("empty", "dug_pit"):
                continue
            placement_cost = dist[r][c] if dist[r][c] != float('inf') else 999
            
            if placement_cost >= 999:
                continue

            for tool, affected_fn in (("bomb", get_bomb_affected_cells), ("drill", get_drill_affected_cells)):
                affected = affected_fn(r, c, H, W)
                affected_set = set(affected)
                
                pits = [pos for pos in affected if is_tool_target(board[pos[0]][pos[1]])]
                
                matched_cluster = None
                if pits:
                    for cluster in clusters:
                        if cluster["cells_set"].issubset(affected_set):
                            if matched_cluster is None or cluster["size"] > matched_cluster["size"]:
                                matched_cluster = cluster
                
                savings = 0.0
                
                if pits:
                    no_tool_cost_sum = 0.0
                    for pr, pc in pits:
                        path_to_pit = dist[pr][pc] if dist[pr][pc] != float('inf') else 999
                        pit_dig_cost = enter_cost(board[pr][pc]) or 0
                        no_tool_cost_sum += path_to_pit + pit_dig_cost
                    
                    current_savings = no_tool_cost_sum - placement_cost
                    savings = max(savings, current_savings)

                    if matched_cluster:
                         cluster_no_tool = sum(
                            (dist[pr][pc] if dist[pr][pc] != float('inf') else 999) + (enter_cost(board[pr][pc]) or 0)
                            for pr, pc in matched_cluster["cells"]
                        )
                         cluster_savings = cluster_no_tool - placement_cost
                         savings = max(savings, cluster_savings)
                
                potential_obstacle_gain = 0.0
                savings = max(savings, potential_obstacle_gain)
                
                if TOOL_DEBUG:
                    print(f"[TOOL DEBUG] try {tool} at {(r,c)} -> placement_cost={placement_cost} savings={savings:.1f} (pits={len(pits)} obs_gain={potential_obstacle_gain:.1f})")

                if pits:
                    no_tool_cost_sum = sum(
                        (dist[pr][pc] if dist[pr][pc] != float('inf') else 999) + (enter_cost(board[pr][pc]) or 0)
                        for pr, pc in pits
                    )
                    if no_tool_cost_sum <= TOOL_MIN_COST_SAVINGS:
                        continue
                    current_savings = no_tool_cost_sum - placement_cost
                    if current_savings <= TOOL_MIN_COST_SAVINGS:
                        continue
                else:
                    continue
                
                if len(pits) <= 1 and savings < 3.0:
                     continue

                current_score = (savings, matched_cluster["size"] if matched_cluster else 0)
                if best_tool is None or current_score > best_score:
                    if TOOL_DEBUG:
                         print(f"[TOOL DEBUG] updating best candidate: {tool} at {(r,c)} savings={savings} score={current_score}")
                    best_score = current_score
                    best_tool = {
                        "tool": tool,
                        "target": (r, c),
                        "pits": pits,
                        "gain": len(pits),
                        "savings": savings,
                        "cluster": matched_cluster,
                    }
    if TOOL_DEBUG:
        if best_tool:
            s_val = best_tool.get("savings", best_tool.get("gain", 0))
            print(f"[TOOL DEBUG] chosen {best_tool['tool']} at {best_tool['target']} savings={s_val:.1f} pits={best_tool['pits']} cluster={bool(best_tool['cluster'])}")
        else:
            print("[TOOL DEBUG] no suitable tool candidate found")
    return best_tool
