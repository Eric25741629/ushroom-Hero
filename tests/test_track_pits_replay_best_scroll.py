"""Lock the row-shift behavior of tools/track_pits_replay.best_scroll.

best_scroll was refactored to delegate to miner.depth_tracker.best_scroll.
These tests pin the original semantics (symbol-char input via NORM, smaller
scroll preferred on ties, -1.0 ratio when too little comparable structure) so
the reconstruction tool's behavior is unchanged.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "track_pits_replay", REPO_ROOT / "tools" / "track_pits_replay.py"
)
track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track)


def _grid(rows):
    return [list(r) for r in rows]


def test_zero_scroll_identical_structure() -> None:
    g = _grid(["RRRRRR", "DDDDDD", "......", "RRRRRR",
               "......", "DDDDDD", "......"])
    s, ratio = track.best_scroll(g, g)
    assert s == 0
    assert ratio == 1.0


def test_scroll_one_row() -> None:
    prev = _grid(["......", "RRRRRR", "DDDDDD", "......",
                  "RRRRRR", "......", "DDDDDD"])
    cur = _grid(["RRRRRR", "DDDDDD", "......", "RRRRRR",
                 "......", "DDDDDD", "RRRRRR"])
    s, ratio = track.best_scroll(prev, cur)
    assert s == 1
    assert ratio == 1.0


def test_insufficient_overlap_returns_negative_ratio() -> None:
    air = _grid(["......"] * 7)
    s, ratio = track.best_scroll(air, air)
    assert ratio == -1.0
