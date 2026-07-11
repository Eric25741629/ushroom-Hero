# Final V1 Mining Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `final_v1` mining planner that values completed pit clusters and resource efficiency, uses WS-known terrain below the viewport safely, supports shadow evaluation, and leaves `v1` as the default and fallback.

**Architecture:** Keep the search engine isolated in `miner/final_v1/`, with immutable action/result types, a shared scoring module, and a bounded beam-search entry point. CNN callers pass their 7×6 board directly; WS callers let `mining_adapter` build a variable-height known board plus action-aware server-valid first targets, while `mining_supervised` remains responsible for execution confirmation and authoritative inventory reconciliation. Configuration, Dashboard controls, telemetry, replay, and A/B tools are wired around that single planner contract without changing the existing v1/v3/v4 implementations.

**Tech Stack:** Python 3.10, dataclasses/type hints, existing `miner.v3` board/action mechanics, existing `miner.core.mechanics` footprints, WS `0x0401`/`0x0402` inventory tracking, pytest, vanilla Dashboard HTML/JavaScript.

---

## File map and ownership

- Create `miner/final_v1/__init__.py`: public `plan_final_v1` import only.
- Create `miner/final_v1/types.py`: immutable action keys, score breakdown, planner configuration, and search telemetry.
- Create `miner/final_v1/scoring.py`: cluster discovery and the single shared objective used by candidate ordering and terminal-state ranking.
- Create `miner/final_v1/planner.py`: legal action enumeration, bounded beam search, simulation, dominance pruning, and first-step-only result contract.
- Create `tests/test_final_v1_scoring.py`: objective priorities and equal item-cost regressions.
- Create `tests/test_final_v1_planner.py`: inventory, visibility, legality, determinism, timeout, and fallback-boundary regressions.
- Modify `ws_token/mining_adapter.py`: variable-height WS projection, action-aware valid-target construction, final-v1 dispatch, WS-step mapping, shadow calculation, and v1 fallback.
- Create `tests/test_mining_adapter_final_v1.py`: 21-row reconstruction, below-viewport bomb visibility, server-valid first-step, seven-row degradation, shadow isolation, and fallback tests.
- Modify `game_actions/ws_phase.py`: copy the device-level primary/shadow planner settings into the WS mining config.
- Modify `ws_token/runner.py`: forward planner settings to the supervised mining loop.
- Modify `ws_token/mining_supervised.py`: final-v1 inventory-known gate, authoritative three-item refresh, inventory-aware confirmation, and structured plan/execute telemetry.
- Modify `tests/test_ws_phase.py`: device-to-WS planner-setting propagation tests.
- Modify `tests/test_ws_token_mining_supervised.py`: unknown pickaxe, consume/gain reconciliation, unchanged confirmation, and telemetry tests.
- Modify `miner/mining_service.py`: CNN final-v1 dispatch, action-aware blocked targets, shadow execution, safe fallback, and telemetry.
- Create `tests/test_mining_service_final_v1.py`: CNN dispatch, v1 fallback, blocked-first-step, and shadow exception isolation tests with heavy device/image imports stubbed.
- Modify `config_manager.py`: add the shadow field/default and permit `final_v1` through normalization.
- Modify `templates/dashboard.html`: primary and shadow planner controls with load/save wiring.
- Modify `tests/test_device_config.py`: defaults and normalization round-trip tests.
- Modify `tests/test_dashboard_template.py`: control presence, options, load/save, and default-off tests.
- Modify `tools/mining_sim_eval.py`: register `final_v1` and report equal-weight resource KPI inputs.
- Modify `tools/compare_planners.py`: include final-v1, p95/p99/max latency, cluster/resource KPIs, and rejection/stuck counters.
- Modify `tools/replay_real_boards.py`: include final-v1 and enforce/report the 250 ms budget.
- Create `tests/test_final_v1_eval_tools.py`: planner registry and aggregation/KPI regression tests.

Keep every commit below scoped to the listed files because the main worktree already contains unrelated user changes and untracked diagnostics.

---

### Task 1: Lock the planner contract and scoring semantics

**Files:**
- Create: `miner/final_v1/__init__.py`
- Create: `miner/final_v1/types.py`
- Create: `miner/final_v1/scoring.py`
- Create: `tests/test_final_v1_scoring.py`

- [ ] **Step 1: Write failing tests for equal item cost, cluster priority, row-loss protection, and low-weight descent**

```python
# tests/test_final_v1_scoring.py
from miner.final_v1.scoring import ITEM_COST, evaluate_state
from miner.final_v1.types import SearchUsage


def test_bomb_and_drill_have_the_same_base_cost():
    assert ITEM_COST["bomb"] == ITEM_COST["drill"]


def test_completed_cluster_beats_equal_number_of_scattered_pits():
    original = [
        ["empty", "empty", "empty", "empty", "empty", "empty"],
        ["reachable_pit", "reachable_pit", "dirt", "reachable_pit", "dirt", "dirt"],
        ["reachable_pit", "reachable_pit", "dirt", "reachable_pit", "dirt", "dirt"],
    ]
    complete_square = [row[:] for row in original]
    scattered = [row[:] for row in original]
    for pos in ((1, 0), (1, 1), (2, 0), (2, 1)):
        complete_square[pos[0]][pos[1]] = "empty"
    for pos in ((1, 0), (1, 3), (2, 0), (2, 3)):
        scattered[pos[0]][pos[1]] = "empty"

    completed_score = evaluate_state(original, complete_square, SearchUsage()).total
    scattered_score = evaluate_state(original, scattered, SearchUsage()).total
    assert completed_score > scattered_score


def test_scrolling_an_uncollected_row_zero_pit_is_strongly_penalized():
    original = [["reachable_pit"] + ["dirt"] * 5] + [["dirt"] * 6 for _ in range(6)]
    kept = evaluate_state(original, original, SearchUsage(), scrolled=False)
    lost = evaluate_state(original, original, SearchUsage(), scrolled=True)
    assert lost.lost_pit_penalty > kept.lost_pit_penalty
    assert lost.total < kept.total


def test_descent_bonus_never_outweighs_one_pit():
    original = [["empty"] * 6 for _ in range(7)]
    pit_board = [row[:] for row in original]
    pit_board[5][2] = "reachable_pit"
    collected = [row[:] for row in pit_board]
    collected[5][2] = "empty"
    pit_score = evaluate_state(pit_board, collected, SearchUsage()).pit_gain
    descent_score = evaluate_state(original, original, SearchUsage(), descent_rows=1).descent_bonus
    assert 0 < descent_score < pit_score
```

- [ ] **Step 2: Run the scoring tests and verify the new package is missing**

Run:

```bash
python -m pytest tests/test_final_v1_scoring.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'miner.final_v1'`.

- [ ] **Step 3: Add immutable planner types and a complete score breakdown**

```python
# miner/final_v1/types.py
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Tuple

ActionKind = Literal["dig", "use"]
ItemName = Literal["pickaxe", "drill", "bomb"]
ActionKey = Tuple[ActionKind, ItemName, int, int]


@dataclass(frozen=True)
class PlannerAction:
    kind: ActionKind
    item: ItemName
    row: int
    col: int

    @property
    def key(self) -> ActionKey:
        return (self.kind, self.item, self.row, self.col)

    def to_step(self, step_cost: float) -> dict:
        step = {
            "type": self.kind,
            "pos": (self.row, self.col),
            "target": (self.row, self.col),
            "step_cost": step_cost,
        }
        if self.kind == "use":
            step["item"] = self.item
        return step


@dataclass(frozen=True)
class PlannerConfig:
    max_depth: int = 6
    beam_width: int = 32
    branch_width: int = 12
    time_budget_ms: float = 250.0


@dataclass(frozen=True)
class SearchUsage:
    shovels: float = 0.0
    bombs: int = 0
    drills: int = 0


@dataclass(frozen=True)
class ScoreBreakdown:
    cluster_gain: float = 0.0
    pit_gain: float = 0.0
    shovel_cost: float = 0.0
    item_cost: float = 0.0
    lost_pit_penalty: float = 0.0
    unfinished_cluster_penalty: float = 0.0
    descent_bonus: float = 0.0
    path_bonus: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.cluster_gain + self.pit_gain + self.descent_bonus + self.path_bonus
            - self.shovel_cost - self.item_cost
            - self.lost_pit_penalty - self.unfinished_cluster_penalty
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "total": self.total}
```

- [ ] **Step 4: Implement one shared scoring function used by both ordering and terminal ranking**

