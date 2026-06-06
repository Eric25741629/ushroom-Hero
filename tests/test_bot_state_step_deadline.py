"""step_deadline: a per-step countdown anchor immune to last_update churn.

The dashboard countdown used to derive remaining-time from the integer embedded
in the step string minus `last_update`. Background heartbeats (e.g.
update_watchdog_probe) refresh `last_update` every probe, so the countdown froze
near the lamp duration (~2400s) and rendered as an ugly float. `step_deadline`
stores an absolute epoch the frontend can count down from directly.

It is tied to the *step*: setting a new step without a deadline clears any stale
one so the dashboard stops ticking a finished lamp's countdown.
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


def test_update_state_stores_step_deadline():
    ip = "test-deadline-store-5554"
    _cleanup(ip)
    try:
        deadline = time.time() + 2400
        bot_state.update_state(ip, task="開神燈", step="執行中", step_deadline=deadline)
        st = bot_state.get_all_states()[ip]
        assert st["step_deadline"] == deadline
    finally:
        _cleanup(ip)


def test_new_step_without_deadline_clears_stale_deadline():
    ip = "test-deadline-clear-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="開神燈", step="執行中", step_deadline=time.time() + 2400)
        assert "step_deadline" in bot_state.get_all_states()[ip]

        # next task sets a fresh step but no deadline -> the countdown must vanish
        bot_state.update_state(ip, task="農場任務", step="種植中")
        assert "step_deadline" not in bot_state.get_all_states()[ip]
    finally:
        _cleanup(ip)


def test_step_deadline_survives_metadata_only_update():
    """A log-only / task-only update (step is None) must not drop the countdown."""
    ip = "test-deadline-survive-5554"
    _cleanup(ip)
    try:
        deadline = time.time() + 300
        bot_state.update_state(ip, task="開神燈", step="執行中", step_deadline=deadline)
        bot_state.update_state(ip, log="progress line")  # step is None
        assert bot_state.get_all_states()[ip]["step_deadline"] == deadline
    finally:
        _cleanup(ip)
