"""購買種子操作"""

from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from .base import click_with_jitter, wait_jitter
from farm_v2.config import COORD, TIMING

logger = logging.getLogger("farm_v2.seed")


def buy_seed(d: "uiauto.Device") -> bool:
    """購買一組種子"""
    try:
        logger.info("開始購買種子")

        click_with_jitter(
            d, COORD["seed_shop_entry"][0], COORD["seed_shop_entry"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(d, COORD["seed_buy_1"][0], COORD["seed_buy_1"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["short"]))

        for _ in range(3):
            click_with_jitter(
                d, COORD["seed_select"][0], COORD["seed_select"][1], jitter=5
            )
            time.sleep(wait_jitter(TIMING["short"]))

        click_with_jitter(
            d, COORD["seed_confirm"][0], COORD["seed_confirm"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["short"]))

        click_with_jitter(
            d, COORD["seed_buy_btn"][0], COORD["seed_buy_btn"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["short"]))

        click_with_jitter(
            d, COORD["seed_confirm_page"][0], COORD["seed_confirm_page"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["short"]))

        for _ in range(3):
            click_with_jitter(
                d, COORD["seed_select"][0], COORD["seed_select"][1], jitter=5
            )
            time.sleep(wait_jitter(TIMING["short"]))

        click_with_jitter(
            d, COORD["seed_confirm"][0], COORD["seed_confirm"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["short"]))

        click_with_jitter(
            d, COORD["seed_buy_btn"][0], COORD["seed_buy_btn"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["short"]))

        from tools import click_white

        click_white(d)
        click_white(d)

        click_with_jitter(
            d, COORD["seed_shop_entry"][0], COORD["seed_shop_entry"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["very_long"]))

        logger.info("購買種子完成")
        return True

    except Exception as e:
        logger.error(f"購買種子失敗: {e}")
        return False