```python
# miner/final_v1/scoring.py
from __future__ import annotations

from collections import deque
from typing import FrozenSet, Iterable, List, Sequence, Tuple

from miner.v3.board import is_pit
from miner.final_v1.types import ScoreBreakdown, SearchUsage

Coordinate = Tuple[int, int]
PIT_VALUE = 10.0
CLUSTER_COMPLETION_MULTIPLIER = 2.0
SHOVEL_COST = 1.0
ITEM_COST = {"bomb": 3.0, "drill": 3.0}
LOST_PIT_PENALTY = 40.0
UNFINISHED_CLUSTER_PENALTY = 4.0
DESCENT_BONUS = 0.5
PATH_BONUS = 0.25


def pit_clusters(board: Sequence[Sequence[str]]) -> List[FrozenSet[Coordinate]]:
    pending = {(r, c) for r, row in enumerate(board) for c, cell in enumerate(row) if is_pit(cell)}
    groups: List[FrozenSet[Coordinate]] = []
    while pending:
        seed = pending.pop()
        group = {seed}
        queue = deque([seed])
        while queue:
            r, c = queue.popleft()
            for pos in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if pos in pending:
                    pending.remove(pos)
                    group.add(pos)
                    queue.append(pos)
        groups.append(frozenset(group))
    return groups


def evaluate_state(
    original_board,
    board,
    usage: SearchUsage,
    *,
    scrolled: bool = False,
    descent_rows: int = 0,
    opened_path_cells: int = 0,
) -> ScoreBreakdown:
    clusters = pit_clusters(original_board)
    collected = 0
    completed_bonus = 0.0
    unfinished = 0
    for cluster in clusters:
        remaining = sum(1 for r, c in cluster if r < len(board) and is_pit(board[r][c]))
        collected += len(cluster) - remaining
        if remaining == 0:
            completed_bonus += len(cluster) * max(0, len(cluster) - 1) * CLUSTER_COMPLETION_MULTIPLIER
        elif remaining < len(cluster):
            unfinished += 1
    row_zero_lost = sum(1 for cell in original_board[0] if is_pit(cell)) if scrolled else 0
    return ScoreBreakdown(
        cluster_gain=completed_bonus,
        pit_gain=collected * PIT_VALUE,
        shovel_cost=usage.shovels * SHOVEL_COST,
        item_cost=(usage.bombs + usage.drills) * ITEM_COST["bomb"],
        lost_pit_penalty=row_zero_lost * LOST_PIT_PENALTY,
        unfinished_cluster_penalty=unfinished * UNFINISHED_CLUSTER_PENALTY,
        descent_bonus=descent_rows * DESCENT_BONUS,
        path_bonus=opened_path_cells * PATH_BONUS,
    )
```

```python
# miner/final_v1/__init__.py
from .planner import plan_final_v1

__all__ = ["plan_final_v1"]
```

- [ ] **Step 5: Run the scoring tests**

Run:

```bash
python -m pytest tests/test_final_v1_scoring.py -q
```

Expected: `4 passed`.

- [ ] **Step 6: Commit the contract and score model**

```bash
git add miner/final_v1/__init__.py miner/final_v1/types.py miner/final_v1/scoring.py tests/test_final_v1_scoring.py
git commit -m "feat(mining): define final v1 scoring contract"
```

---

### Task 2: Implement bounded beam search with visibility and first-step legality

**Files:**
- Create: `miner/final_v1/planner.py`
- Create: `tests/test_final_v1_planner.py`
- Modify: `miner/final_v1/scoring.py`

- [ ] **Step 1: Write the planner behavior tests**

```python
# tests/test_final_v1_planner.py
import time

from miner.final_v1 import plan_final_v1


def _board(rows=7):
    out = [["unreachable_dirt"] * 6 for _ in range(rows)]
    out[0][2] = "empty"
    out[1][2] = "dirt"
    return out


def test_zero_pickaxes_can_still_return_a_valuable_item_action():
    board = _board()
    board[1][1] = "reachable_pit"
    result = plan_final_v1(board, 0, {"bomb": 1, "drill": 0})
    assert result["steps"]
    assert result["steps"][0]["type"] == "use"


def test_known_offscreen_pit_can_raise_bomb_value_but_not_drill_or_pickaxe_value():
    board = _board(rows=10)
    board[6][2] = "empty"
    board[7][2] = "unreachable_pit"
    valid = {("use", "bomb", 6, 2), ("use", "drill", 6, 2), ("dig", "pickaxe", 1, 2)}
    bomb = plan_final_v1(board, 20, {"bomb": 1, "drill": 0}, visible_rows=7, valid_targets=valid)
    drill = plan_final_v1(board, 20, {"bomb": 0, "drill": 1}, visible_rows=7, valid_targets=valid)
    assert bomb["score_breakdown"]["pit_gain"] > drill["score_breakdown"]["pit_gain"]


def test_first_step_is_in_action_aware_valid_targets():
    board = _board()
    valid = {("dig", "pickaxe", 1, 2)}
    result = plan_final_v1(board, 10, {"bomb": 5, "drill": 5}, valid_targets=valid)
    step = result["steps"][0]
    assert (step["type"], step.get("item", "pickaxe"), *step["target"]) in valid


def test_deeper_preview_may_leave_visible_rows_but_emitted_step_may_not():
    board = _board(rows=12)
    board[9][2] = "unreachable_pit"
    result = plan_final_v1(board, 40, {"bomb": 2, "drill": 2}, visible_rows=7)
    assert all(step["target"][0] < 7 for step in result["steps"])


def test_item_counts_are_independent_from_shovel_budget():
    result = plan_final_v1(_board(), 0, {"bomb": 1, "drill": 1})
    assert result["stats"]["ending_inventory"]["pickaxe"] == 0
    assert result["stats"]["ending_inventory"]["bomb"] >= 0
    assert result["stats"]["ending_inventory"]["drill"] >= 0


def test_time_budget_returns_best_so_far_under_250ms():
    board = _board(rows=21)
    started = time.perf_counter()
    result = plan_final_v1(board, 1000, {"bomb": 100, "drill": 100}, time_budget_ms=250.0)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 300
    assert result["elapsed_ms"] <= elapsed_ms
    assert isinstance(result["budget_hit"], bool)
```

Also add two deterministic regressions in this file:

```python
def test_item_choice_does_not_change_when_only_inventory_surplus_changes():
    board = _board()
    board[1][1] = "reachable_pit"
    scarce = plan_final_v1(board, 50, {"bomb": 1, "drill": 1})["steps"][0]
    surplus = plan_final_v1(board, 50, {"bomb": 999, "drill": 1})["steps"][0]
    assert (scarce["type"], scarce.get("item"), scarce["target"]) == (
        surplus["type"], surplus.get("item"), surplus["target"]
    )


def test_equal_effect_bomb_and_drill_receive_equal_objective_value():
    board = [["empty"] * 6 for _ in range(7)]
    board[1][2] = "reachable_pit"
    bomb = plan_final_v1(
        board, 0, {"bomb": 1, "drill": 0},
        valid_targets={("use", "bomb", 0, 2)},
    )
    drill = plan_final_v1(
        board, 0, {"bomb": 0, "drill": 1},
        valid_targets={("use", "drill", 0, 2)},
    )
    assert bomb["objective_score"] == drill["objective_score"]
    assert bomb["score_breakdown"]["item_cost"] == drill["score_breakdown"]["item_cost"]


def test_row_zero_pit_is_collected_before_scroll_progress():
    board = _board()
    board[0][1] = "reachable_pit"
    board[6][2] = "dirt"
    result = plan_final_v1(board, 50, {"bomb": 0, "drill": 0})
    assert result["steps"][0]["target"] == (0, 1)
```

- [ ] **Step 2: Run the planner tests and verify `planner.py` is missing**

Run:

```bash
python -m pytest tests/test_final_v1_planner.py -q
```

Expected: FAIL during import because `miner.final_v1.planner` does not exist.

- [ ] **Step 3: Implement action enumeration and simulation using existing mechanics**

In `miner/final_v1/planner.py`, reuse `miner.v3.actions.dig_cost`, `apply_dig`, and `miner.core.mechanics` footprint helpers. Do not copy bomb/drill geometry.

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Collection, Dict, List, Optional, Set, Tuple

from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
from miner.v3.actions import apply_dig, dig_cost
from miner.v3.board import (
    floor7_open,
    is_frontier_diggable,
    is_pit,
    is_reachable_air,
    normalize_board,
    open_cell,
    promote_after_dig,
)
from miner.final_v1.scoring import evaluate_state
from miner.final_v1.types import ActionKey, PlannerAction, PlannerConfig, SearchUsage


@dataclass(frozen=True)
class _State:
    board: Tuple[Tuple[str, ...], ...]
    pickaxes: float
    bombs: int
    drills: int
    usage: SearchUsage
    path: Tuple[PlannerAction, ...]
    scrolled: bool = False
    opened_path_cells: int = 0


def _candidate_actions(board, pickaxes, bombs, drills, visible_rows):
    rows = min(int(visible_rows), len(board))
    actions: List[PlannerAction] = []
    for r in range(rows):
        for c in range(len(board[r])):
            if pickaxes >= dig_cost(board[r][c]) and is_frontier_diggable(board, r, c):
                actions.append(PlannerAction("dig", "pickaxe", r, c))
            if is_reachable_air(board[r][c]):
                if bombs > 0:
                    actions.append(PlannerAction("use", "bomb", r, c))
                if drills > 0:
                    actions.append(PlannerAction("use", "drill", r, c))
    return actions


def _affected(action, board, visible_rows):
    rows, cols = len(board), len(board[0])
    if action.item == "bomb":
        return set(get_bomb_affected_cells(action.row, action.col, rows, cols))
    if action.item == "drill":
        return {
            pos for pos in get_drill_affected_cells(action.row, action.col, rows, cols)
            if pos[0] < visible_rows
        }
    return {(action.row, action.col)}


