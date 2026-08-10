"""Tests for the 菜園豐收卡 multi-buy loop in farm_v2.operations.harvest_card.

The loop must buy up to N cards but stop the moment a purchase doesn't go
through (sold out / daily limit), and report whether anything was bought.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import farm_v2.operations.harvest_card as hc


def _patch_sleep():
    return patch("farm_v2.operations.harvest_card.time.sleep")


def test_buys_full_count_when_every_purchase_succeeds():
    d = MagicMock()
    with _patch_sleep(), patch.object(hc, "_buy_one_harvest_card", return_value=True) as buy:
        bought = hc._buy_harvest_card_in_shop(d, count=3)
    assert bought == 3
    assert buy.call_count == 3


def test_returns_zero_when_first_purchase_fails():
    """Card not found / button dead on the first try → nothing bought → 0,
    and we don't keep hammering."""
    d = MagicMock()
    with _patch_sleep(), patch.object(hc, "_buy_one_harvest_card", return_value=False) as buy:
        bought = hc._buy_harvest_card_in_shop(d, count=3)
    assert bought == 0
    assert buy.call_count == 1


def test_stops_early_when_limit_reached_midway():
    """Two succeed, the third doesn't go through → stop, and report the actual
    number bought (2) so the caller can size the plant loop to it."""
    d = MagicMock()
    results = iter([True, True, False])
    with _patch_sleep(), patch.object(
        hc, "_buy_one_harvest_card", side_effect=lambda _d: next(results)
    ) as buy:
        bought = hc._buy_harvest_card_in_shop(d, count=5)
    assert bought == 2
    assert buy.call_count == 3  # stopped at the first failure after 2 buys


def test_count_param_caps_purchases():
    d = MagicMock()
    with _patch_sleep(), patch.object(hc, "_buy_one_harvest_card", return_value=True) as buy:
        hc._buy_harvest_card_in_shop(d, count=1)
    assert buy.call_count == 1


def test_default_count_is_three():
    d = MagicMock()
    with _patch_sleep(), patch.object(hc, "_buy_one_harvest_card", return_value=True) as buy:
        hc._buy_harvest_card_in_shop(d)
    assert buy.call_count == hc.HARVEST_CARD_BUY_COUNT == 3


def _patch_run_harvest_card_helpers(cards_bought: int):
    """Patch every side-effecting helper run_harvest_card calls so the test only
    exercises the plant-loop sizing logic. Returns the patch context managers."""
    return [
        _patch_sleep(),
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
        patch.object(hc, "_card_buff_exhausted", return_value=False),
        patch.object(hc, "_enable_work"),
    ]


def test_plant_cycles_follow_cards_bought():
    """Each card boosts 30 plants; at 6 crops/cycle that's 5 cycles/card. Buying
    2 cards must plant 2*5=10 cycles, not the old fixed 15."""
    import contextlib

    d = MagicMock()
    expected_cycles = 2 * (hc.PLANTS_PER_CARD // hc.CROPS_PER_CYCLE)
    with contextlib.ExitStack() as stack:
        for cm in _patch_run_harvest_card_helpers(cards_bought=2):
            stack.enter_context(cm)
        plant = stack.enter_context(
            patch.object(hc, "_plant_premium_seed", return_value=True)
        )
        ok = hc.run_harvest_card(d, device_ip="x")
    assert plant.call_count == expected_cycles == 10
    assert ok is True


def test_run_returns_false_when_no_card_bought():
    """Zero cards bought → bail out (no planting), report failure."""
    import contextlib

    d = MagicMock()
    with contextlib.ExitStack() as stack:
        for cm in _patch_run_harvest_card_helpers(cards_bought=0):
            stack.enter_context(cm)
        plant = stack.enter_context(
            patch.object(hc, "_plant_premium_seed", return_value=True)
        )
        ok = hc.run_harvest_card(d, device_ip="x")
    assert plant.call_count == 0
    assert ok is False


def test_h5_cancel_uses_cocos_then_reopens_panel_to_verify_stopped():
    """H5 先用 JavaScript 取消；面板自動關閉後重開，看到開始打工才成功。"""
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with _patch_sleep(), \
         patch.object(hc.web_farm, "work_panel_open",
                      side_effect=[False, True, False, True]), \
         patch("utils.cocos_ui.CocosUI.click_text", return_value=True) as click_text, \
         patch.object(hc.web_farm, "work_status", side_effect=["running", "stopped"]), \
         patch.object(hc.web_farm, "click_work_action", return_value=True) as click_action, \
         patch.object(hc.web_farm, "close_work_panel", return_value=True), \
         patch.object(hc, "check_if_parttime") as pixel_check, \
         patch.object(hc.img_tools, "wait_for_any_text", return_value=False):
        assert hc._cancel_work_if_active(d) is True
    assert click_text.call_count == 2
    click_action.assert_called_once_with(d._page, "cancel")
    pixel_check.assert_not_called()


def test_h5_cancel_falls_back_to_ocr_but_still_verifies_stopped():
    """Cocos 找不到取消節點時保留 OCR；OCR 後仍以 JavaScript 驗證狀態。"""
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    with _patch_sleep(), \
         patch.object(hc.web_farm, "work_panel_open", side_effect=[True, True, True]), \
         patch.object(hc.web_farm, "work_status", side_effect=["unknown", "stopped"]), \
         patch.object(hc.web_farm, "click_work_action", return_value=False), \
         patch.object(hc.web_farm, "close_work_panel", return_value=True), \
         patch.object(hc, "check_if_parttime") as pixel_check, \
         patch.object(hc.img_tools, "wait_for_any_text", side_effect=[True, True]) as ocr:
        assert hc._cancel_work_if_active(d) is True
    assert ocr.call_count == 2
    pixel_check.assert_not_called()


def test_run_stops_before_clearing_or_buying_when_work_cancel_not_confirmed():
    """取消打工未被確認時，禁止清場、買卡與種植。"""
    d = MagicMock()
    with _patch_sleep(), \
         patch.object(hc, "_cancel_work_if_active", return_value=False), \
         patch.object(hc, "_ensure_fields_empty") as clear_fields, \
         patch.object(hc, "_navigate_farm_to_home") as leave_farm, \
         patch.object(hc, "_buy_harvest_card_in_shop") as buy_card:
        assert hc.run_harvest_card(d, device_ip="x") is False
    clear_fields.assert_not_called()
    leave_farm.assert_not_called()
    buy_card.assert_not_called()
