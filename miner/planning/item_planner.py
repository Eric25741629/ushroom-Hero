from typing import Any, Dict, List, Optional, Set, Tuple

from miner.core.config import HIT_TABLE
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
from .planner import (
    base_label,
    dijkstra_from_all_empties,
    enter_cost,
    find_best_descend_target,
    summarize_path,
)

TOOL_VALUE_LABELS: Set[str] = {"reachable_pit", "unreachable_pit"}
TOOL_MIN_COST_SAVINGS: float = 2.0
TOOL_DEBUG: bool = True
BOTTOM_TRIPLET_HIDDEN_CELLS: int = 6
PATH_GAIN_WEIGHT: float = 0.35
PATH_GAIN_CONSERVATIVE_FACTOR: float = 0.5


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


def collect_top_row_pits(board: List[List[str]]) -> Set[Tuple[int, int]]:
    """收集最上排的礦洞，這些礦洞必須優先處理。"""
    if not board:
        return set()
    return {(0, c) for c, cell in enumerate(board[0]) if is_tool_target(cell)}


def collect_square_clusters(board: List[List[str]], sizes: Tuple[int, ...] = (3, 2, 1)) -> List[Dict[str, Any]]:
    """找出畫面內已完整露出的 1x1 / 2x2 / 3x3 礦群。"""
    if not board:
        return []

    h = len(board)
    w = len(board[0])
    clusters: List[Dict[str, Any]] = []
    for size in sizes:
        if size <= 0 or size > min(h, w):
            continue
        for r in range(h - size, -1, -1):
            for c in range(w - size + 1):
                cells = [(r + dr, c + dc) for dr in range(size) for dc in range(size)]
                if all(is_tool_target(board[rr][cc]) for rr, cc in cells):
                    clusters.append(
                        {
                            "size": size,
                            "cells": cells,
                            "cells_set": frozenset(cells),
                        }
                    )
    return clusters


def estimate_visible_cluster_gain(
    pits: List[Tuple[int, int]],
    clusters: List[Dict[str, Any]],
) -> int:
    """估算畫面內可計分的礦洞數。

    規則：
    - 單獨礦洞照常計分
    - 2x2 / 3x3 只有完整覆蓋時，才把整塊算進收益
    """
    pit_set = set(pits)
    multi_cluster_cells: Set[Tuple[int, int]] = set()
    completed_cluster_cells: Set[Tuple[int, int]] = set()

    for cluster in clusters:
        if cluster["size"] <= 1:
            continue
        cells_set = cluster["cells_set"]
        multi_cluster_cells.update(cells_set)
        if cells_set.issubset(pit_set):
            completed_cluster_cells.update(cells_set)

    single_cells = [pos for pos in pits if pos not in multi_cluster_cells]
    return len(single_cells) + len(completed_cluster_cells)


def collect_bottom_triplets(board: List[List[str]]) -> List[Dict[str, Any]]:
    """找出底部連續 3 格裸露礦。

    這個規則只提供給炸彈使用：
    如果最底列看到橫向 3x1 裸露礦，就視為下方還有畫面外 3x2，
    也就是額外 6 格隱藏收益。
    """
    if not board:
        return []

    bottom_r = len(board) - 1
    w = len(board[0])
    triplets: List[Dict[str, Any]] = []
    for c in range(w - 2):
        cells = [(bottom_r, c + dc) for dc in range(3)]
        if all(is_tool_target(board[r][cc]) for r, cc in cells):
            triplets.append(
                {
                    "cells_set": frozenset(cells),
                    "hidden_cells": BOTTOM_TRIPLET_HIDDEN_CELLS,
                }
            )
    return triplets


def estimate_edge_strip_hidden_gain(board: List[List[str]], pits: List[Tuple[int, int]]) -> float:
    """估算邊界直線裸露礦的畫面外收益。

    保持簡單：
    - 只看炸彈已覆蓋到的裸露礦
    - 若這些裸露礦在上/下/左/右邊界形成 2 格或 3 格直線
      就給一點隱藏收益，代表它可能是 2x2 或 3x3 的剩餘部分
    """
    if not pits:
        return 0.0

    rows = sorted({r for r, _ in pits})
    cols = sorted({c for _, c in pits})
    h = len(board)
    w = len(board[0])

    if len(rows) == 1 and len(cols) in (2, 3) and (rows[0] == 0 or rows[0] == h - 1):
        return float(len(cols) * (len(cols) - 1))

    if len(cols) == 1 and len(rows) in (2, 3) and (cols[0] == 0 or cols[0] == w - 1):
        return float(len(rows) * (len(rows) - 1))

    return 0.0