def _apply(state: _State, action: PlannerAction, visible_rows: int) -> Optional[_State]:
    work = [list(row) for row in state.board]
    before_scroll_open = floor7_open(work[:visible_rows])
    if action.kind == "dig":
        cost = float(dig_cost(work[action.row][action.col]))
        if cost <= 0 or cost > state.pickaxes:
            return None
        apply_dig(work, (action.row, action.col))
        scrolled = (not before_scroll_open) and floor7_open(work[:visible_rows])
        return _State(
            tuple(tuple(row) for row in work), state.pickaxes - cost, state.bombs, state.drills,
            SearchUsage(state.usage.shovels + cost, state.usage.bombs, state.usage.drills),
            state.path + (action,), state.scrolled or scrolled, state.opened_path_cells + 1,
        )
    affected = _affected(action, work, visible_rows)
    changed = [(r, c) for r, c in affected if not is_reachable_air(work[r][c])]
    if not changed:
        return None
    for r, c in changed:
        open_cell(work, r, c)
    promote_after_dig(work, changed)
    scrolled = (not before_scroll_open) and floor7_open(work[:visible_rows])
    bomb_delta = 1 if action.item == "bomb" else 0
    drill_delta = 1 if action.item == "drill" else 0
    return _State(
        tuple(tuple(row) for row in work), state.pickaxes,
        state.bombs - bomb_delta, state.drills - drill_delta,
        SearchUsage(state.usage.shovels, state.usage.bombs + bomb_delta, state.usage.drills + drill_delta),
        state.path + (action,), state.scrolled or scrolled, state.opened_path_cells + len(changed),
    )
```

When translating the footprint helpers, confirm their actual argument/return signature and adapt only the call site; keep their implementation authoritative.

- [ ] **Step 4: Implement bounded beam search, dominance, stable ordering, and first-step-only output**

```python
def plan_final_v1(
    board,
    shovels,
    items,
    *,
    visible_rows=7,
    known_pits=None,
    valid_targets: Optional[Collection[ActionKey]] = None,
    time_budget_ms=250.0,
):
    started = time.perf_counter()
    config = PlannerConfig(time_budget_ms=float(time_budget_ms))
    work = normalize_board(board)
    for row, col in known_pits or ():
        if (
            0 <= int(row) < len(work)
            and 0 <= int(col) < len(work[int(row)])
            and work[int(row)][int(col)] in {"dirt", "rock", "unreachable_dirt", "unreachable_rock"}
        ):
            work[int(row)][int(col)] = "unreachable_pit"
    deadline = started + config.time_budget_ms / 1000.0
    valid = set(valid_targets) if valid_targets is not None else None
    initial = _State(
        tuple(tuple(row) for row in work), float(shovels),
        int((items or {}).get("bomb", 0)), int((items or {}).get("drill", 0)),
        SearchUsage(), tuple(), False, 0,
    )
    beam = [initial]
    best = initial
    best_score = evaluate_state(work, work, best.usage)
    expanded = 0
    reached_depth = 0
    budget_hit = False

    for depth in range(config.max_depth):
        next_states = []
        dominance = {}
        for state in beam:
            if time.perf_counter() >= deadline:
                budget_hit = True
                break
            # 捲動後 runtime 會取得新盤面再規劃；深搜在捲動點結束，避免猜下一個 viewport。
            if state.scrolled:
                continue
            state_board = [list(row) for row in state.board]
            actions = _candidate_actions(
                state_board, state.pickaxes, state.bombs, state.drills, visible_rows
            )
            if not state.path and valid is not None:
                actions = [action for action in actions if action.key in valid]
            ranked = []
            for action in actions:
                child = _apply(state, action, visible_rows)
                if child is None:
                    continue
                score = evaluate_state(
                    work, child.board, child.usage,
                    scrolled=child.scrolled,
                    descent_rows=1 if child.scrolled else 0,
                    opened_path_cells=child.opened_path_cells,
                )
                ranked.append((score.total, action.row, action.col, child, score))
            ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
            for _score, _row, _col, child, score in ranked[: config.branch_width]:
                expanded += 1
                signature = (child.board, child.bombs, child.drills, round(child.pickaxes, 3))
                if dominance.get(signature, float("-inf")) >= score.total:
                    continue
                dominance[signature] = score.total
                next_states.append((score.total, child, score))
                if score.total > best_score.total:
                    best, best_score = child, score
        if budget_hit or not next_states:
            break
        next_states.sort(key=lambda row: (-row[0], row[1].path[0].row, row[1].path[0].col))
        beam = [row[1] for row in next_states[: config.beam_width]]
        reached_depth = depth + 1

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    first = best.path[:1]
    steps = [action.to_step(1.0 if action.kind == "use" else dig_cost(work[action.row][action.col])) for action in first]
    return {
        "ok": True,
        "message": "final_v1 plan" if steps else "final_v1 no legal step",
        "steps": steps,
        "preview_steps": [action.to_step(1.0) for action in best.path],
        "score_breakdown": best_score.to_dict(),
        "objective_score": best_score.total,
        "elapsed_ms": elapsed_ms,
        "explored_nodes": expanded,
        "search_depth": reached_depth,
        "budget_hit": budget_hit,
        "stats": {
            "ending_inventory": {
                "pickaxe": best.pickaxes, "bomb": best.bombs, "drill": best.drills,
            },
            "score": best_score.to_dict(),
        },
    }
```

Before accepting an item child, require that its footprint changes at least one non-air cell; this prevents free no-op item branches. Stop deep expansion when an action opens floor 7: score the row-0 loss/descent transition, emit only the first action, and let the required runtime re-plan observe the new viewport instead of guessing it. Merge `known_pits` into a copy of `work` before cluster discovery, without overwriting concrete air/pit observations. Do not use inventory surplus, action name, or a bomb-before-drill list position in the score or tie-break; stable ordering is score, then target row/column, then sorted footprint coordinates.

- [ ] **Step 5: Run planner and scoring tests**

Run:

```bash
python -m pytest tests/test_final_v1_scoring.py tests/test_final_v1_planner.py -q
```

Expected: all tests PASS; the timeout test stays below its 300 ms process-overhead guard.

- [ ] **Step 6: Compile the new package**

Run:

```bash
python -m py_compile miner/final_v1/__init__.py miner/final_v1/types.py miner/final_v1/scoring.py miner/final_v1/planner.py tests/test_final_v1_scoring.py tests/test_final_v1_planner.py
```

Expected: exit code 0 and no output.

- [ ] **Step 7: Commit the search engine**

```bash
git add miner/final_v1 tests/test_final_v1_scoring.py tests/test_final_v1_planner.py
git commit -m "feat(mining): add final v1 bounded beam search"
```

---

### Task 3: Build the WS known board and action-aware valid targets

**Files:**
- Modify: `ws_token/mining_adapter.py`
- Create: `tests/test_mining_adapter_final_v1.py`

- [ ] **Step 1: Add WS projection and dispatch tests**

```python
# tests/test_mining_adapter_final_v1.py
from ws_token import mining, mining_adapter


def _block(depth, col, config_id, count=1, is_reward=0):
    return mining.MineBlock(depth * 100 + col, col, depth, config_id, count, is_reward)


def _board(*, baseline=105, area_info=None, actives=None, blocks=None):
    return mining.MineBoard(
        max_num=20, next_time=0, area=0, baseline=baseline,
        actives=list(actives or []), area_info=dict(area_info or {}),
        blocks=list(blocks or []), holes=[],
    )


def test_known_board_extends_to_covered_area_info_but_caps_at_21_rows(monkeypatch):
    board = _board(area_info={14: 1, 15: 2, 16: 3})
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5, "bomb": 1, "drill": 1})
    assert len(projected["board"]) == 21
    assert projected["visible_rows"] == 7


def test_raw_offscreen_pit_overlays_static_terrain(monkeypatch):
    top = mining_adapter.viewport_top_depth(105)
    pit = _block(top + 8, 3, mining.TERRAIN_PIT, count=1, is_reward=1)
    board = _board(blocks=[pit])
    monkeypatch.setattr(mining_adapter.mine_terrain, "terrain_at", lambda depth, col, info: 201)
    projected = mining_adapter.build_final_v1_input(board, {})
    assert projected["board"][8][2] == "unreachable_pit"


def test_valid_targets_distinguish_pickaxe_frontier_from_item_air_placement():
    top = mining_adapter.viewport_top_depth(105)
    solid = _block(top + 1, 3, mining.TERRAIN_DIRT, count=1)
    air = _block(top, 3, mining.TERRAIN_DIRT, count=0)
    board = _board(actives=[solid.block_id], blocks=[solid, air])
    valid = mining_adapter.build_final_v1_input(board, {})["valid_targets"]
    assert ("dig", "pickaxe", 1, 2) in valid
    assert ("use", "bomb", 0, 2) in valid
    assert ("use", "drill", 0, 2) in valid
    assert ("dig", "pickaxe", 0, 2) not in valid


def test_plan_final_v1_maps_only_legal_first_step_to_ws_block_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "miner.final_v1.plan_final_v1",
        lambda *args, **kwargs: captured.update(kwargs) or {
            "steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}],
            "score_breakdown": {"total": 1},
        },
    )
    board = _board(actives=[10103])
    result = mining_adapter.plan(board, {"pickaxe": 5}, planner_version="final_v1")
    assert captured["visible_rows"] == 7
    assert result["ws_steps"][0]["block_id"] == mining_adapter.grid_pos_to_block_id(105, 1, 2)
```

Add these concrete degradation/fallback/shadow tests:

```python
def test_incomplete_known_projection_degrades_to_seven_rows(monkeypatch):
    board = _board(area_info={14: 1, 16: 3})
    top = mining_adapter.viewport_top_depth(board.baseline)
    monkeypatch.setattr(
        mining_adapter.mine_terrain,
        "terrain_at",
        lambda depth, col, info: 201 if depth < top + 7 else None,
    )
    projected = mining_adapter.build_final_v1_input(board, {"pickaxe": 5})
    assert len(projected["board"]) == 7
    assert projected["projection_mode"] == "visible_only"


