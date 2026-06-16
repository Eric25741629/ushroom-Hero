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

import sys
import threading
import types

import pytest

if "cv2" not in sys.modules:
    cv2_stub = types.ModuleType("cv2")
    cv2_stub.IMREAD_COLOR = 1
    cv2_stub.COLOR_BGR2RGB = 4
    cv2_stub.imdecode = lambda *args, **kwargs: None
    cv2_stub.cvtColor = lambda img, *args, **kwargs: img
    sys.modules["cv2"] = cv2_stub

import device_wrapper


class _SentinelDevice:
    """Stand-in registry entry. Records every is_alive() call so we can
    assert the helper never touches it (canvas probe must not run)."""

    def __init__(self, owner_thread_id: int) -> None:
        self.owner_thread_id = owner_thread_id
        self.is_alive_calls = 0

    def is_alive(self) -> bool:  # pragma: no cover — must NOT be called
        self.is_alive_calls += 1
        return False


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _PageAtGameUrl:
    def __init__(self, url: str) -> None:
        self.url = url


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


def test_app_start_skips_second_goto_when_restart_already_loaded_game_url(monkeypatch):
    dev = device_wrapper.PlaywrightGameDevice.__new__(
        device_wrapper.PlaywrightGameDevice
    )
    dev.device_id = "emulator-5554"
    dev.logger = _Logger()
    dev.web_url = "https://game.example/play"
    dev._page = None
    dev._in_game = False
    dev._closed_by_stop = True

    open_calls = []

    def ensure_session(reason: str = ""):
        assert reason == "app_start"
        dev._page = _PageAtGameUrl(dev.web_url)
        return True

    def open_game_url():
        open_calls.append("goto")
        return True

    monkeypatch.setattr(dev, "_ensure_browser_session", ensure_session)
    monkeypatch.setattr(dev, "_open_game_url", open_game_url)

    assert dev.app_start("com.mxdzz.tw.and") is True
    assert open_calls == []
    assert dev._in_game is True
    assert dev._closed_by_stop is False


class _Keyboard:
    def __init__(self) -> None:
        self.presses = []

    def press(self, key):
        self.presses.append(key)


class _HealthyPage:
    def __init__(self) -> None:
        self.keyboard = _Keyboard()


class _ClosedPage:
    """Stand-in for a page whose target is already gone — keyboard.press raises."""

    def __init__(self) -> None:
        self.keyboard = self

    def press(self, key):  # pragma: no cover — must NOT be reached after self-heal
        raise RuntimeError(
            "Keyboard.press: Target page, context or browser has been closed"
        )


def test_press_back_self_heals_session_before_keyboard(monkeypatch):
    """Regression: web_h5 press('back') used to call self._page.keyboard.press
    directly with no session guard — unlike tap/swipe/screenshot — so a closed
    page raised a raw TargetClosedError up into the startup loop
    (game_initialization.py:240). press() must first _ensure_browser_session +
    _sync_active_page so a dead page is reopened, mirroring tap()."""
    dev = device_wrapper.PlaywrightGameDevice.__new__(
        device_wrapper.PlaywrightGameDevice
    )
    dev.device_id = "emulator-5554"
    dev.logger = _Logger()
    dev._page = _ClosedPage()

    order = []

    def ensure_session(reason: str = ""):
        order.append(("ensure", reason))
        dev._page = _HealthyPage()  # self-heal swaps in a live page
        return True

    def sync_page():
        order.append(("sync",))
        return True

    monkeypatch.setattr(dev, "_ensure_browser_session", ensure_session)
    monkeypatch.setattr(dev, "_sync_active_page", sync_page)

    assert dev.press("back") is True
    # Session guard must run BEFORE we touch the keyboard.
    assert order and order[0] == ("ensure", "press")
    # And the Escape must land on the healed (live) page, not the closed one.
    assert isinstance(dev._page, _HealthyPage)
    assert dev._page.keyboard.presses == ["Escape"]


def test_press_home_is_noop_without_touching_page(monkeypatch):
    """home() must stay a cheap no-op that never touches the page/session."""
    dev = device_wrapper.PlaywrightGameDevice.__new__(
        device_wrapper.PlaywrightGameDevice
    )
    dev.device_id = "emulator-5554"
    dev.logger = _Logger()
    dev._page = _ClosedPage()  # would raise if home touched it

    def boom(*_a, **_k):  # pragma: no cover - must NOT run for home
        raise AssertionError("home() must not call _ensure_browser_session")

    monkeypatch.setattr(dev, "_ensure_browser_session", boom)
    assert dev.press("home") is True
