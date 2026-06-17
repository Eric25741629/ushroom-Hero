# Prospective Pit Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When v5 planner sees a horizontal N×1 pit run, infer the full N×N cluster and include the unexcavated cells below as "unreachable_pit" before planning, so bomb/drill scoring and cluster completion bonuses reflect the full expected cluster.

**Architecture:** Add `find_prospective_pits(board)` to `miner/v3/clusters.py`; call it at the start of `plan_v5()` in `miner/v5/planner.py` to mutate the working board before `_identify_pit_groups` and `_incomplete_bottom_squares` run. No search logic changes — all existing machinery (cluster scoring, B&B, priority ordering) works correctly once the board is augmented.

**Tech Stack:** Python, existing miner.v3 board/cluster utilities, pytest

---

## File Map

| File | Change |
|------|--------|
| `miner/v3/clusters.py` | Add `find_prospective_pits(board)` |
| `miner/v5/planner.py` | Import `find_prospective_pits`; call it in `plan_v5()` before `_identify_pit_groups` |
| `tests/test_miner_v5_planner.py` | New tests: unit tests for `find_prospective_pits` + integration test for planner behaviour |

---

## Background: Key Facts

- **Cell states**: `reachable_pit`, `unreachable_pit` (both pass `is_pit()`); `dirt`, `rock`, `one_hit_rock`, `unreachable_dirt`, `unreachable_rock` (unexcavated); `empty`, `dug_pit`, `unreachable_empty` (all pass `is_air()`)
- **Board indexing**: row 0 = top, row 6 = bottom (7-row viewport)
- **Bomb placement**: only at `is_reachable_air` cells (`enumerate_item_actions` line 115). Bombs fire across any cell in the viewport regardless of reachability.
- **`open_cell`**: sets `reachable_pit`/`unreachable_pit` → `dug_pit`; hard cells → `empty`
- **`_identify_pit_groups`**: BFS over all cells where `is_pit()` is True — includes `unreachable_pit`
- **`_cluster_completion_delta`**: awards `PIT_VALUE * cells_dug + n*(n-1)*2` bonus when `cells_dug == n` for a group of `n` cells
- **`_incomplete_bottom_squares`** (line 218): handles the out-of-viewport edge case (bottom-row runs whose prospective rows are below viewport) — keep it, it's complementary not redundant
- **`affected_pit_cells`**: counts cells where `is_pit(board[r][c])` is True — includes `unreachable_pit` ✓

---

## Task 1: Unit Tests for `find_prospective_pits`

**Files:**
- Test: `tests/test_miner_v5_planner.py`

- [ ] **Step 1: Add failing unit tests**

Append to `tests/test_miner_v5_planner.py`:

```python
# ---------------------------------------------------------------------------
# find_prospective_pits unit tests
# ---------------------------------------------------------------------------
from miner.v3.clusters import find_prospective_pits


def test_prospective_pits_2x1_returns_one_row_below():
    """2×1 run → one row below (width=2, need 1 more row) is prospective."""
    board = _board([
        "......",
        "D**DDD",   # row 1: 2×1 at cols 1-2
        "DDDDDD",   # row 2: dirt → prospective
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == {(2, 1), (2, 2)}


def test_prospective_pits_3x1_returns_two_rows_below():
    """3×1 run → two rows below (width=3, need 2 more rows) are prospective."""
    board = _board([
        "......",
        "D***DD",   # row 1: 3×1 at cols 1-3
        "DDDDDD",   # row 2: prospective
        "DDDDDD",   # row 3: prospective
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == {(2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)}


def test_prospective_pits_3x2_visible_returns_one_row():
    """3×2 visible (rows 1-2 confirmed) → only row 3 is prospective."""
    board = _board([
        "......",
        "D***DD",   # row 1: top of 3×3
        "D***DD",   # row 2: second confirmed row
        "DDDDDD",   # row 3: prospective
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == {(3, 1), (3, 2), (3, 3)}


def test_prospective_pits_invalidated_by_empty_below():
    """If a cell below the pit run is already empty/dug, run is invalid."""
    board = _board([
        "......",
        "D**DDD",   # row 1: 2×1
        "D.DDDD",   # row 2: col 1 is empty → invalidates
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == set()


def test_prospective_pits_3x1_at_bottom_returns_empty():
    """3×1 at last row → rows below are out of viewport → no prospective cells."""
    board = _board([
        "......",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "D***DD",   # row 6: 3×1 at bottom, rows 7-8 out of viewport
    ])
    result = find_prospective_pits(board)
    assert result == set()


def test_prospective_pits_isolated_single_pit_ignored():
    """Width-1 runs are never prospective (1×1 does not infer a 1×1 square)."""
    board = _board([
        "......",
        "DD*DDD",   # row 1: isolated single pit at col 2
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == set()


def test_prospective_pits_complete_cluster_excluded():
    """Fully-confirmed 2×2 is already handled by find_clusters — no prospective."""
    board = _board([
        "......",
        "D**DDD",   # row 1: top of 2×2
        "D**DDD",   # row 2: bottom confirmed — complete 2×2
        "DDDDDD",
        "DDDDDD",
        "DDDDDD",
        "......",
    ])
    result = find_prospective_pits(board)
    assert result == set()
```

