"""V5 mining planner — probabilistic / expected-cost search.

Builds on the v4 bounded DFS skeleton (action format, B&B, transposition,
wall-clock deadline) but folds in the empirical priors measured from 343 real
sessions (docs/MINING_V5_PRIORS.md). The priors change *which* move the search
prefers, not the search machinery:

1. Expected-cost descent (priors §2/§3 air|air = 27.9% = 1.50x lift): when a
   plan opens floor 7 to trigger a scroll, the column it descends through is
   not free — the cells below the viewport still have to be dug later. v5
   prices that future cost per column using the vertical transition priors and
   prefers descending an air column (cheap future) over a rock column.

2. Pit continuation (priors §2 pit|pit = 42.5% = 13.78x lift): a column that
   already holds a pit is likely to hold more pit directly below, so its future
   value is higher — v5 biases digging *down* a pit column.

3. Square-cluster bomb delay (priors §6: w2 -> 43%, w3 -> 77% pit-below): a
   wide pit run sitting at the viewport's bottom edge with fewer rows visible
   than its width is an INCOMPLETE square that will grow once scrolled. Bombing
   it now wastes the bomb on a partial cluster — v5 suppresses item use on
   incomplete bottom-edge squares and lets them reveal first.

4. Row-0 rescue (unchanged from v4): a collectable row-0 pit is worth more than
   any scroll, because scrolling loses it permanently.

The cluster-completion reward and item rarity costs are inherited from v4 so
the simulator's scoring rule (n*10 + n*(n-1)*2 on full clear) still drives the
objective.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
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
    is_air,
    is_pit,
    is_reachable_air,
    normalize_board,
)
from miner.v3.types import Board, Coordinate, PlanResult, PlanStats
from miner.v4.planner import (
    FLOOR7_OPEN_BONUS,
    MAX_DEPTH,
    NODE_BUDGET,
    PIT_VALUE,
    SHOVEL_COST,
    TIME_BUDGET_MS,
    _cluster_completion_delta,
    _identify_pit_groups,
    _max_cluster_completion_gain,
    _no_pit_dig_filter,
    _simulate,
    _state_signature,
)
from miner.v5.priors import Priors, get_priors
from miner.v5.priors_runtime import merged_priors

# Item rarity weights inherited from v4's tuning (scarce-inventory safe). The
# priors-driven incomplete-square DELAY (below) is what makes v5's item use
# smarter — bombs land on COMPLETE squares only — rather than re-pricing items.
DRILL_COST = 2.5
BOMB_COST = 3.5

# ---------------------------------------------------------------------------
# v5 tuning knobs (priors-driven, swept against the simulator). The cluster /
# item / floor7 economy is inherited from v4 (imported above). These weights
# only modulate the action *ordering* and the small expected-future bonus that
# breaks ties between otherwise-equivalent scroll/descent moves.
# ---------------------------------------------------------------------------

# Strength of the "descend an air column" preference. The expected future dig
# cost of a column is roughly `E[cost below] / (1 - P(air|air))` rows; the
# difference between an air column and a rock column is ~1 shovel of future
# work, so this stays below SHOVEL_COST to act as a tie-break, not a driver.
DESCENT_COLUMN_WEIGHT = 6.0

# Bonus for digging *down* a column that already holds a pit (pit-below-pit
# lift). Encourages committing to a pit column rather than abandoning it.
PIT_COLUMN_BONUS = 4.0

# A bottom-edge pit run of width w whose visible height h < w is an INCOMPLETE
# square (it will likely grow once scrolled). Items landing on such a run are
# deprioritised so the planner waits for the full square to reveal.
INCOMPLETE_SQUARE_PENALTY = 400.0


# Per-device merged-priors cache. Building merged priors reads the runtime JSON
# from disk, so we cache the result per device and only rebuild when the runtime
# file's mtime changes (cheap stat, never a per-plan re-read of the contents).
_MERGED_PRIORS_CACHE: Dict[str, Tuple[float, Priors]] = {}


def _resolve_priors(device: Optional[str]) -> Priors:
    """Return static priors (device is None) or this device's merged priors.

    Merged priors are cached and invalidated by the runtime file's mtime so a
    long-running session that keeps appending observations eventually picks up
    its own accumulated counts without paying a file read on every plan.
    """
    if not device:
        return get_priors()
    from miner.v5.priors_runtime import runtime_path

    path = runtime_path(device)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _MERGED_PRIORS_CACHE.get(device)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    priors = merged_priors(device)
    _MERGED_PRIORS_CACHE[device] = (mtime, priors)
    return priors


@dataclass
class _SearchBest:
    score: float = -float("inf")
    plan: List[Dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0
    shovel_cost: float = 0.0
    pits_cleared: int = 0
    drills_used: int = 0
    bombs_used: int = 0
    final_board: Optional[Board] = None


def _classify_strategy(board: Board) -> str:
    return "has_pit" if count_remaining_pits(board) > 0 else "no_pit"


# ---------------------------------------------------------------------------
# Prior-derived board features (computed once per plan)
# ---------------------------------------------------------------------------


def _column_descent_quality(board: Board, priors: Priors) -> List[float]:
    """Per-column 'cheapness of descending here' score.

    Higher = cheaper expected future digging once the viewport scrolls down
    this column. Two signals from the priors:

    - The column's bottom-most known cell predicts the (unknown) cell below it
      via the vertical transition table; an air bottom predicts more air
      (P(air|air)=1.50x lift) = cheap future, a rock bottom predicts rock/dirt
      = expensive.
    - Existing air run-length in the column: a column that is already air at
      the bottom rows tunnels for free.

    Returned as a *bonus* (higher is better) so it can be added to action
    priority.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    quality: List[float] = [0.0] * cols
    worst = priors.expected_dig_cost_below("rock")
    for c in range(cols):
        # Bottom-edge label drives the expected FUTURE (below-viewport) cost.
        # rows is a fixed full viewport (callers pass a non-empty 7-row board),
        # so the bottom edge is simply the last row.
        cell = board[rows - 1][c]
        if is_air(cell):
            bottom_label = "air"
        elif is_pit(cell):
            bottom_label = "pit"
        elif "rock" in cell:
            bottom_label = "rock"
        else:
            bottom_label = "dirt"
        exp_cost = priors.expected_dig_cost_below(bottom_label)
        future_bonus = worst - exp_cost

        # Path cost to OPEN row 6 in this column = sum of HP of the solid cells
        # that have to be dug to expose a row-6 air cell. This is the dominant
        # shovel sink the no_pit descent must minimise (v1's edge). A column
        # already air to the bottom costs 0; a rock-stacked column costs most.
        path_cost = 0.0
        for r in range(rows):
            cell = board[r][c]
            if is_air(cell):
                continue
            path_cost += 2.0 if "rock" in cell else 1.0
        # Cheaper path => higher quality. Scale so a 1-shovel saving on the
        # path outweighs the (sub-shovel) future-prior tie-break.
        quality[c] = future_bonus - path_cost
    return quality


