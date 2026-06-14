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
# calc_aligned_wake_ts — even/odd hour parity (load distribution / 分流)
# ---------------------------------------------------------------------------

def test_calc_aligned_wake_ts_even_parity_lands_on_even_hour(sleep_mod, monkeypatch):
    # parity=0 must produce a wake whose LOCAL hour is even, still within
    # the 00~20-min window (so 深淵之門 is still reachable).
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)
    for base in (1_700_000_000, 1_700_003_600, 1_700_001_800):
        result = sleep_mod.calc_aligned_wake_ts(base, 600, 20, hour_parity=0)
        lt = time.localtime(result)
        assert lt.tm_hour % 2 == 0
        assert 0 <= lt.tm_min <= 20
        assert result >= base + 600  # never scheduled in the past


def test_calc_aligned_wake_ts_odd_parity_lands_on_odd_hour(sleep_mod, monkeypatch):
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)
    for base in (1_700_000_000, 1_700_003_600, 1_700_001_800):
        result = sleep_mod.calc_aligned_wake_ts(base, 600, 20, hour_parity=1)
        lt = time.localtime(result)
        assert lt.tm_hour % 2 == 1
        assert 0 <= lt.tm_min <= 20
        assert result >= base + 600


def test_calc_aligned_wake_ts_parity_none_is_backward_compatible(sleep_mod, monkeypatch):
    # Explicit: parity=None reproduces the original inside-window behavior.
    base = 1_700_000_000 + 1800
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)
    assert sleep_mod.calc_aligned_wake_ts(base, 2100, 20, hour_parity=None) == float(base + 2100)


def test_parse_hour_parity_maps_config_values(sleep_mod):
    assert sleep_mod._parse_hour_parity("even") == 0
    assert sleep_mod._parse_hour_parity("odd") == 1
    assert sleep_mod._parse_hour_parity("EVEN") == 0
    assert sleep_mod._parse_hour_parity(0) == 0
    assert sleep_mod._parse_hour_parity(1) == 1
    assert sleep_mod._parse_hour_parity(None) is None
    assert sleep_mod._parse_hour_parity("whatever") is None
    assert sleep_mod._parse_hour_parity(5) is None


def test_run_sleep_cycle_applies_even_hour_parity(sleep_mod, sleep_cycle_env, monkeypatch):
    monkeypatch.setattr(
        sleep_mod.config_manager,
        "get_device_config",
        lambda ip: {"sleep_min_hours": 1, "sleep_max_hours": 1, "wake_hour_parity": "even"},
    )
    monkeypatch.setattr(sleep_mod.random, "randint", lambda a, b: a)
    monkeypatch.setattr(sleep_mod.random, "uniform", lambda a, b: a)
    wake_ts, _, _ = sleep_mod.run_sleep_cycle("emu-x", logging.getLogger("t"))
    assert time.localtime(wake_ts).tm_hour % 2 == 0


# ---------------------------------------------------------------------------
# calc_aligned_wake_ts — fixed per-device minute offset (intra-group 分流)
# ---------------------------------------------------------------------------

def test_calc_aligned_wake_ts_minute_offset_is_deterministic(sleep_mod):
    # Deterministic minute path uses no RNG: wake lands exactly on the offset
    # minute, on the minute (sec=0), and never before earliest.
    base = 1_700_000_000
    result = sleep_mod.calc_aligned_wake_ts(base, 3600, 20, wake_minute_offset=5)
    lt = time.localtime(result)
    assert lt.tm_min == 5
    assert lt.tm_sec == 0
    assert result >= base + 3600


def test_calc_aligned_wake_ts_minute_offset_clamped_to_window(sleep_mod):
    base = 1_700_000_000
    result = sleep_mod.calc_aligned_wake_ts(base, 3600, 20, wake_minute_offset=99)
    assert time.localtime(result).tm_min == 20  # clamped to win_min


def test_calc_aligned_wake_ts_minute_offset_combines_with_parity(sleep_mod):
    base = 1_700_000_000
    result = sleep_mod.calc_aligned_wake_ts(
        base, 3600, 20, hour_parity=0, wake_minute_offset=15
    )
    lt = time.localtime(result)
    assert lt.tm_hour % 2 == 0
    assert lt.tm_min == 15
    assert result >= base + 3600


def test_run_sleep_cycle_applies_minute_offset(sleep_mod, sleep_cycle_env, monkeypatch):
    monkeypatch.setattr(
        sleep_mod.config_manager,
        "get_device_config",
        lambda ip: {
            "sleep_min_hours": 1,
            "sleep_max_hours": 1,
            "wake_hour_parity": "even",
            "wake_minute_offset": 5,
        },
    )
    monkeypatch.setattr(sleep_mod.random, "uniform", lambda a, b: a)
    wake_ts, _, _ = sleep_mod.run_sleep_cycle("emu-x", logging.getLogger("t"))
    lt = time.localtime(wake_ts)
    assert lt.tm_hour % 2 == 0
    assert lt.tm_min == 5


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


# ---------------------------------------------------------------------------
# carpark re-park / 09:59 grab wake clamp
# ---------------------------------------------------------------------------

def test_carpark_repark_pulls_wake_earlier(sleep_mod):
    cur = 1_700_000_000.0
    wake = cur + 3600          # aligned wake an hour out
    next_ts = cur + 300        # car frees / window opens in 5 min
    out = sleep_mod._apply_carpark_repark_wake(
        "dev1", wake, cur, logging.getLogger("t"),
        next_ts_loader=lambda ip: next_ts)
    assert out == next_ts


