"""Characterization tests for game_actions.lamp_scheduler.

These tests are written BEFORE the extraction (Phase 2) and are expected
to fail until game_actions/lamp_scheduler.py exists with the expected API.

API under test:

  _run_lamp(d, ip, lamp_dur, is_compare=True)
    - always routes to opengold_v2 LampService (V1 Open_gold_paddle_ocr is
      deprecated; the legacy use_opengold_v2 flag has been removed from config
      and is never read — a leftover flag in an old config is ignored)
    - duration has ±10s jitter (via random.randint(-10, 10))

  _run_lamp_if_due(d, ip, stage)
    - phone OCR mode (use_phone_ocr_lamp_mode): runs with is_compare=True
    - emulator-5560 in ip: runs with is_compare=False (always runs,
      independent of interval)
    - general device (not 5558, not 5560, not phone-OCR): runs only if
      elapsed since last run >= lamp_check_interval hours; records the
      time on success
    - emulator-5558 is completely excluded from general + 5560 branches
    - stage != 主頁面: logs mismatch via log_main_page_mismatch (general
      branch also emits an extra log line before the mismatch)
"""
from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest


# Stub heavy native/network modules before importing the module-under-test.
# Same pattern as tests/test_wake_loop_escape.py.
for _name in ("opencc", "paddleocr", "img_tools"):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        if _name == "opencc":
            _m.OpenCC = lambda *a, **kw: types.SimpleNamespace(convert=lambda s: s)
        sys.modules[_name] = _m

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

# Stub Open_gold_paddle_ocr + opengold_v2 so we don't pull in adb + playwright.
if "Open_gold_paddle_ocr" not in sys.modules:
    _gp = types.ModuleType("Open_gold_paddle_ocr")
    _gp.open_the_gold = lambda *a, **kw: None
    sys.modules["Open_gold_paddle_ocr"] = _gp

if "opengold_v2" not in sys.modules:
    # Make it a real *package* (set __path__) so sibling real submodules like
    # opengold_v2.lamp_startup still import, while we stub the heavy lamp_service.
    import os as _os
    _og = types.ModuleType("opengold_v2")
    _og.__path__ = [_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "opengold_v2")]
    sys.modules["opengold_v2"] = _og
if "opengold_v2.lamp_service" not in sys.modules:
    _ls = types.ModuleType("opengold_v2.lamp_service")
    class _FakeLampSvc:
        def __init__(self, *a, **kw): pass
        def run(self, *a, **kw): return True
    _ls.LampService = _FakeLampSvc
    sys.modules["opengold_v2.lamp_service"] = _ls

# NOTE: do NOT stub `runtime_services` as a package — doing so makes
# `from runtime_services.device_runtime_service import ...` fail in other
# test files that run after us. We let the real package load; individual
# functions (e.g. use_phone_ocr_lamp_mode) are monkeypatched per-test.
# We DO need to stub `device` (used by runtime_services.device_scan_service)
# to avoid pulling in adb operations.
if "device" not in sys.modules:
    _dev = types.ModuleType("device")
    _dev.get_adb_devices = lambda *a, **k: []
    _dev.close_notification = lambda *a, **k: None
    _dev.open_notification = lambda *a, **k: None
    sys.modules["device"] = _dev


@pytest.fixture(autouse=True)
def _ensure_real_logger(monkeypatch):
    import importlib
    mod = importlib.import_module("game_actions.lamp_scheduler")
    if getattr(mod, "logger", None) is None:
        monkeypatch.setattr(mod, "logger", logging.getLogger("test_lamp_scheduler"))


@pytest.fixture
def lamp_mod():
    import importlib
    return importlib.import_module("game_actions.lamp_scheduler")


@pytest.fixture
def fake_config(monkeypatch, lamp_mod):
    """Replace config_manager.get_device_config with a dict-returning stub."""
    configs: dict[str, dict] = {}

    def _get(ip: str):
        return configs.get(ip, {})

    monkeypatch.setattr(lamp_mod.config_manager, "get_device_config", _get)
    return configs


@pytest.fixture
def fake_bot_state(monkeypatch, lamp_mod):
    calls: list[dict] = []
    monkeypatch.setattr(
        lamp_mod.bot_state, "update_state",
        lambda ip, **kw: calls.append({"ip": ip, **kw}),
    )
    return calls


@pytest.fixture
def mismatch_log(monkeypatch, lamp_mod):
    calls: list[dict] = []

    def _log(device_obj, ip, stage, task, reason):
        calls.append({"ip": ip, "stage": stage, "task": task, "reason": reason})
        return "/tmp/shot.png"

    monkeypatch.setattr(lamp_mod, "log_main_page_mismatch", _log)
    return calls