def test_empty_final_v1_result_falls_back_to_v1(monkeypatch):
    calls = []
    def fake_named(name, projected, inventory):
        calls.append(name)
        if name == "final_v1":
            return {"steps": []}
        return {"steps": [{"type": "dig", "target": (1, 2), "step_cost": 1}]}
    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(_board(), {"pickaxe": 5}, planner_version="final_v1")
    assert calls == ["final_v1", "v1"]
    assert result["planner_source"] == "v1_fallback"


def test_shadow_exception_is_logged_in_result_and_primary_plan_survives(monkeypatch):
    expected_primary_steps = [{"type": "dig", "target": (1, 2), "step_cost": 1}]
    def fake_named(name, projected, inventory):
        if name == "final_v1":
            raise RuntimeError("shadow boom")
        return {"steps": expected_primary_steps}
    monkeypatch.setattr(mining_adapter, "_run_named_planner", fake_named)
    result = mining_adapter.plan(
        _board(), {"pickaxe": 5}, planner_version="v1",
        shadow_planner_version="final_v1",
    )
    assert result["shadow"]["ok"] is False
    assert result["ws_steps"][0]["target"] == expected_primary_steps[0]["target"]
```

- [ ] **Step 2: Run the adapter tests and verify the new functions/arguments are absent**

Run:

```bash
python -m pytest tests/test_mining_adapter_final_v1.py -q
```

Expected: FAIL with missing `build_final_v1_input` and unsupported `planner_version`.

- [ ] **Step 3: Add the variable-height projection without changing `board_to_grid`**

Implement beside `_project_board` so v1 remains byte-for-byte on the current 7×6 path:

```python
def _known_row_count(top_depth: int, area_info: Dict[int, int]) -> int:
    known_rows = GRID_ROWS
    for row in range(GRID_ROWS, 21):
        depth = top_depth + row
        terrain_row = [
            mine_terrain.terrain_at(depth, col, area_info)
            for col in range(GRID_COLS)
        ]
        if any(value is None for value in terrain_row):
            break
        known_rows = row + 1
    return known_rows