def _pit_columns(board: Board) -> Set[int]:
    """Columns that currently hold at least one pit cell (reachable or not)."""
    cols = len(board[0]) if board else 0
    out: Set[int] = set()
    for c in range(cols):
        for row in board:
            if is_pit(row[c]):
                out.add(c)
                break
    return out


def _incomplete_bottom_squares(board: Board) -> Set[Coordinate]:
    """Cells belonging to a bottom-edge pit run whose visible height is less
    than its horizontal width — i.e. an incomplete square that the priors say
    is likely to extend downward once scrolled (w2 43%, w3 77%).

    Returns the set of pit cells the planner should avoid spending an item on
    *yet*. We look at the last row: any maximal horizontal pit run there of
    width w whose vertical extent upward is < w is flagged.
    """
    rows = len(board)
    cols = len(board[0]) if board else 0
    if rows == 0:
        return set()
    last = rows - 1
    flagged: Set[Coordinate] = set()
    c = 0
    while c < cols:
        if not is_pit(board[last][c]):
            c += 1
            continue
        # Measure horizontal run width on the bottom row.
        start = c
        while c < cols and is_pit(board[last][c]):
            c += 1
        width = c - start
        if width < 2:
            continue  # width-1 squares never grow meaningfully (2.1%)
        # Visible vertical height of this run: how many rows up are all-pit
        # across the run's columns.
        height = 0
        for r in range(last, -1, -1):
            if all(is_pit(board[r][cc]) for cc in range(start, start + width)):
                height += 1
            else:
                break
        if height < width:
            for cc in range(start, start + width):
                flagged.add((last, cc))
    return flagged