@pytest.fixture
def captured_lamp_calls(monkeypatch, lamp_mod):
    calls: list[dict] = []

    def _fake_run_lamp(d, ip, lamp_dur, is_compare=True):
        calls.append({"ip": ip, "lamp_dur": lamp_dur, "is_compare": is_compare})
        return True

    monkeypatch.setattr(lamp_mod, "_run_lamp", _fake_run_lamp)
    return calls


# ---------------------------------------------------------------------------
# _run_lamp routing
# ---------------------------------------------------------------------------

def test_run_lamp_routes_to_opengold_v2_when_flag_set(lamp_mod, fake_config, monkeypatch):
    fake_config["emu"] = {"use_opengold_v2": True,
                          "ws_token": {"lamp_min_keep": 500000}}
    v2_calls: list[dict] = []

    class FakeSvc:
        def __init__(self, d, device_ip):
            v2_calls.append({"init_ip": device_ip})

        def run(self, times, is_compare, min_keep=0):
            v2_calls.append({"run_times": times, "is_compare": is_compare,
                             "min_keep": min_keep})

    monkeypatch.setattr(lamp_mod, "_LampServiceV2", FakeSvc)
    monkeypatch.setattr(lamp_mod.random, "randint", lambda a, b: 0)  # no jitter

    lamp_mod._run_lamp(SimpleNamespace(), "emu", 300, is_compare=True)
    # min_keep 從 ws_token.lamp_min_keep 帶入（WS 失敗 fallback 時仍尊重保留量）
    assert v2_calls == [{"init_ip": "emu"},
                        {"run_times": 300, "is_compare": True, "min_keep": 500000}]


def test_run_lamp_always_routes_to_v2_even_without_flag(lamp_mod, fake_config, monkeypatch):
    """V1(Open_gold_paddle_ocr)已廢棄：即使 config 沒有 use_opengold_v2，也一律走 V2。"""
    fake_config["emu"] = {}  # 無 use_opengold_v2 旗標、無 ws_token → min_keep 預設 0
    v2_calls: list[dict] = []

    class FakeSvc:
        def __init__(self, d, device_ip):
            v2_calls.append({"init_ip": device_ip})

        def run(self, times, is_compare, min_keep=0):
            v2_calls.append({"run_times": times, "is_compare": is_compare,
                             "min_keep": min_keep})

    monkeypatch.setattr(lamp_mod, "_LampServiceV2", FakeSvc)
    monkeypatch.setattr(lamp_mod.random, "randint", lambda a, b: 0)

    lamp_mod._run_lamp(SimpleNamespace(), "emu", 300, is_compare=False)
    assert v2_calls == [{"init_ip": "emu"},
                        {"run_times": 300, "is_compare": False, "min_keep": 0}]


# ---------------------------------------------------------------------------
# _run_lamp_if_due — phone-OCR mode
# ---------------------------------------------------------------------------

def test_phone_ocr_mode_runs_when_on_main_page(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    """Phone-OCR branch fires with is_compare=True; the three branches are
    mutually exclusive so general branch must not also fire."""
    fake_config["phone-ip"] = {"lamp_duration_sec": 200, "lamp_check_interval": 2}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: ip == "phone-ip")
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "phone-ip", "主頁面")

    assert captured_lamp_calls == [{"ip": "phone-ip", "lamp_dur": 200, "is_compare": True}]


def test_phone_ocr_mode_does_not_also_trigger_general_branch(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    """Regression: phone-OCR device must NOT also run the general-interval
    branch. Previously the first `if` block did not return, so phone-OCR
    devices fell through and ran the lamp twice per cycle whenever the
    interval threshold also passed."""
    fake_config["phone-ip"] = {"lamp_duration_sec": 200, "lamp_check_interval": 2}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: ip == "phone-ip")
    # No prior record → general branch would fire under the old buggy code.
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    recorded: list[dict] = []
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: recorded.append({"ip": ip, "name": name}))

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "phone-ip", "主頁面")

    assert captured_lamp_calls == [{"ip": "phone-ip", "lamp_dur": 200, "is_compare": True}]
    # General branch's bookkeeping must NOT fire for phone-OCR devices.
    assert recorded == []


def test_phone_ocr_mode_logs_mismatch_when_not_on_main_page(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, mismatch_log, monkeypatch,
):
    fake_config["phone-ip"] = {"lamp_duration_sec": 200}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: ip == "phone-ip")

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "phone-ip", "載入中")

    assert captured_lamp_calls == []
    assert any(c["task"] == "開神燈 (OCR)" for c in mismatch_log)


# ---------------------------------------------------------------------------
# _run_lamp_if_due — 5560 branch
# ---------------------------------------------------------------------------

