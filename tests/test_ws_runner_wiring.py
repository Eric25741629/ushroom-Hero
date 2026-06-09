"""Wiring tests for the ws_token pure-WS backend integration.

Covers the three seams added to run a device over ``ws_token.runner.run_device``
instead of the ADB/Playwright daily pipeline, all gated behind
``use_ws_runner`` (default False → legacy behavior is unchanged):

  1. config_manager: new ``use_ws_runner`` / ``ws_token_spend`` /
     ``ws_token_sweep_list`` fields + ``"ws_token"`` backend whitelist entry.
  2. device_scan_service: ``get_ws_runner_devices`` selects opted-in devices.
  3. runtime_services.ws_runner_service: ``run_ws_device_cycle`` calls
     run_device (not daily_pipeline) and the loop respects pause / force-sleep.

new_main_v2 itself is import-heavy (device_wrapper → cv2, miner, Skill …), so
the WS branch lives in ``runtime_services.ws_runner_service`` as a small,
light-to-import function that new_main_v2.main delegates to. The tests target
that function directly rather than importing new_main_v2.
"""
import json
import sys
import types

import pytest

import config_manager


# ── shared fixtures / helpers (mirror test_device_enabled_gate.py) ───────────

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
    # device_scan_service imports `from device import get_adb_devices`; stub the
    # `device` module so the import stays light in test envs.
    if "device" not in sys.modules:
        stub = types.ModuleType("device")
        stub.get_adb_devices = lambda: []
        sys.modules["device"] = stub
    from runtime_services import device_scan_service
    return device_scan_service


# ── 1. config: new fields default off + survive round-trip ───────────────────

def test_ws_fields_are_typed_with_off_defaults():
    from config_manager import DeviceConfig, DEFAULT_DEVICE_CONFIG
    for key in ("use_ws_runner", "ws_token_spend", "ws_token_sweep_list"):
        assert key in DeviceConfig.__dataclass_fields__
        assert key in DEFAULT_DEVICE_CONFIG
    dc = DeviceConfig()
    assert dc.use_ws_runner is False
    assert dc.ws_token_spend is False
    assert dc.ws_token_sweep_list == []


def test_from_dict_reads_ws_fields():
    from config_manager import DeviceConfig
    dc = DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_spend": True, "ws_token_sweep_list": [[1, 2, 3]]}
    )
    assert dc.get("use_ws_runner") is True
    assert dc.get("ws_token_spend") is True
    assert dc.get("ws_token_sweep_list") == [[1, 2, 3]]


def test_from_dict_defaults_when_keys_absent():
    from config_manager import DeviceConfig
    dc = DeviceConfig.from_dict({"backend": "adb"})
    assert dc.get("use_ws_runner") is False
    assert dc.get("ws_token_spend") is False
    assert dc.get("ws_token_sweep_list") == []


# ── 1b. backend whitelist keeps "ws_token" (not downgraded to adb) ───────────

def test_update_device_config_keeps_ws_token_backend(temp_config):
    config_manager.update_device_config("ws-001", {"backend": "ws_token"})
    raw = json.loads(temp_config.read_text(encoding="utf-8"))
    assert raw["devices"]["ws-001"]["backend"] == "ws_token"


def test_update_device_config_unknown_backend_downgrades_to_adb(temp_config):
    config_manager.update_device_config("bad-001", {"backend": "nonsense"})
    raw = json.loads(temp_config.read_text(encoding="utf-8"))
    assert raw["devices"]["bad-001"]["backend"] == "adb"