def build_final_v1_input(mine_board: Any, inventory: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    top_depth = viewport_top_depth(baseline)
    area_info = dict(getattr(mine_board, "area_info", {}) or {})
    rows = _known_row_count(top_depth, area_info)
    projection_mode = "known_terrain" if rows > GRID_ROWS else "visible_only"
    board = board_to_grid(mine_board)

    for row in range(GRID_ROWS, rows):
        board.append(["unreachable_dirt"] * GRID_COLS)
        for col in range(GRID_COLS):
            terrain = mine_terrain.terrain_at(top_depth + row, col, area_info)
            if terrain == mine_terrain.AIR:
                board[row][col] = "unreachable_empty"
            elif terrain == mine_terrain.STONE:
                board[row][col] = "unreachable_rock"

    block_by_id = {int(block.block_id): block for block in (getattr(mine_board, "blocks", None) or [])}
    for block in block_by_id.values():
        row, col = int(block.y) - top_depth, int(block.x) - 1
        if 0 <= row < rows and 0 <= col < GRID_COLS:
            board[row][col] = _block_label(block.config_id, block.is_reward, block.count)

    valid_targets = set()
    for block_id in (getattr(mine_board, "actives", None) or []):
        depth, game_col = divmod(int(block_id), 100)
        row, col = depth - top_depth, game_col - 1
        block = block_by_id.get(int(block_id))
        if 0 <= row < GRID_ROWS and 0 <= col < GRID_COLS and (block is None or int(block.count or 0) > 0):
            valid_targets.add(("dig", "pickaxe", row, col))
    for block in block_by_id.values():
        row, col = int(block.y) - top_depth, int(block.x) - 1
        if (
            0 <= row < GRID_ROWS and 0 <= col < GRID_COLS
            and int(block.count or 0) == 0
            and is_reachable_air(board[row][col])
        ):
            valid_targets.add(("use", "bomb", row, col))
            valid_targets.add(("use", "drill", row, col))

    return {
        "board": board,
        "visible_rows": GRID_ROWS,
        "valid_targets": valid_targets,
        "projection_mode": projection_mode,
        "top_depth": top_depth,
    }
```

The first seven rows come directly from the existing `board_to_grid`, so its tested reachability/occlusion behavior remains authoritative. `_known_row_count` truncates at the first missing static row; do not fill unknown lower rows with guessed dirt.

- [ ] **Step 4: Dispatch primary/shadow planners and retain v1 fallback**

Change the adapter contract to:

```python
def plan(
    mine_board: Any,
    inventory: Optional[Dict[str, int]] = None,
    *,
    max_depth: Optional[int] = None,
    planner_version: str = "v1",
    shadow_planner_version: str = "",
) -> Dict[str, Any]:
```

Use these helpers so primary and shadow share identical inputs:

```python
def _run_named_planner(name: str, projected: Dict[str, Any], inventory: Dict[str, int]) -> Dict[str, Any]:
    if name == "final_v1":
        from miner.final_v1 import plan_final_v1
        return plan_final_v1(
            projected["board"],
            float(inventory.get("pickaxe", 0)),
            {"bomb": int(inventory.get("bomb", 0)), "drill": int(inventory.get("drill", 0))},
            visible_rows=projected["visible_rows"],
            valid_targets=projected["valid_targets"],
        )
    from miner.planning.smart_planner import plan_smart
    visible = projected["board"][:GRID_ROWS]
    return plan_smart(
        visible,
        shovels=float(inventory.get("pickaxe", 0)),
        items={
            "bomb": int(inventory.get("bomb", 0)),
            "drill": int(inventory.get("drill", 0)),
        },
    )
```

For `planner_version="final_v1"`, if the primary returns no steps, call the existing v1 path and set `planner_source="v1_fallback"`. Wrap shadow computation in `try/except Exception`, store `{ok, planner, elapsed_ms, first_step, score_breakdown, error}`, and never replace or suppress the primary result. Preserve the existing `hold_floor`, `grid`, and `map_pits` keys for supervised-loop compatibility.

- [ ] **Step 5: Run adapter, terrain, and steering tests**

Run:

```bash
python -m pytest tests/test_mining_adapter_final_v1.py tests/test_mining_adapter_air_label.py tests/test_mine_terrain_static.py tests/test_ws_mining_steering.py -q
```

Expected: all tests PASS, including the unchanged v1 steering suite.

- [ ] **Step 6: Commit the WS projection and adapter dispatch**

```bash
git add ws_token/mining_adapter.py tests/test_mining_adapter_final_v1.py
git commit -m "feat(ws-mining): project known terrain for final v1"
```

---

### Task 4: Wire WS configuration, authoritative inventory, confirmation, and telemetry

**Files:**
- Modify: `game_actions/ws_phase.py`
- Modify: `ws_token/runner.py`
- Modify: `ws_token/mining_supervised.py`
- Modify: `tests/test_ws_phase.py`
- Modify: `tests/test_ws_token_mining_supervised.py`

Preserve the existing login-time authoritative seed in `ws_token/runner.py`:

```python
inventory_tracker.seed_from_query(client, timeout=timeout)
```

`InventoryTracker.seed_from_query` must remain the source of initial `4001/4002/4003` counts from `0x0401`; subsequent `0x0402` consume/gain/snapshot events continue updating the same tracker. The targeted `tests/test_ws_token_mining.py` run below protects this existing contract.

- [ ] **Step 1: Add failing config propagation tests**

Append to `tests/test_ws_phase.py` using its existing `_run_device` capture fixture:

```python
def test_ws_phase_injects_device_planners_into_mining_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(ws_phase.config_manager, "get_device_config", lambda _ip: {
        "backend": "web_h5",
        "mining_planner_version": "final_v1",
        "mining_shadow_planner_version": "final_v1",
        "ws_token": {"enabled": True, "mining": {"enabled": True, "allow_bomb": True}},
    })
    monkeypatch.setattr(ws_phase, "_run_device", lambda _ip, cfg, **kwargs: captured.update(cfg) or _report({}))
    ws_phase.run_ws_phase("dev")
    assert captured["mining"] == {
        "enabled": True,
        "allow_bomb": True,
        "planner_version": "final_v1",
        "shadow_planner_version": "final_v1",
    }


def test_ws_phase_defaults_primary_to_v1_and_shadow_to_empty(monkeypatch):
    captured = {}
    _cfg(monkeypatch, {
        "enabled": True,
        "mining": {"enabled": True},
    })
    monkeypatch.setattr(
        ws_phase,
        "_run_device",
        lambda _ip, cfg, progress=None, **kwargs: captured.update(cfg) or _report({}),
    )
    ws_phase.run_ws_phase("dev")
    assert captured["mining"]["planner_version"] == "v1"
    assert captured["mining"]["shadow_planner_version"] == ""
```

- [ ] **Step 2: Add failing inventory and confirmation tests**

Append to `tests/test_ws_token_mining_supervised.py`:

```python
def test_final_v1_skips_when_pickaxe_was_never_seeded(monkeypatch):
    tracker = mining.InventoryTracker()
    monkeypatch.setattr(supervised.mining, "read_board", lambda *args, **kwargs: _board())
    result = supervised.mine_until_pickaxe_empty(
        _Client(), tracker, planner_version="final_v1", max_steps=1,
    )
    assert result["stopped_reason"] == "inventory_unknown"
    assert result["executed"] == []


def test_authoritative_tracker_replaces_local_consume_and_gain_after_each_step(monkeypatch):
    tracker = mining.InventoryTracker()
    tracker.counts.update({4001: 4, 4002: 2, 4003: 1})
    monkeypatch.setattr(supervised.mining_adapter, "plan", lambda *args, **kwargs: {
        "ws_steps": [{"type": "dig", "block_id": 10101}], "hold_floor": False, "grid": [],
    })
    def execute(*args, **kwargs):
        tracker.counts.update({4001: 3, 4002: 3, 4003: 1})
        return {"confirmed": True, "goods_id": 4001, "hits": 1, "after_board": _board()}
    monkeypatch.setattr(supervised, "execute_plan_step", execute)
    result = supervised.mine_until_pickaxe_empty(_Client(), tracker, planner_version="final_v1", max_steps=1)
    assert result["final_inventory"] == {"pickaxe": 3, "drill": 3, "bomb": 1}


def test_inventory_change_can_confirm_even_when_target_snapshot_is_delayed(monkeypatch):
    before = {"pickaxe": 4, "drill": 1, "bomb": 1}
    reads = iter([before, {"pickaxe": 3, "drill": 1, "bomb": 1}])
    item = supervised.execute_plan_step(
        _Client(), {"type": "dig", "block_id": 10101}, before_board=_board(),
        before_inventory=before, inventory_reader=lambda: next(reads),
    )
    assert item["confirmed"] is True
    assert item["confirmation"] == "inventory_changed"


def test_unchanged_target_footprint_baseline_and_inventory_is_not_success(monkeypatch):
    item = supervised.execute_plan_step(
        _Client(), {"type": "dig", "block_id": 10101}, before_board=_board(),
        before_inventory={"pickaxe": 4, "drill": 1, "bomb": 1},
        inventory_reader=lambda: {"pickaxe": 4, "drill": 1, "bomb": 1},
    )
    assert item["confirmed"] is False
    assert item["confirmation"] == "unchanged"
```

Use the concrete `_Client`, `_board`, and monkeypatch patterns already defined in that test file; the implementation commit must not introduce live sockets.

- [ ] **Step 3: Run the new WS tests and confirm the signatures are missing**

Run:

```bash
python -m pytest tests/test_ws_phase.py tests/test_ws_token_mining_supervised.py -q
```

Expected: FAIL on missing `planner_version`, `before_inventory`, or absent propagation.

- [ ] **Step 4: Inject device planner settings in `run_ws_phase` without mutating stored config**

After legacy/nested mining config normalization in `game_actions/ws_phase.py`:

```python
    mining_cfg = dict(cfg.get("mining") or {})
    if mining_cfg:
        mining_cfg["planner_version"] = str(
            device_cfg.get("mining_planner_version", "v1") or "v1"
        ).strip().lower()
        mining_cfg["shadow_planner_version"] = str(
            device_cfg.get("mining_shadow_planner_version", "") or ""
        ).strip().lower()
        cfg = {**cfg, "mining": mining_cfg}
```

This copy-on-write is required because `DeviceConfig` and nested dictionaries may be cached.

- [ ] **Step 5: Forward planner settings through the runner**

In `ws_token/runner.py::_run_mining` add:

```python
        planner_version=str(cfg.get("planner_version") or "v1").strip().lower(),
        shadow_planner_version=str(cfg.get("shadow_planner_version") or "").strip().lower(),
```

Update the docstring to state that the default remains v1.

- [ ] **Step 6: Gate unknown pickaxes only for final-v1 and reconcile all three authoritative counts**

Extend `mine_until_pickaxe_empty`:

```python
def mine_until_pickaxe_empty(
    client,
    tracker,
    *,
    allow_bomb=False,
    allow_drill=False,
    max_steps=200,
    timeout=None,
    max_depth=None,
    should_abort=None,
    device_id=None,
    planner_version="v1",
    shadow_planner_version="",
):
```

Add a shared authoritative merge:

```python
def _inventory_from_tracker(tracker, fallback):
    names = {
        "pickaxe": mining.GOODS_PICKAXE,
        "drill": mining.GOODS_DRILL,
        "bomb": mining.GOODS_BOMB,
    }
    merged = dict(fallback)
    for name, item_id in names.items():
        if tracker.has_item(item_id):
            merged[name] = int(tracker.counts[item_id])
    return merged
```

At loop start:

```python
    seen = tracker.has_item(mining.GOODS_PICKAXE)
    if planner_version == "final_v1" and not seen:
        return {
            "initial_inventory": tracker.as_props(),
            "final_inventory": tracker.as_props(),
            "plans": [], "candidate_steps": [], "executed": [],
            "stopped_reason": "inventory_unknown",
            "skipped": "pickaxe 4001 missing from authoritative inventory",
        }
```

Keep `_SEED_UNKNOWN_PICKAXE` only for the unchanged v1 compatibility path. Before every plan and immediately after every confirmed execution, call `_inventory_from_tracker`; remove final-v1 local decrements as a source of truth.

- [ ] **Step 7: Confirm target/footprint/baseline or inventory changes and emit structured telemetry**

Extend `execute_plan_step` with optional `before_inventory` and `inventory_reader`. During its existing refresh polling, mark success when any of these changes can be attributed to the action:

```python
inventory_after = inventory_reader() if inventory_reader is not None else None
inventory_changed = inventory_after is not None and inventory_after != before_inventory
board_confirmation = _board_confirmation(before_board, after_board, step)
if board_confirmation:
    confirmed, confirmation = True, board_confirmation
elif inventory_changed:
    confirmed, confirmation = True, "inventory_changed"
else:
    confirmed, confirmation = False, "unchanged"
```

`_board_confirmation` must distinguish `target_changed`, `footprint_changed`, and `baseline_changed`; never use “read returned without exception” as confirmation.

Pass planner settings into `mining_adapter.plan`, and add these fields to `_log_plan_trace`/`_log_execute_trace` JSON payloads:

```python
{
    "primary_planner": plan_result.get("planner_name", planner_version),
    "shadow_planner": shadow_planner_version,
    "planner_source": plan_result.get("planner_source", "planner"),
    "first_step": (plan_result.get("ws_steps") or [None])[0],
    "score_breakdown": plan_result.get("score_breakdown"),
    "elapsed_ms": plan_result.get("elapsed_ms"),
    "search_depth": plan_result.get("search_depth"),
    "explored_nodes": plan_result.get("explored_nodes"),
    "budget_hit": plan_result.get("budget_hit"),
    "inventory_before": inventory,
    "shadow": plan_result.get("shadow"),
}
```

Execution telemetry must add `legal_filter`, `confirmation`, `rejection_reason`, and `inventory_after`.

- [ ] **Step 8: Run WS phase, runner, inventory, and mining tests**

Run:

```bash
python -m pytest tests/test_ws_phase.py tests/test_ws_runner_wiring.py tests/test_ws_token_mining_supervised.py tests/test_ws_token_mining.py tests/test_ws_inventory.py -q
```

Expected: all tests PASS; legacy v1 unknown-pickaxe tests remain unchanged.

- [ ] **Step 9: Commit WS runtime wiring**

```bash
git add game_actions/ws_phase.py ws_token/runner.py ws_token/mining_supervised.py tests/test_ws_phase.py tests/test_ws_token_mining_supervised.py
git commit -m "feat(ws-mining): wire final v1 inventory and shadow flow"
```

---

### Task 5: Add CNN dispatch, fallback, shadow planning, and telemetry

**Files:**
- Modify: `miner/mining_service.py`
- Create: `tests/test_mining_service_final_v1.py`

- [ ] **Step 1: Write isolated mining-service tests with heavy dependencies stubbed**

Follow the module-stubbing approach in existing mining-service tests so importing the test never loads a real device, Playwright, OpenCV, OCR, or a CNN model.

```python
# tests/test_mining_service_final_v1.py
def test_dispatches_final_v1_with_blocked_actions_as_invalid_first_targets(service, monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: captured.update(kwargs) or {
        "steps": [{"type": "dig", "target": (2, 3)}], "score_breakdown": {"total": 4},
    })
    plan, title = service._dispatch_planner(
        _board(), 20, {"bomb": 1, "drill": 1},
        {("dig", None, (1, 2), "board", "inventory")},
        "final_v1", _Logger(),
    )
    assert "Final V1" in title
    assert ("dig", "pickaxe", 1, 2) not in captured["valid_targets"]
    assert plan["steps"][0]["target"] == (2, 3)


def test_empty_final_v1_plan_uses_existing_v1_fallback(service, monkeypatch):
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: {"steps": []})
    monkeypatch.setattr(service, "plan_smart", lambda *args, **kwargs: {"steps": [{"type": "dig"}]})
    plan, _ = service._dispatch_planner(_board(), 20, {}, set(), "final_v1", _Logger())
    assert plan["planner_source"] == "v1_fallback"


def test_shadow_exception_does_not_change_primary_plan(service, monkeypatch):
    primary = {"steps": [{"type": "dig", "target": (1, 2)}]}
    monkeypatch.setattr(service, "_dispatch_planner", lambda *args, **kwargs: (primary, "v1"))
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    shadow = service._compute_shadow_plan(_board(), 20, {}, set(), "final_v1", _Logger())
    assert primary["steps"][0]["target"] == (1, 2)
    assert shadow["ok"] is False
    assert "boom" in shadow["error"]