def test_emulator_5560_runs_with_is_compare_false(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    fake_config["emulator-5560"] = {"lamp_duration_sec": 150}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5560", "主頁面")

    assert captured_lamp_calls == [{"ip": "emulator-5560", "lamp_dur": 150, "is_compare": False}]


# ---------------------------------------------------------------------------
# _run_lamp_if_due — general branch (interval-gated)
# ---------------------------------------------------------------------------

def test_general_branch_runs_when_no_prior_record(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    fake_config["emulator-5554"] = {"lamp_check_interval": 2, "lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    recorded: list[dict] = []
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: recorded.append({"ip": ip, "name": name}))

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")

    assert captured_lamp_calls == [{"ip": "emulator-5554", "lamp_dur": 300, "is_compare": True}]
    assert recorded == [{"ip": "emulator-5554", "name": "general_lamp_last_execution"}]


def test_general_branch_does_not_record_when_lamp_service_fails(
    lamp_mod, fake_config, fake_bot_state, monkeypatch,
):
    fake_config["emulator-5554"] = {"lamp_check_interval": 2, "lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    monkeypatch.setattr(lamp_mod, "_run_lamp", lambda *args, **kwargs: False)
    recorded: list[dict] = []
    monkeypatch.setattr(
        lamp_mod, "time_recording",
        lambda ip, name: recorded.append({"ip": ip, "name": name}),
    )

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")

    assert recorded == []


def test_general_branch_skips_when_within_interval(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    import time
    fake_config["emulator-5554"] = {"lamp_check_interval": 2, "lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    # last run 1h ago, interval is 2h → should skip
    monkeypatch.setattr(
        lamp_mod, "return_time",
        lambda ip, name: {"timestamp": time.time() - 3600},
    )
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")

    assert captured_lamp_calls == []


def test_general_branch_runs_when_past_interval(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    import time
    fake_config["emulator-5554"] = {"lamp_check_interval": 1, "lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    # last run 2h ago, interval is 1h → should run
    monkeypatch.setattr(
        lamp_mod, "return_time",
        lambda ip, name: {"timestamp": time.time() - 7200},
    )
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")

    assert captured_lamp_calls == [{"ip": "emulator-5554", "lamp_dur": 300, "is_compare": True}]


def test_general_branch_invalid_interval_falls_back_to_2h(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    import time
    # lamp_check_interval=0 is invalid → fallback 2h. Last run 1.5h ago → skip.
    fake_config["emulator-5554"] = {"lamp_check_interval": 0, "lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    monkeypatch.setattr(
        lamp_mod, "return_time",
        lambda ip, name: {"timestamp": time.time() - 5400},
    )
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")

    assert captured_lamp_calls == []  # 1.5h < 2h fallback


# ---------------------------------------------------------------------------
# _run_lamp_if_due — 5558 exclusion
# ---------------------------------------------------------------------------

def test_emulator_5558_skipped_from_general_and_5560_branches(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    fake_config["emulator-5558"] = {"lamp_duration_sec": 300}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    # Neither return_time nor time_recording should be called since 5558 is excluded
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5558", "主頁面")

    assert captured_lamp_calls == []


# ---------------------------------------------------------------------------
# step_deadline: dashboard counts down from an absolute epoch, not the digits
# baked into the step string. The step push must carry step_deadline ~ now +
# lamp_dur and must NOT embed a "(Ns)" label any more.
# ---------------------------------------------------------------------------

def test_general_branch_pushes_step_deadline_for_countdown(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    import time

    fake_config["emulator-5554"] = {"lamp_check_interval": 1, "lamp_duration_sec": 2400}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: False)
    monkeypatch.setattr(lamp_mod, "return_time", lambda ip, name: None)
    monkeypatch.setattr(lamp_mod, "time_recording", lambda ip, name: None)

    before = time.time()
    lamp_mod._run_lamp_if_due(SimpleNamespace(), "emulator-5554", "主頁面")
    after = time.time()

    pushes = [c for c in fake_bot_state if c.get("task") == "開神燈"]
    assert pushes, "expected a 開神燈 state push at lamp start"
    push = pushes[-1]
    assert "step_deadline" in push
    assert before + 2400 <= push["step_deadline"] <= after + 2400
    # the brittle embedded-seconds label is gone (frontend counts down from deadline)
    assert "s)" not in (push.get("step") or "")


def test_phone_ocr_branch_pushes_step_deadline(
    lamp_mod, fake_config, fake_bot_state, captured_lamp_calls, monkeypatch,
):
    import time

    fake_config["phone-ip"] = {"lamp_duration_sec": 200}
    monkeypatch.setattr(lamp_mod, "use_phone_ocr_lamp_mode", lambda ip: ip == "phone-ip")

    before = time.time()
    lamp_mod._run_lamp_if_due(SimpleNamespace(), "phone-ip", "主頁面")
    after = time.time()

    pushes = [c for c in fake_bot_state if c.get("task") == "開神燈 (OCR)"]
    assert pushes, "expected a 開神燈 (OCR) state push at lamp start"
    push = pushes[-1]
    assert "step_deadline" in push
    assert before + 200 <= push["step_deadline"] <= after + 200
    assert "s)" not in (push.get("step") or "")
