"""Coordinate math for the 航海 (sea) season map.

Two coordinate spaces are in play:

* **logical** — what cocos `camera.worldToScreen()` returns: origin bottom-left,
  y-up, sized to the design resolution 720x1280.
* **frame** — the screenshot / click surface used by Playwright and ADB:
  origin top-left, y-down, sized 540x960.

All functions here are pure (no page / device access) so the navigation maths can
be unit-tested without a live game. The live driving loop lives in the task layer.

Calibration measured live on 5554 (2026-05-25): a horizontal drag of N frame-px
moves the camera by ``WORLD_PER_PX * N`` world units in the **opposite** direction
(drag left -> camera/world-x increases).
"""
from __future__ import annotations

import math
from typing import Tuple

Point = Tuple[float, float]

DESIGN_W, DESIGN_H = 720, 1280
FRAME_W, FRAME_H = 540, 960
WORLD_PER_PX = 2.0  # world units moved per frame-px dragged (magnitude; direction inverse)


def world_to_pixel(
    logical_x: float,
    logical_y: float,
    frame: Tuple[int, int] = (FRAME_W, FRAME_H),
    design: Tuple[int, int] = (DESIGN_W, DESIGN_H),
) -> Point:
    """Convert a cocos ``worldToScreen`` logical point to a click/screenshot pixel."""
    fw, fh = frame
    dw, dh = design
    px = logical_x * fw / dw
    py = (dh - logical_y) * fh / dh
    return (px, py)


def is_on_screen(
    pixel: Point,
    frame: Tuple[int, int] = (FRAME_W, FRAME_H),
    margin: float = 0.0,
) -> bool:
    """True when ``pixel`` sits inside the frame, keeping ``margin`` px clear of edges."""
    px, py = pixel
    fw, fh = frame
    return (margin <= px <= fw - margin) and (margin <= py <= fh - margin)


def world_delta_to_drag_px(world_delta: float, world_per_px: float = WORLD_PER_PX) -> float:
    """Frame-px to drag to move the camera by ``world_delta`` world units (X axis).

    Open-loop helper for the ADB path (no ``worldToScreen`` feedback there). The sign
    is inverse: to push the camera toward higher world-x you drag the canvas left.
    """
    return -world_delta / world_per_px


def center_drag(
    tile_pixel: Point,
    frame: Tuple[int, int] = (FRAME_W, FRAME_H),
    gain: float = 1.0,
    max_step: float = math.inf,
) -> Tuple[Point, Point]:
    """One closed-loop pan step that nudges ``tile_pixel`` toward the frame centre.

    The map is direct-manipulation: dragging the canvas by ``v`` shifts on-screen
    content by ``~gain*v``. So to move the tile to centre we drag by ``(centre-tile)/gain``,
    starting the gesture at the centre. ``max_step`` caps a single drag's magnitude so a
    far-off tile is reached in several small pans (no inertia flick, no overshoot) — the
    caller re-reads ``worldToScreen`` and calls again until the tile is centred.
    """
    fw, fh = frame
    cx, cy = fw / 2.0, fh / 2.0
    dx = (cx - tile_pixel[0]) / gain
    dy = (cy - tile_pixel[1]) / gain
    dist = math.hypot(dx, dy)
    if dist > max_step and dist > 0:
        scale = max_step / dist
        dx *= scale
        dy *= scale
    start = (cx, cy)
    end = (cx + dx, cy + dy)
    return start, end
