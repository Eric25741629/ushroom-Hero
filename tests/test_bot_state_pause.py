"""bot_state.is_paused: non-blocking peek used by WS should_abort hooks.

check_pause() blocks; the WS pipeline polls a non-blocking predicate instead,
so pause must be observable without blocking. These guard that primitive.
"""
import bot_state


def test_is_paused_reflects_set_pause():
    ip = "test-pause-primitive"
    bot_state.init_device(ip)
    assert bot_state.is_paused(ip) is False        # default: running
    bot_state.set_pause(ip, True)
    assert bot_state.is_paused(ip) is True
    bot_state.set_pause(ip, False)
    assert bot_state.is_paused(ip) is False


def test_is_paused_false_for_unregistered_device():
    assert bot_state.is_paused("never-registered-device-xyz") is False
