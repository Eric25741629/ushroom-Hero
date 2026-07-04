"""每週卡片操作 — 只保留 check_if_parttime 打工偵測。

舊的 run_weekly_card / buy_shop_items / collect_weekly_card / do_fertilize /
cancel_work 流程已由 harvest_card.py 取代，於 audit 清除。
"""

from __future__ import annotations
import logging
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomator2 as uiauto

from .base import safe_screenshot

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
