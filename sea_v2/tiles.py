"""Parse the season-map object layer into typed tiles and choose daily-task targets.

The cocos node name under ``SeasonMapScene/unit/obj/*`` *is* the tile type, so no OCR
is needed:

    base        主城 / 大本營  (every player's — ours is the one nearest the camera)
    resource_1  資源 Lv1       remain   遺跡 (relic)
    resource_2  資源 Lv2       s4_totem 圖騰
    resource_3  資源 Lv3       s4_empire 帝國中心

All functions are pure: callers pass the raw ``[{"name","wp":{"x","y"}}, ...]`` list
drained from the scene, so selection logic is unit-testable without a live game.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

BASE = "base"
RESOURCE_LV1 = "resource_1"
RELIC = "remain"

Point = Tuple[float, float]


@dataclass(frozen=True)
class Tile:
    name: str
    wx: float
    wy: float

    @property
    def wp(self) -> Point:
        return (self.wx, self.wy)


@dataclass
class DailyTargets:
    """Tiles chosen to satisfy the daily quota (garrison 2x resource_1 + attack 1 relic)."""

    resources: List[Tile] = field(default_factory=list)
    relic: Optional[Tile] = None


def parse_tiles(raw_objs: Sequence[dict]) -> List[Tile]:
    """Build :class:`Tile` list, skipping entries without a usable world position."""
    out: List[Tile] = []
    for o in raw_objs or []:
        name = o.get("name")
        wp = o.get("wp")
        if not name or not isinstance(wp, dict):
            continue
        if "x" not in wp or "y" not in wp:
            continue
        out.append(Tile(name=name, wx=wp["x"], wy=wp["y"]))
    return out


def _dist2(a: Point, b: Point) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def home_base(tiles: Sequence[Tile], cam_wp: Point) -> Optional[Tile]:
    """The ``base`` tile nearest the camera — i.e. our own main city, not a neighbour's."""
    bases = [t for t in tiles if t.name == BASE]
    if not bases:
        return None
    return min(bases, key=lambda t: _dist2(t.wp, cam_wp))


def nearest(tiles: Sequence[Tile], type_name: str, origin: Point, n: int = 1) -> List[Tile]:
    """The ``n`` tiles of ``type_name`` closest to ``origin`` (nearest first)."""
    cands = [t for t in tiles if t.name == type_name]
    cands.sort(key=lambda t: _dist2(t.wp, origin))
    return cands[:n]


def pick_daily_targets(tiles: Sequence[Tile], home: Tile) -> DailyTargets:
    """Choose the tiles for today's quota: 2 bottom-most resource_1 + nearest relic.

    Resources are sorted by ``wy`` ascending (cocos Y-up, so lowest wy = bottom of map)
    to reach the furthest resource_1 tiles first.
    """
    origin = home.wp
    res_candidates = [t for t in tiles if t.name == RESOURCE_LV1]
    res_candidates.sort(key=lambda t: t.wy)  # lowest wy = bottom of map first
    resources = res_candidates[:2]
    relics = nearest(tiles, RELIC, origin=origin, n=1)
    return DailyTargets(resources=resources, relic=relics[0] if relics else None)
