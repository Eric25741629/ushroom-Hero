"""Real-device integration tests for utils.page_detector.

Navigates the live emulator-5554 page through several states via
CocosNavigator + ad-hoc emit-clicks, then verifies the detector
classifies each one correctly via the cocos fast-path.

OCR is intentionally NOT exercised against the live device — the OCR
server may be slow or unavailable in CI; OCR correctness is covered by
unit tests with mocked text lists.

Skipped automatically when CDP port 9230 is not listening.
"""
from __future__ import annotations
import socket
import time
import pytest


_CDP_HOST = "127.0.0.1"
_CDP_PORT = 9230
_GAME_HOST = "mushroomh5.acenetgame.com"


def _cdp_listening() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect((_CDP_HOST, _CDP_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


pytestmark = pytest.mark.skipif(
    not _cdp_listening(),
    reason=f"emulator-5554 CDP port {_CDP_PORT} not listening",
)


# Mirror the click helper from CocosNavigator so we can navigate to pages
# that don't have a built-in goto helper yet (Mine, Statue, etc.).
_CLICK_JS = r"""
([path]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked: false, err: 'no_cc'};
  const scene = cc.director.getScene();
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p);
      if (!n) return null;
    }
    return n;
  };
  const node = find(scene, path.split('/').filter(Boolean));
  if (!node) return {clicked: false, err: 'not_found', path};
  if (node.emit) node.emit('click', node);
  return {clicked: true};
}
"""


@pytest.fixture(scope="module")
def page():
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
        pytest.skip(f"no game page on CDP {_CDP_PORT}")
    yield game_page
    pw.stop()


@pytest.fixture
def reset_to_main(page):
    """Reset to 主頁 before each test using CocosNavigator. Assert success so
    any previous test's residue surfaces immediately rather than corrupting
    the current test's assertions."""
    from utils.cocos_navigator import CocosNavigator
    nav = CocosNavigator(page)
    ok = nav.goto_main()
    time.sleep(0.6)
    assert ok, "reset_to_main: goto_main returned False — prior test left state we can't recover from"


# ──────────────────────────────────────────────────────────────────────
# Detection tests — drive UI, then assert page_detector classifies right
# ──────────────────────────────────────────────────────────────────────


def test_detect_main(page, reset_to_main):
    from utils.page_detector import PageDetector, PageState
    state, src = PageDetector(page, ocr_enabled=False).detect()
    assert state == PageState.MAIN
    assert src == "cocos"


def test_detect_home(page, reset_to_main):
    from utils.cocos_navigator import CocosNavigator
    from utils.page_detector import PageDetector, PageState
    assert CocosNavigator(page).goto_home() is True
    time.sleep(0.3)
    state, _ = PageDetector(page, ocr_enabled=False).detect()
    assert state == PageState.HOME


def test_detect_farm(page, reset_to_main):
    from utils.cocos_navigator import CocosNavigator
    from utils.page_detector import PageDetector, PageState
    assert CocosNavigator(page).goto_farm() is True
    time.sleep(0.3)
    state, _ = PageDetector(page, ocr_enabled=False).detect()
    assert state == PageState.FARM


def _click_home_item(page, item_name: str) -> None:
    """Helper: ensure on home, then click an item inside MysteryMainView.

    Each overlay click takes time for cocos to bind data + render — the
    sleeps below were tuned against a live emulator-5554 session; shorter
    waits cause flake when the network roundtrip for `0x4707` is slow.
    """
    from utils.cocos_navigator import CocosNavigator
    nav = CocosNavigator(page)
    ok_home = nav.goto_home()
    time.sleep(1.0)
    assert ok_home, f"failed to reach home before clicking {item_name}"
    base = "/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/"
    res = page.evaluate(_CLICK_JS, [base + item_name])
    assert res.get("clicked"), f"failed to click {item_name}: {res}"
    time.sleep(2.5)  # overlay animation + WS roundtrip + render


@pytest.mark.parametrize("item,expected", [
    ("Mine", "MINE"),
    ("FarmStatue", "STATUE"),
    ("CarPark", "CARPARK"),
    ("WorkShop", "WORKSHOP"),
    ("Science", "SCIENCE"),
    ("mysteryShop", "MYSTERY_SHOP"),
    # Marry, Farm covered separately (different navigation flow / already tested).
])
def test_detect_home_overlays(page, reset_to_main, item, expected):
    from utils.page_detector import PageDetector, PageState
    _click_home_item(page, item)
    state, _ = PageDetector(page, ocr_enabled=False).detect()
    assert state == getattr(PageState, expected), f"item={item} got {state}"


# ──────────────────────────────────────────────────────────────────────
# wait_for behavior on live device
# ──────────────────────────────────────────────────────────────────────


def test_wait_for_home_completes_after_navigation(page, reset_to_main):
    """wait_for should return True shortly after we navigate to home."""
    from utils.cocos_navigator import CocosNavigator
    from utils.page_detector import PageDetector, PageState
    det = PageDetector(page, ocr_enabled=False)
    # Trigger navigation, then wait_for to confirm.
    CocosNavigator(page).goto_home()
    # The cocos transition is ~instant; with a generous timeout this should pass.
    assert det.wait_for(PageState.HOME, timeout=3.0, poll_interval=0.1) is True


def test_wait_for_returns_false_when_state_never_arrives(page, reset_to_main):
    """We're on MAIN; waiting for FARM with a tight timeout should fail."""
    from utils.page_detector import PageDetector, PageState
    det = PageDetector(page, ocr_enabled=False)
    assert det.wait_for(PageState.FARM, timeout=0.6, poll_interval=0.1) is False


# ──────────────────────────────────────────────────────────────────────
# try_detect_main_page_fast — the integration hook used by new_main_v2
# ──────────────────────────────────────────────────────────────────────


class _FakeWebDevice:
    """Minimal stand-in for the bot's MonitoredDevice / web wrapper.

    Real wrapper holds `_page` (Playwright page) plus a `cfg` dict, but the
    fast-path only reads `_page` directly.
    """
    def __init__(self, page):
        self._page = page


def test_fast_path_returns_main_when_actually_on_main(page, reset_to_main, monkeypatch):
    """End-to-end on live device: after reset_to_main, fast-path returns "主頁面"."""
    from utils import page_detector
    monkeypatch.setattr(page_detector, "_legacy_fast_path_enabled", lambda ip: True)
    d = _FakeWebDevice(page)
    assert page_detector.try_detect_main_page_fast(d, "emulator-5554") == "主頁面"


def test_fast_path_returns_none_when_on_home(page, reset_to_main, monkeypatch):
    """Even on web_h5 with flag on, non-MAIN cocos states return None so
    legacy OCR runs and can detect 異地登錄 popups etc."""
    from utils import page_detector
    from utils.cocos_navigator import CocosNavigator
    CocosNavigator(page).goto_home()
    time.sleep(0.6)
    monkeypatch.setattr(page_detector, "_legacy_fast_path_enabled", lambda ip: True)
    d = _FakeWebDevice(page)
    assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None


def test_fast_path_returns_none_when_on_farm(page, reset_to_main, monkeypatch):
    from utils import page_detector
    from utils.cocos_navigator import CocosNavigator
    CocosNavigator(page).goto_farm()
    time.sleep(0.6)
    monkeypatch.setattr(page_detector, "_legacy_fast_path_enabled", lambda ip: True)
    d = _FakeWebDevice(page)
    assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None


def test_fast_path_returns_none_when_flag_off_even_if_on_main(page, reset_to_main, monkeypatch):
    """Disabling the flag must completely opt the device out."""
    from utils import page_detector
    monkeypatch.setattr(page_detector, "_legacy_fast_path_enabled", lambda ip: False)
    d = _FakeWebDevice(page)
    assert page_detector.try_detect_main_page_fast(d, "emulator-5554") is None
