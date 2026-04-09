from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DEFAULT_CNN_MODEL = os.path.join(os.path.dirname(__file__), '..', 'models', 'checkpoints', 'best.pth')

GRID_CFG = {
    'H': 7,
    'W': 6,
    'x0': 6,
    'y0': 227,
    'x1': 535,
    'y1': 852,
}

expected_points: List[Tuple[Tuple[int, int], Tuple[int, int, int]]] = [
    ((382, 79), (17, 20, 28)),
    ((307, 151), (19, 25, 48)),
    ((397, 180), (20, 16, 22)),
    ((315, 72), (21, 20, 24)),
    ((350, 117), (22, 24, 42)),
    ((429, 138), (33, 36, 64)),
    ((289, 189), (14, 21, 48)),
    ((132, 56), (11, 18, 38)),
    ((500, 216), (35, 42, 51)),
]
MIN_REQUIRED = 5
TOL = 3

SCREEN_CHECK_PROFILES = (
    {
        'name': 'legacy_dark_ui',
        'points': expected_points,
        'min_required': MIN_REQUIRED,
        'tol': TOL,
    },
    {
        'name': 'web_h5_bright_ui_v1',
        'points': [
            ((382, 79), (45, 38, 73)),
            ((307, 151), (40, 56, 109)),
            ((397, 180), (44, 37, 50)),
            ((315, 72), (38, 38, 50)),
            ((350, 117), (39, 51, 93)),
            ((429, 138), (71, 76, 137)),
            ((289, 189), (23, 43, 100)),
            ((132, 56), (142, 149, 169)),
            ((500, 216), (78, 93, 112)),
        ],
        'min_required': 5,
        'tol': 18,
    },
)

SCREEN_CHECK_REGION_GUARD = {
    'roi_std_min': 12.0,
    'roi_mean_min': 20.0,
    'roi_mean_max': 235.0,
    'bright_ratio_max': 0.55,
    'dark_ratio_max': 0.55,
}

COST_TABLE: Dict[str, Optional[int]] = {
    'empty': 0,
    'void': 0,
    'pit': 0,
    'dug_pit': 0,
    'dirt': 1,
    'unreachable_dirt': 1,
    'one_hit_rock': 1,
    'rock': 2,
    'unreachable_rock': 2,
    'reachable_pit': 1,
    'unreachable_pit': None,
    'unreachable_empty': 0,
}

REWARD_TABLE: Dict[str, int] = {
    'pit': 0,
    'reachable_pit': 50,
    'unreachable_pit': 50,
    'dug_pit': 0,
}

MINE_LABELS = set(REWARD_TABLE.keys())

DEFAULT_CLASSES: Sequence[str] = (
    'dirt',
    'dug_pit',
    'empty',
    'one_hit_rock',
    'reachable_pit',
    'rock',
    'unreachable_dirt',
    'unreachable_pit',
    'unreachable_rock',
    'unreachable_empty',
)

HIT_TABLE = {
    'empty': 0,
    'dirt': 1,
    'unreachable_dirt': 1,
    'rock': 2,
    'unreachable_rock': 2,
    'one_hit_rock': 1,
    'pit': 1,
    'reachable_pit': 1,
    'unreachable_pit': 1,
    'dug_pit': 0,
    'unreachable_empty': 0,
}

OCR_SERVER_URL = 'http://127.0.0.1:5001'

__all__ = [
    'PROJECT_ROOT',
    'DEFAULT_CNN_MODEL',
    'GRID_CFG',
    'expected_points',
    'MIN_REQUIRED',
    'TOL',
    'SCREEN_CHECK_PROFILES',
    'SCREEN_CHECK_REGION_GUARD',
    'COST_TABLE',
    'REWARD_TABLE',
    'MINE_LABELS',
    'DEFAULT_CLASSES',
    'HIT_TABLE',
    'OCR_SERVER_URL',
]