- [ ] **Step 2: Run tests to confirm they fail with ImportError**

```
python -m pytest tests/test_miner_v5_planner.py::test_prospective_pits_2x1_returns_one_row_below -v
```

Expected: `ImportError: cannot import name 'find_prospective_pits'`

---

## Task 2: Implement `find_prospective_pits` in clusters.py

**Files:**
- Modify: `miner/v3/clusters.py`

- [ ] **Step 1: Add `Set` and `is_air` to imports**

In `miner/v3/clusters.py`, change:

```python
from typing import FrozenSet, List, Tuple

from .board import is_pit
```

to:

```python
from typing import FrozenSet, List, Set, Tuple

from .board import is_air, is_pit
```

- [ ] **Step 2: Append `find_prospective_pits` after `cluster_value`**

Append to `miner/v3/clusters.py`:

```python

def find_prospective_pits(board: Board) -> Set[Coordinate]:
    """Return in-viewport unexcavated cells that are certain to be pits.

    A horizontal run of N >= 2 adjacent pits in a row is guaranteed to be the
    top portion of an N×N square cluster (domain rule: isolated adjacent pits
    do not occur). The unexcavated cells in the rows directly below (within
    the viewport) are prospective pits — bombs/drills can reach them and the
    full N×N cluster bonus becomes plannable.

    Only returns cells within the viewport (row index < len(board)).
    Out-of-viewport prospective pits cannot be hit and are handled separately
    by _incomplete_bottom_squares in the v5 planner.
    """
    if not board:
        return set()
    rows = len(board)
    cols = len(board[0])
    prospective: Set[Coordinate] = set()

    for r in range(rows):
        c = 0
        while c < cols:
            if not is_pit(board[r][c]):
                c += 1
                continue
            start = c
            while c < cols and is_pit(board[r][c]):
                c += 1
            width = c - start
            if width < 2:
                continue
            # Only process from the topmost row of the expected square.
            # If the row above is all-pits at these cols, we are not at the top.
            if r > 0 and all(
                is_pit(board[r - 1][cc]) for cc in range(start, start + width)
            ):
                continue
            # Count consecutive all-pit rows already confirmed below the run.
            extra_confirmed = 0
            for rb in range(r + 1, r + width):
                if rb >= rows:
                    break
                if all(is_pit(board[rb][cc]) for cc in range(start, start + width)):
                    extra_confirmed += 1
                else:
                    break
            total_confirmed = 1 + extra_confirmed
            if total_confirmed >= width:
                continue  # complete cluster — find_clusters handles it
            # Collect prospective cells from the remaining unconfirmed rows.
            run_valid = True
            candidates: Set[Coordinate] = set()
            for rb in range(r + total_confirmed, r + width):
                if rb >= rows:
                    break  # out of viewport — bombs cannot reach
                for cc in range(start, start + width):
                    cell = board[rb][cc]
                    if is_air(cell):
                        run_valid = False
                        break
                    if not is_pit(cell):
                        candidates.add((rb, cc))
                if not run_valid:
                    break
            if run_valid:
                prospective.update(candidates)

    return prospective
```

- [ ] **Step 3: Run unit tests — expect all 7 to pass**

```
python -m pytest tests/test_miner_v5_planner.py -k "prospective_pits" -v
```

Expected: 7 passed

- [ ] **Step 4: Commit**

```
git add miner/v3/clusters.py tests/test_miner_v5_planner.py
git commit -m "feat(miner): add find_prospective_pits to v3/clusters"
```

---

## Task 3: Integration Test for Planner Behaviour

**Files:**
- Test: `tests/test_miner_v5_planner.py`

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_miner_v5_planner.py`:

```python
# ---------------------------------------------------------------------------
# Prospective pit inference — planner integration
# ---------------------------------------------------------------------------

def test_prospective_pits_planner_clears_beyond_visible():
    """With prospective marking, the planner plans to collect pits beyond the
    visible 3×1 row.

    Setup: 3×1 visible pits at row 1 (cols 1-3). Rows 2-3 are unexcavated
    dirt that will be marked as unreachable_pit. 1 bomb available.

    Without prospective marking:
      - original_groups has a group of 3 (the visible pits only)
      - Bomb at (0, 2) fully clears group of 3 → no more pits → DFS stops
      - pits_collected == 3

    With prospective marking:
      - original_groups has a group of 9 (3 confirmed + 6 prospective)
      - After bomb, 5 prospective pits remain → DFS continues digging
      - pits_collected > 3
    """
    board = _board([
        "......",   # row 0: all reachable air (bomb placement available)
        "D***DD",   # row 1: 3×1 confirmed pits at cols 1-3
        "DDDDDD",   # row 2: dirt → prospective after marking
        "DDDDDD",   # row 3: dirt → prospective after marking
        "DDDDDD",   # row 4
        "DDDDDD",   # row 5
        "......",   # row 6: open air (floor7 already open)
    ])
    plan = plan_v5(board, shovels=20, items={"drill": 0, "bomb": 1})
    assert plan["ok"] is True
    pits_collected = plan["stats"]["pits_collected"]
    assert pits_collected > 3, (
        f"Expected planner to target prospective pits (>3 collected), "
        f"got {pits_collected}. Prospective marking may not be integrated."
    )