def test_update_device_config_persists_ws_flags(temp_config):
    config_manager.update_device_config(
        "ws-002",
        {"backend": "ws_token", "use_ws_runner": True, "ws_token_spend": 1,
         "ws_token_sweep_list": [[10, 1, 5, 1]]},
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-002"]
    assert raw["use_ws_runner"] is True
    assert raw["ws_token_spend"] is True  # coerced 1 -> True
    assert raw["ws_token_sweep_list"] == [[10, 1, 5, 1]]


def test_update_device_config_sanitizes_bad_sweep_list(temp_config):
    config_manager.update_device_config(
        "ws-003",
        {"backend": "ws_token", "ws_token_sweep_list": ["junk", [1, 2], [3, 4, 5], 7]},
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-003"]
    # "junk"/7 dropped (not lists); [1,2] dropped (<3 entries); [3,4,5] kept.
    assert raw["ws_token_sweep_list"] == [[3, 4, 5]]


def test_update_device_config_non_list_sweep_becomes_empty(temp_config):
    config_manager.update_device_config(
        "ws-004", {"backend": "ws_token", "ws_token_sweep_list": "not-a-list"}
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-004"]
    assert raw["ws_token_sweep_list"] == []


# ── 2. scanner: get_ws_runner_devices selection ──────────────────────────────

def test_get_ws_runner_devices_selects_opted_in(temp_config):
    _write_devices(
        temp_config,
        {
            "ws-flag": {"backend": "adb", "use_ws_runner": True},
            "ws-backend": {"backend": "ws_token"},
            "ws-off": {"backend": "ws_token", "enabled": False},
            "web-dev": {"backend": "web_h5"},
            "adb-dev": {"backend": "adb"},
        },
    )
    svc = _import_scan_service()
    result = svc.get_ws_runner_devices(_NullLogger())
    assert "ws-flag" in result        # use_ws_runner=True selects regardless of backend
    assert "ws-backend" in result     # backend=="ws_token" selects too
    assert "ws-off" not in result     # disabled device skipped
    assert "web-dev" not in result
    assert "adb-dev" not in result


def test_is_ws_runner_device_helper(temp_config):
    _write_devices(
        temp_config,
        {
            "ws-flag": {"backend": "adb", "use_ws_runner": True},
            "plain": {"backend": "adb"},
        },
    )
    svc = _import_scan_service()
    assert svc.is_ws_runner_device("ws-flag") is True
    assert svc.is_ws_runner_device("plain") is False


# ── 3. ws_runner_service: run_device dispatch + pause/force-sleep ─────────────

@pytest.fixture
def patched_runner(monkeypatch):
    """Stub bot_state side effects and capture run_device calls on the service."""
    import runtime_services.ws_runner_service as svc

    calls = []

    def fake_run_device(ip, *, spend=False, sweep_list=None):
        calls.append({"ip": ip, "spend": spend, "sweep_list": sweep_list})
        return types.SimpleNamespace(
            device=ip, login_ok=True, spend=spend, tasks={"main_tasks": {}}, errors={}
        )

    # _load_run_device is the lazy seam; patch it so no WS deps are touched.
    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    return svc, calls


def test_run_ws_device_cycle_calls_run_device_with_cfg_flags(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_spend": True, "ws_token_sweep_list": [[1, 2, 3]]}
    )
    report = svc.run_ws_device_cycle("ws-x", cfg, _NullLogger())
    assert len(calls) == 1
    assert calls[0] == {"ip": "ws-x", "spend": True, "sweep_list": [[1, 2, 3]]}
    assert report.login_ok is True


def test_run_ws_device_cycle_passes_none_sweep_when_empty(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-y", cfg, _NullLogger())
    # empty list -> None so run_device's "no chapters configured" path triggers.
    assert calls[0]["spend"] is False
    assert calls[0]["sweep_list"] is None


def test_run_ws_device_cycle_login_failure_does_not_raise(patched_runner, monkeypatch):
    svc, calls = patched_runner

    def failing_login(ip, *, spend=False, sweep_list=None):
        calls.append(ip)
        return types.SimpleNamespace(
            device=ip, login_ok=False, spend=spend, tasks={}, errors={"login": "no ticket"}
        )

    monkeypatch.setattr(svc, "_load_run_device", lambda: failing_login)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    report = svc.run_ws_device_cycle("ws-z", cfg, _NullLogger())
    assert report.login_ok is False  # logged as warning, not raised


def test_run_ws_device_cycle_swallows_run_device_exception(patched_runner, monkeypatch):
    svc, _calls = patched_runner

    def boom(ip, *, spend=False, sweep_list=None):
        raise RuntimeError("ws blew up")

    monkeypatch.setattr(svc, "_load_run_device", lambda: boom)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    # one bad pass must not propagate out of the cycle (thread must survive).
    assert svc.run_ws_device_cycle("ws-e", cfg, _NullLogger()) is None


def test_loop_runs_cycle_then_sleeps_then_stops(monkeypatch):
    """One wake: loop calls run_ws_device_cycle exactly once, then sleeps.

    The sleep stub raises after the first cycle to terminate the otherwise
    infinite loop deterministically — proving the cycle ran before sleep and
    that no ADB/Playwright init was attempted.
    """
    import runtime_services.ws_runner_service as svc

    cycle_calls = []
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, cfg, lg: cycle_calls.append(ip))
    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: False)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config",
                        lambda ip: config_manager.DeviceConfig.from_dict({"use_ws_runner": True}))

    # Stub the lazily-imported sleep/startup helpers via their source modules.
    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    class _Stop(Exception):
        pass

    def fake_sleep(ip, lg, **k):
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    # The loop catches the broad Exception, logs, and returns — so _Stop ends it.
    svc.run_ws_device_loop("ws-loop", _NullLogger())
    assert cycle_calls == ["ws-loop"]  # exactly one cycle before sleep


def test_loop_force_sleep_skips_cycle(monkeypatch):
    """When force-sleep is requested, the cycle (run_device) must NOT run."""
    import runtime_services.ws_runner_service as svc

    cycle_calls = []
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, cfg, lg: cycle_calls.append(ip))
    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: True)  # force sleep!
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config",
                        lambda ip: config_manager.DeviceConfig.from_dict({"use_ws_runner": True}))

    import runtime_services.startup_sleep as ss
    import runtime_services.sleep_service as sl
    monkeypatch.setattr(ss, "_handle_startup_sleep", lambda ip, lg: None)

    class _Stop(Exception):
        pass

    seen = {"policy": None}

    def fake_sleep(ip, lg, **k):
        seen["policy"] = k.get("sleep_policy")
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    svc.run_ws_device_loop("ws-fs", _NullLogger())
    assert cycle_calls == []                 # force-sleep skipped the run
    assert seen["policy"] == "force_sleep"   # and applied the force-sleep policy
