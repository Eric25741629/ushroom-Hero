"""Per-device WS -> H5 handoff signal stored by bot_state."""
import bot_state


def _clear(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._ws_h5_handoff_ok.pop(ip, None)


def test_ws_h5_handoff_defaults_false_and_round_trips():
    ip = "handoff-round-trip"
    _clear(ip)
    try:
        assert bot_state.get_ws_h5_handoff_ok(ip) is False
        bot_state.set_ws_h5_handoff_ok(ip, True)
        assert bot_state.get_ws_h5_handoff_ok(ip) is True
        bot_state.set_ws_h5_handoff_ok(ip, False)
        assert bot_state.get_ws_h5_handoff_ok(ip) is False
    finally:
        _clear(ip)


def test_ws_h5_handoff_is_isolated_per_device():
    first = "handoff-first"
    second = "handoff-second"
    _clear(first)
    _clear(second)
    try:
        bot_state.set_ws_h5_handoff_ok(first, True)
        assert bot_state.get_ws_h5_handoff_ok(first) is True
        assert bot_state.get_ws_h5_handoff_ok(second) is False
    finally:
        _clear(first)
        _clear(second)
