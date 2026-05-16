"""ExecutionResult contract for `execute_plan_steps`.

The executor used to return `None`. The mining loop therefore had no way
to know how many shovels were actually consumed during a plan, and
relied entirely on a periodic OCR re-read to track the pickaxe count.

These tests pin the new contract:

  * `execute_plan_steps` returns an `ExecutionResult` dataclass with
    `shovels_used`, `drills_used`, `bombs_used`, `steps_completed`, and
    `terminated_reason`.
  * `NoBoardChangeError` and `OutOfItemError` carry a `partial_result`
    attribute so the caller can still credit any shovels consumed before
    the exception.
  * The early-return paths (deadline, verify-fail, floor7, invalid
    placement) all produce a complete `ExecutionResult`.

Each test stubs the device / classifier / animation helpers so the
runtime can drive the real executor logic in-process without ADB.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Dict, List, Optional

import numpy as np
import pytest


# ----- light-weight import shim (mirrors test_miner_executor_wait_frame_stable) -----
for _stub_name in ("opencc", "paddleocr", "img_tools"):
    if _stub_name not in sys.modules:
        _stub = types.ModuleType(_stub_name)
        if _stub_name == "opencc":
            _stub.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[_stub_name] = _stub

if "tools" not in sys.modules:
    _tools_stub = types.ModuleType("tools")
    _tools_stub.click_white = lambda *a, **kw: None
    sys.modules["tools"] = _tools_stub

from miner.planning import executor  # noqa: E402
from miner.planning.executor import (  # noqa: E402
    ExecutionResult,
    NoBoardChangeError,
    OutOfItemError,
    execute_plan_steps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _blank_frame() -> np.ndarray:
    return np.zeros((960, 540, 3), dtype=np.uint8)


def _empty_board(rows: int = 7, cols: int = 6) -> List[List[str]]:
    return [["empty"] * cols for _ in range(rows)]


class _FakeDevice:
    def __init__(self, frame: np.ndarray):
        self._frame = frame
        self.clicks: List[Any] = []

    def screenshot(self, format: str = "opencv"):  # noqa: A002
        return self._frame

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def sleep(self, _s: float) -> None:
        pass


class _FakeClassifier:
    """Returns a programmable post-action board."""

    def __init__(self, board: List[List[str]]):
        self._board = board
        rows = len(board)
        cols = len(board[0]) if board else 0
        self._conf = [[1.0] * cols for _ in range(rows)]

    def classify_board(self, frame, save_samples: bool = False):
        return [row[:] for row in self._board], self._conf


def _stub_animation(monkeypatch, frame):
    """Stub all animation / vision helpers used inside execute_plan_steps."""
    monkeypatch.setattr(executor, "wait_frame_stable", lambda *a, **kw: frame)
    monkeypatch.setattr(executor, "check_points", lambda *a, **kw: (False, None))
    monkeypatch.setattr(executor.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(executor, "tap_cell", lambda *a, **kw: None)
    monkeypatch.setattr(executor, "click_white", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------
def test_execution_result_empty_plan(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    board = _empty_board()
    clf = _FakeClassifier(board)
    _stub_animation(monkeypatch, frame)

    result = execute_plan_steps(dev, clf, board, [])

    assert isinstance(result, ExecutionResult)
    assert result.shovels_used == 0
    assert result.drills_used == 0
    assert result.bombs_used == 0
    assert result.steps_completed == 0
    assert result.terminated_reason is None


# ---------------------------------------------------------------------------
# Single dig — dirt costs 1 shovel
# ---------------------------------------------------------------------------
def test_execution_result_single_dirt_dig(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[3][3] = "dirt"
    post = _empty_board()  # after dig: empty
    clf = _FakeClassifier(post)

    _stub_animation(monkeypatch, frame)
    monkeypatch.setattr(executor, "verify_cell_empty", lambda *a, **kw: True)

    plan: List[Dict[str, Any]] = [
        {
            "type": "dig",
            "pos": (3, 3),
            "dig_list": [(3, 3)],
            "action": "dig",
            "target": (3, 3),
            "step_cost": 1.0,
        }
    ]
    result = execute_plan_steps(dev, clf, pre, plan)

    assert result.shovels_used == 1
    assert result.steps_completed == 1
    assert result.terminated_reason is None
    assert result.drills_used == 0
    assert result.bombs_used == 0


def test_execution_result_single_rock_dig_costs_two(monkeypatch):
    """Rock takes 2 hits; each click is a shovel — shovels_used == 2."""
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[3][3] = "rock"
    post = _empty_board()
    clf = _FakeClassifier(post)

    _stub_animation(monkeypatch, frame)
    monkeypatch.setattr(executor, "verify_cell_empty", lambda *a, **kw: True)

    plan = [
        {
            "type": "dig",
            "pos": (3, 3),
            "dig_list": [(3, 3)],
            "action": "dig",
            "target": (3, 3),
            "step_cost": 2.0,
        }
    ]
    result = execute_plan_steps(dev, clf, pre, plan)

    assert result.shovels_used == 2
    assert result.steps_completed == 1


# ---------------------------------------------------------------------------
# Floor7 termination — dig at row 6 should return a result with the correct
# termination reason.
# ---------------------------------------------------------------------------
def test_execution_result_row6_dig_terminates_with_floor7(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[6][2] = "dirt"
    post = _empty_board()
    clf = _FakeClassifier(post)

    _stub_animation(monkeypatch, frame)
    monkeypatch.setattr(executor, "verify_cell_empty", lambda *a, **kw: True)

    plan = [
        {
            "type": "dig",
            "pos": (6, 2),
            "dig_list": [(6, 2)],
            "action": "dig",
            "target": (6, 2),
            "step_cost": 1.0,
        }
    ]
    result = execute_plan_steps(dev, clf, pre, plan)

    assert result.terminated_reason == "floor7"
    assert result.shovels_used == 1   # the dig itself was consumed


# ---------------------------------------------------------------------------
# Partial result on exception
# ---------------------------------------------------------------------------
def test_no_board_change_error_carries_partial_result(monkeypatch):
    """If the executor gives up mid-plan, the consumed shovels must still
    be accounted for via NoBoardChangeError.partial_result."""
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[3][3] = "dirt"
    pre[3][4] = "dirt"
    # Classifier returns same board → triggers NoBoardChangeError after dig
    clf = _FakeClassifier(pre)

    _stub_animation(monkeypatch, frame)
    monkeypatch.setattr(executor, "verify_cell_empty", lambda *a, **kw: True)

    plan = [
        {
            "type": "dig",
            "pos": (3, 3),
            "dig_list": [(3, 3)],
            "action": "dig",
            "target": (3, 3),
            "step_cost": 1.0,
        }
    ]
    with pytest.raises(NoBoardChangeError) as excinfo:
        execute_plan_steps(dev, clf, pre, plan)

    partial = excinfo.value.partial_result
    assert isinstance(partial, ExecutionResult)
    # The dig click happened → shovel was consumed by the game even though
    # the board didn't change.
    assert partial.shovels_used == 1
    assert partial.terminated_reason == "no_board_change"


# ---------------------------------------------------------------------------
# Invalid item placement → early return with explicit reason
# ---------------------------------------------------------------------------
def test_execution_result_invalid_item_placement(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[3][3] = "dirt"   # not an "empty" / "dug_pit" — not placeable
    clf = _FakeClassifier(pre)

    _stub_animation(monkeypatch, frame)

    plan = [
        {
            "type": "use",
            "item": "bomb",
            "pos": (3, 3),
            "action": "use_bomb",
            "target": (3, 3),
            "step_cost": 3.0,
        }
    ]
    result = execute_plan_steps(dev, clf, pre, plan)

    assert result.shovels_used == 0
    assert result.bombs_used == 0
    assert result.terminated_reason == "item_placement_invalid"


# ---------------------------------------------------------------------------
# Deadline termination
# ---------------------------------------------------------------------------
def test_execution_result_deadline(monkeypatch):
    import time as _time

    frame = _blank_frame()
    dev = _FakeDevice(frame)
    pre = _empty_board()
    pre[3][3] = "dirt"
    clf = _FakeClassifier(pre)

    _stub_animation(monkeypatch, frame)

    # Past-deadline → executor should bail on first step. Use a real
    # past unix timestamp (not 0.0) so the executor's `if deadline and ...`
    # truthiness guard doesn't short-circuit the check.
    past_deadline = _time.time() - 1
    plan = [
        {
            "type": "dig",
            "pos": (3, 3),
            "dig_list": [(3, 3)],
            "action": "dig",
            "target": (3, 3),
            "step_cost": 1.0,
        }
    ]
    result = execute_plan_steps(dev, clf, pre, plan, deadline=past_deadline)

    assert result.shovels_used == 0
    assert result.steps_completed == 0
    assert result.terminated_reason == "deadline"
