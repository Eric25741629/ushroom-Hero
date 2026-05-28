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
