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
        patch.object(hc, "_enable_work", return_value=True),
    ]


def test_plant_cycles_follow_cards_bought():
    """Each card boosts 30 plants; at 6 crops/cycle that's 5 cycles/card. Buying
    2 cards must plant 2*5=10 cycles, not the old fixed 15."""
    import contextlib

    d = MagicMock(_page=None)
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


def test_run_treats_no_card_bought_as_already_executed():
    """買不到卡代表本週已執行；不種植，恢復打工後回報成功。"""
    import contextlib

    d = MagicMock(_page=None)
    with contextlib.ExitStack() as stack:
        for cm in _patch_run_harvest_card_helpers(cards_bought=0):
            stack.enter_context(cm)
        plant = stack.enter_context(
            patch.object(hc, "_plant_premium_seed", return_value=True)
        )
        ok = hc.run_harvest_card(d, device_ip="x")
    assert plant.call_count == 0
    assert ok is True


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


def test_h5_premium_seed_requires_inventory_drop_and_grow_button():
    """特級種子只有在庫存下降且一鍵施肥出現後才算真的種下。"""
    page = MagicMock()
    d = MagicMock(backend_kind="web_h5", _page=page)
    before = {"premium": "1040", "onekey": {}}
    after = {
        "premium": "1034",
        "onekey": {"btnOneKeyGrow": {"active": True}},
    }
    with _patch_sleep(), \
         patch.object(hc.web_farm, "read_farm_state", side_effect=[before, after]), \
         patch.object(hc.web_farm, "tap_onekey", return_value=True), \
         patch.object(hc.web_farm, "seed_dialog_open", side_effect=[True, False]), \
         patch.object(hc.web_farm, "select_seed_by_name", return_value=True), \
         patch.object(hc.web_farm, "tap_seed_confirm", return_value=True), \
         patch("tools.click_white"):
        assert hc._plant_premium_seed(d) is True


def test_h5_premium_seed_rejects_confirm_without_inventory_drop():
    """按到確認但特級種子數量沒變，不得誤報成功。"""
    page = MagicMock()
    d = MagicMock(backend_kind="web_h5", _page=page)
    unchanged = {
        "premium": "1040",
        "onekey": {"btnOneKeyGrow": {"active": True}},
    }
    with _patch_sleep(), \
         patch.object(hc.web_farm, "read_farm_state", return_value=unchanged), \
         patch.object(hc.web_farm, "tap_onekey", return_value=True), \
         patch.object(hc.web_farm, "seed_dialog_open", return_value=False), \
         patch.object(hc.web_farm, "select_seed_by_name", return_value=True), \
         patch.object(hc.web_farm, "tap_seed_confirm", return_value=True):
        assert hc._plant_premium_seed(d) is False


def test_h5_fertilizer_requires_count_drop_before_next_pass():
    """選高產並確認後，要看到肥料扣除；下一次讀到無施肥按鈕才完成。"""
    page = MagicMock()
    d = MagicMock(backend_kind="web_h5", _page=page)
    before = {"putong": "0", "gaochan": "100", "onekey": {}}
    after = {
        "putong": "0",
        "gaochan": "94",
        "onekey": {"btnOneKeyGrow": {"active": True}},
    }
    with _patch_sleep(), \
         patch.object(hc.web_farm, "onekey_active", side_effect=[True, False]), \
         patch.object(hc.web_farm, "read_farm_state", side_effect=[before, after]), \
         patch.object(hc, "_claim_free_fertilizer", return_value=False), \
         patch.object(hc.web_farm, "tap_onekey", return_value=True), \
         patch.object(hc.web_farm, "fert_dialog_open", return_value=True), \
         patch.object(hc.web_farm, "select_fertilizer_by_name", return_value=True), \
         patch.object(hc.web_farm, "tap_fert_confirm", return_value=True):
        assert hc._fertilize_until_mature_web(d, page, cap=2) is True


def test_h5_view_button_prefers_javascript_listener():
    """btnUse 有 listener 時直接 emit，不再送滑鼠座標點擊。"""
    page = MagicMock()
    page.evaluate.return_value = {"clicked": True, "name": "btnUse"}
    with patch.object(hc.web_farm.time, "sleep"):
        assert hc.web_farm.tap_seed_confirm(page) is True
    page.mouse.click.assert_not_called()


def test_h5_harvest_requires_fetch_not_only_pick():
    """采摘不會消耗 buff；15 秒內沒有領取就必須回報失敗。"""
    page = MagicMock()
    d = MagicMock(backend_kind="web_h5", _page=page)
    fake_clock = MagicMock()
    fake_clock.time.side_effect = [0.0, 16.0]
    with patch.object(hc, "time", fake_clock), \
         patch.object(hc.web_farm, "tap_onekey", side_effect=[True, False]), \
         patch.object(hc.web_farm, "onekey_active", return_value=False), \
         patch("tools.click_white"):
        assert hc._harvest_crops(d) is False
