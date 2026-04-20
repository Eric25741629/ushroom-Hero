"""農場自動化 v2 - 主管理器"""

from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from farm_v2.config import COORD, TIMING, MAX_PLANT_PER_DAY, WEEKLY_CARD_DAYS
from farm_v2.states import FarmState, FarmContext
from farm_v2.operations import (
    click_with_jitter,
    wait_jitter,
    buy_seed,
    plant_one,
    plant_cycle,
    run_weekly_card,
)

logger = logging.getLogger("farm_v2.manager")


def navigate_to_farm(d: "uiauto.Device", cnn_model=None) -> float:
    """導航到農場頁面並返回節省的時間"""
    save_time = 0.0

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    try:
        cnn_s = time.time()
        while True:
            from new_cnn.cnn_model import predict_image
            import os
            from io import BytesIO
            from PIL import Image

            screenshot = d.screenshot(format="pillow")
            if (
                cnn_model
                and cnn_model.predict_image(cnn_model, screenshot) == "homeplace"
            ):
                cnn_p = time.time()
                saved = 5 - (cnn_p - cnn_s)
                if saved > 0:
                    save_time += saved
                logger.info(f"節省時間: {saved:.2f}秒")
                break
            if time.time() - cnn_s > 60:
                break
    except Exception as e:
        logger.warning(f"CNN預測失敗: {e}")
        time.sleep(5)

    click_with_jitter(d, COORD["farm_entry"][0], COORD["farm_entry"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["farm_wait"]))

    return save_time


def navigate_to_home(d: "uiauto.Device", cnn_model=None) -> float:
    """從農場返回首頁"""
    save_time = 0.0

    click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["very_long"]))

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    try:
        cnn_s = time.time()
        while True:
            from new_cnn.cnn_model import predict_image

            screenshot = d.screenshot(format="pillow")
            if cnn_model and cnn_model.predict_image(cnn_model, screenshot) == "main":
                cnn_p = time.time()
                saved = 3 - (cnn_p - cnn_s)
                if saved > 0:
                    save_time += saved
                break
            if time.time() - cnn_s > 60:
                break
    except Exception as e:
        logger.warning(f"CNN預測失敗: {e}")
        time.sleep(3)

    return save_time


def should_do_weekly_card(time_manager) -> bool:
    """檢查今天是否需要執行每週卡片"""
    import time

    weekday = time.localtime().tm_wday
    return weekday in WEEKLY_CARD_DAYS


def run_farm(
    d: "uiauto.Device",
    device_ip: str,
    cnn_model=None,
    time_manager=None,
) -> float:
    """執行完整農場流程

    Args:
        d: uiautomator2 Device 實例
        device_ip: 設備 IP
        cnn_model: CNN 模型（可選）
        time_manager: 時間記錄管理器（可選）

    Returns:
        節省的時間（秒）
    """
    from json_manager import create_time_manager

    if time_manager is None:
        time_manager = create_time_manager(device_ip)

    logger.info(f"開始農場流程 - 設備: {device_ip}")
    save_time = 0.0

    save_time += navigate_to_farm(d, cnn_model)

    seed_record = time_manager.get_time_record("farm_seed_purchase")
    should_buy_seed = not seed_record or seed_record.get("is_next_day", True)

    if should_buy_seed:
        logger.info("需要購買種子")
        buy_seed(d)
        time_manager.record_time("farm_seed_purchase")

    weekday = time.localtime().tm_wday
    if weekday in WEEKLY_CARD_DAYS:
        is_same_week = time_manager.is_same_week("farm_card_weekly")
        if not is_same_week:
            logger.info("執行每週卡片")
            run_weekly_card(d)
            time_manager.record_time("farm_card_weekly")

    start = time.time()
    while time.time() - start < 25:
        from img_tools import find_and_click

        if find_and_click(d, r"getting.jpg"):
            time.sleep(7)
        elif find_and_click(d, r"get_all.jpg"):
            time.sleep(3)
        elif find_and_click(d, "new_get.jpg", threshold=0.6, x=10, y=100):
            time.sleep(7)

        current_hour = time.localtime().tm_hour
        if current_hour >= 8:
            is_same_day = time_manager.is_same_day("farm_plant_click")
            daily_count = (
                time_manager.get_numeric_value("farm_plant_click", "count", 0)
                if is_same_day
                else 0
            )

            if daily_count < MAX_PLANT_PER_DAY:
                if find_and_click(d, r"plants.jpg"):
                    daily_count += 1
                    time_manager.record_timestamp(
                        "farm_plant_click", {"count": daily_count}
                    )
                    time.sleep(2)

                    from farm_v2.operations import check_slot_color

                    if check_slot_color(d):
                        d.click(199, 437)
                        time.sleep(2)
                        d.click(126, 588)
                        time.sleep(1)
                        d.click(165, 460)
                        time.sleep(1)

                    if find_and_click(d, r"put.jpg"):
                        time.sleep(5)

    save_time += navigate_to_home(d, cnn_model)

    logger.info(f"農場流程完成，節省時間: {save_time:.2f}秒")
    return save_time


def quick_farm(device_ip: str) -> float:
    """快速執行農場（從 game_api 調用）"""
    import uiautomator2 as u2

    d = u2.connect(device_ip)
    return run_farm(d, device_ip)
