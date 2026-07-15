# Carpark Strict Cluster Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require five already-present same-server players before every cross-server park, exclude silver levels 1/2/3, disable non-cluster fallback, and persist a complete decision trace in each device's `main.log`.

**Architecture:** Extend the existing cluster-scan configuration with a fixed exclusion set and strict defaults. Keep pure candidate merging/filtering in `ws_token.carpark`, orchestration and revalidation in `ws_token.runner`, and route structured decision messages through the existing progress callback into the per-device logger in `game_actions.ws_phase`.

**Tech Stack:** Python 3, dataclasses, pytest, existing WS protocol helpers and per-device logging utilities.

---

### Task 1: Strict configuration defaults

**Files:**
- Modify: `ws_token/carpark_plan.py`
- Modify: `config_manager.py`
- Test: `tests/test_carpark_cluster_scan.py`
- Test: `tests/test_carpark_plan.py`
- Test: `tests/test_ws_phase_config.py`

- [ ] **Step 1: Write failing tests**

Assert that default `cluster_min` and cluster-scan `min_allies` are 5, `excluded_levels == (1, 2, 3)`, malformed exclusions fall back to `(1, 2, 3)`, and configured scan/priority levels have exclusions removed.

```python
def test_parse_strict_cluster_defaults():
    cs = parse_cluster_scan({"cluster_scan": {"enabled": True}})
    assert cs.min_allies == 5
    assert cs.excluded_levels == (1, 2, 3)
    assert all(level not in cs.levels for level in cs.excluded_levels)
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_carpark_cluster_scan.py tests/test_carpark_plan.py tests/test_ws_phase_config.py -q`

Expected: failures showing the old threshold `3` and missing `excluded_levels`.

- [ ] **Step 3: Implement minimal configuration changes**

Add `DEFAULT_CLUSTER_SCAN_EXCLUDED_LEVELS = (1, 2, 3)`, set both cluster defaults to 5, add `excluded_levels` to `ClusterScanConfig`, sanitize it, and remove excluded values from `levels` and `priority_levels`. Change default `allow_low_noncluster` to `False` so malformed or legacy strict plans cannot silently fall back.

- [ ] **Step 4: Verify GREEN**

Run the same three target test files and expect all tests to pass.

### Task 2: Candidate merge and exclusion

**Files:**
- Modify: `ws_token/carpark.py`
- Test: `tests/test_carpark_cluster_scan.py`

- [ ] **Step 1: Write failing tests**

Add tests for a pure `prepare_cluster_scan_candidates()` helper: merge `collect_space` and `null_space` by `master_id`, retain the richer/parkable entry, exclude non-silver, full, levels 1/2/3, and today's parked ids, and return audit counts plus excluded identities.

```python
candidates, audit = prepare_cluster_scan_candidates(
    null_lots, collect_lots,
    excluded_levels=(1, 2, 3), today_parked=set(),
)
assert [silver_ceng_to_level(x.ceng) for x in candidates] == [4, 5]
assert audit["excluded_levels"] == [1, 2, 3]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_carpark_cluster_scan.py -q`

Expected: import failure because the helper does not exist.

- [ ] **Step 3: Implement the pure helper**

Implement deterministic merging and filtering without WS calls. Return a compact audit dictionary used by both tests and logs.

- [ ] **Step 4: Verify GREEN**

Run the cluster-scan test file and expect all tests to pass.

### Task 3: Strict scan, revalidation, and no fallback

**Files:**
- Modify: `ws_token/runner.py`
- Modify: `ws_token/carpark.py`
- Test: `tests/test_carpark_runner_plan.py`
- Test: `tests/test_carpark_cluster_scan.py`

- [ ] **Step 1: Write failing orchestration tests**

Cover: four allies never park; five allies park; levels 1/2/3 never reach `read_lot`; missing server id returns `strict_cluster_server_id_missing`; timeout returns `strict_cluster_not_found` without calling `auto_select_and_park_many`; pre-park revalidation dropping from five to four skips the park; noon repark follows the same strict path.

```python
assert out["cross"]["reason"] == "strict_cluster_not_found"
assert park_many_calls == []
assert park_into_calls == []
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_carpark_runner_plan.py tests/test_carpark_cluster_scan.py -q`

Expected: the current runner enters fallback or parks without revalidating the ally count.

- [ ] **Step 3: Implement strict orchestration**

Add optional `decision_log` to `_run_carpark`. Use the candidate helper for every round, require a nonzero login server id, require `min_allies >= 5`, re-read and recount before parking, and replace timeout fallback with a zero-park result carrying `strict_cluster_not_found`, `scan_rounds`, and audit fields.

- [ ] **Step 4: Verify GREEN**

Run both target test files and expect all tests to pass.

### Task 4: Persist complete decision logs

**Files:**
- Modify: `ws_token/runner.py`
- Modify: `game_actions/ws_phase.py`
- Test: `tests/test_carpark_runner_plan.py`
- Test: `tests/test_ws_phase.py`

- [ ] **Step 1: Write failing log-routing tests**

Capture `decision_log` messages and assert context, effective config, source/filter audit, each round's candidate counts, revalidation, park result or strict refusal, and final summary are present. Assert `ws_phase._progress("carpark", "progress", detail)` writes `WS 停車決策` rather than `WS 開神燈進度`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_carpark_runner_plan.py tests/test_ws_phase.py -q`

Expected: missing decision callback and carpark progress being formatted as lamp progress.

- [ ] **Step 3: Implement log routing**

Have `_run_carpark` emit single-line `key=value` events. Pass a callback from `run_device` through `_notify("carpark", "progress", detail)`. Update `ws_phase._progress` to route carpark progress to the device logger and dashboard step while keeping lamp formatting unchanged. Callback failures remain best-effort.

- [ ] **Step 4: Verify GREEN**

Run both target files and expect all tests to pass.

### Task 5: Apply device configuration and verify

**Files:**
- Modify: `bot_config.json`
- Test: `tests/test_carpark_cluster_scan.py`
- Test: `tests/test_carpark_runner_plan.py`
- Test: `tests/test_carpark_plan.py`
- Test: `tests/test_ws_phase_config.py`
- Test: `tests/test_ws_phase.py`

- [ ] **Step 1: Patch only the five enabled carpark plans**

Set `cluster_min=5`, `cluster_scan.min_allies=5`, `excluded_levels=[1,2,3]`, and `allow_low_noncluster=false`. Remove 1/2/3 from `silver_levels`, `cluster_scan.levels`, and `priority_levels` wherever present.

- [ ] **Step 2: Run focused tests**

Run:

`python -m pytest tests/test_carpark_cluster_scan.py tests/test_carpark_runner_plan.py tests/test_carpark_plan.py tests/test_ws_phase_config.py tests/test_ws_phase.py -q`

Expected: all selected tests pass.

- [ ] **Step 3: Run syntax checks**

Run:

`python -m py_compile ws_token/carpark_plan.py ws_token/carpark.py ws_token/runner.py game_actions/ws_phase.py tests/test_carpark_cluster_scan.py tests/test_carpark_runner_plan.py tests/test_carpark_plan.py tests/test_ws_phase_config.py tests/test_ws_phase.py`

Expected: exit code 0 with no output.

- [ ] **Step 4: Inspect final diff**

Confirm only the plan/spec, five implementation/test files, and targeted `bot_config.json` carpark fields changed. Confirm unrelated pre-existing working-tree edits are untouched.
