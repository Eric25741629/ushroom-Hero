"""OCR helpers for mining inventory checks."""
from __future__ import annotations

import os
import re
import time
from typing import Optional

import cv2
from config.paths import OCR_ERRORS_DIR_STR

try:
    import img_tools
except Exception:
    try:
        from .. import img_tools
    except Exception:
        import sys
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        import img_tools


try:
    from .config import OCR_SERVER_URL  # noqa: F401
except Exception:
    try:
        from miner.core.config import OCR_SERVER_URL  # noqa: F401
    except Exception:
        try:
            from config import OCR_SERVER_URL  # noqa: F401
        except Exception:
            OCR_SERVER_URL = os.environ.get("OCR_SERVER_URL", "http://127.0.0.1:5001")


def _extract_inventory_number(result: Optional[dict]) -> int:
    """Parse OCR result and return the inventory count (left side of x/y if present)."""
    if not result or result == 404:
        return 0
    if isinstance(result, dict) and result.get("success") is False:
        return 0

    ocr_results = result.get("ocr_results", []) if isinstance(result, dict) else []
    candidates: list[int] = []
    for res in ocr_results:
        text = str(res.get("text", "")).strip()
        if "/" in text:
            left = text.split("/", 1)[0]
            nums = re.findall(r"\d+", left)
            if nums:
                candidates.append(int(nums[-1]))
                continue

        nums = re.findall(r"\d+", text)
        if nums:
            # 保守取最小值，避免 OCR 把 0/2、2/2 或旁邊殘影誤讀成較大的可用數量。
            candidates.append(min(int(num) for num in nums))

    return min(candidates) if candidates else 0


def _get_frame(d, frame=None):
    """Return shared OpenCV frame if provided; otherwise capture one."""
    return frame if frame is not None else d.screenshot(format="opencv")


def check_pickaxe_count(d, frame=None, allow_none: bool = False):
    """OCR current pickaxe count.

    Default fallback (``allow_none=False``): returns 20 on any OCR failure,
    matching the legacy behaviour the old mining loop depended on.

    Opt-in (``allow_none=True``): returns ``None`` on failure so the caller
    can fall back to its own internal counter. The old default silently
    pretended OCR succeeded with a value of 20, which let a broken OCR
    pipeline keep the bot mining indefinitely — the new mining loop treats
    OCR as a validator rather than a source of truth and needs to
    distinguish "OCR says 20" from "OCR is broken".
    """
    fallback = None if allow_none else 20
    try:
        source = _get_frame(d, frame)
        img = source[13:40, 148:251]
        img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, img_bin = cv2.threshold(img_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(img_bin)
        if result is None:
            return fallback

        ocr_results = result.get("ocr_results", [])
        if not ocr_results:
            save_ocr_error_image(img)
            return fallback

        text = ocr_results[0].get("text", "").split("/", 1)[0]
        nums = re.findall(r"\d+", text)
        if not nums:
            save_ocr_error_image(img)
            return fallback
        try:
            return int(nums[0])
        except ValueError:
            save_ocr_error_image(img)
            return fallback
    except Exception:
        return fallback


def check_drill_num(d, frame=None) -> int:
    """OCR drill inventory count. Returns 0 on failure."""
    try:
        img = _get_frame(d, frame)
        roi = img[910:950, 140:210]
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, roi_bin = cv2.threshold(roi_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(roi_bin)
        return _extract_inventory_number(result)
    except Exception:
        return 0


def check_boom_num(d, frame=None) -> int:
    """OCR bomb inventory count. Returns 0 on failure."""
    try:
        img = _get_frame(d, frame)
        roi = img[910:950, 350:420]
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, roi_bin = cv2.threshold(roi_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = img_tools.analyze_skill_via_http(roi_bin)
        return _extract_inventory_number(result)
    except Exception:
        return 0


def save_ocr_error_image(img) -> None:
    """Persist OCR error crop for debugging."""
    os.makedirs(OCR_ERRORS_DIR_STR, exist_ok=True)
    timestamp = int(time.time())
    cv2.imwrite(os.path.join(OCR_ERRORS_DIR_STR, f"ocr_error_{timestamp}.png"), img)


__all__ = [
    "check_pickaxe_count",
    "check_drill_num",
    "check_boom_num",
    "save_ocr_error_image",
]