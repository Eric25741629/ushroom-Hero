"""Tests for the consolidated per-device signal registry (`_signals`).

Covers:
- one-shot semantics via the public API (set/check round-trips once)
- per-device isolation
- the cleanup-leak BUG FIX: clear_offline_devices() / sweep_stale_states()
  now clear EVERY channel in one pop, so web_close / manual_release no longer
  leak to the next device that reuses the same IP key.
- request_force_sleep() still raises FORCE, clears a prior skip/manual, and
  sets the pause event (mirrors tests/test_bot_state_safety.py).
"""
import time

import bot_state


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._wake_overrides.pop(ip, None)
        bot_state._locks.pop(ip, None)


# --------------------------------------------------------------------------
# One-shot semantics via public API
# --------------------------------------------------------------------------


def test_skip_sleep_is_one_shot():
    ip = "test-signals-skip-5554"
    _cleanup(ip)
    try:
        bot_state.set_skip_sleep(ip)
        assert bot_state.check_skip_sleep(ip) is True
        assert bot_state.check_skip_sleep(ip) is False
    finally:
        _cleanup(ip)


def test_skip_sleep_also_requests_immediate_wake():
    ip = "test-signals-skip-wake-5554"
    _cleanup(ip)
    try:
        before = time.time()
        bot_state.set_skip_sleep(ip)
        wake_ts = bot_state.consume_wake_override(ip)
        after = time.time()

        assert wake_ts is not None
        assert before <= wake_ts <= after
    finally:
        _cleanup(ip)


def test_check_skip_sleep_consumes_associated_wake_override():
    ip = "test-signals-skip-consume-wake-5554"
    _cleanup(ip)
    try:
        bot_state.set_skip_sleep(ip)

        assert bot_state.check_skip_sleep(ip) is True
        assert bot_state.consume_wake_override(ip) is None
    finally:
        _cleanup(ip)


def test_consume_wake_override_consumes_associated_skip_sleep():
    ip = "test-signals-wake-consume-skip-5554"
    _cleanup(ip)
    try:
        bot_state.set_skip_sleep(ip)

        assert bot_state.consume_wake_override(ip) is not None
        assert bot_state.check_skip_sleep(ip) is False
    finally:
        _cleanup(ip)


def test_web_close_is_one_shot():
    ip = "test-signals-webclose-5554"
    _cleanup(ip)
    try:
        bot_state.request_web_close(ip)
        assert bot_state.check_web_close(ip) is True
        assert bot_state.check_web_close(ip) is False
    finally:
        _cleanup(ip)


def test_manual_release_is_one_shot():
    ip = "test-signals-manual-5554"
    _cleanup(ip)
    try:
        bot_state.set_manual_release(ip)
        assert bot_state.check_manual_release(ip) is True
        assert bot_state.check_manual_release(ip) is False
    finally:
        _cleanup(ip)


def test_clear_skip_sleep_does_not_consume_other_signals():
    """clear_skip_sleep removes only SKIP without consuming, leaving others."""
    ip = "test-signals-clear-5554"
    _cleanup(ip)
    try:
        bot_state.set_skip_sleep(ip)
        bot_state.set_manual_release(ip)
        bot_state.clear_skip_sleep(ip)
        assert bot_state.check_skip_sleep(ip) is False
        # manual_release must still be present (clear_skip_sleep is targeted)
        assert bot_state.check_manual_release(ip) is True
    finally:
        _cleanup(ip)


# --------------------------------------------------------------------------
# Per-device isolation
# --------------------------------------------------------------------------


def test_signals_are_per_device_isolated():
    ip_a = "test-signals-iso-a"
    ip_b = "test-signals-iso-b"
    _cleanup(ip_a)
    _cleanup(ip_b)
    try:
        bot_state.set_skip_sleep(ip_a)
        # ip_b must be unaffected
        assert bot_state.check_skip_sleep(ip_b) is False
        # ip_a still has its flag
        assert bot_state.check_skip_sleep(ip_a) is True
    finally:
        _cleanup(ip_a)
        _cleanup(ip_b)


# --------------------------------------------------------------------------
# BUG FIX: cleanup clears EVERY channel (no leak to a reused IP)
# --------------------------------------------------------------------------


def test_clear_offline_devices_clears_web_close_and_manual_release():
    """clear_offline_devices() previously forgot _web_close_flags; sweep
    forgot manual_release+web_close. The consolidated _signals.pop fixes both.
    """
    ip = "test-signals-offline-leak"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.request_web_close(ip)
        bot_state.set_manual_release(ip)

        # mark OFFLINE under the device lock like the existing tests do
        with bot_state.get_device_lock(ip):
            bot_state._states[ip]["status"] = "OFFLINE"

        bot_state.clear_offline_devices()

        # No residual signals leak to a future device reusing this IP.
        assert bot_state.check_web_close(ip) is False
        assert bot_state.check_manual_release(ip) is False
    finally:
        _cleanup(ip)


