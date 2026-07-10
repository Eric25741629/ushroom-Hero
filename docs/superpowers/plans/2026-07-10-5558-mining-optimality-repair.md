# 5558 Mining Optimality Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair 5558 mining inventory validation, visible-board route selection, WS below-viewport routing, and successful-dig verification.

**Architecture:** Reuse the existing WS inventory and WS mining modules instead of adding new protocols. Keep each fix at its current ownership boundary: executor live checks, v4 search default, WS phase config normalization, and classifier verification.

**Tech Stack:** Python 3.10, pytest, existing Playwright/WS adapters.

---

### Task 1: Executor WS inventory consistency

**Files:**
- Modify: `tests/test_miner_executor_execution_result.py`
- Modify: `miner/planning/executor.py`

- [ ] Add tests proving WS count wins for web_h5 and OCR remains fallback.
- [ ] Run the new tests and confirm they fail because executor only calls OCR.
- [ ] Import and call `read_ws_prop_counts` inside `get_live_item_count`.
- [ ] Run the executor tests and confirm they pass.

### Task 2: V4 adaptive item lookahead

**Files:**
- Modify: `tests/test_miner_v4_planner.py`
- Modify: `miner/v4/planner.py`

- [ ] Add the 2026-07-10 5558 board regression and assert the default plan starts at `(5, 3)` with no bomb.
- [ ] Confirm the test fails with the current `(4, 3) -> bomb` depth-3 plan.
- [ ] Keep the default horizon at three; when its selected plan consumes an item, compare one depth-four result using the same objective score.
- [ ] Run v4 planner and shovel-budget tests.

### Task 3: WS-first mining config compatibility

**Files:**
- Modify: `tests/test_ws_phase.py`
- Modify: `game_actions/ws_phase.py`

- [ ] Add tests showing top-level `ws_token_mining` becomes runner `mining_config`, while nested `ws_token.mining` wins.
- [ ] Confirm the compatibility test fails because `_run_device` currently sees no mining config.
- [ ] Normalize the config in `run_ws_phase` before `_run_device` is called.
- [ ] Run WS phase and mining steering tests.

### Task 4: Low-confidence successful dig verification

**Files:**
- Modify: `tests/test_miner_executor_execution_result.py`
- Modify: `miner/planning/executor.py`

- [ ] Add a test where the classifier returns low-confidence `empty` and assert verification succeeds without a retry click.
- [ ] Confirm it fails under the current confidence-first check.
- [ ] Evaluate `base_label` before the confidence gate.
- [ ] Run executor verification tests, including unchanged-board deadlock coverage.

### Task 5: Integrated verification

**Files:**
- Verify only all files above plus `tests/test_ws_inventory.py` and WS mining steering tests.

- [ ] Run targeted pytest for all affected modules.
- [ ] Run `py_compile` for modified Python files and tests.
- [ ] Inspect `git diff --check`, scoped diff, comments, and config behavior.
- [ ] Report that no full-repo pytest was run, per AGENTS.md.
