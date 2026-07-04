"""Regression tests for wake-up ordering around Android launcher readiness."""
from __future__ import annotations

import sys
import types


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class _FakeBotState:
    def check_pause(self, ip):
        return None

    def check_force_sleep(self, ip):
        return False

    def has_pending_web_launch_request(self, ip):
        return False

    def update_state(self, *args, **kwargs):
        return None

    def is_online_check_checker(self, ip):
        return False


class _FakeDevice:
    serial = "fc65396d-test"

    def __init__(self, events):
        self.events = events
        self._screen_on = False

    @property
    def info(self):
        return {"screenOn": self._screen_on}

    def unlock(self):
        self.events.append("unlock")
        self._screen_on = True

    def swipe(self, *args, **kwargs):
        self.events.append("swipe")

    def press(self, key):
        self.events.append(f"press:{key}")

    def app_stop(self, package):
        self.events.append(f"app_stop:{package}")


def _import_wake_handler(monkeypatch, events):
    adb_operations = types.ModuleType("adb_operations")
    adb_operations.connect_u2_with_retries = lambda *args, **kwargs: None
    adb_operations.get_battery_level = lambda *args, **kwargs: 100
    adb_operations.ensure_on_launcher = (
        lambda *args, **kwargs: events.append("ensure_on_launcher") or True
    )
    monkeypatch.setitem(sys.modules, "adb_operations", adb_operations)

    device = types.ModuleType("device")
    device.close_notification = lambda d: events.append("close_notification")
    monkeypatch.setitem(sys.modules, "device", device)

    game_initialization = types.ModuleType("game_initialization")
    game_initialization.check_on_line = lambda *args, **kwargs: False
    monkeypatch.setitem(sys.modules, "game_initialization", game_initialization)

    config_manager = types.ModuleType("config_manager")
    config_manager.get_device_config = lambda ip: {}
    config_manager.get_global_config = lambda: {}
    monkeypatch.setitem(sys.modules, "config_manager", config_manager)

    monkeypatch.setitem(sys.modules, "bot_state", _FakeBotState())
    sys.modules.pop("utils.wake_up_handler", None)

    import utils.wake_up_handler as wake_up_handler

    return wake_up_handler


class _FakeWebDevice:
    """Mimics PlaywrightGameDevice: web_h5 backend, no `serial` attribute."""

    backend_kind = "web_h5"

    def __init__(self, events):
        self.events = events

    @property
    def info(self):
        return {"screenOn": True}

    def press(self, key):
        self.events.append(f"press:{key}")

    def app_stop(self, package):
        self.events.append(f"app_stop:{package}")

    def unlock(self):
        self.events.append("unlock")

    def swipe(self, *args, **kwargs):
        self.events.append("swipe")


def test_web_h5_wake_skips_launcher_check(monkeypatch):
    events = []
    wake_up_handler = _import_wake_handler(monkeypatch, events)
    monkeypatch.setattr(wake_up_handler.time, "sleep", lambda *args, **kwargs: None)

    device = _FakeWebDevice(events)
    result = wake_up_handler.handle_device_wakeup(
        device,
        "emulator-9999",
        _Logger(),
        Cnn_model=None,
    )

    assert result is device
    assert "ensure_on_launcher" not in events


def test_web_h5_wake_does_not_run_android_stop_cleanup(monkeypatch):
    events = []
    wake_up_handler = _import_wake_handler(monkeypatch, events)
    monkeypatch.setattr(wake_up_handler.time, "sleep", lambda *args, **kwargs: None)

    device = _FakeWebDevice(events)
    result = wake_up_handler.handle_device_wakeup(
        device,
        "emulator-9999",
        _Logger(),
        Cnn_model=None,
    )

    assert result is device
    assert not any(event.startswith("app_stop:") for event in events)
    assert "close_notification" not in events


def test_phone_wake_waits_for_launcher_before_notification_cleanup(monkeypatch):
    events = []
    wake_up_handler = _import_wake_handler(monkeypatch, events)
    monkeypatch.setattr(wake_up_handler.time, "sleep", lambda *args, **kwargs: None)

    device = _FakeDevice(events)
    result = wake_up_handler.handle_device_wakeup(
        device,
        "fc65396d-test",
        _Logger(),
        Cnn_model=None,
    )

    assert result is device
    game_stop_idx = events.index("app_stop:com.mxdzz.tw.and")
    first_launcher_idx = events.index("ensure_on_launcher")
    notification_idx = events.index("close_notification")
    second_launcher_idx = events.index("ensure_on_launcher", first_launcher_idx + 1)
    messenger_stop_idx = events.index("app_stop:com.facebook.orca")

    assert events[game_stop_idx + 1:first_launcher_idx] == [
        "press:home",
        "press:home",
        "press:home",
    ]
    assert events[notification_idx + 1:second_launcher_idx] == [
        "press:home",
        "press:home",
        "press:home",
    ]
    assert game_stop_idx < first_launcher_idx < notification_idx < second_launcher_idx < messenger_stop_idx
