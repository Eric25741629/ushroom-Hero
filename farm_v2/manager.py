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
from game_state.detector import get_stage
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


def navigate_to_farm(d: "uiauto.Device", cnn_model=None) -> float:
    """導航到農場頁面並返回節省的時間"""
    save_time = 0.0

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
    """從農場返回首頁，最多重試 60 秒。OCR 驅動。

    每輪：OCR get_stage → 主頁面就結束；否則
      1) 點右下 (480, 929) — 純農場頁的「返回」鈕
      2) OCR 找「關閉」處理彈窗（公告 / 商店 / 確認框）
      3) 點 home (321, 920) — 在家園可回主頁面
    沒有 cnn_model 時走盲點簡化路徑（給測試/dry-run）。
    """
    save_time = 0.0

    if cnn_model is None:
        click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))
        click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))
        return save_time

    exit_start = time.time()
    last_stage = "__init__"
    attempt = 0
    reached_main = False
    while time.time() - exit_start < 60:
        attempt += 1
        try:
            stage = get_stage(d, cnn_model)
        except Exception as e:
            logger.debug(f"[farm_v2] OCR get_stage 失敗 #{attempt}: {e}")
            stage = None

        if stage != last_stage:
            elapsed = time.time() - exit_start
            logger.info(f"[farm_v2] 退出嘗試 #{attempt}, OCR={stage}, elapsed={elapsed:.1f}s")
            if device_ip:
                try:
                    save_error_screenshot(d, device_ip, str(stage), f"farm_exit_attempt_{attempt}")
                except Exception as e:
                    logger.debug(f"[farm_v2] 截圖保存失敗: {e}")
            last_stage = stage

        if stage == "主頁面":
            elapsed = time.time() - exit_start
            save_time += max(0, 3 - elapsed)
            logger.info(f"[farm_v2] 已回到主頁面 (OCR)，耗時 {elapsed:.1f}s")
            reached_main = True
            break

        click_with_jitter(d, COORD["farm_tab"][0], COORD["farm_tab"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["medium"]))

        if img_tools.click_str_by_server(d, "關閉", wait_timeout=0):
            logger.info(f"[farm_v2] OCR 找到「關閉」並點擊 (stage={stage})")
            time.sleep(wait_jitter(TIMING["medium"]))
            continue

        click_with_jitter(d, COORD["home"][0], COORD["home"][1], jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

    if not reached_main:
        logger.warning(f"[farm_v2] 退出超時 60s，最後 stage={last_stage}")
        if device_ip:
            try:
                save_error_screenshot(d, device_ip, str(last_stage), "farm_exit_timeout")
            except Exception as e:
                logger.debug(f"[farm_v2] 超時截圖保存失敗: {e}")

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

    save_time += navigate_to_home(d, cnn_model, device_ip=device_ip)

    logger.info(f"農場流程完成，節省時間: {save_time:.2f}秒")
    return save_time


def quick_farm(device_ip: str) -> float:
    """快速執行農場（從 game_api 調用）"""
    import uiautomator2 as u2

    d = u2.connect(device_ip)
    return run_farm(d, device_ip)
