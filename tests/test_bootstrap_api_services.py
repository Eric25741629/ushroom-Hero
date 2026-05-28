"""Characterization tests for bootstrap.api_services (Phase 10).

Tests written BEFORE extraction (expected RED until bootstrap/api_services.py exists).

  start_all(mode, base_dir):
    - master: calls ensure_push_server_started + starts Flask thread on port 5002
    - worker: calls ensure_push_server_started + webhook + sync
    - master does NOT call ensure_worker_webhook_started or ensure_worker_sync_started

  scan_loop(main_fn, running_threads, Cnn_model, oracle_cnn_model, oracle_classes, ocr, log):
    - calls scan_and_start_devices each iteration
    - exits cleanly on KeyboardInterrupt
"""
from __future__ import annotations

import sys
import threading
import types
from types import SimpleNamespace

import pytest

# Guard: import the real utils.logging_utils NOW (at collection time) so that
# test_mining_item_logic.py's module-level stub ("if not in sys.modules: set
# logger=None") cannot replace it before bootstrap tests execute.
import utils.logging_utils as _logging_utils_guard  # noqa: F401


# ---------------------------------------------------------------------------
# Stubs for heavy transitive deps pulled in via bootstrap.api_services
# ---------------------------------------------------------------------------

# device_scan_service → device.py → uiautomator2
if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

if "device" not in sys.modules:
    _dev = types.ModuleType("device")
    _dev.get_adb_devices = lambda *a, **k: []
    _dev.open_notification = lambda *a, **k: None
    _dev.close_notification = lambda *a, **k: None
    sys.modules["device"] = _dev

# control_panel_app: lazily imported inside start_all (master branch).
# Stub to prevent its deep import chain (opencc, img_tools, …).
if "control_panel_app" not in sys.modules:
    _cpa = types.ModuleType("control_panel_app")
    _cpa.run_server = lambda port: None
    sys.modules["control_panel_app"] = _cpa

# worker_webhook_api: lazily imported inside start_all (worker branch).
# Stub to prevent its dependency on adb_operations.reset_screen_settings
# which may not be present in the adb_operations stub set by earlier tests.
if "worker_webhook_api" not in sys.modules:
    _wwh = types.ModuleType("worker_webhook_api")
    _wwh.ensure_worker_webhook_started = lambda: None
    _wwh.apply_remote_commands = lambda *a, **kw: None
    _wwh.normalize_master_url = lambda url: url
    _wwh.resolve_worker_webhook_url = lambda *a, **kw: ""
    sys.modules["worker_webhook_api"] = _wwh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_mod():
    import importlib
    return importlib.import_module("bootstrap.api_services")


# ---------------------------------------------------------------------------
# start_all — master mode
# ---------------------------------------------------------------------------

def test_start_all_master_calls_push_server(api_mod, monkeypatch):
    """master mode: ensure_push_server_started is called with base_dir."""
    calls: list = []
    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda base_dir: calls.append(base_dir))
    monkeypatch.setattr(threading, "Thread", lambda target, args=(), daemon=False: SimpleNamespace(start=lambda: None))
    api_mod.start_all("master", "/fake/base")
    assert calls == ["/fake/base"]


def test_start_all_master_starts_flask_thread(api_mod, monkeypatch):
    """master mode: a daemon thread targeting control_panel_app.run_server is started."""
    started: list = []
    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda base_dir: None)

    def fake_thread(target, args=(), daemon=False):
        t = SimpleNamespace(target=target, args=args, daemon=daemon)
        t.start = lambda: started.append((target, args))
        return t

    monkeypatch.setattr(threading, "Thread", fake_thread)
    api_mod.start_all("master", "/fake/base")
    assert len(started) == 1
    assert started[0][1] == (5002,)


def test_start_all_master_does_not_call_webhook_or_sync(api_mod, monkeypatch):
    """master mode must NOT call worker webhook or sync services."""
    # ensure_worker_webhook_started is now a lazy import inside start_all;
    # track it via the stub module to verify it is not called in master mode.
    webhook_calls: list = []
    sync_calls: list = []
    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda base_dir: None)
    monkeypatch.setattr(api_mod, "ensure_worker_sync_started", lambda: sync_calls.append(True))
    monkeypatch.setattr(
        sys.modules["worker_webhook_api"],
        "ensure_worker_webhook_started",
        lambda: webhook_calls.append(True),
    )
    monkeypatch.setattr(threading, "Thread", lambda target, args=(), daemon=False: SimpleNamespace(start=lambda: None))
    api_mod.start_all("master", "/fake/base")
    assert webhook_calls == []
    assert sync_calls == []


# ---------------------------------------------------------------------------
# start_all — worker mode
# ---------------------------------------------------------------------------

def test_start_all_worker_calls_push_webhook_and_sync(api_mod, monkeypatch):
    """worker mode: push server + webhook + sync all called; Flask NOT started."""
    push_calls: list = []
    webhook_calls: list = []
    sync_calls: list = []
    flask_threads: list = []

    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda base_dir: push_calls.append(base_dir))
    monkeypatch.setattr(
        sys.modules["worker_webhook_api"],
        "ensure_worker_webhook_started",
        lambda: webhook_calls.append(True),
    )
    monkeypatch.setattr(api_mod, "ensure_worker_sync_started", lambda: sync_calls.append(True))
    monkeypatch.setattr(
        threading, "Thread",
        lambda target, args=(), daemon=False: flask_threads.append(True) or SimpleNamespace(start=lambda: None),
    )

    api_mod.start_all("worker", "/fake/base")

    assert len(push_calls) == 1
    assert len(webhook_calls) == 1
    assert len(sync_calls) == 1
    assert flask_threads == []


# ---------------------------------------------------------------------------
# scan_loop
# ---------------------------------------------------------------------------

def test_scan_loop_calls_scan_and_exits_on_keyboard_interrupt(api_mod, monkeypatch):
    """scan_loop calls scan_and_start_devices and exits cleanly on KeyboardInterrupt."""
    scan_calls: list = []

    def fake_scan(*args, **kw):
        scan_calls.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(api_mod, "scan_and_start_devices", fake_scan)
    monkeypatch.setattr(api_mod.bot_state, "check_refresh_needed", lambda: False)
    monkeypatch.setattr(api_mod, "shutdown_web_devices", lambda log: None)
    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)

    fake_log = SimpleNamespace(info=lambda msg: None)
    api_mod.scan_loop(lambda: None, {}, None, None, None, 1, fake_log)

    assert len(scan_calls) >= 1