def _action_priority(
    board: Board,
    action: Dict[str, Any],
    pit_cells_hit: FrozenSet[Coordinate],
    strategy: str,
    column_quality: List[float],
    pit_cols: Set[int],
    incomplete_squares: Set[Coordinate],
) -> float:
    """Higher priority = explored first. Same backbone as v4 plus prior signals."""
    pit_hit_count = len(pit_cells_hit)
    priority = 100.0 * pit_hit_count

    if action["type"] == "use":
        item = action["item"]
        if pit_hit_count >= 2:
            priority += 30.0 * (pit_hit_count - 1)
        # Bomb / drill delay: if the footprint's pit hits are dominated by an
        # incomplete bottom-edge square, hold off (the square will grow).
        if pit_cells_hit:
            incomplete_hits = sum(1 for cell in pit_cells_hit if cell in incomplete_squares)
            if incomplete_hits >= 1 and incomplete_hits == pit_hit_count:
                priority -= INCOMPLETE_SQUARE_PENALTY
        if item == "drill" and strategy == "no_pit":
            priority += 50.0
    else:
        r, c = action["pos"]
        # Row-0 pits are MVP — scrolling would lose them permanently.
        if r == 0 and is_pit(board[r][c]):
            priority += 500.0
        # Pit-column commitment: digging within a column that already holds a
        # pit is worth more (pit-below-pit lift) — keep tunnelling it.
        if c in pit_cols and is_pit(board[r][c]):
            priority += PIT_COLUMN_BONUS
        if strategy == "no_pit":
            # Drive toward a scroll, preferring deeper rows AND columns whose
            # expected future descent cost is low (air columns).
            priority += float(r) * 10.0
            if 0 <= c < len(column_quality):
                priority += DESCENT_COLUMN_WEIGHT * column_quality[c]
            # Pocket-cascade bonus: a dig 4-adjacent to an unreachable_empty
            # pocket flood-opens it (promote_after_dig), which can expose row-6
            # air and trigger a multi-row scroll cascade off a single shovel.
            # Prefer these digs in pure-descent mode.
            rows_b = len(board)
            cols_b = len(board[0]) if board else 0
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows_b and 0 <= nc < cols_b:
                    if board[nr][nc] == "unreachable_empty":
                        priority += 40.0
                        break

    return priority


def _score(
    cluster_delta: float,
    cost: float,
    drills_used: int,
    bombs_used: int,
    f7_open: bool,
) -> float:
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
    s = cluster_delta + max_extra_cluster_gain + FLOOR7_OPEN_BONUS
    s -= SHOVEL_COST * cost
    s -= DRILL_COST * drills_used
    s -= BOMB_COST * bombs_used
    return s


def _filter_actions(
    board: Board,
    items: Dict[str, int],
    strategy: str,
    max_depth: int,
) -> List[Dict[str, Any]]:
    """Same action universe as v4's Manhattan filter, minus the corridor
    machinery (v5 leans on the no_pit fallback for buried pits, which keeps the
    empty-plan guard simple and the deadline tight)."""
    actions: List[Dict[str, Any]] = []
    rows = len(board)
    cols = len(board[0]) if board else 0

    if strategy == "has_pit":
        pit_positions: Set[Coordinate] = {
            (r, c) for r in range(rows) for c in range(cols) if is_pit(board[r][c])
        }
        within_reach: Set[Coordinate] = set(pit_positions)
        frontier: List[Coordinate] = list(pit_positions)
        for _ in range(max_depth):
            nxt: List[Coordinate] = []
            for r, c in frontier:
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in within_reach:
                        within_reach.add((nr, nc))
                        nxt.append((nr, nc))
            frontier = nxt
            if not frontier:
                break
        for action in enumerate_dig_actions(board):
            if tuple(action["pos"]) in within_reach:
                actions.append(action)
        for action in enumerate_item_actions(board, items):
            if affected_pit_cells(board, action):
                actions.append(action)
        if not actions:
            actions = _no_pit_dig_filter(board)
    else:
        actions = _no_pit_dig_filter(board)
    return actions


