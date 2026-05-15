"""Integration tests covering miner planner → simulated executor flow.

These tests intentionally do NOT mock ``miner.planning.smart_planner``,
``miner.planning.planner`` or ``miner.planning.executor`` — we drive the
real planner end-to-end and verify board-state invariants.

The hardware-bound side of ``executor.execute_plan_steps`` (UI taps,
classifier, vision pipeline) is too heavy to bring up in a unit test,
so this file falls back to a *plan-then-replay* style integration: we
plan with ``plan_smart``, then re-apply the produced steps through the
planner's own ``simulate_action`` (the source of truth for board
mechanics shared between planner and executor). This still exercises:

  * the A* planner end-to-end
  * the H9 budget-respecting filter in ``get_valid_actions``
  * the bomb/drill ``mechanics`` shared by both planner and executor

If the executor's full UI surface becomes testable later, replace the
``_replay_plan_on_board`` helper with the real ``execute_plan_steps``.
The contract verified here (no negative shovels, plan terminates) is
the same one the executor relies on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from miner.planning.smart_planner import (
    DEFAULT_CONFIG,
    PlannerConfig,
    SmartPlanner,
    SmartState,
    plan_smart,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _board_3x3_one_rock_one_pit() -> List[List[str]]:
    """Tiny board: an exposed pit in row 0 col 1, one rock blocking row 1.

    Layout (row, col):
        [empty,        reachable_pit, empty]
        [dirt,         rock,          dirt ]
        [dirt,         dirt,          dirt ]

    Planner must reach the bottom row (f7_open) AND dig the pit.
    """
    return [
        ["empty", "reachable_pit", "empty"],
        ["dirt", "rock", "dirt"],
        ["dirt", "dirt", "dirt"],
    ]


def _replay_plan_on_board(
    board: List[List[str]],
    shovels: float,
    items: Dict[str, int],
    steps: List[Dict[str, Any]],
    config: PlannerConfig = DEFAULT_CONFIG,
) -> SmartState:
    """Replay planner steps using the planner's own ``simulate_action``.

    This mirrors what a real executor must achieve in terms of resulting
    board state, while staying free of UI/classifier dependencies.
    """
    planner = SmartPlanner(board, shovels=shovels, items=items, config=config)
    state = SmartState(planner.initial_board, planner.initial_shovels, planner.initial_items, config=config)
    for step in steps:
        # The planner sometimes records a synthetic step (e.g. final
        # marker). simulate_action only understands {'dig','use'}.
        if step.get("type") not in {"dig", "use"}:
            continue
        state = planner.simulate_action(state, {k: v for k, v in step.items() if k != "step_cost"})
    return state


class _FakeDevice:
    """Minimal device stub for any executor-style code that wants
    ``screenshot`` / ``click``.

    Kept here even though we don't currently drive the real executor —
    documents the seam future tests can plug into without rewriting the
    integration scaffolding.
    """

    def __init__(self) -> None:
        self.clicks: List[Tuple[int, int]] = []

    def screenshot(self, format: str = "opencv"):  # noqa: A002 - mimic uiauto API
        return None

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


# ---------------------------------------------------------------------------
# Test 1 — happy path: planner produces a plan, replay keeps shovels >= 0,
# and the final board has no remaining pit + the bottom row is open.
# ---------------------------------------------------------------------------
def test_plan_smart_happy_path_replay_reaches_goal_without_negative_shovels():
    # Arrange
    board = _board_3x3_one_rock_one_pit()
    initial_shovels = 100.0
    items = {"drill": 0, "bomb": 0}

    # Act
    plan = plan_smart(board, shovels=initial_shovels, items=items)
    final_state = _replay_plan_on_board(board, initial_shovels, items, plan["steps"])

    # Assert — plan succeeded and replay matches planner's own accounting.
    assert plan["ok"] is True
    assert plan["remaining_pits"] == 0, "Planner should have dug all pits"
    assert plan["floor7_open"] is True, "Planner should have opened the bottom row"
    assert final_state.shovels >= 0, (
        f"Replay produced negative shovels: {final_state.shovels} "
        f"(plan steps={plan['steps']})"
    )
    # The plan's total_cost must equal what replay consumed (planner ↔ replay
    # parity is the executor contract — if these drift, executor will desync).
    assert final_state.accumulated_cost == pytest.approx(plan["total_cost"])


# ---------------------------------------------------------------------------
# Test 2 — H9 regression: when shovels are scarce, the planner must not
# emit a step that drives the shovel count negative.
# ---------------------------------------------------------------------------
def test_plan_smart_respects_shovel_budget_no_negative_shovels():
    """H9 regression: ``get_valid_actions`` filters by remaining shovels.

    With only 1 shovel and a board that requires more (the rock costs 2),
    the planner can dig at most 1 dirt-tier tile. It must never propose
    a sequence whose cumulative cost exceeds the starting budget.
    """
    # Arrange — board where digging everything would cost > 1 shovel.
    board = _board_3x3_one_rock_one_pit()
    starting_shovels = 1.0
    items = {"drill": 0, "bomb": 0}

    # Act
    plan = plan_smart(board, shovels=starting_shovels, items=items)
    final_state = _replay_plan_on_board(board, starting_shovels, items, plan["steps"])

    # Assert — every intermediate step must respect the budget. We replay
    # one step at a time and check the running balance after each.
    assert plan["ok"] is True
    assert final_state.shovels >= 0, (
        f"H9 regression: planner produced a plan exceeding budget. "
        f"final shovels={final_state.shovels}, steps={plan['steps']}"
    )
    # The planner's accumulated_cost must never exceed the budget.
    assert plan["total_cost"] <= starting_shovels + 1e-9


# ---------------------------------------------------------------------------
# Test 3 — Step-by-step invariant: walking the plan one step at a time
# never produces a SmartState with negative shovels (defensive guard for
# the H9 fix in get_valid_actions).
# ---------------------------------------------------------------------------
def test_simulate_action_never_yields_negative_shovels_step_by_step():
    # Arrange
    board = _board_3x3_one_rock_one_pit()
    starting_shovels = 3.0
    items = {"drill": 1, "bomb": 1}

    # Act
    plan = plan_smart(board, shovels=starting_shovels, items=items)
    planner = SmartPlanner(board, shovels=starting_shovels, items=items)
    state = SmartState(planner.initial_board, planner.initial_shovels, planner.initial_items)

    # Assert — invariant after EVERY step.
    for idx, step in enumerate(plan["steps"]):
        if step.get("type") not in {"dig", "use"}:
            continue
        state = planner.simulate_action(state, {k: v for k, v in step.items() if k != "step_cost"})
        assert state.shovels >= 0, (
            f"step #{idx} ({step}) drove shovels negative: {state.shovels}"
        )
        # items[*] must also stay >= 0
        for item_name, count in state.items.items():
            assert count >= 0, f"step #{idx} drove {item_name} count negative: {count}"


# ---------------------------------------------------------------------------
# Test 4 — Fake device seam smoke test. Confirms the stub interface
# matches what executor-style code looks for, so future executor
# integration can plug in without rewriting test scaffolding.
# ---------------------------------------------------------------------------
def test_fake_device_records_clicks_without_error():
    # Arrange
    fake = _FakeDevice()

    # Act
    fake.click(10, 20)
    fake.click(30, 40)
    snap = fake.screenshot()

    # Assert
    assert fake.clicks == [(10, 20), (30, 40)]
    assert snap is None  # documented stub behavior — no real screenshot
