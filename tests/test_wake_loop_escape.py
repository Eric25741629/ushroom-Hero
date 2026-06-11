"""Tests for the escape controls inside `utils.wake_up_handler`.

Regression: when a device (typically emulator-5558) is stuck in the
`等待放行` wait loop polling 5554's busy state every `online_check_interval`
minutes, pressing pause / force-sleep / open-web on the dashboard did
nothing — the wait loop had no escape checks. `_honor_dashboard_controls`
is called every second to honor those inputs.
"""
from __future__ import annotations

import sys
import types

import pytest


# Heavy deps aren't needed for this unit test — stub them out. Same pattern
# as tests/test_miner_executor_wait_frame_stable.py.
for stub_name in ("opencc", "paddleocr", "img_tools"):
    if stub_name not in sys.modules:
        stub = types.ModuleType(stub_name)
        if stub_name == "opencc":
            stub.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[stub_name] = stub

if "uiautomator2" not in sys.modules:
    sys.modules["uiautomator2"] = types.ModuleType("uiautomator2")

# Stub a bunch of modules wake_up_handler imports via its helpers so we can
# reach `_honor_dashboard_controls` without spinning up the real device
# stack.
for name in (
    "adb_operations",
    "device",
    "game_initialization",
    "config_manager",
):
    if name not in sys.modules:
        m = types.ModuleType(name)
        if name == "adb_operations":
            m.connect_u2_with_retries = lambda *a, **k: None
            m.get_battery_level = lambda *a, **k: 100
        if name == "device":
            m.close_notification = lambda *a, **k: None
        if name == "game_initialization":
            m.check_on_line = lambda *a, **k: False
        if name == "config_manager":
            m.get_device_config = lambda ip: {}
            m.get_global_config = lambda: {}
        sys.modules[name] = m

from runtime_services.device_runtime_service import (  # noqa: E402
    ForceSleepRequested,
    WakeLoopInterrupted,
)


class _FakeBotState:
    """Tracks calls against bot_state so tests can drive each scenario."""

    def __init__(
        self,
        force_sleep: bool = False,
        pending_web: bool = False,
        paused: bool = False,
    ):
        self._force_sleep = force_sleep
        self._pending_web = pending_web
        self._paused = paused
        self.check_pause_calls = 0
        self.check_force_sleep_calls = 0
        self.has_pending_web_calls = 0

    # API surface used by _honor_dashboard_controls
    def check_pause(self, ip):
        self.check_pause_calls += 1
        # Real impl blocks until event is set; for the test we just return.
        # If a test needs "paused blocks forever" behaviour we could add a
        # threading.Event, but the functions under test only ever call
        # check_pause and rely on *it* to block — nothing else to verify.
        return None

    def check_force_sleep(self, ip):
        self.check_force_sleep_calls += 1
        # Atomic-consume semantics: returns True once then False.
        if self._force_sleep:
            self._force_sleep = False
            return True
        return False

    def has_pending_web_launch_request(self, ip):
        self.has_pending_web_calls += 1
        return self._pending_web


@pytest.fixture
def fresh_handler(monkeypatch):
    """Import wake_up_handler with a fake bot_state injected."""
    fake_state = _FakeBotState()
    # wake_up_handler does `import bot_state` at top — patch before import.
    # teardown 還原原模組物件而非 pop（pop 會造成跨檔 bot_state 參照分裂，
    # 汙染之後才 lazy import device_wrapper 的測試，如 test_pause_routing）。
    orig_bot_state = sys.modules.get("bot_state")
    sys.modules.pop("utils.wake_up_handler", None)
    sys.modules["bot_state"] = fake_state  # type: ignore[assignment]
    import utils.wake_up_handler as wuh  # noqa: E402

    # Make sure the module picked up our fake.
    assert wuh.bot_state is fake_state
    yield wuh, fake_state
    sys.modules.pop("utils.wake_up_handler", None)
    if orig_bot_state is not None:
        sys.modules["bot_state"] = orig_bot_state
    else:
        sys.modules.pop("bot_state", None)


def test_honor_dashboard_controls_noop_when_idle(fresh_handler):
    wuh, state = fresh_handler
    # No flags set → returns without raising.
    wuh._honor_dashboard_controls("emulator-5558")
    assert state.check_pause_calls == 1
    assert state.check_force_sleep_calls == 1
    assert state.has_pending_web_calls == 1


def test_honor_dashboard_controls_raises_force_sleep(fresh_handler):
    wuh, state = fresh_handler
    state._force_sleep = True
    with pytest.raises(ForceSleepRequested):
        wuh._honor_dashboard_controls("emulator-5558")
    # check_pause was still called first (pause must be honored before
    # throwing — a paused thread shouldn't race ahead to raise).
    assert state.check_pause_calls == 1


def test_honor_dashboard_controls_raises_wake_loop_interrupted_on_web_launch(
    fresh_handler,
):
    wuh, state = fresh_handler
    state._pending_web = True
    with pytest.raises(WakeLoopInterrupted):
        wuh._honor_dashboard_controls("emulator-5558")
    assert state.check_pause_calls == 1
    # Force-sleep must be checked before web-launch so an explicit force-sleep
    # wins over a stale web-launch request.
    assert state.check_force_sleep_calls == 1


def test_force_sleep_beats_web_launch_when_both_pending(fresh_handler):
    wuh, state = fresh_handler
    state._force_sleep = True
    state._pending_web = True
    with pytest.raises(ForceSleepRequested):
        wuh._honor_dashboard_controls("emulator-5558")
    # Web-launch check should NOT fire once force-sleep already raised.
    assert state.has_pending_web_calls == 0


def test_check_pause_is_called_every_invocation(fresh_handler):
    # Even when other flags are clear, check_pause must still be called so
    # the thread honors a paused state at every poll tick.
    wuh, state = fresh_handler
    for _ in range(3):
        wuh._honor_dashboard_controls("emulator-5558")
    assert state.check_pause_calls == 3
