"""Characterization tests for runtime_services.sleep_service (Phase 5).

Written BEFORE extraction. Locks:

  calc_aligned_wake_ts(base_ts, min_sleep_sec, win_min=20):
    - if earliest (=base+min) falls inside the next hour's 00~win_min
      window → returns a ts in [earliest, win_end]
    - if earliest is past win_end → returns next hour + random(0..win_min)
    - edge: min_sleep_sec=0 → earliest==base_ts

  stop_runtime_device_for_sleep:
    - None device → logs warning, no-op
    - web_h5 backend with callable `close` → calls close()
    - web_h5 backend without close → falls back to app_stop
    - adb backend → app_stop("com.mxdzz.tw.and")
    - exception in stop → logged, not raised

  run_sleep_cycle:
    - forced_wake_ts is honored verbatim (before adjust_wake_time_for_cars)
    - calls bot_state.update_state with "休眠中" / next_wake_at
    - returns (wake_ts, interrupted, post_time)

  _maybe_resume_sleep:
    - no resume pending → returns (ts, reason, False) with no side effects
    - 5554 with pending online check → processes, returns continue=True
    - non-5554 with resume in future → enters sleep; returns (None, "", False)
      when not interrupted, (None, "", True) when interrupted
"""
from __future__ import annotations

import logging
import sys
import time
import types
from types import SimpleNamespace

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

if "device" not in sys.modules:
    _dev = types.ModuleType("device")
    _dev.get_adb_devices = lambda *a, **k: []
    _dev.close_notification = lambda *a, **k: None
    _dev.open_notification = lambda *a, **k: None
    sys.modules["device"] = _dev


@pytest.fixture
def sleep_mod():
    import importlib
    return importlib.import_module("runtime_services.sleep_service")


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch, sleep_mod):
    if getattr(sleep_mod, "logger", None) is None:
        monkeypatch.setattr(sleep_mod, "logger", logging.getLogger("test_sleep"))


# ---------------------------------------------------------------------------
# calc_aligned_wake_ts (pure function)
# ---------------------------------------------------------------------------

def test_calc_aligned_wake_ts_inside_window(sleep_mod, monkeypatch):
    # base = xx:30 (1800s past hour floor), min_sleep_sec = 2100s (35min)
    # earliest = base + 2100 = next_hour + 300 (5min past hour)
    # win_end = next_hour + 1200 (20min past hour)
    # earliest (5min) <= win_end (20min), so result ∈ [earliest, win_end]
    base = 1_700_000_000 + 1800  # doesn't matter — just fix jitter
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)  # lowest
    result = sleep_mod.calc_aligned_wake_ts(base, min_sleep_sec=2100, win_min=20)
    # Should be earliest (= base + 2100)
    assert result == float(base + 2100)


def test_calc_aligned_wake_ts_past_window_rolls_to_next_hour(sleep_mod, monkeypatch):
    # base = xx:00 (hour-aligned), min_sleep_sec = 1500s (25min)
    # earliest = xx:25, win_end = xx:20
    # earliest > win_end → next hour + random(0..20min)
    base_hour = 1_700_000_000 - (1_700_000_000 % 3600)
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: 0)
    result = sleep_mod.calc_aligned_wake_ts(base_hour, min_sleep_sec=1500, win_min=20)
    # Expected: next hour = base_hour + 3600
    assert result == float(base_hour + 3600)


def test_calc_aligned_wake_ts_min_sleep_zero(sleep_mod, monkeypatch):
    base = 1_700_000_000
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)
    result = sleep_mod.calc_aligned_wake_ts(base, min_sleep_sec=0, win_min=20)
    # earliest == base_ts; floor(base) + 1200 may be > base → result inside window
    assert result >= float(base)


# ---------------------------------------------------------------------------
# stop_runtime_device_for_sleep
# ---------------------------------------------------------------------------

def test_stop_runtime_device_none_is_noop(sleep_mod):
    # No exception raised, just early return
    sleep_mod.stop_runtime_device_for_sleep(
        None, "emu-1", "adb", logging.getLogger("test"),
    )


def test_stop_runtime_device_web_h5_calls_close(sleep_mod):
    close_calls = []
    app_stop_calls = []
    d = SimpleNamespace(
        close=lambda: close_calls.append("called"),
        app_stop=lambda pkg: app_stop_calls.append(pkg),
    )
    sleep_mod.stop_runtime_device_for_sleep(
        d, "emu-1", "web_h5", logging.getLogger("test"),
    )
    assert close_calls == ["called"]
    assert app_stop_calls == []


def test_stop_runtime_device_web_h5_without_close_falls_back(sleep_mod):
    app_stop_calls = []
    d = SimpleNamespace(
        app_stop=lambda pkg: app_stop_calls.append(pkg),
    )
    # No `close` attribute
    sleep_mod.stop_runtime_device_for_sleep(
        d, "emu-1", "web_h5", logging.getLogger("test"),
    )
    assert app_stop_calls == ["com.mxdzz.tw.and"]


