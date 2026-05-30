"""Regression tests for device.close_notification / open_notification backend guard.

Bug: close_notification() is an Android/ADB-only routine (notification shade,
方向鎖定/勿擾, Messenger bubbles). It is called unconditionally for every device
in utils/wake_up_handler.py. For a web_h5 (PlaywrightGameDevice) backend the old
`hasattr(d, 'open_quick_settings')` guard was satisfied by Playwright's no-op
stub, so execution reached `d(packageNameMatches=...)`. PlaywrightGameDevice has
no __call__, raising `'PlaywrightGameDevice' object is not callable`, logged at
startup as "清理遮擋物時發生錯誤".

The fix gates these Android-only routines on backend_kind == "web_h5".
"""

import logging

from device import close_notification, open_notification


class _FakeWebDevice:
    """Minimal stand-in for PlaywrightGameDevice's surface used by these routines.

    Crucially:
    - backend_kind == "web_h5"  (the real marker)
    - exposes an open_quick_settings() stub (this is what defeated the old guard)
    - is NOT callable (calling d(...) raises TypeError, exactly like the real bug)
    """

    backend_kind = "web_h5"

    def __init__(self):
        self.open_quick_settings_calls = 0
        self.info = {"displayWidth": 540, "displayHeight": 960}

    def open_quick_settings(self):
        self.open_quick_settings_calls += 1
        return True


class _NoopQuery:
    exists = False

    @property
    def info(self):
        return {}

    def click(self, *args, **kwargs):
        return False


class _FakeAdbDevice:
    """Android-style device: no backend_kind, supports selector call syntax."""

    def __init__(self):
        self.open_quick_settings_calls = 0
        self.info = {"displayWidth": 540, "displayHeight": 960}

    def open_quick_settings(self):
        self.open_quick_settings_calls += 1

    def xpath(self, *args, **kwargs):
        return _NoopQuery()

    def click(self, *args, **kwargs):
        return None

    def __call__(self, *args, **kwargs):
        return _NoopQuery()


def test_close_notification_skips_web_h5_backend(caplog):
    dev = _FakeWebDevice()
    with caplog.at_level(logging.ERROR):
        close_notification(dev)
    # web_h5 has no Android notification shade — the routine must be a no-op...
    assert dev.open_quick_settings_calls == 0
    # ...and must never log the "not callable" obstruction error.
    assert "清理遮擋物時發生錯誤" not in caplog.text


def test_open_notification_skips_web_h5_backend(caplog):
    dev = _FakeWebDevice()
    with caplog.at_level(logging.ERROR):
        open_notification(dev)
    assert dev.open_quick_settings_calls == 0


def test_close_notification_still_runs_for_adb_backend():
    dev = _FakeAdbDevice()
    close_notification(dev)
    # ADB devices must still enter the routine (regression guard).
    assert dev.open_quick_settings_calls == 1
