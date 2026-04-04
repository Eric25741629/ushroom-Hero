"""影像處理相關的共用工具函式。"""
from __future__ import annotations

import io
from typing import Iterable, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

from .config import MIN_REQUIRED, TOL, expected_points

BGRColor = Tuple[int, int, int]
Point = Tuple[int, int]


def to_bgr_np(image: Image.Image | np.ndarray | bytes) -> np.ndarray:
    """把 PIL / ndarray / bytes 轉換成 OpenCV 慣用的 BGR 陣列。"""
    if isinstance(image, Image.Image):
        arr = np.array(image)
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim == 3 and arr.shape[2] == 3:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        raise ValueError(f"Unsupported PIL shape: {arr.shape}")

    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim == 3 and arr.shape[2] == 3:
            return arr
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        raise ValueError(f"Unsupported ndarray shape: {arr.shape}")

    if isinstance(image, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(image)).convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    raise TypeError(f"Unknown image type: {type(image)}")


def bgr_pixel(img_bgr: np.ndarray, x: int, y: int) -> Tuple[BGRColor, BGRColor]:
    """回傳 BGR 與 RGB 雙版本的像素值。"""
    b, g, r = img_bgr[y, x].tolist()
    return (b, g, r), (r, g, b)


def pixel_match(bgr_actual: np.ndarray, bgr_expected: Sequence[int], tol: int = 0) -> bool:
    """比較像素顏色是否在容忍範圍內。"""
    diff = np.abs(bgr_actual.astype(int) - np.array(bgr_expected, dtype=int))
    return np.all(diff <= tol)


def check_points(
    img, points: Iterable[Tuple[Point, BGRColor]] | None = None, tol: int | None = None, min_required: int | None = None
) -> Tuple[bool, int]:
    """檢查畫面中固定幾個像素是否維持期望顏色，用來偵測 UI 是否遮擋。"""
    img_bgr = to_bgr_np(img)
    samples = list(points or expected_points)
    tolerance = tol if tol is not None else TOL
    threshold = min_required if min_required is not None else MIN_REQUIRED

    matched = []
    for (x, y), bgr_exp in samples:
        bgr_act = img_bgr[y, x]
        matched.append(pixel_match(bgr_act, bgr_exp, tol=tolerance))

    n_ok = sum(matched)
    return n_ok >= threshold, n_ok


__all__ = ["to_bgr_np", "bgr_pixel", "pixel_match", "check_points"]
