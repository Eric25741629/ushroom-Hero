"""Tests for dashboard-control escape inside ``handle_game_startup_pages``.

Regression: the 啟動 loop (`game_initialization.handle_game_startup_pages`) is a
`while True` that only inspects the game's on-screen stage. When the user wanted
to take over the phone — pressing 暫停 / 強制休眠 on the dashboard, or manually
killing the game — the loop ignored those signals and force-relaunched the game,
because:

  1. it never honored dashboard controls at the top of the loop, and
  2. its broad ``except Exception`` swallowed the control-flow exceptions
     (`ForceSleepRequested` / `WakeLoopInterrupted`) that `MonitoredDevice`'s
     pause-guard raises, treating them as a startup failure → relaunch.

These tests pin the fixed contract: force-sleep / web-launch unwind to the main
loop (raise), and check_pause is polled every iteration.
"""
from __future__ import annotations

import importlib
import logging
import sys
import types

import pytest

from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    WakeLoopInterrupted,
)


def _install_startup_import_stubs(monkeypatch):
    """Stub the heavy modules `game_initialization` imports at module load.

    Mirrors tests/test_game_initialization.py so the module imports without a
    real device / OCR / Playwright stack. bot_state and config_manager stay
    real (lightweight); each test monkeypatches the bot_state control hooks it
    needs.
    """
    img_tools = types.ModuleType("img_tools")
    img_tools.click_str_by_server = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "img_tools", img_tools)

    tools = types.ModuleType("tools")
    tools.click_white = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "tools", tools)

    adb_operations = types.ModuleType("adb_operations")
    adb_operations.connect_u2_with_retries = lambda *args, **kwargs: None
    adb_operations.start_game_by_icon = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "adb_operations", adb_operations)

    detector = types.ModuleType("game_state.detector")
    detector.get_stage = lambda *args, **kwargs: "主頁面"
    monkeypatch.setitem(sys.modules, "game_state.detector", detector)

    reward_manager = types.ModuleType("game_actions.reward_manager")
    reward_manager.reward = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "game_actions.reward_manager", reward_manager)

    cnn_model = types.ModuleType("new_cnn.cnn_model")
    monkeypatch.setitem(sys.modules, "new_cnn.cnn_model", cnn_model)

    logging_utils = types.ModuleType("utils.logging_utils")
    logging_utils.logger = logging.getLogger("test_startup_loop_escape")
    logging_utils.default_logger = logging_utils.logger
    monkeypatch.setitem(sys.modules, "utils.logging_utils", logging_utils)

    device_wrapper = types.ModuleType("device_wrapper")
    device_wrapper.create_web_device_if_enabled = lambda *args, **kwargs: None
    device_wrapper.get_alive_web_device = lambda *args, **kwargs: None
    device_wrapper.get_same_thread_web_device = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "device_wrapper", device_wrapper)


def _startup_module(monkeypatch):
    _install_startup_import_stubs(monkeypatch)
    monkeypatch.delitem(sys.modules, "game_initialization", raising=False)
    return importlib.import_module("game_initialization")


class _FakeDevice:
    """Records the side-effecting device calls the relaunch path would make."""

    def __init__(self):
        self.app_stop_calls = []
        self.press_calls = []

    def app_stop(self, pkg=None, *args, **kwargs):
        self.app_stop_calls.append(pkg)

    def press(self, key, *args, **kwargs):
        self.press_calls.append(key)

    def screenshot(self, *args, **kwargs):
        return None


def _set_controls(monkeypatch, startup, *, pause=None, force_sleep=False, web_launch=False):
    """Wire startup.bot_state control hooks; return a dict of call counters."""
    counters = {"pause": 0, "force_sleep": 0, "web_launch": 0}

    def _check_pause(ip):
        counters["pause"] += 1
        if callable(pause):
            return pause(ip)
        return None

    monkeypatch.setattr(startup.bot_state, "check_pause", _check_pause)
    monkeypatch.setattr(
        startup.bot_state,
        "check_force_sleep",
        lambda ip: (counters.__setitem__("force_sleep", counters["force_sleep"] + 1) or force_sleep),
    )
    monkeypatch.setattr(
        startup.bot_state,
        "has_pending_web_launch_request",
        lambda ip: (counters.__setitem__("web_launch", counters["web_launch"] + 1) or web_launch),
    )
    return counters


def _run(startup, d, start_calls):
    return startup.handle_game_startup_pages(
        d=d,
        ip="fc65396d",
        start_game_fn=lambda *a, **k: start_calls.append(1),
        reward_fn=lambda *a, **k: None,
        logger=logging.getLogger("test_startup_loop_escape"),
    )


