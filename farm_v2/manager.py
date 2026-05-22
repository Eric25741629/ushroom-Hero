"""農場自動化 v2 - 主管理器"""

from __future__ import annotations
import time
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import uiautomator2 as uiauto

import img_tools
import new_cnn.cnn_model as _cnn_module
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
from game_actions.navigation import navigate_to_main_page
from game_state.detector import get_stage
from utils.cocos_navigator import try_cocos_navigate
from utils.screenshot_helpers import save_error_screenshot

logger = logging.getLogger("farm_v2.manager")


def _predict_stage(cnn_model, pil_img):
    """Wrap the awkward `module.predict_image(instance, image)` call so callers
    don't trip on the fact that predict_image is a module-level function, not a
    method on SimpleCNN. Returns class name or None on failure."""
    try:
        return _cnn_module.predict_image(cnn_model, pil_img)
    except Exception as e:
        logger.debug(f"[farm_v2] CNN predict failed: {e}")
        return None


def navigate_to_farm(d: "uiauto.Device", cnn_model=None, device_ip: Optional[str] = None) -> float:
    """導航到農場頁面並返回節省的時間"""
    save_time = 0.0

    # Experimental fast-path: cocos emit-click bypasses screenshot+OCR loop.
    # Only fires when the device has experimental_cocos_navigation=true in
    # bot_config.json. None means "not applicable" (flag off / not web_h5) —
    # caller MUST fall back to the click-based logic below.
    cocos_result = try_cocos_navigate(d, device_ip, "farm")
    if cocos_result is True:
        logger.info(f"[farm_v2] cocos fast-path succeeded for {device_ip}")
        # Saved roughly the full OCR wait + two animations (≈8s) vs blind clicks.
        return 6.0
    if cocos_result is False:
        logger.warning(f"[farm_v2] cocos fast-path failed for {device_ip}, falling back")

    click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["long"]))

    if cnn_model is not None:
        cnn_s = time.time()
        while time.time() - cnn_s <= 60:
            if _predict_stage(cnn_model, d.screenshot(format="pillow")) == "homeplace":
                saved = max(0.0, 5.0 - (time.time() - cnn_s))
                save_time += saved
                logger.info(f"節省時間: {saved:.2f}秒")
                break
        else:
            time.sleep(5)

    click_with_jitter(d, COORD["farm_entry"][0], COORD["farm_entry"][1], jitter=5)
    time.sleep(wait_jitter(TIMING["farm_wait"]))

    return save_time


def navigate_to_home(
    d: "uiauto.Device",
    cnn_model=None,
    device_ip: Optional[str] = None,
) -> float:
    """從農場返回首頁。委託給 game_actions.navigation.navigate_to_main_page。

    Returns:
        節省時間秒數（若導航在 3 秒內完成則補回差額；否則 0.0）。
    """
    nav_start = time.time()
    navigate_to_main_page(d, cnn_model, device_ip, label="farm_v2")
    elapsed = time.time() - nav_start
    return max(0.0, 3.0 - elapsed)


def should_do_weekly_card(time_manager) -> bool:
    """檢查今天是否需要執行每週卡片"""
    import time

    weekday = time.localtime().tm_wday
    return weekday in WEEKLY_CARD_DAYS


def farm(
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

    save_time += navigate_to_farm(d, cnn_model, device_ip=device_ip)

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

    save_time += navigate_to_home(d, cnn_model, device_ip=device_ip)

    logger.info(f"農場流程完成，節省時間: {save_time:.2f}秒")
    return save_time


def quick_farm(device_ip: str) -> float:
    """快速執行農場（從 game_api 調用）"""
    import uiautomator2 as u2

    d = u2.connect(device_ip)
    return farm(d, device_ip)
