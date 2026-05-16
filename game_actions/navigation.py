"""Shared navigation utility for returning to main page from farm/mining areas.

NOTE: lamp service exit uses different coordinates (447,801 -> 273,560) and
is NOT handled here. Only the farm/mining exit sequence is covered.
"""
import logging
import time
from typing import Optional

import img_tools
from farm_v2.operations import click_with_jitter, wait_jitter
from farm_v2.config import TIMING
from game_state.detector import get_stage
from utils.screenshot_helpers import save_error_screenshot

logger = logging.getLogger(__name__)

# Navigation coordinates: farm-area -> main page
_FARM_TAB = (480, 929)   # 農場頁右下「返回」鈕 / 家園 tab
_HOME_BTN = (321, 920)   # 家園 -> 主頁面 home 按鈕


def navigate_to_main_page(
    d,
    cnn_model=None,
    device_ip: Optional[str] = None,
    *,
    timeout: float = 60.0,
    label: str = "",
) -> bool:
    """從農場/挖礦頁面返回主頁面。

    策略：
    1. 若無 cnn_model：盲點擊 farm_tab -> home，直接回 True。
    2. 若有 cnn_model：
       a. 第一輪 sleep 等頁面穩定（避免剛退出時拍到過渡畫面）。
       b. OCR get_stage；若已是「主頁面」-> 成功。
       c. 否則：點 farm_tab -> 找「關閉」彈窗 -> 點 home。
       d. 超時則 log warning + error screenshot，回 False。

    Args:
        d:          裝置物件（adb 或 web_h5）。
        cnn_model:  CNN 模型，None 表示無 OCR 模式。
        device_ip:  裝置 IP，用於 error screenshot 命名。
        timeout:    最長等待秒數（預設 60s）。
        label:      log prefix 附加標籤（如 "farm_v2", "mining"）。

    Returns:
        True 若成功到達主頁面，False 若超時。
    """
    prefix = f"[navigate_to_main{'/' + label if label else ''}]"

    if cnn_model is None:
        # Blind mode -- no OCR available, just send the clicks
        click_with_jitter(d, *_FARM_TAB, jitter=5)
        time.sleep(wait_jitter(TIMING["very_long"]))
        click_with_jitter(d, *_HOME_BTN, jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))
        return True

    # First round: let the page stabilize after task completion
    time.sleep(wait_jitter(TIMING["medium"]))

    exit_start = time.time()
    last_stage = "__init__"
    attempt = 0

    while time.time() - exit_start < timeout:
        attempt += 1
        try:
            stage = get_stage(d, cnn_model)
        except Exception as e:
            logger.debug(f"{prefix} get_stage 失敗 #{attempt}: {e}")
            stage = None

        if stage != last_stage:
            elapsed = time.time() - exit_start
            logger.info(f"{prefix} 嘗試 #{attempt}, OCR={stage}, elapsed={elapsed:.1f}s")
            if device_ip and stage != "主頁面":
                try:
                    save_error_screenshot(d, device_ip, str(stage), f"nav_main_{attempt}")
                except Exception:
                    pass
            last_stage = stage

        if stage == "主頁面":
            elapsed = time.time() - exit_start
            logger.info(f"{prefix} 成功，耗時 {elapsed:.1f}s")
            return True

        # Navigate: farm tab -> dismiss popup -> home button
        click_with_jitter(d, *_FARM_TAB, jitter=5)
        time.sleep(wait_jitter(TIMING["medium"]))

        if img_tools.click_str_by_server(d, "關閉", wait_timeout=0):
            logger.info(f"{prefix} 找到「關閉」並點擊 (stage={stage})")
            time.sleep(wait_jitter(TIMING["medium"]))
            continue

        click_with_jitter(d, *_HOME_BTN, jitter=5)
        time.sleep(wait_jitter(TIMING["long"]))

    logger.warning(f"{prefix} 超時 {timeout:.0f}s，最後 stage={last_stage}")
    if device_ip:
        try:
            save_error_screenshot(d, device_ip, str(last_stage), "nav_main_timeout")
        except Exception:
            pass
    return False