```

- [ ] **Step 2: Run test — expect failure**

```
python -m pytest tests/test_miner_v5_planner.py::test_prospective_pits_planner_clears_beyond_visible -v
```

Expected: FAIL — `AssertionError: Expected planner to target prospective pits (>3 collected), got 3`

---

## Task 4: Integrate into `plan_v5`

**Files:**
- Modify: `miner/v5/planner.py`

- [ ] **Step 1: Add import**

In `miner/v5/planner.py`, change the `miner.v3.clusters` import block. Currently there is no import from `miner.v3.clusters` in v5. Add one line after the existing `miner.v3.board` imports (around line 55):

```python
from miner.v3.clusters import find_prospective_pits
```

(Insert after the `from miner.v3.board import (...)` block, before `from miner.v3.types import ...`)

- [ ] **Step 2: Mark prospective cells in `plan_v5` before group identification**

In `plan_v5()`, the sequence currently is (lines 406–420):

```python
work = normalize_board(board)
canonicalize_in_place(work)
initial_pits = count_remaining_pits(work)
strategy = _classify_strategy(work)
item_state = {...}
blocked = set(blocked_actions or set())
original_groups = _identify_pit_groups(work)
...
column_quality = _column_descent_quality(work, priors)
pit_cols = _pit_columns(work)
incomplete_squares = _incomplete_bottom_squares(work)
```

Add the prospective marking block AFTER `canonicalize_in_place` and BEFORE `initial_pits`:

```python
work = normalize_board(board)
canonicalize_in_place(work)
# Mark in-viewport prospective pits before counting and group identification.
# A horizontal N×1 run is guaranteed to be the top of an N×N square; the
# unexcavated cells below are treated as unreachable_pit so the full cluster
# is visible to _identify_pit_groups and _cluster_completion_delta.
for _pr, _pc in find_prospective_pits(work):
    work[_pr][_pc] = "unreachable_pit"
initial_pits = count_remaining_pits(work)
...
```

Everything after this line runs unchanged. `_identify_pit_groups`, `_incomplete_bottom_squares`, `_action_priority` all work correctly because:
- `_identify_pit_groups`: BFS includes `unreachable_pit` → full N×N group formed
- `_cluster_completion_delta`: awards full N×N bonus when all cells dug
- `affected_pit_cells`: counts `unreachable_pit` → bomb priority reflects full cluster
- `_incomplete_bottom_squares`: out-of-viewport runs are NOT marked → still fires as before

- [ ] **Step 3: Run integration test — expect pass**

```
python -m pytest tests/test_miner_v5_planner.py::test_prospective_pits_planner_clears_beyond_visible -v
```

Expected: PASS

- [ ] **Step 4: Run full v5 test suite — no regressions**

```
python -m pytest tests/test_miner_v5_planner.py -v
```

Expected: all existing tests pass + the new ones pass.

- [ ] **Step 5: Run v3/v4 tests to confirm clusters.py change didn't break anything**

```
python -m pytest tests/test_miner_v3_planner.py tests/test_miner_v4_planner.py tests/test_miner_v4_shovel_budget.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```
git add miner/v5/planner.py tests/test_miner_v5_planner.py
git commit -m "feat(miner/v5): prospective pit inference — mark N×1 cluster below-rows as pits before planning"
```

---

## Self-Review

**Spec coverage:**
- ✓ `find_prospective_pits`: defined in Task 2
- ✓ In-viewport only: `if rb >= rows: break` constraint
- ✓ Invalidation by empty cell below: `if is_air(cell): run_valid = False`
- ✓ Complete cluster excluded: `if total_confirmed >= width: continue`
- ✓ `_incomplete_bottom_squares` kept for out-of-viewport edge cases
- ✓ Integration via board mutation before `_identify_pit_groups`

**Placeholder scan:** None found.

**Type consistency:** `find_prospective_pits` returns `Set[Coordinate]`; called via `for _pr, _pc in find_prospective_pits(work)` which correctly unpacks `Coordinate = Tuple[int, int]`.

**Not changing:** DFS structure, v5 priors, `_incomplete_bottom_squares`, `INCOMPLETE_SQUARE_PENALTY`, all action priority weights, v4 cluster machinery.
