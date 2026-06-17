# Mining Deadlock + Top-Pileup Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the mining loop from spinning forever (or piling ore at the top undug) when the planner repeatedly emits a dig the real game refuses to execute — recover gracefully by blacklisting the futile action, scrolling past genuinely-uncollectable ore, and aborting only as a last resort.

**Architecture:** Three independent, separately-valuable layers. (A) Executor: a dig that fails verification AND leaves the board unchanged is surfaced as `NoBoardChangeError` (not a silent `verify_fail` return), so the loop blacklists it. (B) Loop: when the planner is stuck on uncollectable pits (empty plans while pits remain), force one descent dig to scroll past them instead of aborting. (C) Investigate + tune the v5 "delay" (incomplete-square penalty) contribution to top-pileup using the HTML eval harness.

**Tech Stack:** Python 3.10 (conda env `mushroom1`), pytest, Playwright (for the HTML harness in Phase C).

## Global Constraints

- Run tests with `conda activate mushroom1; python -m pytest <files> -q` — never bare `pytest` (imports real device/cv2/Playwright and hangs). Use the PowerShell `activate` form, NOT `conda run`.
- Files carry UTF-8 BOM; read with `encoding="utf-8-sig"` if parsing.
- This touches the LIVE mining loop (`miner/mining_service.py`, `miner/planning/executor.py`). Do not change unrelated behavior. Keep each change minimal and behind its own test.
- The mining executor uses `print()` for step traces (console) and `miner_logger` for the file log. Blacklist/abort messages MUST use `miner_logger` (they are the observable signal).
- Symbol→label map (board dumps): `.`=empty `_`=unreachable_empty `D`=dirt `d`=unreachable_dirt `R`=rock `r`=unreachable_rock `*`=reachable_pit `X`=unreachable_pit.

---

## Background: confirmed root cause (do not re-investigate Phase A)

Real deadlock captured on device `7fe98fc6` (2026-06-17 21:10–21:16): **122 identical iterations** of plan `dig(1,2)→dig(2,2)→bomb(2,2)` over 6 minutes, board pixel-identical, `depth=0(+0)`, **zero** blacklist or abort log lines.

Board (6 pits all `unreachable_pit`, reachable only via row-0 air above):
```
   0 1 2 3 4 5
 0 _ . . . R .
 1 d X X X D .
 2 _ X X X D .
```

Chain (confirmed by reading code + log):
1. Planner's frontier rule (`is_frontier_diggable`, `miner/v3/board.py:76-94`) treats `(1,2)` diggable because `(0,2)` above is reachable air → emits `dig(1,2)`.
2. Executor taps `(1,2)`; the real game won't dig it (the row-0 air is a sealed pocket, not a genuine connected entry) → `verify_cell_empty` fails → hits the **verify-fail early-return** (`miner/planning/executor.py:450-471`): sets `terminated_reason="verify_fail"`, `return acc` — returns BEFORE the `NoBoardChangeError` check at line 508.
3. `mining_service` (`miner/mining_service.py:620-648`) gets a normal result (no exception) → `except NoBoardChangeError` never fires → no blacklist; plan was non-empty so `consecutive_empty_plans` stays 0 (reset at line 610).
4. Board unchanged → identical signature → same plan → repeat forever.

