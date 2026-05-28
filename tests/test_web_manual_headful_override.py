"""Regression tests for manual web launch with headless-configured devices."""
from __future__ import annotations

import importlib
import logging
import sys
import types

import pytest


@pytest.fixture
def web_session_service(monkeypatch):
    """Import target module without pulling heavy runtime deps like cv2/playwright."""
    stub_device_wrapper = types.ModuleType("device_wrapper")

    class _StubMonitoredDevice:
        def __init__(self, original_d, _ip):
            self._d = original_d

        def __getattr__(self, name):
            return getattr(self._d, name)

    stub_device_wrapper.MonitoredDevice = _StubMonitoredDevice
    stub_device_wrapper.close_all_web_devices = lambda logger_obj=None: None
    stub_device_wrapper.create_web_device_if_enabled = (
        lambda _ip, cfg=None, logger_obj=None: None
    )

    # Ensure runtime_services.web_session_service resolves our stubbed module
    # only for this test; leaking it globally breaks unrelated tests.
    monkeypatch.setitem(sys.modules, "device_wrapper", stub_device_wrapper)
    monkeypatch.delitem(sys.modules, "runtime_services.web_session_service", raising=False)
    return importlib.import_module("runtime_services.web_session_service")


class _FakeBotState:
    def __init__(self, req_payload):
        self._req_payload = req_payload
        self.updated = []
        self.completed = []
        self.manual_release = False

    def consume_web_launch_request(self, _ip):
        return {"payload": dict(self._req_payload)}

    def update_state(self, ip, **kwargs):
        self.updated.append((ip, kwargs))

    def complete_web_launch_request(self, ip, ok, message="", error=""):
        self.completed.append((ip, ok, message, error))

    def check_force_sleep(self, _ip):
        return False

    def check_manual_release(self, _ip):
        if self.manual_release:
            self.manual_release = False
            return True
        return False


class _FakeDevice:
    def __init__(self, alive_sequence=None):
        self.app_start_calls = []
        self.restore_calls = []
        self._alive_sequence = list(alive_sequence or [False])

    def app_start(self, *args, **kwargs):
        self.app_start_calls.append((args, dict(kwargs)))
        return True

    def restore_configured_headless_session(self, **kwargs):
        self.restore_calls.append(dict(kwargs))
        return True

    def is_alive(self):
        if self._alive_sequence:
            return bool(self._alive_sequence.pop(0))
        return False


def test_manual_web_launch_defaults_to_force_headful(monkeypatch, web_session_service):
    fake_state = _FakeBotState(req_payload={})
    fake_device = _FakeDevice()
    monkeypatch.setattr(web_session_service, "bot_state", fake_state)
    monkeypatch.setattr(web_session_service.time, "sleep", lambda _s: None)

    handled = web_session_service.handle_pending_web_launch(
        "emu-1",
        fake_device,
        backend_kind="web_h5",
        logger_obj=logging.getLogger("test"),
    )

    assert handled is True
    assert len(fake_device.app_start_calls) == 1
    _, kwargs = fake_device.app_start_calls[0]
    assert kwargs["force_headful"] is True
    assert kwargs["package_name"] == "com.mxdzz.tw.and"
    assert fake_state.completed and fake_state.completed[0][1] is True


def test_manual_web_launch_can_opt_out_of_force_headful(monkeypatch, web_session_service):
    fake_state = _FakeBotState(req_payload={"force_headful": False})
    fake_device = _FakeDevice()
    monkeypatch.setattr(web_session_service, "bot_state", fake_state)
    monkeypatch.setattr(web_session_service.time, "sleep", lambda _s: None)

    handled = web_session_service.handle_pending_web_launch(
        "emu-1",
        fake_device,
        backend_kind="web_h5",
        logger_obj=logging.getLogger("test"),
    )

    assert handled is True
    _, kwargs = fake_device.app_start_calls[0]
    assert kwargs["force_headful"] is False


def test_manual_hold_restores_configured_headless_after_release(monkeypatch, web_session_service):
    fake_state = _FakeBotState(
        req_payload={"manual_hold_until_closed": True, "force_headful": True}
    )
    fake_state.manual_release = True
    fake_device = _FakeDevice(alive_sequence=[True, True])
    monkeypatch.setattr(web_session_service, "bot_state", fake_state)
    monkeypatch.setattr(web_session_service.time, "sleep", lambda _s: None)

    handled = web_session_service.handle_pending_web_launch(
        "emu-1",
        fake_device,
        backend_kind="web_h5",
        logger_obj=logging.getLogger("test"),
    )

    assert handled is True
    assert len(fake_device.restore_calls) == 1
    assert fake_device.restore_calls[0]["reason"] == "manual web launch completed"
