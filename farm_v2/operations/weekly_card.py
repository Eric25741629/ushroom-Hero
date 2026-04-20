"""每週卡片操作"""

from __future__ import annotations
import time
import logging
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from .base import click_with_jitter, wait_jitter, safe_screenshot
from farm_v2.config import COORD, TIMING

logger = logging.getLogger("farm_v2.card")


def check_if_parttime(d: "uiauto.Device") -> bool:
    """檢查是否正在打工"""
    try:
        img = safe_screenshot(d)

        cond1 = np.sum(img[713, 339]) - np.sum([52, 64, 200]) < 10
        cond2 = abs(np.sum(img[710, 211]) - np.sum([57, 65, 196])) < 10

        if cond1 and cond2:
            logger.info("偵測到打工中")
            return True
        return False
    except Exception as e:
        logger.warning(f"打工檢查失敗: {e}")
        return False


def cancel_work(d: "uiauto.Device") -> None:
    """取消打工"""
    click_with_jitter(d, COORD["work_button"][0], COORD["work_button"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    click_with_jitter(d, COORD["cancel_work"][0], COORD["cancel_work"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["medium"]) + random.random())

    click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))


def do_fertilize(d: "uiauto.Device") -> bool:
    """執行施肥"""
    try:
        from img_tools import find_and_click

        if not find_and_click(d, r"fertilize.jpg"):
            logger.info("未找到施肥按鈕")
            return False

        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(
            d, COORD["fertilize_btn"][0], COORD["fertilize_btn"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["long"]))

        click_with_jitter(
            d, COORD["fertilize_confirm"][0], COORD["fertilize_confirm"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["very_long"]))

        return True
    except Exception as e:
        logger.error(f"施肥失敗: {e}")
        return False


def collect_weekly_card(d: "uiauto.Device") -> bool:
    """領取每週卡片獎勵"""
    try:
        from img_tools import find_and_click

        if find_and_click(d, r"getting.jpg"):
            time.sleep(7)

        if find_and_click(d, r"get_all.jpg"):
            time.sleep(3)

        click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))

        return True
    except Exception as e:
        logger.error(f"領取每週卡片失敗: {e}")
        return False


def buy_shop_items(d: "uiauto.Device", timeout: int = 300) -> bool:
    """在商店購買物品"""
    try:
        import cv2

        click_with_jitter(d, COORD["shop_entry"][0], COORD["shop_entry"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))

        click_with_jitter(d, COORD["shop_buy"][0], COORD["shop_buy"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

        import os

        want_to_buy_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "farm_card.jpg"
        )
        if os.path.exists(want_to_buy_path):
            want_to_buy = cv2.imread(want_to_buy_path)
        else:
            want_to_buy = cv2.imread("farm_card.jpg")

        if want_to_buy is None:
            logger.warning("找不到 farm_card.jpg 模板")
            return False

        start_time = time.time()
        err = 0

        while time.time() - start_time < timeout:
            img = safe_screenshot(d)
            res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

            h, w = want_to_buy.shape[:-1]
            top_left = max_loc

            if max_val > 0.7 and top_left[1] < 552:
                click_with_jitter(d, top_left[0] + w // 2, top_left[1] + h // 2 + 100)
                time.sleep(wait_jitter(TIMING["long"]))

                click_with_jitter(
                    d, COORD["buy_confirm_1"][0], COORD["buy_confirm_1"][1], jitter=5
                )
                time.sleep(wait_jitter(TIMING["medium"]))

                click_with_jitter(
                    d, COORD["buy_confirm_2"][0], COORD["buy_confirm_2"][1], jitter=5
                )
                time.sleep(wait_jitter(TIMING["long"]))

                d.click(514, 16)
                time.sleep(wait_jitter(TIMING["long"]))
                break
            else:
                d.swipe(0.5, 0.8, 0.5, 0.6, 1)
                click_with_jitter(
                    d,
                    COORD["shop_scroll_point"][0],
                    COORD["shop_scroll_point"][1],
                    jitter=5,
                )
                time.sleep(wait_jitter(TIMING["medium"]))
                err += 1
                if err > 10:
                    break

        click_with_jitter(d, COORD["shop_close"][0], COORD["shop_close"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["medium"]))

        click_with_jitter(
            d, COORD["shop_close_2"][0], COORD["shop_close_2"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["very_long"]))

        click_with_jitter(
            d, COORD["shop_scroll_point"][0], COORD["shop_scroll_point"][1], jitter=5
        )
        time.sleep(wait_jitter(TIMING["very_long"]))

        return True

    except Exception as e:
        logger.error(f"商店購買失敗: {e}")
        return False


def run_weekly_card(d: "uiauto.Device") -> bool:
    """執行每週卡片流程"""
    try:
        logger.info("開始每週卡片流程")

        click_with_jitter(d, COORD["work_button"][0], COORD["work_button"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

        parttime = check_if_parttime(d)
        if parttime:
            cancel_work(d)
        else:
            click_with_jitter(d, COORD["close"][0], COORD["close"][1], jitter=5)
            time.sleep(wait_jitter(TIMING["long"]))

        do_fertilize(d)
        collect_weekly_card(d)

        click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))

        buy_shop_items(d)

        logger.info("每週卡片流程完成")
        return True

    except Exception as e:
        logger.error(f"每週卡片流程失敗: {e}")
        return False
