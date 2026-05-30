"""Regression: 5554 wakes immediately on a pending 5558 online-check request.

The even/odd wake-parity scheduling (`wake_hour_parity`) only changes the
*periodic* wake time. 5558's online-check must still get an immediate
response from 5554 — that is interrupt-driven in
`sleep_until_wake_or_interrupt` (1-second poll, returns True the moment a
request is pending), independent of the scheduled `wake_ts`. These tests
lock that guarantee so parity — or any future scheduling change — cannot
silently introduce a delay.
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
        has_pending_web_launch_request=lambda ip: False,
        has_pending_online_check_request=lambda ip: False,
        update_state=lambda *a, **k: None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_5554_wakes_immediately_on_pending_online_check(drs):
    mod, monkeypatch = drs
    monkeypatch.setattr(
        mod,
        "bot_state",
        _fake_state(has_pending_online_check_request=lambda ip: ip == "emulator-5554"),
    )
    far_future = 9_999_999_999.0  # a parity-scheduled wake far in the future
    result = mod.sleep_until_wake_or_interrupt(
        "emulator-5554", far_future, logging.getLogger("t")
    )
    # Interrupted immediately by the pending request — did NOT wait for wake_ts.
    assert result is True


def test_non_5554_does_not_early_wake_on_online_check_flag(drs):
    mod, monkeypatch = drs
    # Even with the flag set, a non-checker device must not treat it as its
    # own interrupt. With wake_ts already past, it returns False (reached
    # the scheduled wake), proving the 5554-only branch didn't fire.
    monkeypatch.setattr(
        mod,
        "bot_state",
        _fake_state(has_pending_online_check_request=lambda ip: True),
    )
    past = 1.0
    result = mod.sleep_until_wake_or_interrupt(
        "emulator-5560", past, logging.getLogger("t")
    )
    assert result is False
