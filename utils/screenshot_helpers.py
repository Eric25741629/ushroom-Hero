"""Screenshot + main-page-mismatch helpers extracted from new_main_v2.py.

These helpers wrap SmartScreenshotRecorder with the conventional
bot_state / logger side effects used throughout the daily task flow.
"""
from __future__ import annotations

from typing import Optional

import bot_state
from utils.logging_utils import logger
from utils.smart_screenshot import SmartScreenshotRecorder

_smart_shot = SmartScreenshotRecorder()


def save_error_screenshot(device_obj, ip: str, stage: str, reason: str) -> Optional[str]:
    try:
        image_path = _smart_shot.capture(
            device_obj=device_obj,
            ip=ip,
            stage=stage,
            reason=reason,
            task="",
        )
        if not image_path:
            logger.error(f"[{ip}] {reason} 失敗，無法取得截圖，stage={stage}")
            return None
        logger.error(f"[{ip}] {reason}，已保存截圖，stage={stage}, path={image_path}")
        return image_path
    except Exception as e:
        logger.error(f"[{ip}] 保存錯誤截圖失敗: reason={reason}, stage={stage}, err={e}", exc_info=True)
        return None


def log_main_page_mismatch(device_obj, ip: str, stage: str, task: str, reason: str) -> Optional[str]:
    bot_state.update_state(ip, task=task, step=f"未在主頁面: {stage}")
    screenshot_path = _smart_shot.capture(
        device_obj=device_obj,
        ip=ip,
        stage=stage,
        reason=reason,
        task=task,
    )
    if not screenshot_path:
        screenshot_path = save_error_screenshot(device_obj, ip, stage, reason)
    logger.error(f"[{ip}] {reason}，stage={stage}, screenshot={screenshot_path}")
    return screenshot_path
