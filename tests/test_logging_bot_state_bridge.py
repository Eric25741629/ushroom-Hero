"""BotStateLogHandler: bridges per-device file loggers onto the dashboard log-box.

Root cause this guards against: the dashboard log-box (templates/dashboard.html
log-${ip}) renders bot_state._states[ip]["logs"], which was fed only by the ~10
rare update_state(log=...) sites — so it stayed near-empty while devices emitted
hundreds of logger.info lines. This handler forwards every device logger line into
the ring buffer via bot_state.append_log, keyed by the "logger_<id>" / "miner_<id>"
logger name.
"""
import logging

import bot_state
from utils.logging_utils import (
    BotStateLogHandler,
    _attach_bot_state_handler,
    _device_id_from_logger_name,
)


def _cleanup(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._states.pop(ip, None)
        bot_state._pause_events.pop(ip, None)
        bot_state._signals.pop(ip, None)
        bot_state._locks.pop(ip, None)
        bot_state._local_device_ids.discard(ip)


def test_device_id_parsed_from_logger_name():
    assert _device_id_from_logger_name("logger_emulator-5554") == "emulator-5554"
    assert _device_id_from_logger_name("miner_emulator-5554") == "emulator-5554"
    assert _device_id_from_logger_name("logger_127.0.0.1:5555") == "127.0.0.1:5555"
    # WS-only loggers must also bridge to the dashboard (ws_farm_ / ws_mining_).
    assert _device_id_from_logger_name("ws_farm_emulator-5554") == "emulator-5554"
    assert _device_id_from_logger_name("ws_mining_emulator-5554") == "emulator-5554"
    # original device_id (not the filename-safe form) is preserved -> exact ip key
    assert _device_id_from_logger_name("ws_farm_127.0.0.1:5555") == "127.0.0.1:5555"
    assert _device_id_from_logger_name("ws_mining_127.0.0.1:5555") == "127.0.0.1:5555"
    assert _device_id_from_logger_name("ocr_trace_emulator-5554") is None
    assert _device_id_from_logger_name("__main__") is None


def test_ws_loggers_forward_lines_to_bot_state():
    """WS-only farm/mining logger lines must land on the dashboard log-box too."""
    for prefix, msg in (("ws_farm_", "豐收卡循環完成"),
                        ("ws_mining_", "WS 挖礦掘進中")):
        ip = f"test-bridge-{prefix}5554"
        _cleanup(ip)
        log = logging.getLogger(f"{prefix}{ip}")
        try:
            bot_state.update_state(ip, task="待命")  # device must exist first
            log.handlers[:] = []
            log.propagate = False
            log.setLevel(logging.INFO)
            log.addHandler(BotStateLogHandler())

            log.info(msg)

            logs = bot_state.get_all_states()[ip]["logs"]
            assert any(msg in line for line in logs)
        finally:
            log.handlers[:] = []
            _cleanup(ip)


def test_handler_forwards_line_to_bot_state():
    ip = "test-bridge-fwd-5554"
    _cleanup(ip)
    try:
        bot_state.update_state(ip, task="待命")  # device must exist first
        log = logging.getLogger(f"logger_{ip}")
        log.handlers[:] = []
        log.propagate = False
        log.setLevel(logging.INFO)
        log.addHandler(BotStateLogHandler())

        log.info("購買種子完成")

        logs = bot_state.get_all_states()[ip]["logs"]
        assert any("購買種子完成" in line for line in logs)
        # compact "HH:MM:SS - message" shape, no file:line noise
        assert " - 購買種子完成" in logs[-1]
    finally:
        log.handlers[:] = []
        _cleanup(ip)


def test_handler_skips_non_device_logger():
    """A non-device logger name must not forward (no phantom device key)."""
    ip = "some-unrelated-id"
    _cleanup(ip)
    try:
        log = logging.getLogger("__some_lib__")
        log.handlers[:] = []
        log.propagate = False
        log.setLevel(logging.INFO)
        log.addHandler(BotStateLogHandler())
        log.info("library chatter")
        assert ip not in bot_state.get_all_states()
    finally:
        log.handlers[:] = []


def test_attach_is_idempotent():
    log = logging.getLogger("logger_test-bridge-idem-5554")
    log.handlers[:] = []
    _attach_bot_state_handler(log)
    _attach_bot_state_handler(log)
    bridges = [h for h in log.handlers if isinstance(h, BotStateLogHandler)]
    assert len(bridges) == 1
    log.handlers[:] = []