def plan_v5(
    board: Board,
    shovels: float = 100.0,
    items: Optional[Dict[str, int]] = None,
    max_depth: int = MAX_DEPTH,
    blocked_actions: Optional[Set[Tuple[Any, ...]]] = None,
    node_budget: int = NODE_BUDGET,
    depth: Optional[int] = None,
    device: Optional[str] = None,
) -> Dict[str, Any]:
    """Probabilistic bounded-horizon planner. Returns the best plan within depth.

    ``depth`` is the *board* depth (rows already scrolled past), recorded into
    stats for telemetry. Priors are pooled (not depth-bucketed) so it does not
    change the search — see docs/MINING_V5_PRIORS.md §5.
    """
    started_at = time.perf_counter()
    priors = _resolve_priors(device)
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

    # Prior-derived static features for action ordering.
    column_quality = _column_descent_quality(work, priors)
    pit_cols = _pit_columns(work)
    incomplete_squares = _incomplete_bottom_squares(work)

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
        cur_depth: int,
    ) -> None:
        explored[0] += 1
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
            best.shovel_cost = cur_shovel_cost
            best.pits_cleared = cur_pits_cleared
            best.drills_used = cur_drills
            best.bombs_used = cur_bombs
            best.final_board = [row[:] for row in cur_board]

        if cur_depth >= max_depth:
            return

        remaining_pits = count_remaining_pits(cur_board)
        if strategy == "has_pit" and remaining_pits == 0:
            return
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

        raw_actions = _filter_actions(cur_board, cur_items, strategy, max_depth)
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
            priority = _action_priority(
                cur_board,
                action,
                pit_hits,
                strategy,
                column_quality,
                pit_cols,
                incomplete_squares,
            )
            scored.append((priority, action, pit_hits))
        scored.sort(key=lambda t: t[0], reverse=True)
        if len(scored) > 12:
            scored = scored[:12]

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
            if cur_shovel_cost + shovel_cost_delta > shovels:
                continue
            next_pits = count_remaining_pits(next_board)
            pits_hit_now = max(0, remaining_pits - next_pits)
            next_reachable_pits, _ = count_pits(next_board)

            # Anti-scroll: never open floor7 while a REACHABLE pit remains if
            # the action itself doesn't collect a pit (matches v4 / row-0
            # rescue requirement).
            if (
                strategy == "has_pit"
                and next_reachable_pits > 0
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
                cur_depth + 1,
            )
            plan.pop()
            if budget_hit[0]:
                return

    dfs(work, item_state, 0.0, 0.0, 0, 0, 0, [], 0)

    # Empty-plan guard: never surrender. If the DFS found no improving move
    # (e.g. every action's cost outweighs its reward under the current score),
    # fall back to the cheapest scroll-progress dig so the runtime advances.
    if not best.plan:
        fallback = _no_pit_dig_filter(work)
        if fallback:
            # Cheapest, then deepest, then best descent column.
            def _key(a: Dict[str, Any]) -> Tuple[float, int, float]:
                r, c = a["pos"]
                cq = column_quality[c] if 0 <= c < len(column_quality) else 0.0
                from miner.v3.actions import dig_cost as _dc

                return (float(_dc(work[r][c])), -r, -cq)

            fallback.sort(key=_key)
            chosen = dict(fallback[0])
            chosen["target"] = chosen.get("pos")
            best.plan = [chosen]
            best.final_board = [row[:] for row in work]

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
        shovel_cost=best.shovel_cost,
        drills_used=best.drills_used,
        bombs_used=best.bombs_used,
        cost_per_pit=(best.shovel_cost / max(1, initial_pits - pits_after)),
    )
    result = PlanResult(
        ok=True,
        message=(
            f"v5 plan (depth={max_depth}, nodes={explored[0]}, "
            f"pits {stats.pits_collected}/{initial_pits}, "
            f"shovels={stats.shovel_cost:.1f}, "
            f"drill={stats.drills_used}, bomb={stats.bombs_used}, "
            f"priors={priors.source})"
        ),
        steps=best.plan,
        stats=stats,
        strategy_class=strategy,
        floor7_open=floor7_open(final_board),
        exit_guard_required=pits_after > 0,
    ).to_dict()
    # Telemetry: record the caller-supplied board depth (priors are pooled).
    result["stats"]["requested_depth"] = depth
    result["stats"]["priors_source"] = priors.source
    return result


__all__ = ["plan_v5"]
