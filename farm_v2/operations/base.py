"""農場原子操作 - 點擊與截圖工具"""

from __future__ import annotations
import time
import random
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

logger = logging.getLogger("farm_v2.ops")


def click_with_jitter(d: "uiauto.Device", x: int, y: int, jitter: int = 5) -> None:
    """帶隨機偏移的點擊"""
    d.click(x + random.randint(-jitter, jitter), y + random.randint(-jitter, jitter))


def wait_jitter(base: float) -> float:
    """帶隨機偏移的等待時間"""
    return base + random.uniform(-0.2, 0.2)


def safe_screenshot(d: "uiauto.Device", format: str = "opencv", retries: int = 3):
    """安全截圖，失敗時重試"""
    for i in range(retries):
        try:
            return d.screenshot(format=format)
        except Exception as e:
            logger.warning(f"截圖失敗 #{i + 1}: {e}")
            time.sleep(1)
    raise RuntimeError("截圖失敗次數過多")


def find_and_click_by_template(
    d: "uiauto.Device",
    template_path: str,
    threshold: float = 0.7,
    region: tuple = None,
) -> bool:
    """透過模板匹配點擊"""
    import cv2
    import os

    if not os.path.exists(template_path):
        logger.warning(f"模板不存在: {template_path}")
        return False

    template = cv2.imread(template_path)
    if template is None:
        logger.warning(f"無法讀取模板: {template_path}")
        return False

    img = safe_screenshot(d)

    if region:
        x, y, w, h = region
        img = img[y : y + h, x : x + w]
        match_x, match_y = x, y
    else:
        match_x, match_y = 0, 0

    res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

    if max_val > threshold:
        h, w = template.shape[:-1]
        click_with_jitter(
            d, match_x + max_loc[0] + w // 2, match_y + max_loc[1] + h // 2
        )
        time.sleep(wait_jitter(1.0))
        return True
    return False
