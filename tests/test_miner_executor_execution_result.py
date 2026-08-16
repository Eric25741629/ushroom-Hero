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
        self.backend_kind = "adb"

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


def test_get_live_item_count_prefers_ws_for_web_h5(monkeypatch):
    """5558 regression: executor 不可用 OCR=0 否決 WS 已確認的 bomb=12。"""
    dev = _FakeDevice(_blank_frame())
    monkeypatch.setattr(
        executor,
        "read_ws_prop_counts",
        lambda _d: {"pickaxe": 39, "drill": 0, "bomb": 12},
    )
    monkeypatch.setattr(
        executor,
        "check_boom_num",
        lambda *_a, **_kw: pytest.fail("web_h5 WS 可用時不應呼叫 bomb OCR"),
    )

    assert executor.get_live_item_count(dev, "bomb") == 12


def test_get_live_item_count_falls_back_to_ocr_when_ws_unavailable(monkeypatch):
    dev = _FakeDevice(_blank_frame())
    monkeypatch.setattr(executor, "read_ws_prop_counts", lambda _d: None)
    monkeypatch.setattr(executor, "check_boom_num", lambda *_a, **_kw: 7)

    assert executor.get_live_item_count(dev, "bomb") == 7


def test_ws_refresh_failed_is_not_a_success_confirmation(monkeypatch):
    """Executor WS refresh failure must reach item retry/blacklist logic."""
    before_board = types.SimpleNamespace(
        baseline=100, actives=[], blocks=[], holes=[], area=1,
    )
    inventory = {"pickaxe": 4, "drill": 1, "bomb": 1}
    monkeypatch.setattr(executor, "read_ws_mine_board", lambda _d: None)
    monkeypatch.setattr(executor, "read_ws_prop_counts", lambda _d: dict(inventory))
    monkeypatch.setattr(executor.time, "sleep", lambda *_a, **_kw: None)

    event = executor._verify_ws_action(
        object(), before_board,
        {"type": "dig", "block_id": 10001}, inventory,
        max_retry=1,
    )

    assert event["confirmation"] == "refresh_failed"
    assert event["success"] is False


