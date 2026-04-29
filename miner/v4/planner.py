"""V4 mining planner — bounded 5-step rolling-horizon search.

See design discussion 2026-04-25:

- Depth-limited DFS (default max_depth=5). mining_service re-plans each
  iteration anyway, so "global" search is wasted effort.
- Item rarity is PRICED into the objective: drill and bomb aren't worth 3
  shovels, they're scarce resources.
- Cluster-bonus breaks ties between "1×3 partial cluster" (shovel) and
  "2×2 full cluster" (bomb): only a SQUARE cluster cover rewards bombs.
- Pruning: action filter + B&B upper bound + dominance transposition
  + ordered expansion. Target: well under 10k nodes typical.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from miner.v3.actions import (
    affected_pit_cells,
    apply_bomb,
    apply_dig,
    apply_drill,
    enumerate_dig_actions,
    enumerate_item_actions,
)
from miner.v3.board import (
    canonicalize_in_place,
    count_pits,
    count_remaining_pits,
    floor7_open,
    is_pit,
    normalize_board,
)
from miner.v3.types import Board, Coordinate, PlanResult, PlanStats

# ---------------------------------------------------------------------------
# Scoring model — cluster-completion based, matches the simulator's reward
# rule (`tools/mining_sim.html`). Each visible pit group is worth
# `cluster_value(n) = n*PIT_VALUE + n*(n-1)*2` ON FULL CLEAR. Partially-dug
# clusters are PENALISED by the same value to force commit-or-skip behaviour
# (user-requested 2026-04-29: "少挖一個就要算扣掉 1×1/2×2/3×3 的扣分").
# Untouched clusters contribute 0.
# ---------------------------------------------------------------------------
PIT_VALUE = 10.0
SHOVEL_COST = 1.0
# Item rarity > shovel. Real-game starting inventory is 1000 shovels vs 10
# drills / 10 bombs, so items must out-price shovels (user-clarified
# 2026-04-29: "炸彈和鑽頭比鎬子稀有很多, 不得犧牲"). Bomb > drill because
# its 13-cell footprint is best saved for full 2×2/3×3 cluster covers.
# DRILL=2.5 / BOMB=3.0 emerged from a (drill, bomb) sweep on the simulator —
# higher drill cost forces the planner to land drills on multi-pit columns
# (column-3-of-3×3 plays) rather than spam them on single-pit hits, which
# improves both score and shovel cost simultaneously (planner-eval sweep
# 2026-04-29: score 2492→2626, cost 15.6→13.0).
DRILL_COST = 2.5
BOMB_COST = 3.0
# Game scrolls when ANY row 6 cell becomes reachable air (user-confirmed
# 2026-04-29). One air cell is the trigger; clearing more is wasted shovels.
FLOOR7_OPEN_BONUS = 20.0

# Depth-3 sweet spot under the 300 ms wall-clock budget. Empirically (skill:
# planner-eval, 2026-04-29) depth-5 only adds +9 score / +1 pit per game vs
# depth-3 but blows past 300 ms on cluster-rich seeds (peak 1122 ms). The
# rolling re-plan in mining_service compensates — multi-iter completes any
# combo depth-3 can't fit.
MAX_DEPTH = 3
# 8k catches the long tail without hurting plan quality at depth 3.
NODE_BUDGET = 8_000
# Wall-clock cap (ms). Hard deadline — when crossed the DFS returns whatever
# best plan it has explored so far. Keeps the worst-case plan time predictable
# for the runtime caller (mining_service expects sub-300 ms).
TIME_BUDGET_MS = 250.0


def _cluster_value(n: int) -> float:
    """Reward for fully clearing an n-cell pit cluster — matches simulator's
    `n * 10 + n * (n - 1) * 2`."""
    return n * PIT_VALUE + n * (n - 1) * 2.0


# ---------------------------------------------------------------------------
# Search bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _SearchBest:
    score: float = -float("inf")
    plan: List[Dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0
    pits_cleared: int = 0
    drills_used: int = 0
    bombs_used: int = 0
    final_board: Optional[Board] = None


def _classify_strategy(board: Board) -> str:
    return "has_pit" if count_remaining_pits(board) > 0 else "no_pit"


def _board_signature(board: Board) -> Tuple[Tuple[str, ...], ...]:
    return tuple(tuple(row) for row in board)


def _state_signature(board: Board, items: Dict[str, int]) -> Tuple[Any, ...]:
    return (
        _board_signature(board),
        items.get("drill", 0),
        items.get("bomb", 0),
    )


def _identify_pit_groups(board: Board) -> List[FrozenSet[Coordinate]]:
    """Find connected-component pit groups on the board.

    The simulator places clusters as 1×1 / 2×2 / 3×3 blocks with a 1-cell
    isolation ring, so a 4-cell group is always a 2×2 and a 9-cell group is
    always a 3×3. Multi-iteration play can leave irregular remnants (e.g. a
    3-cell L from a half-dug 2×2) — those are still treated as a single
    group of size N for completion scoring.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    visited: Set[Coordinate] = set()
    groups: List[FrozenSet[Coordinate]] = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in visited or not is_pit(board[r][c]):
                continue
            group: Set[Coordinate] = set()
            queue = [(r, c)]
            while queue:
                cr, cc = queue.pop()
                if (cr, cc) in visited:
                    continue
                if not (0 <= cr < rows and 0 <= cc < cols):
                    continue
                if not is_pit(board[cr][cc]):
                    continue
                visited.add((cr, cc))
                group.add((cr, cc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    queue.append((cr + dr, cc + dc))
            if group:
                groups.append(frozenset(group))
    return groups


def _cluster_completion_delta(
    board: Board,
    original_groups: List[FrozenSet[Coordinate]],
) -> float:
    """Hybrid pit-cell + completion-bonus scoring.

    Per-cell reward (`PIT_VALUE` each) gives the planner incremental signal so
    multi-iteration clearing of a cluster larger than the depth budget still
    makes progress. Full-clear adds the `n*(n-1)*2` cluster bonus on top —
    matching the simulator's `n*10 + n*(n-1)*2` total reward. Partial digs
    earn the per-cell reward but no bonus, so a fully-cleared 3×3 (+234) is
    far more attractive than a half-dug one (+50 from 5 pits).

    Earlier "penalty for partial = -cluster_value" was tried but caused
    out-of-drills lockup: when only shovels were left, partial cluster digs
    always scored negative vs do-nothing, so the planner abandoned the
    cluster and the game stopped advancing.
    """
    delta = 0.0
    for group in original_groups:
        n = len(group)
        cells_dug = sum(1 for (r, c) in group if not is_pit(board[r][c]))
        delta += PIT_VALUE * cells_dug
        if cells_dug == n and n > 1:
            delta += n * (n - 1) * 2.0
    return delta


def _max_cluster_completion_gain(
    board: Board,
    original_groups: List[FrozenSet[Coordinate]],
) -> float:
    """Admissible bound on extra cluster reward still in play. Each
    not-yet-completed group can deliver at most: (remaining cells × PIT_VALUE)
    + (full-clear bonus if n > 1)."""
    max_gain = 0.0
    for group in original_groups:
        n = len(group)
        cells_remaining = sum(1 for (r, c) in group if is_pit(board[r][c]))
        if cells_remaining == 0:
            continue
        max_gain += cells_remaining * PIT_VALUE
        if n > 1:
            max_gain += n * (n - 1) * 2.0
    return max_gain


def _score(
    cluster_delta: float,
    cost: float,
    drills_used: int,
    bombs_used: int,
    f7_open: bool,
) -> float:
    """Score = cluster reward/penalty − shovels − items + (floor7 open bonus).

    Cluster delta already encodes the full-clear / partial / untouched
    semantics; nothing else needs to know about pit cells individually.
    """
    s = cluster_delta
    s -= SHOVEL_COST * cost
    s -= DRILL_COST * drills_used
    s -= BOMB_COST * bombs_used
    if f7_open:
        s += FLOOR7_OPEN_BONUS
    return s


def _upper_bound_score(
    cluster_delta: float,
    max_extra_cluster_gain: float,
    cost: float,
    drills_used: int,
    bombs_used: int,
) -> float:
    """Admissible bound: assume all remaining cluster gains realised, floor7
    is open (it can only become open from any reachable descendant — once
    air, never not-air), and no further costs are incurred. FLOOR7_OPEN_BONUS
    is always included because the score function awards it on any state
    where row 6 has reachable air, and digging can only add air."""
    s = cluster_delta + max_extra_cluster_gain + FLOOR7_OPEN_BONUS
    s -= SHOVEL_COST * cost
    s -= DRILL_COST * drills_used
    s -= BOMB_COST * bombs_used
    return s


def _action_priority(
    board: Board,
    action: Dict[str, Any],
    pit_cells_hit: FrozenSet[Coordinate],
    strategy: str = "has_pit",
) -> float:
    """Higher priority = explore first. Good ordering builds a strong lower
    bound early so B&B prunes more aggressively."""
    pit_hit_count = len(pit_cells_hit)
    priority = 100.0 * pit_hit_count

    if action["type"] == "use":
        # Items only outrank shovels when they cover ≥2 pits in one shot —
        # on a single-pit hit, drill (cost 1.5) and shovel (cost 1.0) deliver
        # the same gain, so we don't want a base bonus that promotes drill
        # above shovel and risks the branching cap dropping the cheaper dig.
        if pit_hit_count >= 2:
            priority += 30.0 * (pit_hit_count - 1)
        if action["item"] == "drill" and strategy == "no_pit":
            # Pure descent mode: drill's vertical footprint tunnels to row 6
            # in one action, regardless of pit count.
            priority += 50.0
    else:
        r, c = action["pos"]
        # Row-0 pits are MVP — scrolling would lose them permanently.
        if r == 0 and is_pit(board[r][c]):
            priority += 500.0
        if strategy == "no_pit":
            priority += float(r) * 10.0

    return priority


def _simulate(
    board: Board,
    items: Dict[str, int],
    action: Dict[str, Any],
) -> Optional[Tuple[Board, Dict[str, int], float, float]]:
    """Simulate one action. Returns (next_board, next_items, step_cost,
    shovel_cost). step_cost mirrors v3 semantics for display consistency; the
    internal score only consumes shovel_cost, and item uses contribute 0
    shovels (their cost is the rarity weight DRILL_COST / BOMB_COST)."""
    next_board = [row[:] for row in board]
    next_items = dict(items)
    if action["type"] == "dig":
        step_cost = apply_dig(next_board, action["pos"])
        shovel_cost = step_cost
    elif action["type"] == "use":
        item = action["item"]
        if next_items.get(item, 0) <= 0:
            return None
        next_items[item] -= 1
        if item == "drill":
            step_cost, _ = apply_drill(next_board, action["pos"])
        else:
            step_cost, _, _ = apply_bomb(next_board, action["pos"])
        shovel_cost = 0.0
    else:
        return None
    return next_board, next_items, float(step_cost), float(shovel_cost)


def _filter_actions(
    board: Board,
    items: Dict[str, int],
    strategy: str,
    max_depth: int = MAX_DEPTH,
) -> List[Dict[str, Any]]:
    """Drop actions that can't improve the plan.

    - has_pit: digs within Manhattan distance ≤ `max_depth` from any pit.
      Tighter than "anywhere" (avoids wasteful rock tours far from pits) but
      looser than "4-adjacent to a pit" (which dropped the entry-point dig
      when a pit is buried under multiple layers of unreachable rock — see
      Case 2 in the 2026-04-28 follow-up). Item uses must still hit ≥1 pit.
    - no_pit: any dig that touches row 6 reachability — directly (a row-6
      hard cell) or indirectly (a hard cell with `unreachable_empty` in its
      4-neighbourhood, since `promote_after_dig` floods through those). This
      catches the "row 5 dirt above an unreachable air pocket" case where
      one cost-1 dig floods a whole row-6 strip.
    """
    actions: List[Dict[str, Any]] = []
    rows = len(board)
    cols = len(board[0]) if board else 0

    if strategy == "has_pit":
        pit_positions: Set[Coordinate] = {
            (r, c)
            for r in range(rows)
            for c in range(cols)
            if is_pit(board[r][c])
        }
        # BFS Manhattan distance from any pit; keep cells within max_depth
        # so the filter retains every wall-crack on a path the planner could
        # actually drill through within its depth budget.
        within_reach: Set[Coordinate] = set(pit_positions)
        frontier: List[Coordinate] = list(pit_positions)
        for _ in range(max_depth):
            next_frontier: List[Coordinate] = []
            for r, c in frontier:
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in within_reach:
                        within_reach.add((nr, nc))
                        next_frontier.append((nr, nc))
            frontier = next_frontier
            if not frontier:
                break

        for action in enumerate_dig_actions(board):
            if tuple(action["pos"]) in within_reach:
                actions.append(action)
        for action in enumerate_item_actions(board, items):
            if affected_pit_cells(board, action):
                actions.append(action)
    else:
        # no_pit: dig anywhere that can affect row 6 reachability. Always
        # include row-6 frontier digs; for higher rows include only digs
        # adjacent to an `unreachable_empty` (their flood can promote row 6
        # cells via a chain). This keeps the branching factor low while still
        # finding cost-1 dirt digs that flood entire row-6 strips.
        last_row = rows - 1
        for action in enumerate_dig_actions(board):
            r, c = action["pos"]
            if r == last_row:
                actions.append(action)
                continue
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols:
                    cell = board[nr][nc]
                    # Normalise without import: `unreachable_empty` is the
                    # only label that triggers flood promotion in v3 board.
                    if cell == "unreachable_empty" or cell == "unreachable_void":
                        actions.append(action)
                        break

    return actions


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def plan_v4(
    board: Board,
    shovels: float = 100.0,
    items: Optional[Dict[str, int]] = None,
    max_depth: int = MAX_DEPTH,
    blocked_actions: Optional[Set[Tuple[Any, ...]]] = None,
    node_budget: int = NODE_BUDGET,
) -> Dict[str, Any]:
    """Bounded rolling-horizon planner. Returns the best plan within depth."""
    started_at = time.perf_counter()
    work = normalize_board(board)
    canonicalize_in_place(work)
    initial_pits = count_remaining_pits(work)
    strategy = _classify_strategy(work)
    item_state = {
        "drill": int((items or {}).get("drill", 0)),
        "bomb": int((items or {}).get("bomb", 0)),
    }
    blocked = set(blocked_actions or set())
    original_groups = _identify_pit_groups(work)

    best = _SearchBest(final_board=[row[:] for row in work])
    best.score = _score(
        cluster_delta=_cluster_completion_delta(work, original_groups),
        cost=0.0,
        drills_used=0,
        bombs_used=0,
        f7_open=floor7_open(work),
    )

    dominance: Dict[Tuple[Any, ...], float] = {}
    explored = [0]
    budget_hit = [False]
    deadline = started_at + TIME_BUDGET_MS / 1000.0

    def dfs(
        cur_board: Board,
        cur_items: Dict[str, int],
        cur_total_cost: float,
        cur_shovel_cost: float,
        cur_pits_cleared: int,
        cur_drills: int,
        cur_bombs: int,
        plan: List[Dict[str, Any]],
        depth: int,
    ) -> None:
        explored[0] += 1
        # Sample the clock every 64 nodes — checking on every node would
        # itself dominate runtime on large searches.
        if (explored[0] & 0x3F) == 0 and time.perf_counter() >= deadline:
            budget_hit[0] = True
            return
        if explored[0] > node_budget:
            budget_hit[0] = True
            return

        f7 = floor7_open(cur_board)
        cluster_delta = _cluster_completion_delta(cur_board, original_groups)
        s = _score(
            cluster_delta=cluster_delta,
            cost=cur_shovel_cost,
            drills_used=cur_drills,
            bombs_used=cur_bombs,
            f7_open=f7,
        )
        if s > best.score:
            best.score = s
            best.plan = plan[:]
            best.cost = cur_total_cost
            best.pits_cleared = cur_pits_cleared
            best.drills_used = cur_drills
            best.bombs_used = cur_bombs
            best.final_board = [row[:] for row in cur_board]

        if depth >= max_depth:
            return

        remaining_pits = count_remaining_pits(cur_board)
        if strategy == "has_pit" and remaining_pits == 0:
            return
        # No-pit goal: row 6 has any reachable air → game scrolls. Once that
        # condition is met any extra digging is wasted shovels.
        if strategy == "no_pit" and f7:
            return

        sig = _state_signature(cur_board, cur_items)
        prev = dominance.get(sig)
        if prev is not None and prev <= cur_shovel_cost:
            return
        dominance[sig] = cur_shovel_cost

        max_extra = _max_cluster_completion_gain(cur_board, original_groups)
        ub = _upper_bound_score(
            cluster_delta=cluster_delta,
            max_extra_cluster_gain=max_extra,
            cost=cur_shovel_cost,
            drills_used=cur_drills,
            bombs_used=cur_bombs,
        )
        if ub <= best.score:
            return

        raw_actions = _filter_actions(cur_board, cur_items, strategy)
        scored: List[Tuple[float, Dict[str, Any], FrozenSet[Coordinate]]] = []
        for action in raw_actions:
            sig_action = (
                action.get("type"),
                action.get("item"),
                tuple(action.get("pos", ())),
            )
            if sig_action in blocked:
                continue
            pit_hits = affected_pit_cells(cur_board, action)
            priority = _action_priority(cur_board, action, pit_hits, strategy)
            scored.append((priority, action, pit_hits))
        scored.sort(key=lambda t: t[0], reverse=True)

        # Branching cap — empirically the optimal plan is always within the
        # top-priority handful, and limiting branching to K=12 keeps the
        # depth-3 tree at ≤ 12³ = 1728 leaf paths even before B&B kicks in.
        # 30+ candidates per node otherwise blow up the worst-case search.
        if len(scored) > 12:
            scored = scored[:12]

        # Pre-action upper bound. The child's UB drops by at least this
        # action's minimum cost. If the dynamic margin (recomputed each iter
        # so we credit best-improvements from earlier actions) is below the
        # action's cost, this action can't improve and is skipped. We
        # `continue` rather than `break` because actions are sorted by
        # priority, NOT by cost — a cheap shovel can still beat best after
        # a higher-priority but more-expensive bomb has been tried first.
        for _priority, action, pit_hits in scored:
            if action["type"] == "dig":
                action_min_cost = SHOVEL_COST
            elif action.get("item") == "drill":
                action_min_cost = DRILL_COST
            else:
                action_min_cost = BOMB_COST
            if ub - best.score <= action_min_cost:
                continue

            sim = _simulate(cur_board, cur_items, action)
            if sim is None:
                continue
            next_board, next_items, step_cost, shovel_cost_delta = sim
            next_pits = count_remaining_pits(next_board)
            pits_hit_now = max(0, remaining_pits - next_pits)

            # Anti-scroll: never open floor7 while pits remain if the action
            # itself doesn't collect pits. Collecting a row-6 pit may open
            # floor7 as a side effect — allowed because we got the pit.
            if (
                strategy == "has_pit"
                and next_pits > 0
                and not f7
                and floor7_open(next_board)
                and pits_hit_now == 0
            ):
                continue

            next_drills = cur_drills + (1 if action.get("item") == "drill" else 0)
            next_bombs = cur_bombs + (1 if action.get("item") == "bomb" else 0)

            step = dict(action)
            step["step_cost"] = step_cost
            step["target"] = action.get("pos")

            plan.append(step)
            dfs(
                next_board,
                next_items,
                cur_total_cost + step_cost,
                cur_shovel_cost + shovel_cost_delta,
                cur_pits_cleared + pits_hit_now,
                next_drills,
                next_bombs,
                plan,
                depth + 1,
            )
            plan.pop()

            if budget_hit[0]:
                return

    dfs(work, item_state, 0.0, 0.0, 0, 0, 0, [], 0)

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    final_board = best.final_board if best.final_board is not None else work
    pits_after_r, pits_after_u = count_pits(final_board)
    pits_after = pits_after_r + pits_after_u

    stats = PlanStats(
        explored_nodes=explored[0],
        elapsed_ms=elapsed_ms,
        pits_at_start=initial_pits,
        pits_collected=max(0, initial_pits - pits_after),
        pits_remaining=pits_after,
        pits_unreachable_remaining=pits_after_u,
        shovel_cost=best.cost,
        drills_used=best.drills_used,
        bombs_used=best.bombs_used,
        cost_per_pit=(best.cost / max(1, initial_pits - pits_after)),
    )
    message = (
        f"v4 plan (depth={max_depth}, nodes={explored[0]}, "
        f"pits {stats.pits_collected}/{initial_pits}, "
        f"shovels={stats.shovel_cost:.1f}, "
        f"drill={stats.drills_used}, bomb={stats.bombs_used})"
    )
    return PlanResult(
        ok=True,
        message=message,
        steps=best.plan,
        stats=stats,
        strategy_class=strategy,
        floor7_open=floor7_open(final_board),
        exit_guard_required=pits_after > 0,
    ).to_dict()


__all__ = ["plan_v4"]