def test_carpark_repark_never_pushes_wake_later(sleep_mod):
    cur = 1_700_000_000.0
    wake = cur + 600           # aligned wake sooner than the carpark event
    out = sleep_mod._apply_carpark_repark_wake(
        "dev1", wake, cur, logging.getLogger("t"),
        next_ts_loader=lambda ip: cur + 5000)
    assert out == wake


def test_carpark_repark_ignores_past_next_ts(sleep_mod):
    cur = 1_700_000_000.0
    wake = cur + 3600
    out = sleep_mod._apply_carpark_repark_wake(
        "dev1", wake, cur, logging.getLogger("t"),
        next_ts_loader=lambda ip: cur - 10)
    assert out == wake


def test_carpark_repark_none_is_noop(sleep_mod):
    cur = 1_700_000_000.0
    wake = cur + 3600
    out = sleep_mod._apply_carpark_repark_wake(
        "dev1", wake, cur, logging.getLogger("t"),
        next_ts_loader=lambda ip: None)
    assert out == wake


def test_load_carpark_next_ts_gated_on_enabled(sleep_mod, monkeypatch):
    monkeypatch.setattr(sleep_mod, "_load_ws_state",
                        lambda ip: {"carpark_repark": {"next_ts": 1234.0}})
    monkeypatch.setattr(sleep_mod.config_manager, "get_device_config",
                        lambda ip: {"ws_token": {"carpark_plan": {"enabled": False}}})
    assert sleep_mod._load_carpark_next_ts("dev1") is None
    monkeypatch.setattr(sleep_mod.config_manager, "get_device_config",
                        lambda ip: {"ws_token": {"carpark_plan": {"enabled": True}}})
    assert sleep_mod._load_carpark_next_ts("dev1") == 1234.0


def test_run_sleep_cycle_clamps_to_carpark_next_ts(sleep_mod, sleep_cycle_env,
                                                   monkeypatch):
    now = time.time()
    monkeypatch.setattr(sleep_mod, "_load_carpark_next_ts", lambda ip: now + 300)
    monkeypatch.setattr(sleep_mod.random, "uniform", lambda a, b: a)
    wake_ts, _, _ = sleep_mod.run_sleep_cycle("emu-x", logging.getLogger("t"))
    assert wake_ts == now + 300


def test_run_sleep_cycle_forced_wake_not_clamped_by_carpark(sleep_mod,
                                                            sleep_cycle_env,
                                                            monkeypatch):
    forced = time.time() + 1800
    monkeypatch.setattr(sleep_mod, "_load_carpark_next_ts",
                        lambda ip: time.time() + 60)  # earlier, but forced wins
    wake_ts, _, _ = sleep_mod.run_sleep_cycle(
        "emu-1", logging.getLogger("t"), forced_wake_ts=forced)
    assert wake_ts == forced


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


# ---------------------------------------------------------------------------
# _maybe_resume_sleep — carpark 09:59 grab clamp (mirrors run_sleep_cycle)
# ---------------------------------------------------------------------------
# The resume-sleep path (interrupted device returning to sleep) must pull its
# wake EARLIER to the carpark grab time too, so a device that got interrupted
# before 10:00 doesn't oversleep the daily 搶車位 window.


def test_maybe_resume_non_checker_clamps_to_carpark_next_ts(sleep_mod, resume_env, monkeypatch):
    now = time.time()
    resume_ts = now + 3600         # would otherwise sleep an hour
    next_ts = now + 300            # carpark window opens in 5 min
    monkeypatch.setattr(sleep_mod, "_load_carpark_next_ts", lambda ip: next_ts)
    captured = {}

    def _capture(ip, ts, log):
        captured["ts"] = ts
        return False
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", _capture)

    sleep_mod._maybe_resume_sleep(
        "emu-other", object(), resume_ts, "reason", logging.getLogger("t"),
    )
    assert captured["ts"] == next_ts


def test_maybe_resume_checker_branch_clamps_to_carpark_next_ts(sleep_mod, resume_env, monkeypatch):
    # 5554 is both the online-check checker AND a carpark device, so its
    # (separate) resume-sleep branch must clamp to the grab time too.
    now = time.time()
    resume_ts = now + 3600
    next_ts = now + 300
    monkeypatch.setattr(sleep_mod.bot_state, "is_online_check_priority_active", lambda ip: False)
    monkeypatch.setattr(sleep_mod, "_load_carpark_next_ts", lambda ip: next_ts)
    captured = {}

    def _capture(ip, ts, log):
        captured["ts"] = ts
        return False
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", _capture)

    sleep_mod._maybe_resume_sleep(
        "emulator-5554", object(), resume_ts, "reason", logging.getLogger("t"),
    )
    assert captured["ts"] == next_ts


def test_maybe_resume_no_carpark_next_ts_keeps_resume_ts(sleep_mod, resume_env, monkeypatch):
    # No stored carpark next_ts → clamp is a no-op, resume sleeps to the
    # original ts (non-carpark devices / normal resume unaffected).
    now = time.time()
    resume_ts = now + 600
    monkeypatch.setattr(sleep_mod, "_load_carpark_next_ts", lambda ip: None)
    captured = {}

    def _capture(ip, ts, log):
        captured["ts"] = ts
        return False
    monkeypatch.setattr(sleep_mod, "sleep_until_wake_or_interrupt", _capture)

    sleep_mod._maybe_resume_sleep(
        "emu-other", object(), resume_ts, "reason", logging.getLogger("t"),
    )
    assert captured["ts"] == resume_ts
