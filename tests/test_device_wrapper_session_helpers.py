"""Regression tests for device_wrapper.get_same_thread_web_device.

This helper exists because get_alive_web_device's is_alive() check uses a
canvas probe with a 200ms timeout that returns false-negatives on tabs the
browser has thrown into background throttle. Callers that just need to
identify the same-thread session — typically to decide whether to
close-after-use or to reuse it — must not depend on that probe, otherwise
they will tear down sessions the owner thread still needs (the root cause
of the "web_h5 session unavailable" hourly restart pattern on 5554).

If device_wrapper's heavyweight imports (cv2 / playwright) are unavailable
in this environment, the whole module is skipped — these tests are an
integration check against the real registry.
"""
from __future__ import annotations

import threading

import pytest

device_wrapper = pytest.importorskip("device_wrapper")


class _SentinelDevice:
    """Stand-in registry entry. Records every is_alive() call so we can
    assert the helper never touches it (canvas probe must not run)."""

    def __init__(self, owner_thread_id: int) -> None:
        self.owner_thread_id = owner_thread_id
        self.is_alive_calls = 0

    def is_alive(self) -> bool:  # pragma: no cover — must NOT be called
        self.is_alive_calls += 1
        return False


@pytest.fixture
def clean_registry():
    """Snapshot + restore the global registry so tests don't leak state."""
    saved = dict(device_wrapper._WEB_DEVICE_REGISTRY)
    device_wrapper._WEB_DEVICE_REGISTRY.clear()
    try:
        yield device_wrapper._WEB_DEVICE_REGISTRY
    finally:
        device_wrapper._WEB_DEVICE_REGISTRY.clear()
        device_wrapper._WEB_DEVICE_REGISTRY.update(saved)


def test_returns_none_when_registry_empty(clean_registry):
    assert device_wrapper.get_same_thread_web_device("emulator-5554") is None


def test_returns_device_for_same_thread_entry(clean_registry):
    me = threading.get_ident()
    dev = _SentinelDevice(owner_thread_id=me)
    clean_registry["emulator-5554"] = dev

    result = device_wrapper.get_same_thread_web_device("emulator-5554")
    assert result is dev
    # Critical contract: helper must NOT call is_alive() (which uses canvas
    # probe and can false-negative on throttled tabs).
    assert dev.is_alive_calls == 0


def test_returns_none_for_cross_thread_entry_using_real_thread(clean_registry):
    """Use a real worker thread to register, then probe from the main
    thread. This exercises the actual thread-id mismatch path rather than
    a synthetic + 1 offset that the helper could in principle short-circuit."""

    registered = threading.Event()
    released = threading.Event()
    holder: dict = {}

    def worker():
        dev = _SentinelDevice(owner_thread_id=threading.get_ident())
        clean_registry["emulator-5554"] = dev
        holder["dev"] = dev
        registered.set()
        # Keep the thread alive until the main thread is done probing —
        # if the worker exits early its tid could in theory be reused.
        released.wait(timeout=5)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        assert registered.wait(timeout=5), "worker did not register entry"
        # Probe from the main thread: tid differs from the worker that
        # registered the device, so the helper must reject.
        assert device_wrapper.get_same_thread_web_device("emulator-5554") is None
        assert holder["dev"].is_alive_calls == 0
    finally:
        released.set()
        t.join(timeout=5)


def test_unknown_ip_does_not_match_other_entries(clean_registry):
    me = threading.get_ident()
    clean_registry["emulator-5554"] = _SentinelDevice(owner_thread_id=me)

    assert device_wrapper.get_same_thread_web_device("emulator-5560") is None


def test_handles_entry_without_owner_thread_id(clean_registry):
    """Defensive: a legacy / partially-initialized entry without
    owner_thread_id must not crash and must be treated as foreign."""

    class _NoOwnerAttr:
        pass

    clean_registry["emulator-5554"] = _NoOwnerAttr()
    assert device_wrapper.get_same_thread_web_device("emulator-5554") is None
