"""Tests for the rewritten 豐收卡 fertilizer choice + buff-gated plant loop.

Covers the pure decision logic (`_choose_fertilizer`, `_lead_int`,
`_card_buff_exhausted`) and the run_harvest_card loop's early-stop when the card
buff is exhausted. The page-driven primitives in farm_v2.web_farm are verified
live, not unit-tested.
"""
from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import farm_v2.operations.harvest_card as hc


class _DelegatingWebDevice:
    """模擬 runtime 的 MonitoredDevice：_page 只存在內層裝置。"""

    def __init__(self, page):
        self._d = type("InnerWebDevice", (), {
            "backend_kind": "web_h5",
            "_page": page,
        })()

    def __getattr__(self, name):
        return getattr(self._d, name)


def test_page_of_reads_delegated_playwright_page():
    page = object()
    assert hc._page_of(_DelegatingWebDevice(page)) is page


def test_fertilize_uses_js_for_delegating_web_device():
    d = _DelegatingWebDevice(object())
    with patch.object(hc, "_fertilize_until_mature_web", return_value=True) as web, \
            patch.object(hc, "_fertilize_until_mature_adb") as adb:
        assert hc._fertilize_until_mature(d) is True
    web.assert_called_once_with(d, d._page, 8)
    adb.assert_not_called()


def test_harvest_uses_js_for_delegating_web_device():
    d = _DelegatingWebDevice(object())
    with patch.object(hc.web_farm, "tap_onekey", return_value=False) as tap, \
            patch.object(hc.web_farm, "onekey_active", return_value=False), \
            patch.object(hc.img_tools, "click_str_by_server") as ocr, \
            patch("tools.click_white"), patch.object(hc.time, "sleep"):
        assert hc._harvest_crops(d) is False
    tap.assert_called_once_with(d._page, "btnOneKeyPick")
    ocr.assert_not_called()


def test_web_h5_without_page_never_falls_back_to_ocr():
    d = _DelegatingWebDevice(None)
    with patch.object(hc, "_fertilize_until_mature_adb") as adb, \
            patch.object(hc.img_tools, "click_str_by_server") as ocr:
        assert hc._fertilize_until_mature(d) is False
        assert hc._harvest_crops(d) is False
    adb.assert_not_called()
    ocr.assert_not_called()


def test_run_harvest_card_web_h5_without_page_stops_before_actions():
    d = _DelegatingWebDevice(None)
    with patch.object(hc, "_cancel_work_if_active") as cancel, \
            patch.object(hc.img_tools, "click_str_by_server") as ocr:
        assert hc.run_harvest_card(d, device_ip="emulator-5554") is False
    cancel.assert_not_called()
    ocr.assert_not_called()


# ---------------------------------------------------------------------------
# _choose_fertilizer (pure): 普通 first, 高產 fallback, None when both empty
# ---------------------------------------------------------------------------

def test_choose_prefers_putong_when_available():
    assert hc._choose_fertilizer(putong=5, gaochan=1000) == hc.PUTONG_FERTILIZER


def test_choose_falls_back_to_gaochan_when_putong_empty():
    assert hc._choose_fertilizer(putong=0, gaochan=1000) == hc.GAOCHAN_FERTILIZER


def test_choose_none_when_both_empty():
    assert hc._choose_fertilizer(putong=0, gaochan=0) is None


def test_lead_int_parses_slash_plain_and_garbage():
    assert hc._lead_int("20/20") == 20
    assert hc._lead_int("1393") == 1393
    assert hc._lead_int(None) == 0
    assert hc._lead_int("oops") == 0


# ---------------------------------------------------------------------------
# _card_buff_exhausted: scene-active based, adb (no page) → False
# ---------------------------------------------------------------------------

def test_buff_exhausted_false_on_adb_no_page():
    d = MagicMock()
    d._page = None
    assert hc._card_buff_exhausted(d) is False


def test_buff_exhausted_true_when_specialbuff_inactive():
    d = MagicMock()
    d.backend_kind = "web_h5"
    d._page = object()  # truthy web page sentinel
    with patch.object(hc.web_farm, "buff_active", return_value=False):
        assert hc._card_buff_exhausted(d) is True


def test_buff_exhausted_false_when_specialbuff_active():
    d = MagicMock()
    d.backend_kind = "web_h5"
    d._page = object()
    with patch.object(hc.web_farm, "buff_active", return_value=True):
        assert hc._card_buff_exhausted(d) is False


# ---------------------------------------------------------------------------
# run_harvest_card loop: early stop on buff exhaustion is a SUCCESS
# ---------------------------------------------------------------------------

def _patch_loop(cards_bought, buff_side_effect):
    return [
        patch("farm_v2.operations.harvest_card.time.sleep"),
        patch.object(hc, "_cancel_work_if_active"),
        patch.object(hc, "_ensure_fields_empty"),
        patch.object(hc, "_navigate_farm_to_home"),
        patch.object(hc, "_navigate_home_to_carpark", return_value=True),
        patch.object(hc, "_open_carpark_shop", return_value=True),
        patch.object(hc, "_buy_harvest_card_in_shop", return_value=cards_bought),
        patch.object(hc, "_close_carpark_shop"),
        patch.object(hc, "_navigate_carpark_to_home"),
        patch.object(hc, "_navigate_home_to_farm"),
        patch.object(hc, "_fertilize_until_mature", return_value=True),
        patch.object(hc, "_harvest_crops", return_value=True),
        patch.object(hc, "_card_buff_exhausted", side_effect=buff_side_effect),
        patch.object(hc, "_enable_work"),
    ]


def test_loop_stops_early_when_buff_exhausted():
    """3 cards → cap 15 cycles, but the buff goes inactive after the 2nd harvest,
    so we plant only twice and stop — and that counts as success (no re-buy)."""
    d = MagicMock()
    with contextlib.ExitStack() as stack:
        for cm in _patch_loop(cards_bought=3, buff_side_effect=[False, True]):
            stack.enter_context(cm)
        plant = stack.enter_context(patch.object(hc, "_plant_premium_seed", return_value=True))
        ok = hc.run_harvest_card(d, device_ip="x")
    assert plant.call_count == 2
    assert ok is True  # buff fully consumed early = success


def test_loop_runs_full_cap_when_buff_never_exhausted():
    d = MagicMock()
    with contextlib.ExitStack() as stack:
        for cm in _patch_loop(cards_bought=1, buff_side_effect=lambda _d: False):
            stack.enter_context(cm)
        plant = stack.enter_context(patch.object(hc, "_plant_premium_seed", return_value=True))
        ok = hc.run_harvest_card(d, device_ip="x")
    assert plant.call_count == 1 * (hc.PLANTS_PER_CARD // hc.CROPS_PER_CYCLE)  # 5
    assert ok is True


def test_loop_returns_false_on_plant_failure():
    """A real plant failure (e.g. 特級種子 not found) must return False so the
    weekly flow retries next wake."""
    d = MagicMock()
    with contextlib.ExitStack() as stack:
        for cm in _patch_loop(cards_bought=2, buff_side_effect=lambda _d: False):
            stack.enter_context(cm)
        stack.enter_context(patch.object(hc, "_plant_premium_seed", return_value=False))
        ok = hc.run_harvest_card(d, device_ip="x")
    assert ok is False