def test_force_sleep_at_loop_top_raises_and_skips_relaunch(monkeypatch):
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    _set_controls(monkeypatch, startup, force_sleep=True)
    monkeypatch.setattr(startup, "resolve_stage_until_stable", lambda *a, **k: "主頁面")

    start_calls = []
    d = _FakeDevice()
    with pytest.raises(ForceSleepRequested):
        _run(startup, d, start_calls)

    # Force-sleep must abort the startup loop, not relaunch the game.
    assert start_calls == []
    assert d.app_stop_calls == []


def test_web_launch_at_loop_top_raises_wake_loop_interrupted(monkeypatch):
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    _set_controls(monkeypatch, startup, web_launch=True)
    monkeypatch.setattr(startup, "resolve_stage_until_stable", lambda *a, **k: "主頁面")

    start_calls = []
    d = _FakeDevice()
    with pytest.raises(WakeLoopInterrupted):
        _run(startup, d, start_calls)

    assert start_calls == []


def test_force_sleep_from_stage_probe_propagates_not_swallowed(monkeypatch):
    """A ForceSleepRequested raised mid-loop (e.g. by the guarded screenshot in
    get_stage) must propagate, not be caught-and-relaunched."""
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    _set_controls(monkeypatch, startup)

    calls = {"n": 0}

    def fake_resolve(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ForceSleepRequested("force sleep from guarded screenshot")
        return "主頁面"

    monkeypatch.setattr(startup, "resolve_stage_until_stable", fake_resolve)

    start_calls = []
    d = _FakeDevice()
    with pytest.raises(ForceSleepRequested):
        _run(startup, d, start_calls)

    # Must NOT have fallen into the except→app_stop→relaunch path.
    assert start_calls == []


def test_unbounded_relaunch_is_capped_and_returns_false(monkeypatch):
    """Regression: handle_game_startup_pages had no global restart ceiling.

    The only timeout (wait_timeout=60s) is reset (`wait_time = time.time()`)
    after every relaunch, so it never fires. When the stage probe keeps failing
    (e.g. web_h5 page repeatedly closed by a same-account login conflict), the
    `except Exception` path relaunches forever — the live 7fe98fc6 log showed
    restart_count=105. The loop must give up after a bounded number of restarts
    and return False so the main loop applies the 30-minute avoidance sleep.
    """
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(startup.random, "randint", lambda *_a, **_k: 0)
    _set_controls(monkeypatch, startup)

    # Every stage probe blows up → each iteration hits the except→relaunch path.
    def boom(*_a, **_k):
        raise RuntimeError("stage probe failed")

    monkeypatch.setattr(startup, "resolve_stage_until_stable", boom)

    # Safety cap turns an unbounded loop into a deterministic failure instead of
    # hanging the test runner: on unfixed code start_game_fn is called forever.
    SAFETY_CAP = 50
    start_calls = []

    def start_fn(*_a, **_k):
        start_calls.append(1)
        if len(start_calls) > SAFETY_CAP:
            raise AssertionError(
                "startup loop relaunched past the safety cap — no restart ceiling"
            )

    d = _FakeDevice()
    result = startup.handle_game_startup_pages(
        d=d,
        ip="fc65396d",
        start_game_fn=start_fn,
        reward_fn=lambda *a, **k: None,
        logger=logging.getLogger("test_startup_loop_escape"),
    )

    assert result is False
    assert len(start_calls) <= SAFETY_CAP


def test_clear_controls_reaches_main_page_and_polls_pause(monkeypatch):
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    counters = _set_controls(monkeypatch, startup)
    monkeypatch.setattr(startup, "resolve_stage_until_stable", lambda *a, **k: "主頁面")

    start_calls = []
    d = _FakeDevice()
    result = _run(startup, d, start_calls)

    assert result is True
    # check_pause polled every iteration (2 main-page confirmations needed).
    assert counters["pause"] >= 2


def test_h5_state_unavailable_stops_startup_without_legacy_resolver(monkeypatch):
    """Web H5 unknown/Cocos failure must not be converted into OCR startup."""
    startup = _startup_module(monkeypatch)
    monkeypatch.setattr(startup.time, "sleep", lambda *_a, **_k: None)
    _set_controls(monkeypatch, startup)

    from utils.page_detector import H5State, H5StateResult

    monkeypatch.setattr(
        "utils.page_detector.probe_h5_state",
        lambda *_a, **_k: H5StateResult(
            H5State.H5_STATE_UNAVAILABLE,
            reason="unknown_cocos_state",
        ),
    )

    def _must_not_resolve(*_args, **_kwargs):
        raise AssertionError("H5 startup must not enter legacy OCR resolver")

    monkeypatch.setattr(startup, "resolve_stage_until_stable", _must_not_resolve)
    d = _FakeDevice()
    d.backend_kind = "web_h5"
    d._page = object()

    assert _run(startup, d, []) is False
