"""Regression: online-check no longer wakes a checker device.

Online-check is served out-of-loop by the master-only `online_check_service`
(pure WS, idle checker), so a pending request must NOT interrupt a sleeping
checker. The old SKIP_SLEEP-every-checker path woke the whole web_h5 fleet
every ~30s and cold-started browsers (帳號一直在重啟); these tests lock the
decoupling — a checker with a pending online-check still wakes only on its own
schedule / manual override, never on the online-check request itself.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest


@pytest.fixture
def drs(monkeypatch):
    # Stub utils.mumu_control so importing device_runtime_service is cheap.
    if "utils.mumu_control" not in sys.modules:
        m = types.ModuleType("utils.mumu_control")
        m.MuMuController = object
        m.discover_control_exe = lambda *a, **k: None
        sys.modules["utils.mumu_control"] = m
    import importlib

    mod = importlib.import_module("runtime_services.device_runtime_service")
    # Never actually sleep — the 1s poll loop must run instantly under test.
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    return mod, monkeypatch


def _fake_state(**overrides):
    base = dict(
        check_force_sleep=lambda ip: False,
        check_pause=lambda ip: False,
        check_skip_sleep=lambda ip: False,
        consume_wake_override=lambda ip: None,
        has_pending_web_launch_request=lambda ip: False,
        has_pending_online_check_request=lambda ip: False,
        # Default checker list == legacy default config (only 5554 is a checker),
        # so the early-wake gate (is_online_check_checker AND pending) fires for
        # 5554 and never for other devices.
        is_online_check_checker=lambda ip: ip == "emulator-5554",
        update_state=lambda *a, **k: None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_checker_does_not_early_wake_on_pending_online_check(drs):
    mod, monkeypatch = drs
    # A checker (5554) with a pending online-check must NOT early-wake: the gate
    # was removed (online_check_service serves it out-of-loop). With wake_ts
    # already past, it wakes on schedule (False); the removed gate would have
    # returned True before the time check.
    from runtime_services import wake_override_service as wake_override

    fake = _fake_state(has_pending_online_check_request=lambda ip: True)
    monkeypatch.setattr(mod, "bot_state", fake)
    monkeypatch.setattr(wake_override, "bot_state", fake)
    past = 1.0
    result = mod.sleep_until_wake_or_interrupt(
        "emulator-5554", past, logging.getLogger("t")
    )
    assert result is False


def test_non_5554_does_not_early_wake_on_online_check_flag(drs):
    mod, monkeypatch = drs
    # Even with the flag set, a non-checker device must not treat it as its
    # own interrupt. With wake_ts already past, it returns False (reached
    # the scheduled wake), proving the checker-only branch didn't fire.
    # Also patch wake_override_service.bot_state so any real bot_state cross-test
    # wake_override doesn't leak into this deterministic check.
    from runtime_services import wake_override_service as wake_override

    fake = _fake_state(has_pending_online_check_request=lambda ip: True)
    monkeypatch.setattr(mod, "bot_state", fake)
    monkeypatch.setattr(wake_override, "bot_state", fake)
    past = 1.0
    result = mod.sleep_until_wake_or_interrupt(
        "emulator-5560", past, logging.getLogger("t")
    )
    assert result is False


def test_manual_wake_override_interrupts_sleep_now(drs):
    mod, monkeypatch = drs
    from runtime_services import wake_override_service as wake_override

    updates = []
    now_values = iter([100.0, 100.0, 100.0])
    monkeypatch.setattr(mod.time, "time", lambda: next(now_values))
    fake_state = _fake_state(
        consume_wake_override=lambda ip: 100.0,
        update_state=lambda ip, **kw: updates.append({"ip": ip, **kw}),
    )
    monkeypatch.setattr(mod, "bot_state", fake_state)
    monkeypatch.setattr(wake_override, "bot_state", fake_state)
    far_future = 10_000.0
    result = mod.sleep_until_wake_or_interrupt(
        "emulator-5554", far_future, logging.getLogger("t")
    )
    assert result is True
    assert updates[-1]["next_wake_at"] == 100.0
