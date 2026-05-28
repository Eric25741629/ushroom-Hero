"""Real-device integration tests for utils.cocos_navigator.

These tests connect to a *running* emulator-5554 web session via CDP on
the configured `web_debug_port` (default 9230 per bot_config.json) and
exercise the navigator against actual game state.

They are intentionally skipped when the CDP port is not reachable, so
running `pytest` on a dev machine without a live emulator stays green.

Run only these:
    pytest tests/integration/test_cocos_navigator_live.py -v

Run all integration tests (when emulator is up):
    pytest tests/integration/ -v
"""
from __future__ import annotations
import socket
import time
import pytest


_CDP_HOST = "127.0.0.1"
_CDP_PORT = 9230  # emulator-5554's web_debug_port; aligns with bot_config.json
_GAME_HOST = "mushroomh5.acenetgame.com"


def _cdp_listening() -> bool:
    """True iff a process is listening on the CDP debug port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect((_CDP_HOST, _CDP_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


# Skip all tests in this module if the emulator-5554 web session isn't live.
pytestmark = pytest.mark.skipif(
    not _cdp_listening(),
    reason=f"emulator-5554 CDP port {_CDP_PORT} not listening; start web session via dashboard first",
)


@pytest.fixture(scope="module")
def page():
    """Attach to the live emulator-5554 game page once per test module."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://{_CDP_HOST}:{_CDP_PORT}")
    game_page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if _GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                game_page = p
                break
        if game_page:
            break
    if game_page is None:
        pw.stop()
        pytest.skip(f"no game page (host={_GAME_HOST}) on CDP {_CDP_PORT}")
    yield game_page
    pw.stop()


@pytest.fixture
def nav(page):
    """Fresh navigator + ensure we start from a known state (主頁)."""
    from utils.cocos_navigator import CocosNavigator
    nav = CocosNavigator(page)
    # Reset to main page before each test so failures don't cascade.
    nav.goto_main()
    time.sleep(0.5)
    return nav


# ──────────────────────────────────────────────────────────────────────
# Smoke tests — assert the navigator can see the live page at all.
# ──────────────────────────────────────────────────────────────────────


def test_current_view_returns_a_known_string(nav):
    """After resetting to main, current_view should report a known label."""
    assert nav.current_view() in {"main", "home", "farm", "unknown"}


def test_reset_lands_on_main(nav):
    """Fixture's reset step should leave us on 主頁面."""
    # If this fails, the goto_main implementation can't navigate to main from
    # whatever state the page was in — investigate before trusting other tests.
    assert nav.current_view() == "main", (
        "expected to start tests on 主頁; subsequent test results are unreliable")


# ──────────────────────────────────────────────────────────────────────
# Navigation tests — go through the same sequence the user described:
#   主頁 → 家園 → 農場 → 家園 → 主頁
# ──────────────────────────────────────────────────────────────────────


def test_goto_home_from_main(nav):
    assert nav.current_view() == "main"
    assert nav.goto_home() is True
    assert nav.current_view() == "home"


def test_goto_farm_from_main_navigates_through_home(nav):
    """goto_farm from main should auto-route via home."""
    assert nav.current_view() == "main"
    assert nav.goto_farm() is True
    assert nav.current_view() == "farm"


def test_goto_farm_from_home_is_direct(nav):
    nav.goto_home()
    assert nav.current_view() == "home"
    assert nav.goto_farm() is True
    assert nav.current_view() == "farm"


def test_goto_main_from_farm_closes_then_toggles(nav):
    """Going from farm → main should close the PlantMainView overlay then
    deselect the home tab."""
    nav.goto_farm()
    assert nav.current_view() == "farm"
    assert nav.goto_main() is True
    assert nav.current_view() == "main"


def test_goto_main_is_noop_when_already_main(nav):
    """If already on main, goto_main returns True without errors."""
    assert nav.current_view() == "main"
    assert nav.goto_main() is True
    assert nav.current_view() == "main"


def test_idempotent_goto_home(nav):
    nav.goto_home()
    assert nav.current_view() == "home"
    # Second call should not break — clicking the same tab again toggles off,
    # so we expect to leave 'home' state (this documents observed behavior).
    nav.goto_home()
    # Don't assert it stays on home — toggle behavior is acceptable. We just
    # check the navigator didn't crash and we're in a known state.
    assert nav.current_view() in {"home", "main"}


def test_full_round_trip(nav):
    """Run 主頁 → 家園 → 農場 → 主頁 in one shot."""
    assert nav.current_view() == "main"
    assert nav.goto_home() is True and nav.current_view() == "home"
    assert nav.goto_farm() is True and nav.current_view() == "farm"
    assert nav.goto_main() is True and nav.current_view() == "main"


# ──────────────────────────────────────────────────────────────────────
# try_cocos_navigate flag gating against the real device.
# ──────────────────────────────────────────────────────────────────────


def test_try_cocos_navigate_respects_flag_off(page, monkeypatch):
    """When flag is off (any device), wrapper short-circuits to None even on
    a real Playwright page."""
    from utils import cocos_navigator

    class FakeDevice:
        def __init__(self, p):
            self._page = p

    monkeypatch.setattr(cocos_navigator, "_device_flag_enabled", lambda ip: False)
    assert cocos_navigator.try_cocos_navigate(FakeDevice(page), "emulator-5554", "main") is None


def test_try_cocos_navigate_executes_when_flag_on(page, monkeypatch):
    from utils import cocos_navigator

    class FakeDevice:
        def __init__(self, p):
            self._page = p

    monkeypatch.setattr(cocos_navigator, "_device_flag_enabled", lambda ip: True)
    # main should always succeed (close + deselect path).
    result = cocos_navigator.try_cocos_navigate(FakeDevice(page), "emulator-5554", "main")
    assert result is True