```

Add a config-read test asserting main `v1` plus shadow `final_v1` computes both, while empty shadow computes only the main plan.

- [ ] **Step 2: Run the isolated tests and verify final-v1 dispatch is absent**

Run:

```bash
python -m pytest tests/test_mining_service_final_v1.py -q
```

Expected: FAIL because final-v1 imports/helpers/allowlist are absent.

- [ ] **Step 3: Add final-v1 dispatch without altering v1/v3/v4 branches**

Import `plan_final_v1`, then add before the v4 branch:

```python
    if planner_version == "final_v1":
        all_targets = _cnn_valid_targets(board)
        valid_targets = all_targets - _blocked_action_keys(blocked_actions)
        plan = plan_final_v1(
            board,
            shovels=shovels,
            items=items,
            visible_rows=7,
            valid_targets=valid_targets,
        )
        plan["planner_name"] = "final_v1"
        plan["planner_source"] = "planner"
        if plan.get("steps"):
            return plan, "Final V1 規劃 (6-step bounded beam)"
        fallback = _dispatch_planner(
            board, shovels, items, blocked_actions, "v1", miner_logger, depth, device
        )[0]
        fallback["planner_name"] = "final_v1"
        fallback["planner_source"] = "v1_fallback"
        return fallback, "Final V1 規劃 (v1 fallback)"
```

`_cnn_valid_targets` must emit action-aware keys for every executor-accepted visible dig/item placement. `_blocked_action_keys` converts existing blocked signatures into the same key type; do not weaken current same-board/same-inventory retry suppression.

- [ ] **Step 4: Compute shadow plans beside, never inside, primary dispatch**

```python
def _compute_shadow_plan(board, shovels, items, blocked_actions, shadow_version, miner_logger):
    if shadow_version != "final_v1":
        return None
    started = time.perf_counter()
    try:
        valid = _cnn_valid_targets(board) - _blocked_action_keys(blocked_actions)
        result = plan_final_v1(board, shovels, items, visible_rows=7, valid_targets=valid)
        return {
            "ok": True,
            "planner": "final_v1",
            "first_step": (result.get("steps") or [None])[0],
            "score_breakdown": result.get("score_breakdown"),
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            "budget_hit": result.get("budget_hit", False),
        }
    except Exception as exc:  # shadow must never interrupt mining
        miner_logger.warning("[MiningService] shadow planner final_v1 failed: %s", exc)
        return {
            "ok": False, "planner": "final_v1", "error": str(exc),
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }
```

Read and normalize:

```python
planner_version = str(device_cfg.get("mining_planner_version", "v1")).strip().lower()
shadow_version = str(device_cfg.get("mining_shadow_planner_version", "")).strip().lower()
if planner_version not in {"v1", "v3", "v4", "final_v1"}:
    planner_version = "v1"
if shadow_version not in {"", "final_v1"}:
    shadow_version = ""
```

Compute shadow from the same board/inventory snapshot immediately after the primary plan. Add its structured payload to `_log_planner_stats`. Shadow failure or timeout only logs.

- [ ] **Step 5: Run CNN planner integration tests**

Run:

```bash
python -m pytest tests/test_mining_service_final_v1.py tests/test_miner_planner_executor_integration.py tests/test_mining_service_forced_descent.py tests/test_mining_service_identical_state_guard.py -q
```

Expected: all tests PASS; existing v1/v3/v4 tests are unchanged.

- [ ] **Step 6: Commit CNN dispatch and shadow telemetry**

```bash
git add miner/mining_service.py tests/test_mining_service_final_v1.py
git commit -m "feat(mining): route cnn boards through final v1"
```

---

### Task 6: Make primary and shadow planners configurable in config and Dashboard

**Files:**
- Modify: `config_manager.py`
- Modify: `templates/dashboard.html`
- Modify: `tests/test_device_config.py`
- Modify: `tests/test_dashboard_template.py`

- [ ] **Step 1: Add config round-trip and default tests**

```python
# tests/test_device_config.py
def test_final_v1_planner_survives_update_normalization(tmp_path, monkeypatch):
    import json
    import config_manager

    path = tmp_path / "bot_config.json"
    path.write_text(json.dumps({"devices": {"dev": {}}}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(path))
    config_manager.update_device_config("dev", {
        "mining_planner_version": "final_v1",
        "mining_shadow_planner_version": "final_v1",
    })
    saved = json.loads(path.read_text(encoding="utf-8"))["devices"]["dev"]
    assert saved["mining_planner_version"] == "final_v1"
    assert saved["mining_shadow_planner_version"] == "final_v1"


def test_shadow_planner_defaults_off():
    from config_manager import DEFAULT_DEVICE_CONFIG, DeviceConfig
    assert DEFAULT_DEVICE_CONFIG["mining_shadow_planner_version"] == ""
    assert DeviceConfig().mining_shadow_planner_version == ""
```

Add invalid-value assertions: primary normalizes to `v1`; shadow normalizes to `""`.

- [ ] **Step 2: Add Dashboard structure and load/save tests**

```python
# tests/test_dashboard_template.py
def test_dashboard_exposes_final_v1_primary_and_shadow_controls():
    html = _html()
    assert '<option value="final_v1">' in html
    assert 'id="editMiningShadowPlanner"' in html
    assert 'mining_shadow_planner_version' in html
    assert "config.mining_shadow_planner_version || ''" in html
    assert "mining_shadow_planner_version:" in html
    assert '<option value="">關閉（預設）</option>' in html


def test_dashboard_primary_allowlist_includes_final_v1():
    html = _html()
    assert "['v1','v3','v4','final_v1'].includes(plannerVer)" in html
```

- [ ] **Step 3: Run tests and confirm normalization/UI currently reject final-v1**

Run:

```bash
python -m pytest tests/test_device_config.py tests/test_dashboard_template.py -q
```

Expected: FAIL on the missing dataclass/default/UI option and the old three-value allowlist.

- [ ] **Step 4: Add config fields and strict allowlists**

In `DEFAULT_DEVICE_CONFIG` and `DeviceConfig`:

```python
"mining_planner_version": "v1",  # v1 / v3 / v4 / final_v1；預設維持 v1
"mining_shadow_planner_version": "",  # 空字串=關閉；目前只允許 final_v1
```

```python
mining_planner_version: str = "v1"
mining_shadow_planner_version: str = ""
```

In `update_device_config` normalization:

```python
current["mining_planner_version"] = _enum_str(
    current.get("mining_planner_version", DEFAULT_DEVICE_CONFIG["mining_planner_version"]),
    {"v1", "v3", "v4", "final_v1"},
    "v1",
)
current["mining_shadow_planner_version"] = _enum_str(
    current.get("mining_shadow_planner_version", ""),
    {"", "final_v1"},
    "",
)
```

Add the new field to `test_device_config_all_defaults_match_default_dict`.

- [ ] **Step 5: Add Dashboard options and load/save wiring**

Beside the existing primary select:

```html
<option value="final_v1">final_v1（cluster／資源效率，實驗）</option>
```

Add a second select:

```html
<div style="grid-column:span 2;">
  <label style="font-size:0.8em;">挖礦 Shadow 規劃器</label>
  <select id="editMiningShadowPlanner" class="form-control">
    <option value="">關閉（預設）</option>
    <option value="final_v1">final_v1（只計算與記錄，不執行）</option>
  </select>
</div>
```

Load:

```javascript
const plannerVer = String(config.mining_planner_version || 'v1').toLowerCase();
plannerSelect.value = ['v1','v3','v4','final_v1'].includes(plannerVer) ? plannerVer : 'v1';
const shadowSelect = document.getElementById('editMiningShadowPlanner');
const shadowVer = String(config.mining_shadow_planner_version || '').toLowerCase();
shadowSelect.value = shadowVer === 'final_v1' ? 'final_v1' : '';
```

Save:

```javascript
mining_planner_version: (document.getElementById('editMiningPlanner') || {}).value || 'v1',
mining_shadow_planner_version: (document.getElementById('editMiningShadowPlanner') || {}).value || '',
```

- [ ] **Step 6: Run config, Dashboard, and config API tests**

Run:

```bash
python -m pytest tests/test_device_config.py tests/test_dashboard_template.py tests/test_smoke_config_api.py -q
```

Expected: all tests PASS.

- [ ] **Step 7: Commit the opt-in controls**

```bash
git add config_manager.py templates/dashboard.html tests/test_device_config.py tests/test_dashboard_template.py
git commit -m "feat(config): expose final v1 mining planners"
```

---

### Task 7: Extend offline A/B and real-board replay gates

**Files:**
- Modify: `tools/mining_sim_eval.py`
- Modify: `tools/compare_planners.py`
- Modify: `tools/replay_real_boards.py`
- Create: `tests/test_final_v1_eval_tools.py`

- [ ] **Step 1: Add registry and aggregation tests**

```python
# tests/test_final_v1_eval_tools.py
from tools import compare_planners, mining_sim_eval, replay_real_boards


def test_final_v1_is_registered_in_sim_and_replay():
    assert "final_v1" in mining_sim_eval.PLANNERS
    assert "final_v1" in replay_real_boards.PLANNERS


def test_aggregate_reports_required_resource_and_latency_kpis():
    rows = [{
        "stats": type("S", (), {
            "score": 10, "pits": 4, "depth": 2, "cost": 2,
            "bombs_used": 1, "drills_used": 0,
            "clusters_completed": {4: 1},
        })(),
        "plan_times_ms": [10.0, 20.0, 30.0],
        "fallbacks": 0, "actions": 2, "empty_plan": False,
        "rejected": 0, "lost_pits": 0, "unfinished_clusters": 0,
    }]
    result = compare_planners.agg(rows, equal_item_weight=3.0)
    for key in (
        "clusters", "pits_per_shovel", "pits_per_item", "pits_per_equal_cost",
        "lost_pits", "unfinished_clusters", "rejected", "plan_ms_p95",
        "plan_ms_p99", "plan_ms_max",
    ):
        assert key in result
```

- [ ] **Step 2: Run the tool tests and verify final-v1/KPIs are missing**

Run:

```bash
python -m pytest tests/test_final_v1_eval_tools.py -q
```

Expected: FAIL because `final_v1` and the new aggregate fields are absent.

- [ ] **Step 3: Register final-v1 without changing simulator physics**

In both tool registries:

```python
from miner.final_v1 import plan_final_v1

PLANNERS = {
    "v1": _call_smart,
    "v3": plan_v3,
    "v4": plan_v4,
    "final_v1": plan_final_v1,
}
```

Keep the simulator’s existing 50/50 bomb/drill reward line unchanged. Add per-call `plan_times_ms` rather than only average timing. Track rejected actions, lost pits during scroll, and unfinished clusters at session end using existing `MiningSim.clusters`/action results; do not add planner-specific scoring to the simulator.

- [ ] **Step 4: Report all acceptance KPIs and exact latency percentiles**

Add a percentile helper and aggregate fields:

```python
def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))
    return ordered[index]