def test_clear_offline_devices_clears_wake_override():
    ip = "test-signals-offline-wake-override"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_wake_override(ip, 30)
        bot_state.set_offline(ip)
        bot_state.clear_offline_devices()
        assert bot_state.consume_wake_override(ip) is None
    finally:
        _cleanup(ip)


def test_sweep_stale_states_clears_manual_release_and_web_close_for_remote():
    """sweep_stale_states() removal branch must clear ALL signal channels for a
    stale remote device, not just skip/force.
    """
    ip = "worker1:1.2.3.4"  # remote key (has colon, not a local device)
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="遠端", step="同步中")
        bot_state.request_web_close(ip)
        bot_state.set_manual_release(ip)

        # Make the heartbeat stale enough to trigger the remote-removal branch.
        with bot_state.get_device_lock(ip):
            bot_state._states[ip]["last_update"] = time.time() - 9999

        bot_state.sweep_stale_states(
            mark_offline_after_sec=20.0, remove_remote_after_sec=120.0
        )

        # Remote device fully purged → signals gone with it.
        assert bot_state.check_web_close(ip) is False
        assert bot_state.check_manual_release(ip) is False
        with bot_state._global_lock:
            assert ip not in bot_state._states
            assert ip not in bot_state._signals
    finally:
        _cleanup(ip)


# --------------------------------------------------------------------------
# request_force_sleep behavior preserved
# --------------------------------------------------------------------------


def test_request_force_sleep_raises_force_and_clears_skip_and_manual():
    ip = "test-signals-force-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_skip_sleep(ip)
        bot_state.set_manual_release(ip)
        assert bot_state.consume_wake_override(ip) is not None
        bot_state.set_skip_sleep(ip)

        bot_state.request_force_sleep(ip, reason="強制休眠")

        st = bot_state.get_all_states()[ip]
        assert st["task"] == "休眠中"
        assert st["step"] == "強制休眠"

        # FORCE is raised and one-shot consumable
        assert bot_state.check_force_sleep(ip) is True
        assert bot_state.check_force_sleep(ip) is False
        # SKIP, its immediate wake override, and MANUAL were cleared by request_force_sleep
        assert bot_state.check_skip_sleep(ip) is False
        assert bot_state.consume_wake_override(ip) is None
        assert bot_state.check_manual_release(ip) is False
    finally:
        _cleanup(ip)


def test_request_force_sleep_sets_pause_event():
    """request_force_sleep() must set (unblock) the per-device pause event."""
    ip = "test-signals-force-pause-5554"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.set_pause(ip, True)
        ev = bot_state.get_pause_event(ip)
        assert ev is not None
        assert ev.is_set() is False  # paused → cleared

        bot_state.request_force_sleep(ip)

        assert ev.is_set() is True  # force sleep wakes the waiter
    finally:
        _cleanup(ip)


def test_request_force_sleep_does_not_clear_web_close():
    """force_sleep clears skip+manual but leaves web_close untouched."""
    ip = "test-signals-force-webclose"
    _cleanup(ip)
    try:
        bot_state.init_device(ip)
        bot_state.request_web_close(ip)

        bot_state.request_force_sleep(ip)

        assert bot_state.check_web_close(ip) is True
    finally:
        _cleanup(ip)


def test_has_pending_web_close_request_does_not_consume():
    ip = "test-signals-peek-webclose"
    _cleanup(ip)
    try:
        bot_state.request_web_close(ip)
        assert bot_state.has_pending_web_close_request(ip) is True
        assert bot_state.has_pending_web_close_request(ip) is True
        assert bot_state.check_web_close(ip) is True
        assert bot_state.has_pending_web_close_request(ip) is False
    finally:
        _cleanup(ip)


# --------------------------------------------------------------------------
# New accessors (Part B)
# --------------------------------------------------------------------------


def test_get_pause_event_returns_none_for_unknown_device():
    ip = "test-signals-nopause-unknown"
    _cleanup(ip)
    assert bot_state.get_pause_event(ip) is None


def test_update_remote_metrics_updates_present_device():
    ip = "test-signals-metrics-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="遠端")
        before = None
        with bot_state.get_device_lock(ip):
            before = bot_state._states[ip].get("last_update")

        bot_state.update_remote_metrics(ip, logs=["a", "b"], avg_screenshot_ms=12.5)

        with bot_state.get_device_lock(ip):
            st = bot_state._states[ip]
            assert st["logs"] == ["a", "b"]
            assert st["avg_screenshot_ms"] == 12.5
            # does NOT touch last_update (heartbeat driven elsewhere)
            assert st.get("last_update") == before
    finally:
        _cleanup(ip)


def test_update_remote_metrics_noop_for_absent_device():
    ip = "test-signals-metrics-absent"
    _cleanup(ip)
    # must not raise and must not create the device
    bot_state.update_remote_metrics(ip, logs=["x"], avg_screenshot_ms=1.0)
    with bot_state._global_lock:
        assert ip not in bot_state._states
