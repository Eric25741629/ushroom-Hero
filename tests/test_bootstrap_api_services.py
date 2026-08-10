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


@pytest.fixture(autouse=True)
def isolate_master_background_services(api_mod, monkeypatch):
    """啟動 helper 測試不可真的建立 monitor/checker/tracker daemon。"""
    monkeypatch.setattr(api_mod, "_start_online_check_service", lambda: True)
    monkeypatch.setattr(api_mod, "_start_online_monitor", lambda: True)
    monkeypatch.setattr(api_mod, "_start_mount_tracker", lambda: True)
    monkeypatch.setattr(api_mod, "_service_threads", {})


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

    def fake_thread(target, args=(), daemon=False, name=None):
        t = SimpleNamespace(target=target, args=args, daemon=daemon)
        t.start = lambda: started.append((target, args))
        return t

    monkeypatch.setattr(threading, "Thread", fake_thread)
    api_mod.start_all("master", "/fake/base")
    assert len(started) == 1
    assert started[0][1][-1] == 5002


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
    monkeypatch.setattr(
        threading,
        "Thread",
        lambda target, args=(), daemon=False, name=None: SimpleNamespace(
            start=lambda: None, is_alive=lambda: True
        ),
    )
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


def test_start_all_master_uses_configured_dashboard_port(api_mod, monkeypatch):
    ports: list[int] = []
    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda **kwargs: True)
    monkeypatch.setattr(
        api_mod, "_start_dashboard", lambda port: ports.append(port) or True
    )

    result = api_mod.start_all("master", "/fake/base", dashboard_port=5317)

    assert ports == [5317]
    assert result["control_panel"] is True


def test_start_all_reports_push_server_failure(api_mod, monkeypatch):
    monkeypatch.setattr(
        api_mod, "ensure_push_server_started", lambda **kwargs: False
    )
    monkeypatch.setattr(api_mod, "_start_dashboard", lambda port: True)

    result = api_mod.start_all("master", "/fake/base")

    assert result["push_server"] is False


def test_start_all_master_isolates_each_service_failure(api_mod, monkeypatch):
    calls: list[str] = []

    def fail_dashboard(port):
        calls.append("dashboard")
        raise RuntimeError("bind failed")

    def fail_monitor():
        calls.append("monitor")
        raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda **kwargs: True)
    monkeypatch.setattr(api_mod, "_start_dashboard", fail_dashboard)
    monkeypatch.setattr(
        api_mod,
        "_start_online_check_service",
        lambda: calls.append("online_check") or False,
    )
    monkeypatch.setattr(api_mod, "_start_online_monitor", fail_monitor)
    monkeypatch.setattr(
        api_mod,
        "_start_mount_tracker",
        lambda: calls.append("mount_tracker") or True,
    )

    result = api_mod.start_all("master", "/fake/base", dashboard_port=5317)

    assert calls == ["dashboard", "online_check", "monitor", "mount_tracker"]
    assert result == {
        "push_server": True,
        "control_panel": False,
        "online_check_service": False,
        "online_monitor": False,
        "mount_tracker": True,
    }


def test_start_all_keeps_dashboard_thread_for_health_check(api_mod, monkeypatch):
    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.alive = True

        def start(self):
            pass

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(api_mod.threading, "Thread", FakeThread)
    monkeypatch.setattr(api_mod, "ensure_push_server_started", lambda **kwargs: True)
    api_mod.start_all("master", "/fake/base")

    status = api_mod.get_service_status()
    assert status["control_panel"] == {"started": True, "alive": True}


@pytest.mark.parametrize(
    "raw, expected",
    [(5317, 5317), ("bad", 5002), (0, 5002), (65536, 5002)],
)
def test_dashboard_port_config_is_validated(monkeypatch, raw, expected):
    import config_manager

    monkeypatch.setattr(
        config_manager, "get_global_config", lambda: {"dashboard_port": raw}
    )

    assert config_manager.get_dashboard_port() == expected


# ---------------------------------------------------------------------------
# scan_loop
# ---------------------------------------------------------------------------

def test_scan_loop_sweeps_stale_remote_devices_each_iteration(api_mod, monkeypatch):
    """掉線判離線 fix: scan_loop 每輪呼叫 bot_state.sweep_stale_remote_devices()."""
    sweep_calls: list = []
    scan_calls: list = []

    def fake_scan(*args, **kw):
        scan_calls.append(True)
        if len(scan_calls) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(api_mod, "scan_and_start_devices", fake_scan)
    monkeypatch.setattr(
        api_mod.bot_state,
        "sweep_stale_remote_devices",
        lambda *a, **kw: sweep_calls.append(True),
        raising=False,
    )
    monkeypatch.setattr(api_mod.bot_state, "check_refresh_needed", lambda: True)
    monkeypatch.setattr(api_mod, "shutdown_web_devices", lambda log: None)
    monkeypatch.setattr(api_mod.time, "sleep", lambda s: None)

    fake_log = SimpleNamespace(info=lambda msg: None)
    api_mod.scan_loop(lambda: None, {}, None, None, None, 1, fake_log)

    assert len(sweep_calls) >= 1


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
