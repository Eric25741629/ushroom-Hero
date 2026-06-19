"""append_log: the fast path that bridges per-device file logs onto the dashboard.

The dashboard log-box reads bot_state._states[ip]["logs"] via /api/status. It was
previously fed only by the ~10 rare update_state(log=...) call sites, so the box
rendered near-empty. append_log lets logging_utils.BotStateLogHandler forward every
logger line into the ring buffer without faking a heartbeat (no last_update churn)
and without auto-creating phantom devices.
"""
import time

import bot_state


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._local_device_ids.discard(ip)


def test_append_log_adds_line_to_existing_device():
    ip = "test-appendlog-add-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="挖礦", step="掘進中")
        bot_state.append_log(ip, "10:00:00 - 開始挖礦")
        logs = bot_state.get_all_states()[ip]["logs"]
        assert logs[-1] == "10:00:00 - 開始挖礦"
    finally:
        _cleanup(ip)


def test_append_log_ignores_unknown_device():
    """Stray logging before init_device must not spawn a phantom dashboard card."""
    ip = "test-appendlog-unknown-5554"
    _cleanup(ip)
    try:
        bot_state.append_log(ip, "orphan line")
        assert ip not in bot_state.get_all_states()
    finally:
        _cleanup(ip)


def test_append_log_does_not_touch_last_update():
    """A log line is not a heartbeat; last_update must stay frozen."""
    ip = "test-appendlog-heartbeat-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="待命")
        before = bot_state.get_all_states()[ip]["last_update"]
        time.sleep(0.02)
        bot_state.append_log(ip, "some progress")
        after = bot_state.get_all_states()[ip]["last_update"]
        assert after == before
    finally:
        _cleanup(ip)


def test_append_log_caps_ring_buffer():
    ip = "test-appendlog-cap-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="待命")
        for i in range(bot_state._MAX_DEVICE_LOGS + 50):
            bot_state.append_log(ip, f"line {i}")
        logs = bot_state.get_all_states()[ip]["logs"]
        assert len(logs) == bot_state._MAX_DEVICE_LOGS
        # oldest dropped, newest kept
        assert logs[-1] == f"line {bot_state._MAX_DEVICE_LOGS + 49}"
        assert logs[0] == f"line {50}"
    finally:
        _cleanup(ip)


def test_append_log_skips_blank():
    ip = "test-appendlog-blank-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="待命")
        bot_state.append_log(ip, "   ")
        bot_state.append_log(ip, None)
        assert bot_state.get_all_states()[ip]["logs"] == []
    finally:
        _cleanup(ip)


def test_update_state_log_uses_new_cap():
    """update_state(log=...) shares the same enlarged ring-buffer cap."""
    ip = "test-appendlog-updatecap-5554"
    _cleanup(ip)
    try:
        for i in range(bot_state._MAX_DEVICE_LOGS + 5):
            bot_state.update_state(ip, log=f"evt {i}")
        logs = bot_state.get_all_states()[ip]["logs"]
        assert len(logs) == bot_state._MAX_DEVICE_LOGS
    finally:
        _cleanup(ip)
