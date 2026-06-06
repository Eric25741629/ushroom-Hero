"""Miner V3 planner.

Design intent:

1. Reachability is the CNN's word — never inferred via plan-start BFS.
2. Pits are precious. The planner clears every pit it can reach (and digs
   walls indefinitely to reach unreachable_pits — `unreachable_pit_max_extra_cost
   = +inf` per user requirement).
3. Floor7 is opened ONLY after all visible pits are accounted for. While
   any pit remains, the planner refuses any action that would scroll the
   board (and lose row 0 / row 1 pits).
4. Items prefer covering full N×N clusters in one shot — the bigger the
   cluster, the higher the placement bonus.
5. Returns rich `PlanStats` so we can later analyse efficiency.

Search shape: bounded best-first. State = (board, items, cost, history).
Goal in `has_pit` strategy = pits == 0. Goal in `no_pit` = floor7_open.
We don't bound shovel cost — user says shovels are cheap relative to pits.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from .actions import (
    affected_pit_cells,
    apply_bomb,
    apply_dig,
    apply_drill,
    enumerate_dig_actions,
    enumerate_item_actions,
)
from .board import (
    canonicalize_in_place,
    count_pits,
    count_remaining_pits,
    floor7_open,
    is_pit,
    normalize_board,
    top_row_pit_count,
)
from .clusters import PitCluster, cluster_value, find_clusters
from .types import Board, Coordinate, PlanResult, PlanStats


PLAN_NODE_BUDGET = 6000
# Hard wall-clock deadline so a single plan call can never exceed the 300 ms
# per-step budget. Without it the 6000-node best-first search peaked at ~453 ms
# on 2/1062 real boards (planner-eval 2026-06-05). The clock is sampled every
# 32 nodes; when it trips the loop exits and the best partial plan found so far
# is returned (the same graceful-degradation path used when the node budget is
# hit). 230 ms leaves margin for the in-flight node before the next check.
TIME_BUDGET_MS = 230.0
ITEM_NO_PIT_PENALTY = 400.0
FLOOR7_PIT_LOSS_PENALTY = 100000.0  # effectively forbids the action


@dataclass(order=True)
class _Node:
    priority: float
    seq: int
    cost: float = field(compare=False)
    board: Board = field(compare=False)
    items: Dict[str, int] = field(compare=False)
    history: List[Dict[str, Any]] = field(compare=False)
    pits_collected: int = field(compare=False, default=0)
    drills_used: int = field(compare=False, default=0)
    bombs_used: int = field(compare=False, default=0)


def _board_signature(board: Board) -> Tuple[Tuple[str, ...], ...]:
    return tuple(tuple(row) for row in board)


def _state_signature(
    board: Board,
    items: Dict[str, int],
) -> Tuple[Any, ...]:
    return (
        _board_signature(board),
        items.get("drill", 0),
        items.get("bomb", 0),
    )


def _classify_strategy(board: Board) -> str:
    return "has_pit" if count_remaining_pits(board) > 0 else "no_pit"


def _heuristic(
    board: Board,
    strategy: str,
) -> float:
    """Admissible-ish heuristic: lower bound on remaining shovel cost."""
    reachable, unreachable = count_pits(board)
    if strategy == "has_pit":
        # Each reachable pit costs ≥1 shovel to collect; each unreachable
        # pit costs ≥2 (one path-cell + the pit itself).
        return reachable * 1.0 + unreachable * 2.0
    return 0.0 if floor7_open(board) else 1.0


def _action_value(
    board_before: Board,
    board_after: Board,
    items_before: Dict[str, int],
    items_after: Dict[str, int],
    action: Dict[str, Any],
    pit_cells_hit: FrozenSet[Coordinate],
    clusters: List[PitCluster],
    strategy: str,
    step_cost: float,
    floor7_before: bool,
    floor7_after: bool,
    pits_before: int,
    pits_after: int,
    top_before: int,
    top_after: int,
    offscreen_bottom_hits: int = 0,
) -> float:
    """Score an action transition. Positive = good. Used both for action
    ordering and for `partial_progress_rank` fallback."""
    score = 0.0
    pits_cleared = float(pits_before - pits_after)
    top_cleared = float(top_before - top_after)
    score += top_cleared * 1000.0
    score += pits_cleared * 500.0
    score -= float(step_cost) * 0.5

    if strategy == "no_pit":
        if floor7_after:
            score += 200.0
    else:
        if not floor7_before and floor7_after:
            score -= FLOOR7_PIT_LOSS_PENALTY  # never trade a pit for floor7

    if action["type"] == "use":
        # Penalize wasted item usage in has_pit mode.
        if strategy == "has_pit" and pits_cleared <= 0:
            score -= ITEM_NO_PIT_PENALTY
        # Cluster cover bonus — bigger square covered, bigger reward.
        for cluster in clusters:
            covered = cluster.fully_covered_by(pit_cells_hit)
            if covered:
                score += cluster_value(cluster.size) * 30.0
            else:
                # Partial cluster cover still helps a bit.
                overlap = cluster.overlaps(pit_cells_hit)
                if overlap and cluster.size > 1:
                    score += float(overlap) * 8.0
        # Bomb hitting offscreen bottom row often opens floor7 cleanly.
        if action["item"] == "bomb":
            score += float(offscreen_bottom_hits) * 4.0
    return score


def _simulate(
    board: Board,
    items: Dict[str, int],
    action: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    next_board = [row[:] for row in board]
    next_items = dict(items)
    offscreen_bottom = 0
    if action["type"] == "dig":
        cost = apply_dig(next_board, action["pos"])
    elif action["type"] == "use":
        item = action["item"]
        if next_items.get(item, 0) <= 0:
            return None
        next_items[item] -= 1
        if item == "drill":
            cost, _ = apply_drill(next_board, action["pos"])
        else:
            cost, _, offscreen_bottom = apply_bomb(next_board, action["pos"])
    else:
        return None
    return {
        "next_board": next_board,
        "next_items": next_items,
        "step_cost": float(cost),
        "offscreen_bottom_hits": offscreen_bottom,
    }


def _expand(
    node: _Node,
    strategy: str,
    clusters: List[PitCluster],
) -> List[Tuple[Dict[str, Any], Dict[str, Any], float, int]]:
    """Return [(action, transition, score, pits_cleared)]."""
    pits_before = count_remaining_pits(node.board)
    top_before = top_row_pit_count(node.board)
    floor7_before = floor7_open(node.board)
    actions = enumerate_dig_actions(node.board) + enumerate_item_actions(node.board, node.items)

    out: List[Tuple[Dict[str, Any], Dict[str, Any], float, int]] = []
    safe_alt = False
    pending: List[Tuple[Dict[str, Any], Dict[str, Any], float, int, bool]] = []
    for action in actions:
        pit_cells = affected_pit_cells(node.board, action)
        transition = _simulate(node.board, node.items, action)
        if transition is None:
            continue
        nb = transition["next_board"]
        pits_after = count_remaining_pits(nb)
        top_after = top_row_pit_count(nb)
        floor7_after = floor7_open(nb)
        score = _action_value(
            board_before=node.board,
            board_after=nb,
            items_before=node.items,
            items_after=transition["next_items"],
            action=action,
            pit_cells_hit=pit_cells,
            clusters=clusters,
            strategy=strategy,
            step_cost=transition["step_cost"],
            floor7_before=floor7_before,
            floor7_after=floor7_after,
            pits_before=pits_before,
            pits_after=pits_after,
            top_before=top_before,
            top_after=top_after,
            offscreen_bottom_hits=transition["offscreen_bottom_hits"],
        )
        pits_cleared = pits_before - pits_after
        # Digging a pit is the whole point — never filter it out as a floor7
        # loss. `pits_cleared > 0` covers both direct pit digs and item uses
        # that hit pits. Non-productive row-6 digs (rock/dirt) still get
        # filtered so the planner doesn't scroll the board for no reward.
        triggers_floor7_loss = (
            strategy == "has_pit"
            and pits_after > 0
            and not floor7_before
            and floor7_after
            and pits_cleared <= 0
        )
        pending.append((action, transition, score, pits_cleared, triggers_floor7_loss))
        if not triggers_floor7_loss:
            safe_alt = True

    for action, transition, score, pits_cleared, triggers_floor7_loss in pending:
        if triggers_floor7_loss and safe_alt:
            continue
        out.append((action, transition, score, pits_cleared))
    out.sort(key=lambda item: item[2], reverse=True)
    return out


def _make_step(
    action: Dict[str, Any],
    transition: Dict[str, Any],
) -> Dict[str, Any]:
    step = dict(action)
    step["step_cost"] = float(transition["step_cost"])
    step["target"] = action.get("pos")
    if action["type"] == "use" and action["item"] == "bomb":
        step["offscreen_bottom_hits"] = transition["offscreen_bottom_hits"]
    return step


def plan_v3(
    board: Board,
    shovels: float = 100.0,
    items: Optional[Dict[str, int]] = None,
    max_nodes: int = PLAN_NODE_BUDGET,
    blocked_actions: Optional[Set[Tuple[Any, ...]]] = None,
) -> Dict[str, Any]:
    """Best-first search for the cheapest pit-clearing plan.

    `shovels` acts as a soft hint, not a hard cap — the user prefers spending
    extra shovels over leaving pits. We DO ignore plans that exceed the budget
    by more than `2x` to keep the search bounded.
    """
    started_at = time.perf_counter()
    work = normalize_board(board)
    canonicalize_in_place(work)

    initial_pits = count_remaining_pits(work)
    strategy = _classify_strategy(work)
    item_state = {"drill": int((items or {}).get("drill", 0)), "bomb": int((items or {}).get("bomb", 0))}
    blocked = set(blocked_actions or set())

    clusters = find_clusters(work)

    cost_budget = max(float(shovels) * 2.0, float(shovels) + 6.0)

    start = _Node(
        priority=_heuristic(work, strategy),
        seq=0,
        cost=0.0,
        board=work,
        items=item_state,
        history=[],
    )
    pq: List[_Node] = [start]
    seen: Dict[Tuple[Any, ...], float] = {_state_signature(work, item_state): 0.0}
    explored = 0
    seq = 1
    deadline = started_at + TIME_BUDGET_MS / 1000.0
    deadline_hit = False

    best_goal: Optional[_Node] = None
    best_partial: Optional[Tuple[Tuple[float, ...], _Node]] = None

    def _partial_rank(node: _Node) -> Tuple[float, ...]:
        pits_after = count_remaining_pits(node.board)
        top_after = top_row_pit_count(node.board)
        return (
            float(initial_pits - pits_after),  # pits cleared
            float(top_row_pit_count(work) - top_after),  # top cleared
            -node.cost,
            -float(len(node.history)),
        )

    while pq and explored < max_nodes:
        current = heapq.heappop(pq)
        explored += 1

        # Sample the wall clock every 32 nodes — checking every node would
        # itself dominate runtime on large searches. On trip, fall through to
        # the best_goal/best_partial return below.
        if (explored & 0x1F) == 0 and time.perf_counter() >= deadline:
            deadline_hit = True
            break

        if strategy == "has_pit":
            goal = count_remaining_pits(current.board) == 0
        else:
            goal = floor7_open(current.board)

        if goal:
            if best_goal is None or current.cost < best_goal.cost:
                best_goal = current
            break

        if current.history:
            rank = _partial_rank(current)
            if best_partial is None or rank > best_partial[0]:
                best_partial = (rank, current)

        for action, transition, score, _pits_cleared in _expand(current, strategy, clusters):
            sig_action = (
                action.get("type"),
                action.get("item"),
                tuple(action.get("pos", ())),
            )
            if sig_action in blocked:
                continue
            next_cost = current.cost + transition["step_cost"]
            if next_cost > cost_budget:
                continue
            sig = _state_signature(transition["next_board"], transition["next_items"])
            if sig in seen and seen[sig] <= next_cost:
                continue
            seen[sig] = next_cost
            next_history = list(current.history)
            next_history.append(_make_step(action, transition))
            new_node = _Node(
                priority=next_cost + _heuristic(transition["next_board"], strategy),
                seq=seq,
                cost=next_cost,
                board=transition["next_board"],
                items=transition["next_items"],
                history=next_history,
                pits_collected=current.pits_collected + max(0, _pits_cleared),
                drills_used=current.drills_used + (1 if action.get("item") == "drill" else 0),
                bombs_used=current.bombs_used + (1 if action.get("item") == "bomb" else 0),
            )
            seq += 1
            heapq.heappush(pq, new_node)

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0

    final = best_goal or (best_partial[1] if best_partial else None)
    if final is None:
        stats = PlanStats(
            explored_nodes=explored,
            elapsed_ms=elapsed_ms,
            pits_at_start=initial_pits,
            pits_remaining=initial_pits,
            pits_unreachable_remaining=count_pits(work)[1],
        )
        return PlanResult(
            ok=False,
            message=f"v3 no plan found (nodes={explored})",
            stats=stats,
            strategy_class=strategy,
            floor7_open=floor7_open(work),
        ).to_dict()

    pits_after_reachable, pits_after_unreachable = count_pits(final.board)
    pits_after = pits_after_reachable + pits_after_unreachable
    stats = PlanStats(
        explored_nodes=explored,
        elapsed_ms=elapsed_ms,
        pits_at_start=initial_pits,
        pits_collected=initial_pits - pits_after,
        pits_remaining=pits_after,
        pits_unreachable_remaining=pits_after_unreachable,
        shovel_cost=final.cost,
        drills_used=final.drills_used,
        bombs_used=final.bombs_used,
        cost_per_pit=(final.cost / max(1, initial_pits - pits_after)),
    )
    is_partial = best_goal is None
    label = "v3 partial" if is_partial else "v3 plan"
    deadline_note = " deadline" if deadline_hit else ""
    return PlanResult(
        ok=True,
        message=(
            f"{label}{deadline_note} (nodes={explored}, pits {stats.pits_collected}/{initial_pits}, "
            f"shovels={stats.shovel_cost:.1f}, drill={stats.drills_used}, bomb={stats.bombs_used})"
        ),
        steps=final.history,
        stats=stats,
        strategy_class=strategy,
        floor7_open=floor7_open(final.board),
        exit_guard_required=pits_after > 0,
    ).to_dict()


__all__ = ["plan_v3"]