def test_verify_cell_empty_accepts_low_confidence_empty_without_retry_click(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    board = _empty_board()
    clf = _FakeClassifier(board)
    clf._conf[3][3] = 0.51
    monkeypatch.setattr(executor, "check_points", lambda *_a, **_kw: (False, None))

    assert executor.verify_cell_empty(dev, clf, 3, 3, max_retry=1) is True
    assert dev.clicks == []


def test_h5_dig_uses_ws_confirmation_and_inventory_without_cnn_retry(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    dev.backend_kind = "web_h5"
    pre = _empty_board()
    pre[3][3] = "dirt"
    clf = _FakeClassifier(pre)  # CNN 故意仍判 dirt
    _stub_animation(monkeypatch, frame)
    ws_before = types.SimpleNamespace(baseline=100, actives=[], blocks=[], holes=[], area=1)
    ws_after = types.SimpleNamespace(baseline=101, actives=[], blocks=[], holes=[], area=1)
    boards = iter([ws_before, ws_after])
    inventories = iter([
        {"pickaxe": 5, "drill": 1, "bomb": 1},
        {"pickaxe": 4, "drill": 1, "bomb": 1},
    ])
    monkeypatch.setattr(executor, "read_ws_mine_board", lambda _d: next(boards))
    monkeypatch.setattr(executor, "read_ws_prop_counts", lambda _d: next(inventories))
    monkeypatch.setattr(
        executor,
        "verify_cell_empty",
        lambda *a, **kw: pytest.fail("H5 WS 可用時不應呼叫 CNN verify"),
    )

    result = execute_plan_steps(dev, clf, pre, [{
        "type": "dig", "pos": (3, 3), "target": (3, 3),
        "action": "dig", "dig_list": [(3, 3)], "step_cost": 1.0,
    }])

    assert result.shovels_used == 1
    assert result.pickaxe_count_after == 4
    assert result.terminated_reason is None
    assert result.verification_events[0]["confirmation"] == "baseline_changed"


def test_h5_floor7_dig_accounts_authoritative_inventory(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    dev.backend_kind = "web_h5"
    pre = _empty_board()
    pre[6][2] = "dirt"
    clf = _FakeClassifier(_empty_board())
    _stub_animation(monkeypatch, frame)
    ws_before = types.SimpleNamespace(baseline=100, actives=[], blocks=[], holes=[], area=1)
    ws_after = types.SimpleNamespace(baseline=101, actives=[], blocks=[], holes=[], area=1)
    boards = iter([ws_before, ws_after])
    inventories = iter([
        {"pickaxe": 2, "drill": 1, "bomb": 1},
        {"pickaxe": 1, "drill": 1, "bomb": 1},
    ])
    monkeypatch.setattr(executor, "read_ws_mine_board", lambda _d: next(boards))
    monkeypatch.setattr(executor, "read_ws_prop_counts", lambda _d: next(inventories))

    result = execute_plan_steps(dev, clf, pre, [{
        "type": "dig", "pos": (6, 2), "target": (6, 2),
        "action": "dig", "dig_list": [(6, 2)], "step_cost": 1.0,
    }])

    assert result.terminated_reason == "floor7"
    assert result.shovels_used == 1
    assert result.pickaxe_count_after == 1


def test_h5_dispatch_uses_javascript_executor_instead_of_pixel_click(monkeypatch):
    calls = []
    dev = _FakeDevice(_blank_frame())
    dev.backend_kind = "web_h5"
    dev._page = object()
    before = types.SimpleNamespace(baseline=100)

    class FakeH5Executor:
        def __init__(self, page):
            assert page is dev._page

        def use_pickaxe(self, block_id):
            calls.append(("pickaxe", block_id))

        def use_drill(self, block_id):
            calls.append(("drill", block_id))

        def use_bomb(self, block_id):
            calls.append(("bomb", block_id))

    monkeypatch.setattr(
        "ws_token.mining_h5_executor.H5MiningExecutor", FakeH5Executor
    )

    dispatched = executor._dispatch_h5_ws_action(
        dev,
        before,
        {"type": "use", "item": "drill", "target": (2, 3)},
    )

    assert dispatched is True
    assert calls == [("drill", 9704)]
    assert dev.clicks == []


def test_h5_dig_rejects_non_active_target_before_sending(monkeypatch):
    calls = []
    dev = _FakeDevice(_blank_frame())
    dev.backend_kind = "web_h5"
    dev._page = object()
    before = types.SimpleNamespace(
        baseline=100, actives=[9804], blocks=[], holes=[], area=1
    )

    class FakeH5Executor:
        def __init__(self, _page):
            pass

        def use_pickaxe(self, block_id):
            calls.append(block_id)
            return {"ok": True}

    monkeypatch.setattr(
        "ws_token.mining_h5_executor.H5MiningExecutor", FakeH5Executor
    )

    with pytest.raises(NoBoardChangeError) as excinfo:
        executor._dispatch_h5_ws_action(
            dev,
            before,
            {"type": "dig", "target": (3, 4)},
        )

    assert calls == []
    assert excinfo.value.diagnostics == {
        "phase": "h5_preflight",
        "validation": "not_active",
        "block_id": 9805,
        "baseline": 100,
        "active_count": 1,
        "block_count": None,
    }


def test_h5_dig_rejects_already_dug_active_target_before_sending(monkeypatch):
    calls = []
    dev = _FakeDevice(_blank_frame())
    dev.backend_kind = "web_h5"
    dev._page = object()
    before = types.SimpleNamespace(
        baseline=100,
        actives=[9804],
        blocks=[types.SimpleNamespace(block_id=9804, count=0)],
        holes=[],
        area=1,
    )

    class FakeH5Executor:
        def __init__(self, _page):
            pass

        def use_pickaxe(self, block_id):
            calls.append(block_id)
            return {"ok": True}

    monkeypatch.setattr(
        "ws_token.mining_h5_executor.H5MiningExecutor", FakeH5Executor
    )

    with pytest.raises(NoBoardChangeError) as excinfo:
        executor._dispatch_h5_ws_action(
            dev,
            before,
            {"type": "dig", "target": (3, 3)},
        )

    assert calls == []
    assert excinfo.value.diagnostics["validation"] == "already_dug"
    assert excinfo.value.diagnostics["block_count"] == 0


def test_h5_dig_surfaces_server_error_without_fallback_click(monkeypatch):
    calls = []
    dev = _FakeDevice(_blank_frame())
    dev.backend_kind = "web_h5"
    dev._page = object()
    before = types.SimpleNamespace(
        baseline=100, actives=[9804], blocks=[], holes=[], area=1
    )

    class FakeH5Executor:
        def __init__(self, _page):
            pass

        def use_pickaxe(self, block_id):
            calls.append(block_id)
            return {
                "ok": False,
                "response_cmd": 0x0201,
                "error_code": 71,
                "raw_body_hex": "0847",
            }

    monkeypatch.setattr(
        "ws_token.mining_h5_executor.H5MiningExecutor", FakeH5Executor
    )

    with pytest.raises(NoBoardChangeError) as excinfo:
        executor._dispatch_h5_ws_action(
            dev,
            before,
            {"type": "dig", "target": (3, 3)},
        )

    assert calls == [9804]
    assert excinfo.value.diagnostics["phase"] == "h5_server_response"
    assert excinfo.value.diagnostics["error_code"] == 71
    assert excinfo.value.diagnostics["raw_body_hex"] == "0847"


def test_h5_does_not_pixel_fallback_when_authoritative_board_is_unavailable(monkeypatch):
    frame = _blank_frame()
    dev = _FakeDevice(frame)
    dev.backend_kind = "web_h5"
    dev._page = object()
    board = _empty_board()
    board[3][3] = "dirt"
    clf = _FakeClassifier(board)
    _stub_animation(monkeypatch, frame)
    monkeypatch.setattr(executor, "read_ws_mine_board", lambda _d: None)
    monkeypatch.setattr(executor, "tap_cell", lambda *a, **kw: pytest.fail("H5 不應退回像素點擊"))

    with pytest.raises(NoBoardChangeError) as excinfo:
        execute_plan_steps(dev, clf, board, [{
            "type": "dig", "pos": (3, 3), "target": (3, 3),
            "action": "dig", "dig_list": [(3, 3)], "step_cost": 1.0,
        }])

    assert excinfo.value.partial_result.shovels_used == 0
    assert excinfo.value.diagnostics["validation"] == "board_unavailable"


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
