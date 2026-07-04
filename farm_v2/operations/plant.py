"""種植操作 — 只保留 check_slot_color 空槽偵測。

舊的 plant_one / plant_cycle 手動種植流程已由打工自動種植 + harvest_card
取代，於 audit 清除。
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from .base import safe_screenshot
from farm_v2.config import COORD

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
