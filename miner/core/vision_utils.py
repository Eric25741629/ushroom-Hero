from __future__ import annotations

import io
from typing import Iterable, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

from .config import (
    GRID_CFG,
    MIN_REQUIRED,
    SCREEN_CHECK_PROFILES,
    SCREEN_CHECK_REGION_GUARD,
    TOL,
    expected_points,
)

BGRColor = Tuple[int, int, int]
Point = Tuple[int, int]


def to_bgr_np(image: Image.Image | np.ndarray | bytes) -> np.ndarray:
    if isinstance(image, Image.Image):
        arr = np.array(image)
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim == 3 and arr.shape[2] == 3:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        raise ValueError(f'Unsupported PIL shape: {arr.shape}')

    if isinstance(image, np.ndarray):
        arr = image
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim == 3 and arr.shape[2] == 3:
            return arr
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        raise ValueError(f'Unsupported ndarray shape: {arr.shape}')

    if isinstance(image, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(image)).convert('RGB')
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    raise TypeError(f'Unknown image type: {type(image)}')


def bgr_pixel(img_bgr: np.ndarray, x: int, y: int) -> Tuple[BGRColor, BGRColor]:
    b, g, r = img_bgr[y, x].tolist()
    return (b, g, r), (r, g, b)


def pixel_match(bgr_actual: np.ndarray, bgr_expected: Sequence[int], tol: int = 0) -> bool:
    diff = np.abs(bgr_actual.astype(int) - np.array(bgr_expected, dtype=int))
    return bool(np.all(diff <= tol))


def _count_matches(
    img_bgr: np.ndarray,
    points: Iterable[Tuple[Point, BGRColor]],
    tol: int,
) -> int:
    matched = 0
    for (x, y), bgr_exp in points:
        bgr_act = img_bgr[y, x]
        if pixel_match(bgr_act, bgr_exp, tol=tol):
            matched += 1
    return matched


def _board_region_guard(img_bgr: np.ndarray) -> bool:
    x0, y0, x1, y1 = GRID_CFG['x0'], GRID_CFG['y0'], GRID_CFG['x1'], GRID_CFG['y1']
    roi = img_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return False

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mean_val = float(gray.mean())
    std_val = float(gray.std())
    bright_ratio = float(np.mean(gray >= 235))
    dark_ratio = float(np.mean(gray <= 20))

    return (
        std_val >= SCREEN_CHECK_REGION_GUARD['roi_std_min']
        and SCREEN_CHECK_REGION_GUARD['roi_mean_min'] <= mean_val <= SCREEN_CHECK_REGION_GUARD['roi_mean_max']
        and bright_ratio <= SCREEN_CHECK_REGION_GUARD['bright_ratio_max']
        and dark_ratio <= SCREEN_CHECK_REGION_GUARD['dark_ratio_max']
    )


def check_points(
    img,
    points: Iterable[Tuple[Point, BGRColor]] | None = None,
    tol: int | None = None,
    min_required: int | None = None,
) -> Tuple[bool, int]:
    img_bgr = to_bgr_np(img)
    samples = list(points or expected_points)
    tolerance = tol if tol is not None else TOL
    threshold = min_required if min_required is not None else MIN_REQUIRED

    if points is not None:
        n_ok = _count_matches(img_bgr, samples, tol=tolerance)
        return n_ok >= threshold, n_ok

    best_matches = 0
    guard_ok = _board_region_guard(img_bgr)
    for profile in SCREEN_CHECK_PROFILES:
        n_ok = _count_matches(img_bgr, profile['points'], tol=profile['tol'])
        best_matches = max(best_matches, n_ok)
        if n_ok >= profile['min_required'] and guard_ok:
            return True, n_ok

    return False, best_matches


__all__ = ['to_bgr_np', 'bgr_pixel', 'pixel_match', 'check_points']
