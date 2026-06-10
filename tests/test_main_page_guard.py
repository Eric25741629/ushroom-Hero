"""Unit tests for utils/main_page_guard.is_main_page_with_popup.

Pure boolean matcher: no device / cv2 dependency, build images with numpy.
"""
import numpy as np
import pytest

from utils.main_page_guard import (
    MAIN_PAGE_POPUP_PROFILE,
    PIXEL_SUM_TOLERANCE,
    is_main_page_with_popup,
)


def _blank_image() -> np.ndarray:
    # 540x960 BGR canvas (h=960, w=540) initialised to black.
    return np.zeros((960, 540, 3), dtype=np.uint8)


def _paint_profile(img: np.ndarray) -> np.ndarray:
    """Paint every profile pixel to its exact expected BGR (sum-match)."""
    for (x, y), expected_bgr in MAIN_PAGE_POPUP_PROFILE:
        img[y, x] = np.array(expected_bgr, dtype=np.uint8)
    return img


def test_returns_true_when_all_nine_pixels_match():
    img = _paint_profile(_blank_image())
    assert is_main_page_with_popup(img) is True


def test_returns_false_on_blank_image():
    img = _blank_image()
    assert is_main_page_with_popup(img) is False


def test_returns_false_when_one_pixel_off():
    img = _paint_profile(_blank_image())
    # Corrupt the last profile pixel well beyond tolerance.
    (x, y), _ = MAIN_PAGE_POPUP_PROFILE[-1]
    img[y, x] = np.array([0, 0, 0], dtype=np.uint8)
    assert is_main_page_with_popup(img) is False


def test_tolerance_boundary_inclusive_just_inside():
    # A channel-sum delta strictly less than the tolerance still matches.
    img = _paint_profile(_blank_image())
    (x, y), expected_bgr = MAIN_PAGE_POPUP_PROFILE[0]
    bump = PIXEL_SUM_TOLERANCE - 1
    img[y, x] = np.array(
        [min(255, expected_bgr[0] + bump), expected_bgr[1], expected_bgr[2]],
        dtype=np.uint8,
    )
    assert is_main_page_with_popup(img) is True


def test_tolerance_boundary_exclusive_at_threshold():
    # A channel-sum delta equal to the tolerance is rejected (>= threshold).
    img = _paint_profile(_blank_image())
    (x, y), expected_bgr = MAIN_PAGE_POPUP_PROFILE[0]
    img[y, x] = np.array(
        [min(255, expected_bgr[0] + PIXEL_SUM_TOLERANCE), expected_bgr[1], expected_bgr[2]],
        dtype=np.uint8,
    )
    assert is_main_page_with_popup(img) is False


def test_does_not_mutate_input():
    img = _paint_profile(_blank_image())
    before = img.copy()
    is_main_page_with_popup(img)
    assert np.array_equal(img, before)


def test_profile_has_nine_points():
    assert len(MAIN_PAGE_POPUP_PROFILE) == 9


def test_matches_legacy_inline_condition():
    """Byte-for-byte parity with the old inline 9-condition check.

    Reproduce the exact legacy expression on a random image and assert the
    helper agrees for every case.
    """
    rng = np.random.default_rng(1234)
    for _ in range(50):
        img = rng.integers(0, 256, size=(960, 540, 3), dtype=np.uint8)
        legacy = all(
            abs(np.sum(img[y, x]) - np.sum(list(bgr))) < 10
            for (x, y), bgr in MAIN_PAGE_POPUP_PROFILE
        )
        assert is_main_page_with_popup(img) == bool(legacy)
