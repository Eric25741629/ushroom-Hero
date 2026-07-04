"""Regression test for device_wrapper._reset_thread_event_loop().

Root cause of the 5554 "Sync API inside the asyncio loop"永久卡死 (2026-06-13):
when an 異地-kicked Chrome can no longer be stopped cleanly, the device thread is
left with a *running* asyncio loop registered (asyncio's C-level _running_loop
thread-state stays set). Playwright's sync start() reads `asyncio.get_running_loop()`
and refuses to start while that loop is present, so every web_h5 restart fails
forever.

The old `_reset_thread_event_loop()` only did `asyncio.set_event_loop(new_loop)`,
which swaps the thread's *default* loop but does NOT clear the running-loop
thread-state — so it could never rescue a poisoned thread. This test pins the
contract that the reset MUST clear the running loop.

device_wrapper pulls in cv2 at import; stub it so this stays a light unit test.
"""
from __future__ import annotations

import asyncio
import sys
import types

if "cv2" not in sys.modules:
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.IMREAD_COLOR = 1
    cv2_stub.COLOR_BGR2RGB = 4
    cv2_stub.imdecode = lambda *args, **kwargs: None
    cv2_stub.cvtColor = lambda img, *args, **kwargs: img
    sys.modules["cv2"] = cv2_stub

import device_wrapper


def test_reset_clears_a_stuck_running_loop():
    """A running loop registered on the thread must be cleared by the reset.

    Simulates the poisoned state: a leftover loop registered as the thread's
    running loop (as happens when a Playwright dispatcher greenlet can't unwind).
    After the reset, `asyncio.get_running_loop()` must raise — i.e. a fresh
    `sync_playwright().start()` would see a clean slate instead of the zombie.
    """
    stuck = asyncio.new_event_loop()
    asyncio.events._set_running_loop(stuck)
    try:
        # Precondition: the thread looks like it's "inside" a running loop.
        assert asyncio.get_running_loop() is stuck

        device_wrapper._reset_thread_event_loop()

        # The running-loop thread-state must be gone now.
        with __import__("pytest").raises(RuntimeError):
            asyncio.get_running_loop()
    finally:
        asyncio.events._set_running_loop(None)
        stuck.close()


def test_reset_installs_a_fresh_default_loop():
    """The reset still leaves a usable default loop on the thread (no regression)."""
    device_wrapper._reset_thread_event_loop()
    loop = asyncio.get_event_loop()
    assert loop is not None
    assert not loop.is_closed()