The canonical model (`tools/mining_sim.html`, the authoritative game) ALSO marks `(1,1)(1,2)(1,3)` diggable from row-0 air, so the divergence is real-game-only: in-play the HTML tracks reachability from a genuine foothold and never produces a sealed row-0 pocket, but the live CNN labels a sealed pocket as reachable and the planner trusts it. **The robust fix is loop/executor recovery (Phases A+B), not a planner reachability rewrite** (which can't distinguish foothold from pocket on a single snapshot).

---

## Task A1: Executor surfaces futile dig as NoBoardChangeError

**Files:**
- Modify: `miner/planning/executor.py:450-471` (the dig verify-fail branch)
- Test: `tests/test_miner_executor_verify_fail_noboard.py` (create)

**Interfaces:**
- Consumes: `NoBoardChangeError(step, reason, board_before, board_after, partial_result)` (executor.py:82), `ExecutionResult` (executor.py:24).
- Produces: when a dig fails verification AND `clf.classify_board` of a fresh screenshot equals `step_board_before`, `execute_plan_steps` raises `NoBoardChangeError` instead of returning an `ExecutionResult` with `terminated_reason="verify_fail"`.

- [ ] **Step 1: Write the failing test**

`tests/test_miner_executor_verify_fail_noboard.py`:

```python
"""A dig that fails verification AND leaves the board unchanged must raise
NoBoardChangeError so the mining loop blacklists it (regression: 7fe98fc6
122x identical-plan spin on row-0 unreachable pits, 2026-06-17)."""
from __future__ import annotations

import pytest

from miner.planning import executor as ex
from miner.planning.executor import NoBoardChangeError, execute_plan_steps

# 7x6 deadlock board: pits at row1 reachable only via row-0 air above.
_SYM = {".": "empty", "_": "unreachable_empty", "D": "dirt", "d": "unreachable_dirt",
        "R": "rock", "r": "unreachable_rock", "*": "reachable_pit", "X": "unreachable_pit"}
_GRID = ["_...R.", "dXXXD.", "_XXXD.", "drddR.", "d_d_D.", "dd_rR.", "dr_rdD"]
DEADLOCK = [[_SYM[ch] for ch in row] for row in _GRID]


class _FakeDevice:
    def screenshot(self, format=None):
        return object()  # opaque; classifier is stubbed to ignore it
    def click(self, *a, **k):
        pass


class _FakeClassifier:
    """Always returns the deadlock board — i.e. nothing the executor does
    changes the board (models the real 'tap does nothing' case)."""
    def classify_board(self, img, save_samples=False):
        return [row[:] for row in DEADLOCK], None


def test_dig_verify_fail_with_unchanged_board_raises_noboardchange(monkeypatch):
    # Stub the device-touching helpers so no real ADB/screenshot is needed.
    monkeypatch.setattr(ex, "tap_cell", lambda *a, **k: None)
    monkeypatch.setattr(ex, "verify_cell_empty", lambda *a, **k: False)  # never empties
    monkeypatch.setattr(ex, "check_points", lambda *a, **k: None)
    monkeypatch.setattr(ex, "wait_frame_stable", lambda d, **k: object())

    d = _FakeDevice()
    clf = _FakeClassifier()
    board = [row[:] for row in DEADLOCK]
    plan_steps = [{"type": "dig", "action": "dig", "dig_list": [(1, 2)],
                   "target": (1, 2)}]

    with pytest.raises(NoBoardChangeError):
        execute_plan_steps(d, clf, board, plan_steps)
```

- [ ] **Step 2: Run test to verify it FAILS**

```
conda activate mushroom1; python -m pytest tests/test_miner_executor_verify_fail_noboard.py -q
```
Expected: FAIL — the executor currently returns an `ExecutionResult` (verify_fail), so `pytest.raises(NoBoardChangeError)` does not trigger.

- [ ] **Step 3: Implement — raise NoBoardChangeError on futile dig**

In `miner/planning/executor.py`, replace the verify-fail branch (the second `if not success:` block at lines 450-471) with:

```python
                if not success:
                    print(f"    ⚠️ 挖掘驗證失敗 ({r},{c})，停止執行剩餘步驟")
                    cell_events.append(cell_event)
                    # The dig did not empty the target. If the WHOLE board is
                    # also unchanged, this action is futile on this board —
                    # surface it as NoBoardChangeError so the mining loop
                    # blacklists it and re-plans, instead of returning silent
                    # "progress" that lets the same plan repeat forever
                    # (regression: 7fe98fc6 row-0 unreachable-pit spin).
                    board_now, _ = clf.classify_board(
                        d.screenshot(format="opencv"), save_samples=False
                    )
                    if board_now == step_board_before:
                        acc.terminated_reason = "no_board_change"
                        raise NoBoardChangeError(
                            step=step,
                            reason=f"dig at ({r},{c}) verify failed, board unchanged",
                            board_before=step_board_before,
                            board_after=[row[:] for row in board_now],
                            partial_result=acc,
                        )
                    if rl_recorder:
                        rl_recorder.record_transition(
                            {
                                "step_index": i,
                                "plan_action": step["action"],
                                "target": step["target"],
                                "step_cost_expected": step.get("step_cost"),
                                "gain_expected": step.get("gain"),
                                "cell_events": cell_events,
                                "board_before": step_board_before,
                                "board_after": [row[:] for row in board_now],
                                "terminated": "verify_fail",
                            }
                        )
                    acc.steps_completed += 1
                    acc.terminated_reason = "verify_fail"
                    return acc
```

(The screenshot in `_FakeDevice` is opaque; the stubbed `clf.classify_board` ignores it and returns the deadlock board, so `board_now == step_board_before` is True in the test.)

- [ ] **Step 4: Run test to verify it PASSES**

```
conda activate mushroom1; python -m pytest tests/test_miner_executor_verify_fail_noboard.py -q
```
Expected: PASS.

- [ ] **Step 5: Run existing executor tests for no regression**

```
conda activate mushroom1; python -m pytest tests/test_miner_executor_execution_result.py tests/test_miner_executor_wait_frame_stable.py tests/test_miner_planner_executor_integration.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add miner/planning/executor.py tests/test_miner_executor_verify_fail_noboard.py
git commit -m "fix(miner/exec): futile dig (verify-fail + board unchanged) raises NoBoardChangeError so loop blacklists it"
```

---

## Task A2: Loop identical-state guard (defense-in-depth)

The documented "auto-aborts after 3 identical states" (CLAUDE.md) does NOT exist — only an empty-plan counter does. Add the real identical-board guard so ANY future "non-empty plan but board never changes" bug self-terminates.

**Files:**
- Modify: `miner/mining_service.py` (the mining loop, near the signature bookkeeping at lines 570-575 and the post-execute path at 647-648; constant near line 101)
- Test: `tests/test_mining_service_identical_state_guard.py` (create)

**Interfaces:**
- Consumes: `_board_signature(board)` (already used at mining_service.py:571).
- Produces: a module constant `_MAX_IDENTICAL_BOARDS: int = 3`; the loop breaks when the board signature is identical for `_MAX_IDENTICAL_BOARDS` consecutive iterations after executing a non-empty plan.

- [ ] **Step 1: Write the failing test**

`tests/test_mining_service_identical_state_guard.py`:

```python
"""The mining loop must abort when the board signature is identical for
_MAX_IDENTICAL_BOARDS consecutive non-empty-plan iterations (the live
deadlock signature). Pure-logic test of the guard helper."""
from __future__ import annotations

from miner.mining_service import _identical_board_exceeded, _MAX_IDENTICAL_BOARDS


def test_guard_trips_after_threshold_identical():
    sig = "AAA|BBB"
    count = 0
    tripped = False
    for _ in range(_MAX_IDENTICAL_BOARDS + 2):
        count, tripped = _identical_board_exceeded(sig, sig, count)
        if tripped:
            break
    assert tripped is True
    assert count >= _MAX_IDENTICAL_BOARDS


def test_guard_resets_when_board_changes():
    count = 0
    count, tripped = _identical_board_exceeded("A", "A", count)  # same
    assert tripped is False and count == 1
    count, tripped = _identical_board_exceeded("B", "A", count)  # changed
    assert tripped is False and count == 0
```

- [ ] **Step 2: Run test to verify it FAILS**

```
conda activate mushroom1; python -m pytest tests/test_mining_service_identical_state_guard.py -q
```
Expected: FAIL — `ImportError: cannot import name '_identical_board_exceeded'`.

- [ ] **Step 3: Implement the guard helper + wire it**

In `miner/mining_service.py`, after line 101 (`_MAX_EMPTY_PLANS`):

```python
# 連續相同版面（非空 plan 卻毫無變化）的容忍上限 — 真正的「identical state」死結偵測。
_MAX_IDENTICAL_BOARDS: int = 3


def _identical_board_exceeded(cur_sig, prev_sig, count: int):
    """Return (new_count, tripped). Increments when the board signature is
    unchanged from the previous iteration; resets to 0 when it changes."""
    if prev_sig is not None and cur_sig == prev_sig:
        count += 1
    else:
        count = 0
    return count, count >= _MAX_IDENTICAL_BOARDS
```

Then wire it into the loop. Initialize `identical_board_count = 0` and `prev_exec_sig = None` alongside the other loop-state vars (near where `consecutive_empty_plans = 0` is initialized, ~line 506). After a non-empty plan executes (after line 648 `_apply_partial`), re-read the board signature on the NEXT iteration's `state_signature` (line 571) and compare. Concretely, add right after line 648:

```python
        # Identical-state deadlock guard: if executing a non-empty plan left
        # the board unchanged for too many iterations in a row, abort instead
        # of spinning (Task A1 normally blacklists first; this is the backstop).
        identical_board_count, tripped = _identical_board_exceeded(
            state_signature, prev_exec_sig, identical_board_count
        )
        prev_exec_sig = state_signature
        if tripped:
            miner_logger.warning(
                f"[MiningService] 版面連續 {identical_board_count} 次無變化（非空 plan），"
                f"判定死結，中止挖礦迴圈"
            )
            break
```

Add `identical_board_count = 0` and `prev_exec_sig = None` to the loop-state initialization block (search for `consecutive_empty_plans = 0` before the `while` and add them there).

- [ ] **Step 4: Run test to verify it PASSES**

```
conda activate mushroom1; python -m pytest tests/test_mining_service_identical_state_guard.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add miner/mining_service.py tests/test_mining_service_identical_state_guard.py
git commit -m "fix(miner/loop): abort on N consecutive identical boards (real identical-state deadlock guard)"
```

---

## Task B: Scroll past uncollectable top ore instead of aborting

After A1+A2 the loop no longer spins, but if ore is stuck at the top and uncollectable, the loop merely aborts the whole session (ore lost, mining ends early). The user wants the bot to **scroll past** the stuck ore and keep mining. When the planner returns empty plans WHILE reachable pits remain (i.e. the remaining pits are uncollectable / blacklisted), force one descent dig toward row 6 to trigger a scroll, then continue.

**Files:**
- Modify: `miner/mining_service.py` (the empty-plan handling at lines 599-607)
- Test: `tests/test_mining_service_forced_descent.py` (create)

**Interfaces:**
- Consumes: `_diagnose_empty_plan` (mining_service.py:364), `is_frontier_diggable`, `is_pit` from `miner/v3/board.py`, `dig_cost` from `miner/v3/actions.py`.
- Produces: a helper `_forced_descent_dig(board) -> Optional[Tuple[int,int]]` that returns the deepest reachable non-pit frontier cell (drives a scroll) or None if none exists.

- [ ] **Step 1: Write the failing test**

`tests/test_mining_service_forced_descent.py`:

```python
"""When the planner is stuck (empty plans) but reachable pits remain, the loop
should pick a descent dig that drives toward a scroll rather than aborting."""
from __future__ import annotations

from miner.mining_service import _forced_descent_dig

_SYM = {".": "empty", "_": "unreachable_empty", "D": "dirt", "d": "unreachable_dirt",
        "R": "rock", "r": "unreachable_rock", "*": "reachable_pit", "X": "unreachable_pit"}


def _board(rows):
    return [[_SYM[ch] for ch in r] for r in rows]


def test_forced_descent_picks_deepest_reachable_nonpit_frontier():
    # Row-0 air strip over a column of dirt; deepest diggable dirt should win
    # so digging it advances toward row 6 (a scroll).
    b = _board(["...DDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD", "DDDDDD"])
    pos = _forced_descent_dig(b)
    assert pos is not None
    r, c = pos
    # must be a frontier dirt/rock cell (not a pit), and as deep as reachable
    assert b[r][c] in ("dirt", "rock", "one_hit_rock")


def test_forced_descent_none_when_no_frontier():
    b = _board(["______", "______", "______", "______", "______", "______", "______"])
    assert _forced_descent_dig(b) is None
```

- [ ] **Step 2: Run test to verify it FAILS**

```
conda activate mushroom1; python -m pytest tests/test_mining_service_forced_descent.py -q
```
Expected: FAIL — `ImportError: cannot import name '_forced_descent_dig'`.

- [ ] **Step 3: Implement the helper + wire into empty-plan path**

In `miner/mining_service.py`, add the helper (near the other module helpers, after `_diagnose_empty_plan`):

```python
def _forced_descent_dig(board):
    """Pick the deepest reachable non-pit frontier cell so digging it drives
    the viewport toward a scroll. Used to escape a board where the only
    remaining pits are uncollectable (blacklisted / sealed-pocket reachable).
    Returns (r, c) or None when no diggable non-pit frontier exists."""
    from miner.v3.board import is_frontier_diggable, is_pit
    rows = len(board)
    cols = len(board[0]) if board else 0
    best = None  # (depth_row, col)
    for r in range(rows):
        for c in range(cols):
            if is_pit(board[r][c]):
                continue
            if is_frontier_diggable(board, r, c):
                if best is None or r > best[0]:
                    best = (r, c)
    return best
```

Wire it into the empty-plan branch. Replace the empty-plan block (lines 599-607) so that, before giving up, it tries a forced descent when pits still remain:

```python
        if not plan.get("steps"):
            _diagnose_empty_plan(board, plan, miner_logger)
            # If reachable pits remain but the planner can't collect them
            # (blacklisted / sealed-pocket reachable), don't just count toward
            # abort — scroll past them so mining continues productively.
            if count >= 1:
                descent = _forced_descent_dig(board)
                if descent is not None:
                    miner_logger.warning(
                        f"[MiningService] 空 plan 但仍有礦無法採集，強制下挖 {descent} 推進下樓"
                    )
                    try:
                        execute_plan_steps(
                            d, clf, board,
                            [{"type": "dig", "action": "dig", "dig_list": [descent],
                              "target": descent}],
                            rl_recorder=rl_recorder, deadline=start_time + max_duration_seconds,
                        )
                    except NoBoardChangeError as exc:
                        # even forced descent did nothing — fall through to abort
                        blocked_action_signatures.add(_step_signature(exc.step))
                    else:
                        consecutive_empty_plans = 0
                        continue
            consecutive_empty_plans += 1
            if consecutive_empty_plans >= _MAX_EMPTY_PLANS:
                miner_logger.warning(
                    f"[MiningService] 連續 {consecutive_empty_plans} 次取得空 plan，中止挖礦迴圈"
                )
                break
            continue
```

(`count` is the remaining-pit count tracked by the loop; verify its exact name in context and use it.)

- [ ] **Step 4: Run test to verify it PASSES**

```
conda activate mushroom1; python -m pytest tests/test_mining_service_forced_descent.py -q
```
Expected: PASS.

- [ ] **Step 5: Run the focused mining-service test set**

```
conda activate mushroom1; python -m pytest tests/test_mining_service_identical_state_guard.py tests/test_mining_service_forced_descent.py -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```
git add miner/mining_service.py tests/test_mining_service_forced_descent.py
git commit -m "feat(miner/loop): force descent dig to scroll past uncollectable top ore instead of aborting"
```

---

## Task C: Investigate + tune the v5 "delay → top pileup" (investigation-first)

User observation: v5 *delays* mining so ore accumulates at the top and is never dug. The v5 incomplete-square penalty (`INCOMPLETE_SQUARE_PENALTY = 400.0`, `miner/v5/planner.py:100`) deprioritizes ITEM use on bottom-edge runs whose height < width. This task REPRODUCES the pileup before changing any weight (systematic-debugging: no fix without a failing repro).

**Files:**
- Modify: `tools/sim_html_eval.py` (add top-pileup instrumentation)
- (Possibly) Modify: `miner/v5/planner.py` (only after repro proves a cause)
- Test: extend the harness; if a planner change is made, add a `tests/test_miner_v5_*` regression.

**Interfaces:**
- Consumes: the existing `play_one` loop in `tools/sim_html_eval.py`.
- Produces: a per-round metric `pits_lost_top` = pits that reached row 0 and scrolled off uncollected; printed per planner.

- [ ] **Step 1: Instrument top-pileup in the harness**

In `tools/sim_html_eval.py`, the page already prunes off-screen clusters on scroll. Add a JS counter: before each `scrollDown`, count pit cells currently in row 0, and accumulate those that are about to scroll off uncollected. Expose via `__snapshot()`. Add to the `_APPLY_HELPER` board snapshot a `row0_pits` field = number of `is_pit` cells in `board[0]`, and in `play_one` track the max/again-seen row-0 pit persistence per round (a pit sitting in row 0 across ≥3 planner iterations = pileup).

- [ ] **Step 2: Run the harness and read the pileup metric**

```
conda activate mushroom1; python tools/sim_html_eval.py --planners v4,v5 --runs 5 --max-iters 500
```
Compare v5 vs v4 `row0_pits` persistence. Expected outcome to confirm/refute: if v5 shows materially more row-0 pit persistence than v4, the delay heuristic is implicated.

- [ ] **Step 3: Branch on the evidence**

- If v5 ≈ v4 (no extra pileup in the canonical model): the pileup is a REAL-game/CNN artifact (sealed-pocket reachability), already mitigated by Tasks A+B. Document this in the plan's review section and STOP — do not tune v5.
- If v5 shows materially more row-0 persistence: form a single hypothesis (most likely: the `-400` item-delay keeps deferring a square that migrates to row 0; row-0 rescue only covers DIG, not the deferred bomb). Make ONE minimal change — e.g. exempt row-0-touching runs from `INCOMPLETE_SQUARE_PENALTY` in `_incomplete_bottom_squares` / `_action_priority` (`miner/v5/planner.py:218-256, 276-281`) — add a v5 unit test asserting a row-0-touching incomplete square is NOT penalized, then re-run Step 2 to confirm the metric drops.

- [ ] **Step 4: Commit (only if a change was made)**

```
git add tools/sim_html_eval.py miner/v5/planner.py tests/test_miner_v5_planner.py
git commit -m "fix(miner/v5): do not defer item use on row-0-touching squares (top-pileup)"
```

If Step 3 concluded "no v5 change needed", commit just the instrumentation:

```
git add tools/sim_html_eval.py
git commit -m "test(miner): add top-pileup instrumentation to HTML eval harness"
```

---

## Task D: End-to-end verification on the HTML harness

**Files:** none (verification only).

- [ ] **Step 1: Re-run the full planner eval after A+B (+C)**

```
conda activate mushroom1; python tools/sim_html_eval.py --planners v1,v3,v4,v5 --runs 5 --max-iters 500
```
Expected: `stuck=0` for all planners (unchanged), scores within noise of the pre-change baseline (v1≈23.6k, v5≈23.5k under the OLD high density; numbers will differ now that density was recalibrated to ~3.6% — record the new baseline). No planner regresses.

- [ ] **Step 2: Replay the captured deadlock board through the loop guard**

Confirm via the Task A1 + A2 tests that the exact `7fe98fc6` board no longer spins. Document the before/after in the plan review section.

---

## Self-Review

**Spec coverage:**
- (b) executor safety net → Task A1 ✓
- (b) loop abort backstop → Task A2 ✓ (implements the documented-but-missing identical-state guard)
- (a) scroll past uncollectable top ore → Task B ✓ (addresses "礦卡在最上面然後沒挖")
- v5 delay observation → Task C ✓ (investigation-first; only tunes v5 if repro proves it)
- end-to-end verification → Task D ✓

**Placeholder scan:** none — every code step shows the code; the only deferred decision (Task C Step 3) is an explicit evidence branch, not a placeholder.

**Type consistency:** `_identical_board_exceeded(cur_sig, prev_sig, count) -> (count, bool)` used consistently in A2. `_forced_descent_dig(board) -> Optional[(r,c)]` used consistently in B. `NoBoardChangeError(step, reason, board_before, board_after, partial_result)` matches executor.py:82.

**Open risk to flag at execution time:** Task B references the loop's remaining-pit counter as `count` and the deadline as `start_time + max_duration_seconds` — verify the exact local variable names in `mining_service.py` before wiring (they were observed at lines 618, 588-590). Adjust to the real names.
