from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from miner.core.mechanics import get_drill_affected_cells
from miner.planning.planner import plan_min_cost_to_floor7

Board = List[List[str]]
Coordinate = Tuple[int, int]
EMPTY_BASE_LABELS = {"empty", "void", "dug_pit"}
PIT_LABELS = {"reachable_pit", "unreachable_pit"}
_FLOOR7_COST_CACHE: Dict[Tuple[Tuple[str, ...], ...], float] = {}


def normalize_label(label: str) -> str:
    if label == "pit":
        return "reachable_pit"
    return label


def base_label(label: str) -> str:
    return normalize_label(label).replace("unreachable_", "")


def is_air(label: str) -> bool:
    return base_label(label) in EMPTY_BASE_LABELS


def is_reachable(label: str) -> bool:
    return not normalize_label(label).startswith("unreachable_")


def dig_cost(label: str) -> int:
    normalized = normalize_label(label)
    base = base_label(normalized)
    if normalized in PIT_LABELS:
        return 1
    if base in {"dirt", "one_hit_rock"}:
        return 1
    if base == "rock":
        return 2
    return 0


def normalize_board(board: Board) -> Board:
    return [[normalize_label(cell) for cell in row] for row in board]


def count_remaining_pits(board: Board) -> int:
    return sum(1 for row in board for cell in row if normalize_label(cell) in PIT_LABELS)


def top_row_pit_count(board: Board) -> int:
    if not board:
        return 0
    return sum(1 for cell in board[0] if normalize_label(cell) in PIT_LABELS)


def floor7_open(board: Board, offscreen_floor7_open: bool = False) -> bool:
    if offscreen_floor7_open:
        return True
    last_row = len(board) - 1
    return any(is_air(cell) and is_reachable(cell) for cell in board[last_row])


def classify_strategy(board: Board) -> str:
    return "has_pit" if count_remaining_pits(board) > 0 else "no_pit"


def get_reachable_air_cells(board: Board) -> List[Coordinate]:
    cells: List[Coordinate] = []
    for r, row in enumerate(board):
        for c, cell in enumerate(row):
            if is_air(cell) and is_reachable(cell):
                cells.append((r, c))
    return cells


def deepest_reachable_air_row(board: Board) -> int:
    deepest = -1
    for r, row in enumerate(board):
        for cell in row:
            if is_air(cell) and is_reachable(cell):
                deepest = max(deepest, r)
    return deepest


def update_exposure(board: Board) -> None:
    rows = len(board)
    cols = len(board[0]) if board else 0
    queue: List[Coordinate] = []
    reachable_air: set[Coordinate] = set()

    for r in range(rows):
        for c in range(cols):
            label = normalize_label(board[r][c])
            if is_air(label) and not label.startswith("unreachable_"):
                board[r][c] = "empty"
                reachable_air.add((r, c))
                queue.append((r, c))

    head = 0
    while head < len(queue):
        r, c = queue[head]
        head += 1
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                label = normalize_label(board[nr][nc])
                if (nr, nc) in reachable_air:
                    continue
                if is_air(label):
                    board[nr][nc] = "empty"
                    reachable_air.add((nr, nc))
                    queue.append((nr, nc))

    for r in range(rows):
        for c in range(cols):
            label = normalize_label(board[r][c])
            if not label.startswith("unreachable_"):
                continue
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if (nr, nc) in reachable_air:
                    board[r][c] = label.replace("unreachable_", "", 1)
                    break


def get_bomb_targets(r: int, c: int, rows: int, cols: int) -> Tuple[List[Coordinate], int]:
    raw_targets: List[Coordinate] = []
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            raw_targets.append((r + dr, c + dc))
    for dr, dc in ((0, 2), (0, -2), (2, 0), (-2, 0)):
        raw_targets.append((r + dr, c + dc))

    visible_targets: List[Coordinate] = []
    offscreen_bottom_hits = 0
    for nr, nc in raw_targets:
        if 0 <= nr < rows and 0 <= nc < cols:
            visible_targets.append((nr, nc))
        elif nr >= rows:
            offscreen_bottom_hits += 1
    return visible_targets, offscreen_bottom_hits


