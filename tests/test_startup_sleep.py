"""Characterization tests for runtime_services.startup_sleep (Phase 6).

Written BEFORE extraction. Locks:

  _handle_startup_sleep:
    - no-op when device has no startup-sleep budget
    - no-op when remaining sleep is 0 (elapsed >= budget)
    - counts down second-by-second when budget > 0
    - early-exit when a pending web launch is detected
    - 5554-specific: early-exit on pending online-check request
    - 5554-specific: early-exit when online-check pending
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

# --- Heavy-dep stubs ---------------------------------------------------------
for _name in ("opencc", "paddleocr", "img_tools", "easyocr"):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        if _name == "opencc":
            _m.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[_name] = _m

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2


@pytest.fixture
def startup_mod():
    import importlib
    return importlib.import_module("runtime_services.startup_sleep")


@pytest.fixture
def fake_bot_state(monkeypatch, startup_mod):
    state = {
        "pending_online": False,
        "pending_web_launch": False,
    }
    updates: list[dict] = []
    monkeypatch.setattr(startup_mod.bot_state, "has_pending_online_check_request", lambda ip: state["pending_online"])
    monkeypatch.setattr(startup_mod.bot_state, "has_pending_web_launch_request", lambda ip: state["pending_web_launch"])
    monkeypatch.setattr(startup_mod.bot_state, "update_state", lambda ip, **kw: updates.append({"ip": ip, **kw}))
    state["_updates"] = updates
    return state


@pytest.fixture
def zero_sleep(monkeypatch, startup_mod):
    """Disable time.sleep so tests run instantly."""
    monkeypatch.setattr(startup_mod.time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# _handle_startup_sleep
# ---------------------------------------------------------------------------

def test_handle_startup_sleep_noop_when_no_budget(startup_mod, fake_bot_state, monkeypatch):
    # Device not in the budget table → zero budget → no updates
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {})
    startup_mod._handle_startup_sleep("emu-unknown", logging.getLogger("t"))
    assert fake_bot_state["_updates"] == []


def test_handle_startup_sleep_noop_when_elapsed_exhausted(startup_mod, fake_bot_state, monkeypatch):
    # Budget 10s, elapsed 20s → remaining=0 → no updates
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {"emu-1": 10})
    monkeypatch.setattr(startup_mod, "PROCESS_START_TS", startup_mod.time.time() - 20)
    startup_mod._handle_startup_sleep("emu-1", logging.getLogger("t"))
    assert fake_bot_state["_updates"] == []


def test_handle_startup_sleep_counts_down(startup_mod, fake_bot_state, zero_sleep, monkeypatch):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {"emu-1": 3})
    monkeypatch.setattr(startup_mod, "PROCESS_START_TS", startup_mod.time.time())
    startup_mod._handle_startup_sleep("emu-1", logging.getLogger("t"))
    assert len(fake_bot_state["_updates"]) == 3
    assert all(u["task"] == "啟動後休眠" for u in fake_bot_state["_updates"])


def test_handle_startup_sleep_early_exits_on_web_launch(
    startup_mod, fake_bot_state, zero_sleep, monkeypatch,
):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {"emu-1": 10})
    monkeypatch.setattr(startup_mod, "PROCESS_START_TS", startup_mod.time.time())
    fake_bot_state["pending_web_launch"] = True
    startup_mod._handle_startup_sleep("emu-1", logging.getLogger("t"))
    # Loop exits at first iteration
    assert len(fake_bot_state["_updates"]) == 0


def test_handle_startup_sleep_5554_early_exits_on_online_check(
    startup_mod, fake_bot_state, zero_sleep, monkeypatch,
):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {"emulator-5554": 10})
    monkeypatch.setattr(startup_mod, "PROCESS_START_TS", startup_mod.time.time())
    fake_bot_state["pending_online"] = True
    startup_mod._handle_startup_sleep("emulator-5554", logging.getLogger("t"))
    assert len(fake_bot_state["_updates"]) == 0


# ---------------------------------------------------------------------------
# Startup 分流 — per-device stagger derived from wake_hour_parity +
# wake_minute_offset, so all devices launch one slot apart instead of
# clumping (replaces the hardcoded STARTUP_SLEEP_SEC_BY_DEVICE table).
# Launch order: even before odd, then minute offset :00 -> :05 -> :15.
# ---------------------------------------------------------------------------

_SCHEDULE_DEVICES = {
    "emulator-5554": {"wake_hour_parity": "even", "wake_minute_offset": 0},
    "emulator-5556": {"wake_hour_parity": "even", "wake_minute_offset": 15},
    "emulator-5558": {"wake_hour_parity": "even", "wake_minute_offset": 5},
    "emulator-5560": {"wake_hour_parity": "odd", "wake_minute_offset": 0},
    "7fe98fc6": {"wake_hour_parity": "odd", "wake_minute_offset": 5},
    "adb-x": {"wake_hour_parity": "odd", "wake_minute_offset": 15},
}


def test_compute_stagger_order_even_before_odd_then_offset(startup_mod):
    order = startup_mod.compute_stagger_order(_SCHEDULE_DEVICES)
    assert order == [
        "emulator-5554",  # even :00
        "emulator-5558",  # even :05
        "emulator-5556",  # even :15
        "emulator-5560",  # odd  :00
        "7fe98fc6",       # odd  :05
        "adb-x",          # odd  :15
    ]


def test_resolve_startup_stagger_sec_is_gap_times_rank(startup_mod, monkeypatch):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {})
    f = startup_mod.resolve_startup_stagger_sec
    assert f("emulator-5554", gap_sec=120, devices=_SCHEDULE_DEVICES) == 0
    assert f("emulator-5558", gap_sec=120, devices=_SCHEDULE_DEVICES) == 120
    assert f("emulator-5556", gap_sec=120, devices=_SCHEDULE_DEVICES) == 240
    assert f("emulator-5560", gap_sec=120, devices=_SCHEDULE_DEVICES) == 360
    assert f("7fe98fc6", gap_sec=120, devices=_SCHEDULE_DEVICES) == 480
    assert f("adb-x", gap_sec=120, devices=_SCHEDULE_DEVICES) == 600


def test_resolve_startup_stagger_sec_explicit_table_overrides(startup_mod, monkeypatch):
    # An explicit entry in STARTUP_SLEEP_SEC_BY_DEVICE wins over config derivation.
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {"emulator-5558": 7})
    assert startup_mod.resolve_startup_stagger_sec(
        "emulator-5558", gap_sec=120, devices=_SCHEDULE_DEVICES
    ) == 7


def test_resolve_startup_stagger_sec_unknown_device_is_zero(startup_mod, monkeypatch):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {})
    assert startup_mod.resolve_startup_stagger_sec(
        "emu-not-configured", gap_sec=120, devices=_SCHEDULE_DEVICES
    ) == 0


def test_resolve_startup_stagger_sec_zero_gap_disables(startup_mod, monkeypatch):
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {})
    assert startup_mod.resolve_startup_stagger_sec(
        "adb-x", gap_sec=0, devices=_SCHEDULE_DEVICES
    ) == 0


def test_default_startup_stagger_sec_is_120(startup_mod):
    assert startup_mod.DEFAULT_STARTUP_STAGGER_SEC == 120


def test_handle_startup_sleep_uses_config_derived_when_not_in_table(
    startup_mod, fake_bot_state, zero_sleep, monkeypatch,
):
    # ip not in the explicit table -> _handle_startup_sleep must consult
    # resolve_startup_stagger_sec (config-derived), then count down.
    monkeypatch.setattr(startup_mod, "STARTUP_SLEEP_SEC_BY_DEVICE", {})
    monkeypatch.setattr(startup_mod, "resolve_startup_stagger_sec", lambda ip, **kw: 2)
    monkeypatch.setattr(startup_mod, "PROCESS_START_TS", startup_mod.time.time())
    startup_mod._handle_startup_sleep("emulator-5558", logging.getLogger("t"))
    steps = [u.get("step", "") for u in fake_bot_state["_updates"]]
    assert any("秒後開始執行" in s for s in steps)


