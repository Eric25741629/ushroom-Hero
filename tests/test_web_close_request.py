"""Unit tests for the close-browser one-shot request flag in bot_state.

The live-view "關閉瀏覽器" button sets this flag from the Flask thread; the owning
device thread consumes it (check_web_close) to close its headless browser on its
own thread. Semantics mirror request_force_sleep/check_force_sleep but WITHOUT any
sleep / pause side effects (device keeps running, cold-restarts the browser).
"""
from __future__ import annotations

import bot_state


def test_check_web_close_is_false_when_no_request():
    ip = "test-emulator-webclose-0"
    assert bot_state.check_web_close(ip) is False


def test_request_then_consume_is_one_shot():
    ip = "test-emulator-webclose-1"
    bot_state.request_web_close(ip)
    assert bot_state.check_web_close(ip) is True
    # One-shot: a second consume returns False (flag was popped).
    assert bot_state.check_web_close(ip) is False


def test_request_web_close_is_per_device():
    ip_a = "test-emulator-webclose-a"
    ip_b = "test-emulator-webclose-b"
    bot_state.request_web_close(ip_a)
    # Consuming a different device must not clear ip_a's pending request.
    assert bot_state.check_web_close(ip_b) is False
    assert bot_state.check_web_close(ip_a) is True


def test_request_web_close_does_not_touch_pause_or_force_sleep():
    """Distinct from force-sleep: must not set force-sleep or change pause state."""
    ip = "test-emulator-webclose-2"
    bot_state.request_web_close(ip)
    # No force-sleep flag was set as a side effect.
    assert bot_state.check_force_sleep(ip) is False
    # Consume our own flag to leave global state clean.
    assert bot_state.check_web_close(ip) is True
