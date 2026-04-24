"""Characterization tests for utils.screenshot_helpers.

Locks the behavior moved out of new_main_v2.py in Phase 1 of the
refactor plan (see todolist.md). These tests should remain green across
subsequent phases — they describe the contract callers rely on:

  save_error_screenshot
    - returns the image path on successful capture
    - returns None when capture produced no path (empty/None)
    - returns None when capture raised, without propagating the exception

  log_main_page_mismatch
    - calls bot_state.update_state with task name + "未在主頁面: {stage}"
    - returns the path from _smart_shot.capture on success
    - falls back to save_error_screenshot when capture returned empty

Note: other tests (notably test_mining_item_logic.py) stub out
utils.logging_utils with `logger=None` in sys.modules. We inject a
fake logger per-test to stay robust against that pre-existing pollution.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from utils import screenshot_helpers


class _FakeRecorder:
    def __init__(self, return_value="/tmp/shot.png", raise_exc=None):
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def capture(self, *, device_obj, ip, stage, reason, task):
        self.calls.append({
            "device_obj": device_obj,
            "ip": ip,
            "stage": stage,
            "reason": reason,
            "task": task,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.return_value


@pytest.fixture
def fake_recorder(monkeypatch):
    rec = _FakeRecorder()
    monkeypatch.setattr(screenshot_helpers, "_smart_shot", rec)
    return rec


@pytest.fixture
def fake_bot_state(monkeypatch):
    calls: list[dict] = []

    def _update_state(ip, **kwargs):
        calls.append({"ip": ip, **kwargs})

    monkeypatch.setattr(screenshot_helpers.bot_state, "update_state", _update_state)
    return calls


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch):
    """Guard against sys.modules pollution from other test files that stub
    utils.logging_utils with `logger=None` (e.g. test_mining_item_logic.py)."""
    if getattr(screenshot_helpers, "logger", None) is None:
        monkeypatch.setattr(screenshot_helpers, "logger", logging.getLogger("test_screenshot_helpers"))


# ---------------------------------------------------------------------------
# save_error_screenshot
# ---------------------------------------------------------------------------

def test_save_error_screenshot_returns_path_on_success(fake_recorder):
    fake_recorder.return_value = "/screens/err1.png"
    result = screenshot_helpers.save_error_screenshot(
        device_obj=SimpleNamespace(), ip="emu-1", stage="主頁面", reason="測試"
    )
    assert result == "/screens/err1.png"
    assert len(fake_recorder.calls) == 1
    assert fake_recorder.calls[0]["stage"] == "主頁面"
    assert fake_recorder.calls[0]["task"] == ""  # always empty for error screenshots


def test_save_error_screenshot_returns_none_when_capture_returns_empty(fake_recorder):
    fake_recorder.return_value = None
    result = screenshot_helpers.save_error_screenshot(
        device_obj=SimpleNamespace(), ip="emu-1", stage="X", reason="no_path"
    )
    assert result is None


def test_save_error_screenshot_swallows_capture_exception(fake_recorder):
    fake_recorder.raise_exc = RuntimeError("disk full")
    result = screenshot_helpers.save_error_screenshot(
        device_obj=SimpleNamespace(), ip="emu-1", stage="X", reason="boom"
    )
    assert result is None  # exception is logged, not raised


# ---------------------------------------------------------------------------
# log_main_page_mismatch
# ---------------------------------------------------------------------------

def test_log_main_page_mismatch_updates_state_with_mismatch_step(fake_recorder, fake_bot_state):
    screenshot_helpers.log_main_page_mismatch(
        device_obj=SimpleNamespace(), ip="emu-2", stage="載入中",
        task="地獄之門", reason="到達執行時間但不在主頁面",
    )
    assert len(fake_bot_state) == 1
    call = fake_bot_state[0]
    assert call["ip"] == "emu-2"
    assert call["task"] == "地獄之門"
    assert call["step"] == "未在主頁面: 載入中"


def test_log_main_page_mismatch_returns_capture_path(fake_recorder, fake_bot_state):
    fake_recorder.return_value = "/screens/mismatch.png"
    result = screenshot_helpers.log_main_page_mismatch(
        device_obj=SimpleNamespace(), ip="emu-2", stage="商店",
        task="某任務", reason="前不在主頁面",
    )
    assert result == "/screens/mismatch.png"


def test_log_main_page_mismatch_falls_back_to_error_screenshot(fake_recorder, fake_bot_state):
    # First capture call (from log_main_page_mismatch) returns empty,
    # triggering fallback to save_error_screenshot → which calls capture
    # again with task="". Final result comes from the fallback.
    returns = iter([None, "/screens/fallback.png"])

    def _capture(**kwargs):
        val = next(returns)
        fake_recorder.calls.append(kwargs)
        return val

    fake_recorder.capture = _capture  # type: ignore[assignment]

    result = screenshot_helpers.log_main_page_mismatch(
        device_obj=SimpleNamespace(), ip="emu-2", stage="X",
        task="fallback_task", reason="trigger_fallback",
    )
    assert result == "/screens/fallback.png"
    assert len(fake_recorder.calls) == 2
    # Fallback call is the "error screenshot" path — task is forced to ""
    assert fake_recorder.calls[1]["task"] == ""