def apply_dig(board: Board, pos: Coordinate) -> float:
    r, c = pos
    label = normalize_label(board[r][c])
    cost = float(dig_cost(label))
    if label in PIT_LABELS:
        board[r][c] = "dug_pit"
    elif not is_air(label):
        board[r][c] = "empty"
    update_exposure(board)
    return cost


def apply_drill(board: Board, pos: Coordinate) -> float:
    rows = len(board)
    cols = len(board[0]) if board else 0
    for nr, nc in get_drill_affected_cells(pos[0], pos[1], rows, cols):
        label = normalize_label(board[nr][nc])
        if label in PIT_LABELS:
            board[nr][nc] = "dug_pit"
        elif not is_air(label):
            board[nr][nc] = "empty"
    update_exposure(board)
    return 3.0


def apply_bomb(board: Board, pos: Coordinate) -> Tuple[float, bool, int]:
    rows = len(board)
    cols = len(board[0]) if board else 0
    targets, offscreen_bottom_hits = get_bomb_targets(pos[0], pos[1], rows, cols)
    for nr, nc in targets:
        label = normalize_label(board[nr][nc])
        if label in PIT_LABELS:
            board[nr][nc] = "dug_pit"
        elif not is_air(label):
            board[nr][nc] = "empty"
    update_exposure(board)
    return 3.0, offscreen_bottom_hits > 0, offscreen_bottom_hits


def board_signature(board: Board, items: Dict[str, int], offscreen_floor7_open: bool) -> Tuple[Any, ...]:
    return (
        tuple(tuple(row) for row in board),
        items.get("drill", 0),
        items.get("bomb", 0),
        bool(offscreen_floor7_open),
    )


def board_cells_signature(board: Board) -> Tuple[Tuple[str, ...], ...]:
    return tuple(tuple(row) for row in board)


@dataclass(order=True)
class SearchNode:
    priority: float
    total_cost: float
    board: Board = field(compare=False)
    items: Dict[str, int] = field(compare=False)
    history: List[Dict[str, Any]] = field(compare=False, default_factory=list)
    offscreen_floor7_open: bool = field(compare=False, default=False)


def build_analysis(board: Board, offscreen_floor7_open: bool = False) -> Dict[str, Any]:
    reachable_pits = 0
    unreachable_pits = 0
    for row in board:
        for cell in row:
            label = normalize_label(cell)
            if label == "reachable_pit":
                reachable_pits += 1
            elif label == "unreachable_pit":
                unreachable_pits += 1
    remaining_pits = reachable_pits + unreachable_pits
    top_row_pits = top_row_pit_count(board)
    return {
        "remaining_pits": remaining_pits,
        "reachable_pits": reachable_pits,
        "unreachable_pits": unreachable_pits,
        "top_row_pits": top_row_pits,
        "deepest_reachable_air_row": deepest_reachable_air_row(board),
        "floor7_open": floor7_open(board, offscreen_floor7_open=offscreen_floor7_open),
        "exit_guard_required": remaining_pits > 0,
    }


def heuristic(
    board: Board,
    strategy_class: str,
    items: Optional[Dict[str, int]] = None,
    offscreen_floor7_open: bool = False,
) -> float:
    analysis = build_analysis(board, offscreen_floor7_open=offscreen_floor7_open)
    if strategy_class == "has_pit":
        score = analysis["remaining_pits"] * 10.0
        score += analysis["top_row_pits"] * 80.0
        if analysis["reachable_pits"] == 0 and analysis["remaining_pits"] > 0:
            score += 4.0
        score -= max(0, analysis["deepest_reachable_air_row"]) * 0.35
        if items and analysis["top_row_pits"] == 0:
            score -= min(2.0, float(items.get("bomb", 0) + items.get("drill", 0)) * 0.5)
        if not analysis["floor7_open"]:
            score += 1.0
        return score

    if analysis["floor7_open"]:
        return 0.0

    cache_key = board_cells_signature(board)
    score = _FLOOR7_COST_CACHE.get(cache_key)
    if score is None:
        descent = plan_min_cost_to_floor7(normalize_board(board))
        if not descent.get("ok"):
            score = 9999.0
        else:
            score = float(descent.get("total_cost", 0.0))
        _FLOOR7_COST_CACHE[cache_key] = score
    if items:
        deepest = max(0, analysis["deepest_reachable_air_row"])
        if items.get("drill", 0) > 0:
            score -= min(2.0, deepest * 0.25)
        if items.get("bomb", 0) > 0:
            score -= min(2.5, deepest * 0.3)
    return score