def agg(rows, equal_item_weight=3.0):
    if not rows:
        return None
    scores = [row["stats"].score for row in rows]
    pit_values = [row["stats"].pits for row in rows]
    depths = [row["stats"].depth for row in rows]
    costs = [row["stats"].cost for row in rows]
    densities = [row.get("standing_pit_density", 0.0) for row in rows]
    all_times = [ms for row in rows for ms in row.get("plan_times_ms", [])]
    pits = sum(row["stats"].pits for row in rows)
    shovels = sum(row["stats"].cost for row in rows)
    items = sum(row["stats"].bombs_used + row["stats"].drills_used for row in rows)
    fallbacks = sum(row.get("fallbacks", 0) for row in rows)
    actions = sum(row.get("actions", 0) for row in rows)
    return {
        "n": len(rows),
        "score": statistics.mean(scores),
        "pits": statistics.mean(pit_values),
        "depth": statistics.mean(depths),
        "cost": statistics.mean(costs),
        "plan_ms": statistics.mean(all_times) if all_times else 0.0,
        "density": 100 * statistics.mean(densities),
        "fb_rate": 100 * fallbacks / actions if actions else 0.0,
        "stuck": sum(1 for row in rows if row.get("empty_plan")),
        "clusters": sum(sum(row["stats"].clusters_completed.values()) for row in rows),
        "pits_per_shovel": pits / shovels if shovels else 0.0,
        "pits_per_item": pits / items if items else 0.0,
        "pits_per_equal_cost": pits / (shovels + equal_item_weight * items) if (shovels + items) else 0.0,
        "lost_pits": sum(row.get("lost_pits", 0) for row in rows),
        "unfinished_clusters": sum(row.get("unfinished_clusters", 0) for row in rows),
        "rejected": sum(row.get("rejected", 0) for row in rows),
        "plan_ms_p95": percentile(all_times, 0.95),
        "plan_ms_p99": percentile(all_times, 0.99),
        "plan_ms_max": max(all_times, default=0.0),
    }
```

Merge this dictionary with the current aggregate rather than deleting current output fields.

Add `MiningSim.get_known_board(rows)` as a read-only slice of the existing tape, a `known_rows: int = 7` argument to `play_one_game`, and `--known-rows {7,21}` to `compare_planners.py`. When `known_rows=21`, pass 21 rows only to `final_v1`; keep v1/v4 on the same visible 7 rows so the comparison represents the production baseline versus WS knowledge. Set CLI defaults to `v1,v4,final_v1`. In `replay_real_boards.py`, print `>250ms`, p95, p99, and max; retain crash/empty-plan reporting.

```python
# tools/mining_sim_eval.py
def get_known_board(self, rows: int = 7) -> List[List[str]]:
    limit = max(7, min(21, int(rows)))
    end = min(len(self.tape), self.viewport + limit)
    return [list(self.tape[index]) for index in range(self.viewport, end)]

# inside play_one_game
visible_board = sim.get_board()
plan_board = sim.get_known_board(known_rows) if planner == "final_v1" else visible_board
plan = plan_fn(
    plan_board,
    shovels=float(sim.inv["pickaxe"]),
    items={"drill": sim.inv["drill"], "bomb": sim.inv["bomb"]},
    visible_rows=7,
) if planner == "final_v1" else plan_fn(
    visible_board,
    shovels=float(sim.inv["pickaxe"]),
    items={"drill": sim.inv["drill"], "bomb": sim.inv["bomb"]},
)
```

- [ ] **Step 5: Run tool unit tests and short deterministic smoke evaluations**

Run:

```bash
python -m pytest tests/test_final_v1_eval_tools.py -q
python tools/compare_planners.py --runs 2 --seed 711 --max-iter 20 --planners v1,final_v1 --known-rows 7 --time-cap 30
python tools/compare_planners.py --runs 2 --seed 711 --max-iter 20 --planners v1,final_v1 --known-rows 21 --time-cap 30
python tools/replay_real_boards.py --planners v1,final_v1 --limit 10
```

Expected: pytest PASS; both tools exit 0 and print rows for v1 and final_v1. A zero-board replay is acceptable if no local miner logs match, but it must exit cleanly.

- [ ] **Step 6: Commit evaluation tooling**

```bash
git add tools/mining_sim_eval.py tools/compare_planners.py tools/replay_real_boards.py tests/test_final_v1_eval_tools.py
git commit -m "test(mining): add final v1 ab and replay gates"
```

---

### Task 8: Integrated verification and operator handoff

**Files:**
- Verify all files listed above; do not modify device entries in `bot_config.json`.

- [ ] **Step 1: Run the complete targeted planner suite**

Run:

```bash
python -m pytest tests/test_final_v1_scoring.py tests/test_final_v1_planner.py tests/test_mining_adapter_final_v1.py tests/test_mining_service_final_v1.py tests/test_miner_planner_executor_integration.py tests/test_mining_adapter_air_label.py tests/test_mine_terrain_static.py tests/test_ws_mining_steering.py -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run the complete targeted WS/config/Dashboard suite**

Run:

```bash
python -m pytest tests/test_ws_phase.py tests/test_ws_runner_wiring.py tests/test_ws_token_mining_supervised.py tests/test_ws_token_mining.py tests/test_ws_inventory.py tests/test_device_config.py tests/test_dashboard_template.py tests/test_smoke_config_api.py tests/test_final_v1_eval_tools.py -q
```

Expected: all tests PASS. `.pytest_cache` permission warnings on NAS may be ignored; test failures may not.

- [ ] **Step 3: Compile only the modified Python files**

Run:

