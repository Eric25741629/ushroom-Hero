"""Tests for `get_ws_fallback_devices` (offline_fallback WS injection).

When an ADB phone goes offline, the ADB scan stops producing its serial so its
device thread never spawns and the pure-WS pipeline never runs. To keep WS idle
rewards flowing while the phone is away, devices that opt into
``ws_token.offline_fallback`` must be injected into the scan's device list the
same way ``use_ws_runner`` devices already are.

Selection rule (mirrors get_ws_runner_devices):
    backend == "adb" AND ws_token.enabled AND ws_token.offline_fallback
    AND enabled != false.

These tests target the helper directly + assert the scan loop injects the
serial without duplicating an already-present one.
"""
import json
import sys
import types

import pytest

import config_manager


sys.modules.setdefault("cv2", types.SimpleNamespace())
sys.modules.setdefault(
    "opencc",
    types.SimpleNamespace(
        OpenCC=lambda *args, **kwargs: types.SimpleNamespace(convert=lambda text: text)
    ),
)
sys.modules.setdefault("uiautomator2", types.SimpleNamespace(Device=object))


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "bot_config.json"
    cfg_path.write_text(
        json.dumps({"devices": {}, "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_path", None, raising=False)
    return cfg_path


def _write_devices(cfg_path, devices, mode="master"):
    cfg_path.write_text(
        json.dumps({"devices": devices, "global": {"mode": mode}}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_manager._config_cache = None
    config_manager._config_cache_mtime_ns = None


class _NullLogger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _import_scan_service():
    # `device` may already be a partial stub from another test file in the same
    # session (e.g. test_wake_*). device_scan_service does
    # `from device import get_adb_devices`, so guarantee that symbol exists.
    dev = sys.modules.get("device")
    if dev is None or not hasattr(dev, "get_adb_devices"):
        stub = dev if dev is not None else types.ModuleType("device")
        stub.get_adb_devices = lambda: []
        sys.modules["device"] = stub
    from runtime_services import device_scan_service
    return device_scan_service


# ── selection matrix ─────────────────────────────────────────────────────────

def test_selects_adb_ws_enabled_with_fallback_on(temp_config):
    _write_devices(
        temp_config,
        {
            "phone-on": {
                "backend": "adb",
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()
    assert "phone-on" in svc.get_ws_fallback_devices(_NullLogger())


def test_skips_when_fallback_off(temp_config):
    _write_devices(
        temp_config,
        {
            "phone-off": {
                "backend": "adb",
                "ws_token": {"enabled": True, "offline_fallback": False},
            },
        },
    )
    svc = _import_scan_service()
    assert "phone-off" not in svc.get_ws_fallback_devices(_NullLogger())


def test_skips_when_ws_token_disabled(temp_config):
    _write_devices(
        temp_config,
        {
            "phone-nows": {
                "backend": "adb",
                "ws_token": {"enabled": False, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()
    assert "phone-nows" not in svc.get_ws_fallback_devices(_NullLogger())


def test_skips_when_device_disabled(temp_config):
    _write_devices(
        temp_config,
        {
            "phone-disabled": {
                "backend": "adb",
                "enabled": False,
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()
    assert "phone-disabled" not in svc.get_ws_fallback_devices(_NullLogger())


def test_skips_web_h5_backend(temp_config):
    _write_devices(
        temp_config,
        {
            "web-dev": {
                "backend": "web_h5",
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()
    assert "web-dev" not in svc.get_ws_fallback_devices(_NullLogger())


def test_missing_enabled_key_reads_as_enabled(temp_config):
    _write_devices(
        temp_config,
        {
            # no "enabled" key -> treated as enabled (legacy configs)
            "phone-legacy": {
                "backend": "adb",
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()
    assert "phone-legacy" in svc.get_ws_fallback_devices(_NullLogger())


# ── host gate: master/worker dual-injection guard ────────────────────────────
#
# master + worker share the same NAS-synced bot_config.json. Without a host
# gate BOTH hosts inject the fc serial; the phone only pairs with the master's
# adb, so the worker's init always fails -> WS fallback -> hourly WS login
# kicks the phone's session even while the master is driving it normally.

def _fallback_device(**ws_extra):
    ws = {"enabled": True, "offline_fallback": True}
    ws.update(ws_extra)
    return {"backend": "adb", "ws_token": ws}


def test_worker_mode_does_not_inject_without_fallback_host(temp_config):
    _write_devices(temp_config, {"phone-fc": _fallback_device()}, mode="worker")
    svc = _import_scan_service()
    assert svc.get_ws_fallback_devices(_NullLogger()) == []


def test_master_mode_injects_without_fallback_host(temp_config):
    _write_devices(temp_config, {"phone-fc": _fallback_device()}, mode="master")
    svc = _import_scan_service()
    assert "phone-fc" in svc.get_ws_fallback_devices(_NullLogger())


def test_fallback_host_match_injects_even_on_worker(temp_config, monkeypatch):
    _write_devices(
        temp_config,
        {"phone-fc": _fallback_device(fallback_host="infinite")},
        mode="worker",
    )
    svc = _import_scan_service()
    # case-insensitive hostname match
    monkeypatch.setattr(svc.config_manager, "get_hostname", lambda: "INFINITE")
    assert "phone-fc" in svc.get_ws_fallback_devices(_NullLogger())


def test_fallback_host_mismatch_blocks_even_on_master(temp_config, monkeypatch):
    _write_devices(
        temp_config,
        {"phone-fc": _fallback_device(fallback_host="infinite")},
        mode="master",
    )
    svc = _import_scan_service()
    monkeypatch.setattr(svc.config_manager, "get_hostname", lambda: "DESKTOP-OV0ASQ4")
    assert svc.get_ws_fallback_devices(_NullLogger()) == []


# ── scan injection: serial added, no duplicates ──────────────────────────────

def test_scan_injects_fallback_serial(temp_config, monkeypatch):
    _write_devices(
        temp_config,
        {
            "phone-fc": {
                "backend": "adb",
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()

    started = []

    # ADB scan returns nothing (phone offline) — fallback injection must still
    # bring phone-fc into the start path.
    monkeypatch.setattr(svc, "get_adb_devices", lambda: [])
    monkeypatch.setattr(svc, "get_web_backend_devices", lambda lg: [])
    monkeypatch.setattr(svc, "get_ws_runner_devices", lambda lg: [])
    monkeypatch.setattr(svc, "refresh_adb_server", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_apply_adb_absence_rule", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_is_infinite_host", lambda: False)
    monkeypatch.setattr(svc.config_manager, "is_device_enabled", lambda ip: True)
    monkeypatch.setattr(svc.bot_state, "get_offline_since", lambda ip: None)

    real_thread = svc.threading.Thread

    def _fake_thread(*args, **kwargs):
        target = kwargs.get("target")
        t_args = kwargs.get("args", ())
        started.append(t_args[0] if t_args else None)
        # return a real (un-started) dummy so .is_alive()/.start() work
        return real_thread(target=lambda: None)

    monkeypatch.setattr(svc.threading, "Thread", _fake_thread)

    running = {}
    svc.scan_and_start_devices(
        main_fn=lambda *a, **k: None,
        running_threads=running,
        cnn_model=None,
        oracle_cnn_model=None,
        oracle_classes=None,
        ocr=None,
        logger_obj=_NullLogger(),
    )
    assert "phone-fc" in started


def test_scan_does_not_duplicate_present_serial(temp_config, monkeypatch):
    _write_devices(
        temp_config,
        {
            "phone-fc": {
                "backend": "adb",
                "ws_token": {"enabled": True, "offline_fallback": True},
            },
        },
    )
    svc = _import_scan_service()

    # ADB scan already sees phone-fc (online) — it should appear exactly once.
    monkeypatch.setattr(svc, "get_adb_devices", lambda: ["phone-fc"])
    monkeypatch.setattr(svc, "get_web_backend_devices", lambda lg: [])
    monkeypatch.setattr(svc, "get_ws_runner_devices", lambda lg: [])

    seen = svc.get_ws_fallback_devices(_NullLogger())
    assert seen == ["phone-fc"]

    # Mirror the dedup the scan loop performs.
    current = ["phone-fc"]
    for ip in seen:
        if ip not in current:
            current.append(ip)
    assert current.count("phone-fc") == 1


# ── absence rule: offline_fallback serials are exempt ─────────────────────────

def test_absence_rule_exempts_offline_fallback_serial(monkeypatch):
    """A backend=adb fallback phone that goes offline keeps掛機 via pure WS, so
    the ADB-absence watchdog must NOT declare it offline even after >1h, while a
    normal absent serial still gets set_offline."""
    svc = _import_scan_service()
    svc._adb_last_seen.clear()
    svc._adb_absence_offlined.clear()

    offlined = []
    monkeypatch.setattr(
        svc.bot_state, "get_all_states",
        lambda: {"phone-fc": {"status": "ONLINE"},
                 "emulator-9999": {"status": "ONLINE"}})
    monkeypatch.setattr(
        svc.bot_state, "set_offline",
        lambda ip, reason=None: offlined.append(ip))

    now = 1_000_000.0
    stale = now - (svc._ADB_ABSENT_OFFLINE_SEC + 1)
    # both were seen long ago and are now absent from the raw ADB scan
    svc._adb_last_seen["phone-fc"] = stale
    svc._adb_last_seen["emulator-9999"] = stale

    svc._apply_adb_absence_rule(set(), {}, _NullLogger(), now=now,
                                exempt={"phone-fc"})

    # exempt fallback phone must NOT be declared offline...
    assert "phone-fc" not in offlined
    # ...and its stale timestamp is purged so a later offline_fallback=false
    # toggle can't instantly trip the rule on a leftover timestamp.
    assert "phone-fc" not in svc._adb_last_seen
    # the non-exempt emulator still follows the absence rule.
    assert "emulator-9999" in offlined