def goal_reached(board: Board, strategy_class: str, offscreen_floor7_open: bool) -> bool:
    if strategy_class == "has_pit":
        return count_remaining_pits(board) == 0
    return floor7_open(board, offscreen_floor7_open=offscreen_floor7_open)


def simulate_action(
    board: Board,
    items: Dict[str, int],
    action: Dict[str, Any],
    offscreen_floor7_open: bool,
) -> Optional[Dict[str, Any]]:
    next_board = [row[:] for row in board]
    next_items = dict(items)
    next_offscreen = offscreen_floor7_open

    if action["type"] == "dig":
        step_cost = apply_dig(next_board, action["pos"])
        offscreen_hits = 0
    else:
        item = action["item"]
        if next_items.get(item, 0) <= 0:
            return None
        next_items[item] -= 1
        if item == "drill":
            step_cost = apply_drill(next_board, action["pos"])
            offscreen_hits = 0
        else:
            step_cost, bomb_opened_floor7, offscreen_hits = apply_bomb(next_board, action["pos"])
            next_offscreen = next_offscreen or bomb_opened_floor7

    next_action = dict(action)
    next_action["step_cost"] = float(step_cost)
    if action["type"] == "use" and action["item"] == "bomb":
        next_action["offscreen_bottom_hits"] = offscreen_hits

    return {
        "action": next_action,
        "next_board": next_board,
        "next_items": next_items,
        "next_offscreen_floor7": next_offscreen,
        "step_cost": float(step_cost),
        "offscreen_hits": offscreen_hits,
    }


def score_transition(
    before: Dict[str, Any],
    after: Dict[str, Any],
    action: Dict[str, Any],
    step_cost: float,
    strategy_class: str,
    offscreen_hits: int,
) -> float:
    score = 0.0
    score += float(before["top_row_pits"] - after["top_row_pits"]) * 200.0
    score += float(before["remaining_pits"] - after["remaining_pits"]) * 50.0
    score += float(after["deepest_reachable_air_row"] - before["deepest_reachable_air_row"]) * 6.0
    score -= float(step_cost) * 2.0

    if strategy_class == "no_pit":
        if after["floor7_open"]:
            score += 120.0
        else:
            score += float(after["deepest_reachable_air_row"]) * 2.0

    if action["type"] == "use":
        pos_r, _ = action["pos"]
        if before["top_row_pits"] > 0:
            score -= 100.0
        score += float(pos_r) * 1.5
        if action["item"] == "bomb":
            score += float(offscreen_hits) * 4.0
        else:
            score += float(after["deepest_reachable_air_row"] - before["deepest_reachable_air_row"]) * 3.0

    return score


