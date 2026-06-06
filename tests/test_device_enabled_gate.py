"""New-device auto-start gate (`enabled` flag).

A freshly registered device must NOT be auto-launched by the scanner until the
user explicitly enables it. This is expressed by a per-device `enabled` flag:

  - missing key  -> treated as enabled (backward compat for every existing device)
  - enabled=true -> eligible for auto-start
  - enabled=false-> scanner skips it (set on register, cleared by the user)

Covers config_manager (`enabled` field + `is_device_enabled`) and the scanner
filter (`get_web_backend_devices`).
"""
import json
import sys
import types

import pytest

import config_manager


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point config_manager at a fresh temp file and reset its mtime cache."""
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


def _write_devices(cfg_path, devices):
    cfg_path.write_text(
        json.dumps({"devices": devices, "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    # mtime bump invalidates the cache, but be explicit so a same-tick write is safe.
    config_manager._config_cache = None
    config_manager._config_cache_mtime_ns = None


# ── config_manager: enabled field ────────────────────────────────────────────

def test_enabled_is_typed_field_default_true():
    from config_manager import DeviceConfig, DEFAULT_DEVICE_CONFIG
    assert "enabled" in DeviceConfig.__dataclass_fields__
    assert DeviceConfig().enabled is True
    assert DEFAULT_DEVICE_CONFIG["enabled"] is True


def test_is_device_enabled_true_when_key_missing(temp_config):
    # Legacy device entry without an `enabled` key must read as enabled.
    _write_devices(temp_config, {"legacy-dev": {"name": "legacy", "backend": "adb"}})
    assert config_manager.is_device_enabled("legacy-dev") is True


def test_is_device_enabled_true_for_unknown_device(temp_config):
    # A device id not present in config falls back to enabled (no false-disable).
    assert config_manager.is_device_enabled("does-not-exist") is True


def test_is_device_enabled_false_when_explicit_false(temp_config):
    config_manager.update_device_config("web-001", {"backend": "web_h5", "enabled": False})
    assert config_manager.is_device_enabled("web-001") is False


def test_is_device_enabled_true_when_explicit_true(temp_config):
    config_manager.update_device_config("web-002", {"backend": "web_h5", "enabled": True})
    assert config_manager.is_device_enabled("web-002") is True


def test_update_device_config_coerces_enabled_to_bool(temp_config):
    config_manager.update_device_config("web-003", {"backend": "web_h5", "enabled": 0})
    raw = json.loads(temp_config.read_text(encoding="utf-8"))
    stored = raw["devices"]["web-003"]["enabled"]
    assert stored is False  # real bool, not int 0


# ── scanner filter: get_web_backend_devices ──────────────────────────────────

class _NullLogger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


def _import_scan_service():
    # device_scan_service imports `from device import get_adb_devices`; `device`
    # pulls uiautomator2/numpy. Stub it so the import is light in CI/test envs.
    if "device" not in sys.modules:
        stub = types.ModuleType("device")
        stub.get_adb_devices = lambda: []
        sys.modules["device"] = stub
    from runtime_services import device_scan_service
    return device_scan_service


def test_get_web_backend_devices_excludes_disabled(temp_config):
    _write_devices(
        temp_config,
        {
            "web-on": {"backend": "web_h5", "enabled": True},
            "web-off": {"backend": "web_h5", "enabled": False},
            "web-legacy": {"backend": "web_h5"},  # no enabled key -> enabled
            "adb-dev": {"backend": "adb"},
        },
    )
    svc = _import_scan_service()
    result = svc.get_web_backend_devices(_NullLogger())
    assert "web-on" in result
    assert "web-legacy" in result
    assert "web-off" not in result  # the gate
    assert "adb-dev" not in result  # adb is never a web device
