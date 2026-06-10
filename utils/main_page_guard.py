"""Shared main-page-with-popup pixel guard.

The bot detects the situation where a popup is overlaid on top of the game's
main page by sampling 9 fixed pixels and comparing each against an expected
BGR value with a loose tolerance. This exact 9-point check was copy-pasted
byte-for-byte across several modules (device.py, Mission.py, park.py,
battle/manager.py, tools.py).

This module exposes a single *pure* boolean matcher so call sites can share
the detection logic while keeping their own retry-loop / dismiss semantics
unchanged.

The pixel profile mirrors ``opengold_v2.ui_controller._match_pixel_profile``:
each entry is ``((x, y), (b, g, r))`` and a pixel matches when
``abs(sum(img[y, x]) - sum(expected_bgr)) < TOLERANCE``.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

# Loose tolerance on the channel-sum, matching the original inline checks.
PIXEL_SUM_TOLERANCE: int = 10

# 9 sample points as ((x, y), (b, g, r)).
# NOTE: images are indexed ``img[y, x]`` (row=y, col=x).
MAIN_PAGE_POPUP_PROFILE: List[Tuple[Tuple[int, int], Tuple[int, int, int]]] = [
    ((189, 234), (179, 91, 70)),
    ((236, 218), (254, 241, 225)),
    ((318, 228), (254, 241, 225)),
    ((363, 236), (179, 91, 70)),
    ((132, 249), (162, 75, 57)),
    ((139, 264), (162, 75, 57)),
    ((154, 329), (194, 219, 227)),
    ((370, 361), (193, 218, 226)),
    ((451, 337), (44, 155, 111)),
]


def is_main_page_with_popup(img: "np.ndarray") -> bool:
    """Return True when *img* shows a popup overlaid on the main page.

    Pure function: samples the 9 fixed pixels and returns True only when every
    sampled pixel's channel-sum is within ``PIXEL_SUM_TOLERANCE`` of its
    expected value. Does not mutate *img* and performs no IO.
    """
    for (x, y), expected_bgr in MAIN_PAGE_POPUP_PROFILE:
        actual_sum = int(np.sum(img[y, x]))
        expected_sum = int(np.sum(expected_bgr))
        if abs(actual_sum - expected_sum) >= PIXEL_SUM_TOLERANCE:
            return False
    return True