def test_stop_runtime_device_adb_calls_app_stop(sleep_mod):
    app_stop_calls = []
    d = SimpleNamespace(
        app_stop=lambda pkg: app_stop_calls.append(pkg),
    )
    sleep_mod.stop_runtime_device_for_sleep(
        d, "emu-1", "adb", logging.getLogger("test"),
    )
    assert app_stop_calls == ["com.mxdzz.tw.and"]


def test_stop_runtime_device_swallows_exception(sleep_mod):
    def _raise(pkg):
        raise RuntimeError("boom")
    d = SimpleNamespace(app_stop=_raise)
    sleep_mod.stop_runtime_device_for_sleep(
        d, "emu-1", "adb", logging.getLogger("test"),
    )  # should not raise


# ---------------------------------------------------------------------------
# run_sleep_cycle
# ---------------------------------------------------------------------------

@pytest.fixture
def sleep_cycle_env(monkeypatch, sleep_mod):
    """Stub collaborators so run_sleep_cycle runs deterministically."""
    state_updates: list[dict] = []
    monkeypatch.setattr(sleep_mod.bot_state, "update_state", lambda ip, **kw: state_updates.append({"ip": ip, **kw}))
    monkeypatch.setattr(sleep_mod, "adjust_wake_time_for_cars", lambda ts: ts)
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", lambda ip, ts, log: False)
    monkeypatch.setattr(sleep_mod.config_manager, "get_device_config", lambda ip: {"sleep_min_hours": 1, "sleep_max_hours": 1})
    monkeypatch.setattr(sleep_mod, "return_time", lambda ip, name: None)
    return state_updates


def test_run_sleep_cycle_honors_forced_wake_ts(sleep_mod, sleep_cycle_env):
    forced = time.time() + 1800
    wake_ts, interrupted, _ = sleep_mod.run_sleep_cycle(
        "emu-1", logging.getLogger("t"),
        forced_wake_ts=forced,
    )
    assert wake_ts == forced
    assert interrupted is False
    assert any(u["task"] == "休眠中" for u in sleep_cycle_env)


def test_run_sleep_cycle_returns_interrupted_flag(sleep_mod, sleep_cycle_env, monkeypatch):
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", lambda ip, ts, log: True)
    _, interrupted, _ = sleep_mod.run_sleep_cycle(
        "emu-1", logging.getLogger("t"),
        forced_wake_ts=time.time() + 300,
    )
    assert interrupted is True


# ---------------------------------------------------------------------------
# _maybe_resume_sleep
# ---------------------------------------------------------------------------

@pytest.fixture
def resume_env(monkeypatch, sleep_mod):
    calls = {"state": [], "check_on_line": [], "process_online": []}
    monkeypatch.setattr(sleep_mod.bot_state, "update_state", lambda ip, **kw: calls["state"].append({"ip": ip, **kw}))
    monkeypatch.setattr(sleep_mod.bot_state, "has_pending_online_check_request", lambda ip: False)
    monkeypatch.setattr(
        sleep_mod, "process_online_check_requests",
        lambda ip, cnn, log, fn: calls["process_online"].append(ip),
    )
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", lambda ip, ts, log: False)
    # check_on_line stub (imported from game_initialization)
    if "game_initialization" in sys.modules and not hasattr(sys.modules["game_initialization"], "check_on_line"):
        sys.modules["game_initialization"].check_on_line = lambda *a, **k: False
    return calls


def test_maybe_resume_no_pending_no_resume_returns_false(sleep_mod, resume_env):
    ts, reason, cont = sleep_mod._maybe_resume_sleep(
        "emu-1", object(), None, "", logging.getLogger("t"),
    )
    assert ts is None
    assert reason == ""
    assert cont is False


def test_maybe_resume_5554_with_pending_online_check_continues(sleep_mod, resume_env, monkeypatch):
    monkeypatch.setattr(sleep_mod.bot_state, "has_pending_online_check_request", lambda ip: True)
    ts, reason, cont = sleep_mod._maybe_resume_sleep(
        "emulator-5554", object(), None, "", logging.getLogger("t"),
    )
    assert cont is True
    assert resume_env["process_online"] == ["emulator-5554"]


def test_maybe_resume_non_5554_with_resume_ts_enters_sleep(sleep_mod, resume_env):
    future = time.time() + 600
    ts, reason, cont = sleep_mod._maybe_resume_sleep(
        "emu-other", object(), future, "test-reason", logging.getLogger("t"),
    )
    # After a non-interrupted sleep, returns (None, "", False)
    assert ts is None
    assert reason == ""
    assert cont is False
    # Should have emitted a state update for "休眠中"
    assert any(u["task"] == "休眠中" for u in resume_env["state"])


def test_maybe_resume_interrupted_returns_continue_true(sleep_mod, resume_env, monkeypatch):
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", lambda ip, ts, log: True)
    future = time.time() + 600
    ts, reason, cont = sleep_mod._maybe_resume_sleep(
        "emu-other", object(), future, "reason", logging.getLogger("t"),
    )
    assert ts is None
    assert cont is True
