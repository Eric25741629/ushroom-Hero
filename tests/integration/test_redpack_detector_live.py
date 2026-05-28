"""Real-device integration tests for utils.redpack_detector.

Exercises the full path against emulator-5554's running web session:
  - RedPoint gate reads the actual cocos node
  - fetch_redbag_list sends 0x2605 and parses the protobuf response

Auto-skips when CDP port 9230 isn't listening.
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
        pytest.skip("no game page")
    yield game_page
    pw.stop()


@pytest.fixture
def on_main_page(page):
    """Reset to 主頁 before tests — needed so we're not in chat panel etc."""
    from utils.cocos_navigator import CocosNavigator
    CocosNavigator(page).goto_main()
    time.sleep(0.5)


# ──────────────────────────────────────────────────────────────────────
# Cheap gate
# ──────────────────────────────────────────────────────────────────────


def test_main_redpoint_node_exists(page, on_main_page):
    """The RedPoint cocos node must exist — sanity check before trusting any
    bool reading. If this fails, the cocos path likely changed in a patch."""
    from utils.redpack_detector import main_redpoint_state
    state = main_redpoint_state(page)
    assert state is not None, "RedPoint node not found at expected path — game patch may have moved it"
    assert "container_active" in state
    assert "any_child_active" in state
    assert isinstance(state["children"], list)
    assert len(state["children"]) >= 1, "RedPoint should have at least one child (point/point1/txtNum)"


def test_main_redpoint_returns_bool(page, on_main_page):
    """has_unread is a bool either way — function doesn't crash on real page."""
    from utils.redpack_detector import main_redpoint_has_unread
    assert isinstance(main_redpoint_has_unread(page), bool)


# ──────────────────────────────────────────────────────────────────────
# API roundtrip
# ──────────────────────────────────────────────────────────────────────


def test_fetch_redbag_list_against_real_server(page, on_main_page):
    """Send 0x2605 to the real server, parse the protobuf response.

    Assertion shape:
      - Returns a list (possibly empty if server has no bags for this account)
      - Every entry has plausible fields (positive IDs, non-empty sender_name,
        timestamp in the last ~30 days)
      - No crashes on real response decoding
    """
    from utils.redpack_detector import fetch_redbag_list
    entries = fetch_redbag_list(page, timeout=3.0)
    assert isinstance(entries, list)
    now = int(time.time())
    for e in entries:
        assert e.bag_id > 0, f"bad bag_id: {e}"
        assert e.sender_id > 0, f"bad sender_id: {e}"
        assert e.sender_name, f"empty sender_name: {e}"
        # Timestamp must be in last 30 days (red bags expire quickly).
        # Use a wide window in case the server clock drifts.
        thirty_days_ago = now - 30 * 86400
        thirty_days_ahead = now + 86400
        assert thirty_days_ago < e.timestamp < thirty_days_ahead, \
            f"timestamp {e.timestamp} not within ±30 days of now ({now}): {e}"


def test_fetch_returns_consistent_shape_across_two_calls(page, on_main_page):
    """Two back-to-back calls should give the same length (server cache /
    consistent view). If they differ wildly, something's wrong with our
    parser or the server is paginating in a way we don't handle."""
    from utils.redpack_detector import fetch_redbag_list
    first = fetch_redbag_list(page, timeout=3.0)
    time.sleep(0.2)
    second = fetch_redbag_list(page, timeout=3.0)
    # Length should match closely (small drift possible if a bag arrives in between).
    assert abs(len(first) - len(second)) <= 1, \
        f"unstable list size: {len(first)} vs {len(second)}"


# ──────────────────────────────────────────────────────────────────────
# Combined gate
# ──────────────────────────────────────────────────────────────────────


def test_has_claimable_returns_bool_on_real_device(page, on_main_page):
    """End-to-end smoke — has_claimable returns a bool, doesn't crash."""
    from utils.redpack_detector import has_claimable_redpack
    assert isinstance(has_claimable_redpack(page), bool)


# ──────────────────────────────────────────────────────────────────────
# Claim action against real server
# ──────────────────────────────────────────────────────────────────────


def test_claim_on_known_historical_bag_returns_expected_error(page, on_main_page):
    """Claim attempt on a known-stale bag from the brief list.

    Expected: server responds with 0x0201 + error_code != 0 (typically 2
    for already-claimed/expired). This proves the entire pipeline is wired
    correctly even though we can't test a successful grab without a live bag.

    Skipped automatically if the server's brief list is empty.
    """
    from utils.redpack_detector import fetch_redbag_list, claim_redpack
    entries = fetch_redbag_list(page)
    if not entries:
        pytest.skip("no historical bags to test against — server returned empty list")

    e = entries[0]
    result = claim_redpack(page, bag_id=e.bag_id, cfg_id=e.cfg_id, timeout=3.0)

    # We expect either:
    #  - success=True with response_cmd=0x2603 (a fresh claimable bag somehow exists)
    #  - success=False with error_code from 0x0201 (already claimed / expired)
    # In both cases, error_code is not "no response" — that would indicate
    # a schema/wiring bug.
    assert result.error != "no response within timeout (server may be silently ignoring)", \
        "server gave no response — schema or hook installation broken"
    assert result.response_cmd in (0x2603, 0x0201), \
        f"unexpected response cmd 0x{result.response_cmd:04x}"
    if not result.success:
        # Failure path: must have an error_code from server.
        assert result.error_code is not None, "0x0201 response missing error_code field"


def test_claim_response_body_is_parseable(page, on_main_page):
    """Whatever the server returns, the body must walk as valid protobuf."""
    from utils.redpack_detector import fetch_redbag_list, claim_redpack, _walk_pb
    entries = fetch_redbag_list(page)
    if not entries:
        pytest.skip("no bags to test")
    e = entries[0]
    result = claim_redpack(page, bag_id=e.bag_id, cfg_id=e.cfg_id, timeout=3.0)
    if result.response_body:
        # If we got a response, it must be valid protobuf (at least one field
        # parses cleanly without errors).
        parsed = _walk_pb(result.response_body)
        assert parsed, "response body did not yield any parsed fields"