def build_actions(
    board: Board,
    items: Dict[str, int],
    strategy_class: str,
    offscreen_floor7_open: bool,
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    rows = len(board)
    cols = len(board[0]) if board else 0

    for r in range(rows):
        for c in range(cols):
            label = normalize_label(board[r][c])
            if not is_air(label) and is_reachable(label):
                actions.append({"type": "dig", "pos": (r, c)})

    air_cells = get_reachable_air_cells(board)
    if items.get("drill", 0) > 0:
        for pos in air_cells:
            actions.append({"type": "use", "item": "drill", "pos": pos})
    if items.get("bomb", 0) > 0:
        for pos in air_cells:
            actions.append({"type": "use", "item": "bomb", "pos": pos})

    before = build_analysis(board, offscreen_floor7_open=offscreen_floor7_open)
    ranked_actions: List[Dict[str, Any]] = []
    for action in actions:
        transition = simulate_action(board, items, action, offscreen_floor7_open)
        if transition is None:
            continue
        after = build_analysis(
            transition["next_board"],
            offscreen_floor7_open=transition["next_offscreen_floor7"],
        )
        transition["score"] = score_transition(
            before,
            after,
            transition["action"],
            transition["step_cost"],
            strategy_class,
            transition["offscreen_hits"],
        )
        ranked_actions.append(transition)

    ranked_actions.sort(key=lambda entry: entry["score"], reverse=True)
    return ranked_actions


def plan_v2(
    board: Board,
    shovels: float = 100.0,
    items: Optional[Dict[str, int]] = None,
    max_nodes: int = 4000,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    work_board = normalize_board(board)
    update_exposure(work_board)
    strategy_class = classify_strategy(work_board)
    item_state = {"drill": int((items or {}).get("drill", 0)), "bomb": int((items or {}).get("bomb", 0))}

    start = SearchNode(
        priority=heuristic(work_board, strategy_class, item_state),
        total_cost=0.0,
        board=work_board,
        items=item_state,
        history=[],
        offscreen_floor7_open=False,
    )
    pq: List[SearchNode] = [start]
    seen: Dict[Tuple[Any, ...], float] = {board_signature(work_board, item_state, False): 0.0}
    best: Optional[SearchNode] = None
    explored = 0

    while pq and explored < max_nodes:
        current = heapq.heappop(pq)
        explored += 1

        if goal_reached(current.board, strategy_class, current.offscreen_floor7_open):
            best = current
            break

        for transition in build_actions(
            current.board,
            current.items,
            strategy_class=strategy_class,
            offscreen_floor7_open=current.offscreen_floor7_open,
        ):
            next_board = transition["next_board"]
            next_items = transition["next_items"]
            next_history = list(current.history)
            next_offscreen_floor7 = transition["next_offscreen_floor7"]
            step_cost = transition["step_cost"]

            if current.total_cost + step_cost > shovels:
                continue

            next_action = dict(transition["action"])
            next_history.append(next_action)

            signature = board_signature(next_board, next_items, next_offscreen_floor7)
            next_total_cost = current.total_cost + step_cost
            if signature in seen and seen[signature] <= next_total_cost:
                continue
            seen[signature] = next_total_cost

            next_priority = next_total_cost + heuristic(
                next_board,
                strategy_class=strategy_class,
                items=next_items,
                offscreen_floor7_open=next_offscreen_floor7,
            )
            heapq.heappush(
                pq,
                SearchNode(
                    priority=next_priority,
                    total_cost=next_total_cost,
                    board=next_board,
                    items=next_items,
                    history=next_history,
                    offscreen_floor7_open=next_offscreen_floor7,
                ),
            )

    if best is None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        return {
            "ok": False,
            "strategy_class": strategy_class,
            "message": f"no plan found (nodes={explored})",
            "steps": [],
            "total_cost": 0.0,
            "remaining_pits": count_remaining_pits(work_board),
            "floor7_open": floor7_open(work_board),
            "explored_nodes": explored,
            "elapsed_ms": round(elapsed_ms, 3),
        }

    best_analysis = build_analysis(best.board, offscreen_floor7_open=best.offscreen_floor7_open)
    mode_label = "pit-clearing" if strategy_class == "has_pit" else "floor7"
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return {
        "ok": True,
        "strategy_class": strategy_class,
        "message": f"{mode_label} plan ready (nodes={explored})",
        "steps": best.history,
        "total_cost": float(best.total_cost),
        "remaining_pits": best_analysis["remaining_pits"],
        "top_row_pits": best_analysis["top_row_pits"],
        "floor7_open": best_analysis["floor7_open"],
        "exit_guard_required": best_analysis["exit_guard_required"],
        "explored_nodes": explored,
        "elapsed_ms": round(elapsed_ms, 3),
    }


__all__ = [
    "apply_bomb",
    "apply_dig",
    "apply_drill",
    "build_analysis",
    "classify_strategy",
    "count_remaining_pits",
    "normalize_board",
    "plan_v2",
    "top_row_pit_count",
    "update_exposure",
]