def estimate_path_gain(obstacle_dig_cost_sum: float, placement_cost: float) -> float:
    """估算往下打通路徑的價值。

    不是非黑即白。
    就算工具打不到畫面外，若它能便宜地打掉土或石頭，讓後續更容易往下，
    仍然該有一些加分，但權重要小於直接取得礦洞的收益。
    """
    return max(0.0, obstacle_dig_cost_sum - placement_cost) * PATH_GAIN_WEIGHT


def _best_descend_profile(board: List[List[str]]) -> Tuple[float, Set[Tuple[int, int]]]:
    """Return best descend cost and the dig cells on that best descend path."""
    if not board:
        return float("inf"), set()
    h = len(board)
    w = len(board[0])
    dist, prev = dijkstra_from_all_empties(board)
    best = find_best_descend_target(dist, prev, h, w)
    if not best:
        return float("inf"), set()
    path = best.get("path") or []
    dig_list, step_cost = summarize_path(board, path)
    return float(step_cost), set(dig_list)


def _get_affected_cells(tool: str, r: int, c: int, h: int, w: int) -> List[Tuple[int, int]]:
    if tool == "bomb":
        return get_bomb_affected_cells(r, c, h, w)
    return get_drill_affected_cells(r, c, h, w)


def _evaluate_tool_candidate(
    board: List[List[str]],
    dist: List[List[float]],
    clusters: List[Dict[str, Any]],
    top_row_pits: Set[Tuple[int, int]],
    bottom_triplets: List[Dict[str, Any]],
    tool: str,
    r: int,
    c: int,
) -> Optional[Dict[str, Any]]:
    """評估單一工具落點的價值。"""
    h = len(board)
    w = len(board[0])
    placement_cost = dist[r][c]
    if placement_cost == float("inf") or placement_cost >= 999:
        return None

    affected = _get_affected_cells(tool, r, c, h, w)
    affected_set = set(affected)

    # 若最上排還有礦洞，而這個落點完全碰不到，就不應該優先選它。
    if top_row_pits and affected_set.isdisjoint(top_row_pits):
        return None

    pits: List[Tuple[int, int]] = []
    hit_cells: List[Tuple[int, int]] = []
    visible_cost = 0.0
    obstacle_dig_cost_sum = 0.0

    for rr, cc in affected:
        cell_label = board[rr][cc]
        base = base_label(cell_label)
        if required_hits(cell_label) > 0:
            hit_cells.append((rr, cc))
        if is_tool_target(cell_label):
            pits.append((rr, cc))
            visible_cost += placement_cost + (enter_cost(cell_label) or 0)
        if base in ("dirt", "one_hit_rock"):
            obstacle_dig_cost_sum += 1
        elif base == "rock":
            obstacle_dig_cost_sum += 2

    if not pits:
        return None

    visible_gain = estimate_visible_cluster_gain(pits, clusters)
    visible_savings = max(0.0, visible_cost - placement_cost)
    hidden_bonus = 0.0
    inferred_gain = visible_gain

    if tool == "bomb":
        hidden_bonus = max(hidden_bonus, estimate_edge_strip_hidden_gain(board, pits))
        for triplet in bottom_triplets:
            if triplet["cells_set"].issubset(affected_set):
                hidden_bonus = max(hidden_bonus, float(triplet["hidden_cells"]))
        inferred_gain = max(inferred_gain, visible_gain + int(hidden_bonus))

    # Estimate path gain by real descend-cost delta, then remove overlap
    # with pit-cost savings that are already counted by visible_savings.
    before_descend_cost, before_descend_dig_cells = _best_descend_profile(board)
    after_board = [row[:] for row in board]
    for rr, cc in affected:
        lbl = after_board[rr][cc]
        if lbl in ("reachable_pit", "unreachable_pit"):
            after_board[rr][cc] = "empty"
        elif base_label(lbl) not in ("empty", "dug_pit", "pit", "void"):
            after_board[rr][cc] = "empty"
    after_descend_cost, _ = _best_descend_profile(after_board)
    descend_saving = max(0.0, before_descend_cost - after_descend_cost)
    overlap_pit_saving = 0.0
    for pr, pc in pits:
        if (pr, pc) in before_descend_dig_cells:
            overlap_pit_saving += float(enter_cost(board[pr][pc]) or 0)
    heuristic_cap = estimate_path_gain(obstacle_dig_cost_sum, placement_cost)
    base_path_gain = max(0.0, min(descend_saving, heuristic_cap) - overlap_pit_saving)
    path_gain = base_path_gain * PATH_GAIN_CONSERVATIVE_FACTOR
    savings = visible_savings + hidden_bonus + path_gain

    matched_cluster_size = 0
    for cluster in clusters:
        if cluster["cells_set"].issubset(set(pits)):
            matched_cluster_size = max(matched_cluster_size, cluster["size"])

    hit_top_row_pit = bool(top_row_pits and not affected_set.isdisjoint(top_row_pits))
    if inferred_gain <= 1 and savings < 3.0 and not hit_top_row_pit:
        return None
    if savings <= TOOL_MIN_COST_SAVINGS and not hit_top_row_pit:
        return None

    score = (savings, inferred_gain, -placement_cost, matched_cluster_size, len(hit_cells))
    return {
        "tool": tool,
        "target": (r, c),
        "pits": pits,
        "gain": visible_gain,
        "inferred_gain": inferred_gain,
        "savings": savings,
        "hidden_bonus": hidden_bonus,
        "path_gain": path_gain,
        "cluster_size": matched_cluster_size,
        "placement_cost": placement_cost,
        "dig_list": hit_cells,
        "score": score,
    }


