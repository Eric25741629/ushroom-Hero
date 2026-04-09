import numpy as np
from miner.core.config import GRID_CFG, SCREEN_CHECK_PROFILES
from miner.core.vision_utils import check_points


def _make_textured_board_image():
    img = np.zeros((960, 540, 3), dtype=np.uint8)
    x0, y0, x1, y1 = GRID_CFG["x0"], GRID_CFG["y0"], GRID_CFG["x1"], GRID_CFG["y1"]
    for y in range(y0, y1):
        for x in range(x0, x1):
            value = 40 + ((x + y) % 120)
            img[y, x] = (value, min(255, value + 20), max(0, value - 10))
    return img


def test_check_points_accepts_bright_web_profile():
    img = _make_textured_board_image()
    profile = next(profile for profile in SCREEN_CHECK_PROFILES if profile["name"] == "web_h5_bright_ui_v1")
    for (x, y), bgr in profile["points"]:
        img[y, x] = np.array(bgr, dtype=np.uint8)

    passed, matched = check_points(img)

    assert passed is True
    assert matched >= profile["min_required"]


def test_check_points_rejects_blank_bright_screen():
    img = np.full((960, 540, 3), 255, dtype=np.uint8)

    passed, matched = check_points(img)

    assert passed is False
    assert matched == 0
