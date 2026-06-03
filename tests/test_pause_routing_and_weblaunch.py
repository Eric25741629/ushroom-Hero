"""Regression tests for two pause/sleep failures.

Bug A — colon-serial misrouting: control-panel control commands (pause /
resume / force_sleep / skip_sleep) were routed local-vs-remote by the
heuristic ``":" in ip``. A TCP-attached local emulator (e.g.
``127.0.0.1:5555``) contains a colon, so its commands were wrongly queued
into the remote-worker command queue and silently dropped — the local
device thread never saw the pause / force-sleep flag.

Bug B — pause defeated by a pending web-launch request: ``check_pause`` has
a carve-out that returns truthy (instead of blocking) when a manual
"open browser" request is pending, so live-view takeover can break through
a paused state. ``MonitoredDevice._pause_guard`` ignored that return value,
so mid-pipeline every device action sailed through the guard and the bot
kept acting while the dashboard showed "paused". The pending request is
only consumed at the top of the wake loop, which is unreachable while the
pipeline runs, so pause stayed defeated for the whole cycle.
"""
from __future__ import annotations

import pytest

import bot_state
# Import eagerly so control_panel_app is cached with the real adb_operations.
# Other test modules (e.g. test_wake_loop_escape) stub adb_operations at import
# time without cleanup; importing here first avoids that cross-test pollution.
import control_panel_app  # noqa: E402,F401


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._web_launch_requests.pop(ip, None)
        local_ids = getattr(bot_state, "_local_device_ids", None)
        if local_ids is not None:
            local_ids.discard(ip)


# --------------------------------------------------------------------------
# Bug A: local-device identification (the reliable local/remote discriminator)
# --------------------------------------------------------------------------


def test_is_local_device_false_before_init():
    ip = "127.0.0.1:5599"
    _cleanup(ip)
    assert bot_state.is_local_device(ip) is False


def test_init_device_marks_tcp_serial_as_local():
    ip = "127.0.0.1:5599"
    _cleanup(ip)
    bot_state.init_device(ip)
    try:
        # A TCP serial has a colon but is unambiguously a local device thread.
        assert bot_state.is_local_device(ip) is True
    finally:
        _cleanup(ip)


def test_remote_worker_id_is_not_local():
    # Remote devices are keyed worker_id:ip and never call init_device().
    rid = "deskmate:emulator-5554"
    _cleanup(rid)
    assert bot_state.is_local_device(rid) is False


def test_sweep_keeps_local_tcp_device_but_purges_stale_remote():
    """A stale local TCP emulator must survive sweep; a stale remote must not.

    Both keys contain a colon — only is_local_device() tells them apart. If the
    local device is purged its _pause_events vanish and pause routing breaks.
    """
    local_ip = "127.0.0.1:5599"
    remote_id = "deskmate:emulator-5554"
    _cleanup(local_ip)
    _cleanup(remote_id)

    bot_state.init_device(local_ip)  # registers as local
    # Force both to look stale (heartbeat far in the past).
    stale = 1.0
    with bot_state.get_device_lock(local_ip):
        bot_state._states[local_ip]["last_update"] = stale
    with bot_state.get_device_lock(remote_id):
        bot_state._states[remote_id] = {
            "status": "ONLINE",
            "task": "x",
            "step": "y",
            "last_update": stale,
            "paused": False,
            "logs": [],
        }

    try:
        bot_state.sweep_stale_states(
            mark_offline_after_sec=5.0, remove_remote_after_sec=10.0
        )
        assert local_ip in bot_state._states, "local TCP device must NOT be purged"
        assert local_ip in bot_state._pause_events, "local pause routing must survive"
        assert remote_id not in bot_state._states, "stale remote must be purged"
    finally:
        _cleanup(local_ip)
        _cleanup(remote_id)


# --------------------------------------------------------------------------
# Bug A: queue_command must route a local TCP device to bot_state, not remote
# --------------------------------------------------------------------------


def test_queue_command_routes_local_tcp_device_to_bot_state(monkeypatch):
    import control_panel_app as cpa  # cached from module-level import

    ip = "127.0.0.1:5599"
    _cleanup(ip)
    bot_state.init_device(ip)

    calls = []
    monkeypatch.setattr(bot_state, "set_pause", lambda i, v: calls.append((i, v)))
    with cpa._commands_lock:
        cpa._remote_commands.pop(ip, None)

    try:
        cpa.queue_command(ip, "paused", True)
        assert calls == [(ip, True)], "local TCP device must reach bot_state.set_pause"
        with cpa._commands_lock:
            assert ip not in cpa._remote_commands, "must NOT be queued as remote"
    finally:
        _cleanup(ip)


def test_queue_command_still_routes_real_remote_to_queue(monkeypatch):
    import control_panel_app as cpa

    rid = "deskmate:emulator-5554"
    _cleanup(rid)

    calls = []
    monkeypatch.setattr(bot_state, "set_pause", lambda i, v: calls.append((i, v)))
    monkeypatch.setattr(cpa, "_push_remote_command_if_possible", lambda i: None)
    with cpa._commands_lock:
        cpa._remote_commands.pop(rid, None)

    try:
        cpa.queue_command(rid, "paused", True)
        assert calls == [], "real remote device must not hit local bot_state"
        with cpa._commands_lock:
            assert cpa._remote_commands.get(rid, {}).get("paused") is True
    finally:
        with cpa._commands_lock:
            cpa._remote_commands.pop(rid, None)
        _cleanup(rid)


# --------------------------------------------------------------------------
# Bug B: a pending web-launch must not silently defeat pause mid-pipeline
# --------------------------------------------------------------------------


@pytest.fixture
def monitored_device():
    import device_wrapper
    from runtime_services.device_runtime_service import (
        ForceSleepRequested,
        WakeLoopInterrupted,
    )

    ip = "test-pauseguard-5599"
    _cleanup(ip)
    bot_state.init_device(ip)

    class _FakeD:
        def __init__(self):
            self.info = {"displayWidth": 540, "displayHeight": 960}
            self.taps = []

        def tap(self, x, y, *a, **k):
            self.taps.append((x, y))
            return True

    fake = _FakeD()
    dev = device_wrapper.MonitoredDevice(fake, ip)
    yield dev, fake, ip, WakeLoopInterrupted, ForceSleepRequested
    _cleanup(ip)


def test_tap_executes_when_not_paused(monitored_device):
    dev, fake, ip, _WLI, _FSR = monitored_device
    dev.tap(100, 200)
    assert fake.taps == [(100, 200)]


def test_pending_weblaunch_while_paused_raises_instead_of_acting(monitored_device):
    dev, fake, ip, WLI, _FSR = monitored_device
    bot_state.set_pause(ip, True)
    bot_state.request_web_launch(ip, payload={"force_headful": False})

    with pytest.raises(WLI):
        dev.tap(100, 200)
    assert fake.taps == [], "device action must NOT execute while paused"


def test_force_sleep_still_raises(monitored_device):
    dev, fake, ip, _WLI, FSR = monitored_device
    bot_state.request_force_sleep(ip)
    with pytest.raises(FSR):
        dev.tap(100, 200)
    assert fake.taps == []