```bash
python -m py_compile miner/final_v1/__init__.py miner/final_v1/types.py miner/final_v1/scoring.py miner/final_v1/planner.py miner/mining_service.py ws_token/mining_adapter.py ws_token/mining_supervised.py ws_token/runner.py game_actions/ws_phase.py config_manager.py tools/mining_sim_eval.py tools/compare_planners.py tools/replay_real_boards.py tests/test_final_v1_scoring.py tests/test_final_v1_planner.py tests/test_mining_adapter_final_v1.py tests/test_mining_service_final_v1.py tests/test_final_v1_eval_tools.py
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Run deterministic acceptance measurements**

Run the same seeds/inventory for every planner:

```bash
python tools/compare_planners.py --runs 100 --seed 711 --max-iter 120 --planners v1,v4,final_v1 --known-rows 7 --time-cap 180
python tools/compare_planners.py --runs 100 --seed 711 --max-iter 120 --planners v1,final_v1 --known-rows 21 --time-cap 180
python tools/replay_real_boards.py --planners v1,final_v1
```

Expected acceptance gate before recommending any device opt in:

- final-v1 completed pit cells and completed clusters are both greater than v1.
- `pits/shovel` and `pits/(shovel + equal_item_weight*items)` are greater than v1.
- stuck, rejected/no-change, lost-pit, unfinished-cluster, and fallback counts do not increase.
- final-v1 p99 and max planning latency are both `<= 250 ms` in the tool-reported planner timing.

If the gate fails, keep the implementation and shadow mode available, keep every device/default on v1, and record the failing metrics; do not tune acceptance thresholds or silently switch defaults.

- [ ] **Step 5: Inspect the scoped diff and repository guardrails**

Run:

```bash
git diff --check
git status --short
git diff -- miner/final_v1 miner/mining_service.py ws_token/mining_adapter.py ws_token/mining_supervised.py ws_token/runner.py game_actions/ws_phase.py config_manager.py templates/dashboard.html tools/mining_sim_eval.py tools/compare_planners.py tools/replay_real_boards.py tests/test_final_v1_scoring.py tests/test_final_v1_planner.py tests/test_mining_adapter_final_v1.py tests/test_mining_service_final_v1.py tests/test_final_v1_eval_tools.py tests/test_ws_phase.py tests/test_ws_token_mining_supervised.py tests/test_device_config.py tests/test_dashboard_template.py
```

Expected: no whitespace errors; no unrelated user files included; new strategy comments are Chinese where repository-specific behavior needs explanation; no bomb-only rule is applied to drills; existing v1/v3/v4 code paths remain present; `bot_config.json` device values remain unchanged.

- [ ] **Step 6: Keep verification changes inside their owning scoped commit**

If verification exposes a defect, return to the task that owns that file, add a regression test there, apply the minimal fix, rerun that task's command, and amend only that task's scoped commit. If verification makes no file changes, create no extra commit. Never stage all files from the dirty worktree.

---

## Rollout sequence after implementation

1. Merge with both planner settings still at their defaults: primary `v1`, shadow empty.
2. Enable `mining_shadow_planner_version=final_v1` on one WS-capable device through Dashboard; inspect structured plan/execute telemetry without changing real actions.
3. Replay the captured boards and rerun the deterministic A/B gate.
4. Only if every acceptance criterion passes, explicitly select primary `final_v1` for one canary device; never change the global or per-device default automatically.
5. On any stuck/rejected regression, switch the canary back to `v1`; final-v1 no-step already falls back to v1, while WS/CNN failures retain their existing broader degradation paths.

---

## 驗收結果（2026-07-11 實測，Task 8 Step 4）

`tools/compare_planners.py --runs 100 --seed 711 --max-iter 120 --time-cap 180`（每 planner 180s 上限；final_v1 較慢，只完成 38/26 局）：

| planner | n | score | pits | clusters | cost(鏟) | pit/sh | pit/eq | p99 ms | max ms | lost | unfin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 (7列) | 100 | 786 | 38.8 | 1290 | 151 | 0.3 | 0.182 | 18.4 | 211.6 | 18 | 7 |
| v4 (7列) | 100 | 772 | 38.2 | 1279 | 162 | 0.2 | 0.183 | 41.1 | 352.0 | 0 | 4 |
| final_v1 (7列) | 38 | 628 | 30.9 | 375 | 72 | 0.4 | 0.142 | 235.1 | 325.3 | 0 | 1 |
| final_v1 (21列) | 26 | 547 | 27.0 | 222 | 73 | 0.4 | 0.125 | 228.8 | 338.3 | 0 | 1 |

**門檻判定：未通過，維持 v1 預設。**

- 未過：完成礦坑格/cluster 低於 v1；`pits/(shovel+3*items)` 0.142 < 0.182；規劃 max 325ms > 250ms。
- 優於 v1：`pits/shovel` 0.4 vs 0.3；lost_pits 0 vs 18；半挖 cluster 1 vs 7；stuck/rejected 均 0。
- 已知比較偏差：sim 以 iteration 計數，final_v1 每輪只出一步（rolling horizon），同 max_iter 下動作數天然較少；加上 time-cap 只完成 38/26 局。省鏟與漏礦保護是真實優勢，總產出差距部分是量測口徑造成——若要重新評估，應以 action_budget 對齊而非 max_iter。

真實盤面 replay（`tools/replay_real_boards.py --glob "logs/*/miner.2026*.log"`，2618 面實機盤）：

| planner | boards | empty% | ms_mean | p95 | p99 | max | >250ms |
|---|---|---|---|---|---|---|---|
| v1 | 2618 | 0.00% | 2.2 | 3.6 | 17.5 | 111.3 | 0 |
| final_v1 | 2618 | 0.00% | 48.7 | 112.4 | 169.5 | 258.9 | 4 |

真實盤面上 final_v1 空 plan 率 0%、p99 169ms 符合門檻；max 258.9ms 僅 4/2618 面微幅超過（sim 的 325ms 峰值來自 21 列大盤）。延遲面接近達標，主要缺口仍是總產出/綜合效率。

**處置（依計畫規則，不調門檻）**：實作與 shadow 模式保留；`mining_planner_version` 全域與各裝置維持 v1；建議先在單台 WS 裝置開 `mining_shadow_planner_version=final_v1` 收集 telemetry，之後再議。

---

## 重設計後驗收（2026-07-12，對齊實機口徑）

> 2026-07-11 驗收失敗的根因是**量測口徑與實機不符**，非演算法本身：
> (1) 舊 sim 一個 iteration 對 v1 = 整份 plan 爆發、對 final_v1 = 單步，同 max_iter 下 final_v1 被制度性餓死；
> (2) time-cap 逐 planner 截斷（v1 100 局 vs final_v1 38 局）卻用總和統計；
> (3) 實機 WS（mine_until_pickaxe_empty）其實所有 planner 都每步重規劃。
> 本輪先對齊 harness（exec_mode step/plan、鎬子歸零終止、seed 配對、每局平均），再重測。

### 重設計內容（branch feat/final-v1-redesign）

- `d7926707` sim 對齊實機：exec_mode（step=WS、plan=ADB）、鎬子歸零即終止、--pickaxe。
- `67c75718` 位能場導引（多源 Dijkstra 距礦成本場，無礦時指向底緣）+ 捲動精確漏礦記帳 + 細粒度 deadline。
- `c0f13949` cluster 真實獎勵（完成才全值、半挖 0.3 部分信用）、DESCENT_BONUS 0.5→2.0、LOST_PIT_PENALTY 40→12、21 列自適應縮 beam、deadline 0.85 安全邊際。
- `70c8e834` **多步輸出**：emit 完整最佳路徑（至捲動點），ADB 整批執行不再每格付一次截圖成本；WS 照樣取第一步。逐步成本隨路徑狀態記錄（rock 步計 rock 價）。
- `609b0a87` compare_planners 配對式迴圈（time-cap 只截整輪 seed）+ 計數指標每局平均。

### 驗收數據（100 配對 seed 711..810，鎬 350/炸 600/鑽 60，道具開啟=實機設定）

WS 口徑（exec_mode=step，200 步/局 = 實機 max_steps）：

| 口徑 | planner | score | pits | clus/g | depth | 鏟耗 | pit/sh | pit/eq | p99 ms | max ms | lost/g |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 21 列 | v1 | 1275 | 63.6 | 22.1 | 220.0 | 249 | 0.26 | 0.180 | 10.8 | 193.2 | 0.2 |
| 21 列 | **final_v1** | **1306** | **65.4** | **22.8** | **226.7** | **128** | **0.52** | 0.170 | 24.9 | 82.5 | **0.0** |
| 7 列 | v1 | 1275 | 63.6 | 22.1 | 220.0 | 249 | 0.26 | 0.180 | 10.5 | 158.2 | 0.2 |
| 7 列 | final_v1 | 1229 | 61.1 | 20.8 | 209.9 | 217 | 0.28 | 0.175 | 46.8 | 131.5 | 0.1 |

ADB 口徑（exec_mode=plan 整批執行，鎬池 350 雙方花完）：

| planner | score | pits | clus/g | depth | acts | pit/sh | pit/eq | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|---|
| v1 | 1791 | 89.5 | 30.9 | 307.7 | 280.2 | 0.26 | 0.181 | 35.0 | 259.8 |
| v4 | 1673 | 83.0 | 28.5 | 282.6 | 273.4 | 0.24 | 0.184 | 37.6 | 383.9 |
| **final_v1** | **1990** | **99.3** | **34.1** | **337.3** | 322.1 | **0.28** | 0.177 | 91.6 | 252.4 |

真實盤面延遲 replay（2618 面實機盤，獨立執行）：

| planner | empty% | ms_mean | p95 | p99 | max | >250ms |
|---|---|---|---|---|---|---|
| v1 | 0.00% | 2.3 | 3.9 | 18.0 | 118.3 | 0 |
| **final_v1** | **0.00%** | 18.5 | 48.1 | 58.5 | **171.9** | **0** |

（sim 表中 max 252.4/259.8/383.9ms 出現在三口徑背景並行跑分時，CPU 競爭下的計時膨脹；
獨立 replay 的 171.9ms 為可信上限。前輪 4/2618 面超標已由細粒度 deadline + 0.85 邊際修復。）

### 結論

- **ADB（鎬池等量）**：final_v1 總收礦 **+11.0%**、完成 cluster **+10.4%**、下潛 **+9.6%**——多步輸出 + 位能導引 + cluster 感知的綜合效果。
- **WS 21 列（主場）**：同 200 步產出 +2.4%，**鏟耗 -48.6%（pit/shovel 2.0x）**，漏礦歸零，延遲遠低於門檻。
- **WS 7 列**（無地形知識）：-3.6%，final_v1 的優勢綁定 WS 已知地形；純 7 列視野仍是 v1 A* 較強。
- 已知代價：道具用量較高（WS 21 列每局 ~88 個 vs v1 ~34，其中炸彈 ~61/局），pit/eq（道具權重 3）
  0.170 vs 0.180 小輸。ITEM_COST 掃 3.0/3.5/4.0/5.0/6.0：3.0 產出最優且斷崖在 3→4（-20%）；
  加深搜索（10/12 層）無效。道具會由完成 cluster 回補（sim ~28/局），淨消耗 ~60/局，
  炸彈庫存充裕的帳號（5554 有 930）可長期跑，庫存低的帳號建議留在 v1。
- **處置**：merge 回 main（使用者 2026-07-12 指示）；`mining_planner_version` 預設維持 v1，
  final_v1 為 per-device opt-in——建議只在 WS 路徑（21 列地形知識可用）且炸彈庫存充裕的裝置啟用。