def plan_with_items_ev(
    board: List[List[str]],
    items_available: Dict[str, int],
    drill_threshold: float = TOOL_MIN_COST_SAVINGS,
    bomb_threshold: float = TOOL_MIN_COST_SAVINGS,
) -> Dict[str, Any]:
    """保留舊介面，實際上直接共用 find_tool_candidate 的結果。"""
    candidate = find_tool_candidate(board, items_available)
    if not candidate:
        if TOOL_DEBUG:
            print("[ITEM_EV] no beneficial item placement")
        return {
            "ok": False,
            "mode": "item_ev_plan",
            "message": "no beneficial item placement",
        }

    threshold = bomb_threshold if candidate["tool"] == "bomb" else drill_threshold
    if candidate["savings"] <= threshold:
        return {
            "ok": False,
            "mode": "item_ev_plan",
            "message": "marginal gain, save item for later",
        }

    step = {
        "action": f"use_{candidate['tool']}",
        "target": candidate["target"],
        "step_cost": 0.0,
        "gain": candidate["inferred_gain"],
        "savings": candidate["savings"],
        "dig_list": candidate["dig_list"],
    }
    if TOOL_DEBUG:
        print(
            f"[ITEM_EV] choose {step['action']} at {step['target']} "
            f"savings={step['savings']:.1f} gain={candidate['inferred_gain']}"
        )
    return {
        "ok": True,
        "mode": "item_ev_plan",
        "total_cost": 0.0,
        "steps": [step],
    }


def find_tool_candidate(
    board: List[List[str]],
    items_available: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    if not board or not board_has_tool_targets(board):
        return None

    h = len(board)
    w = len(board[0])
    clusters = collect_square_clusters(board)
    top_row_pits = collect_top_row_pits(board)
    bottom_triplets = collect_bottom_triplets(board)
    dist, _ = dijkstra_from_all_empties(board)

    best_tool: Optional[Dict[str, Any]] = None
    for r in range(h - 1, -1, -1):
        for c in range(w):
            # 候選落點必須是實際連通的空地，不能是 unreachable_empty。
            if board[r][c] not in ("empty", "dug_pit"):
                continue
            for tool in ("bomb", "drill"):
                if items_available and items_available.get(tool, 0) <= 0:
                    continue
                candidate = _evaluate_tool_candidate(
                    board=board,
                    dist=dist,
                    clusters=clusters,
                    top_row_pits=top_row_pits,
                    bottom_triplets=bottom_triplets,
                    tool=tool,
                    r=r,
                    c=c,
                )
                if not candidate:
                    continue
                if TOOL_DEBUG:
                    print(
                        f"[TOOL DEBUG] try {tool} at {(r, c)} "
                        f"savings={candidate['savings']:.1f} "
                        f"gain={candidate['inferred_gain']} "
                        f"hidden_bonus={candidate['hidden_bonus']:.1f} "
                        f"path_gain={candidate['path_gain']:.1f}"
                    )
                if best_tool is None or candidate["score"] > best_tool["score"]:
                    best_tool = candidate

    if TOOL_DEBUG:
        if best_tool:
            print(
                f"[TOOL DEBUG] chosen {best_tool['tool']} at {best_tool['target']} "
                f"savings={best_tool['savings']:.1f} "
                f"gain={best_tool['inferred_gain']} "
                f"hidden_bonus={best_tool['hidden_bonus']:.1f} "
                f"path_gain={best_tool['path_gain']:.1f}"
            )
        else:
            print("[TOOL DEBUG] no suitable tool candidate found")
    return best_tool
