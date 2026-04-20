"""種植操作"""

from __future__ import annotations
import time
import logging
import cv2
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from .base import click_with_jitter, wait_jitter, safe_screenshot
from farm_v2.config import COORD, TIMING

logger = logging.getLogger("farm_v2.plant")


def check_slot_color(d: "uiauto.Device") -> bool:
    """檢查槽位是否為空"""
    try:
        img = safe_screenshot(d)
        target = COORD["plant_check_color"]
        pixel = img[COORD["plant_check_pixel"][1], COORD["plant_check_pixel"][0]]

        diff = sum(abs(int(p) - int(t)) for p, t in zip(pixel, target))
        return diff <= 10
    except Exception as e:
        logger.warning(f"檢查槽位失敗: {e}")
        return False


def plant_one(d: "uiauto.Device") -> bool:
    """種植一次作物"""
    try:
        from img_tools import find_and_click

        if not find_and_click(d, r"plants.jpg"):
            logger.warning("找不到 plants 按鈕")
            return False

        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(d, COORD["plants_tab"][0], COORD["plants_tab"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(
            d, COORD["plant_confirm"][0], COORD["plant_confirm"][1], jitter=5
        )
        time.sleep(6)

        click_with_jitter(d, COORD["fertilize"][0], COORD["fertilize"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(
            d,
            COORD["fertilize_confirm_btn"][0],
            COORD["fertilize_confirm_btn"][1],
            jitter=5,
        )
        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(
            d, COORD["use_fertilizer"][0], COORD["use_fertilizer"][1], jitter=5
        )
        time.sleep(wait_jitter(2.2))

        click_with_jitter(d, COORD["get_all"][0], COORD["get_all"][1], jitter=5)
        time.sleep(TIMING["plant_wait"])

        click_with_jitter(d, COORD["get_all"][0], COORD["get_all"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

        return True

    except Exception as e:
        logger.error(f"種植失敗: {e}")
        return False


def plant_cycle(d: "uiauto.Device", max_count: int = 2) -> int:
    """執行多次種植循環"""
    count = 0
    start = time.time()
    timeout = 600

    while count < max_count and time.time() - start < timeout:
        if plant_one(d):
            count += 1
            logger.info(f"種植完成 {count}/{max_count}")
        time.sleep(1)

    return count
