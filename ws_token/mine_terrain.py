"""Deterministic undug-terrain reconstruction from the static configMine_template.

WHY: WS 0x0c01 never sends the terrain TYPE of an *undug* cell — the frontend
tiles fixed 7x6 templates client-side. We reverse-engineered exactly how, live
via CDP (5558 + 5554, 2026-06-29), verified 54/54 undug cells across two accounts:

    q = depth + 6 ; area = q // ROWS ; tpl_row = q % ROWS
    terrain[depth][col] = configMine_template[ area_info[area] ][tpl_row][col]

``area_info`` is the parsed 0x0c01 field-6 (area_id -> template_id), already on
every MineBoard. So undug terrain — INCLUDING cells never dug or revealed — is
computable for every depth whose area is in area_info (current +/-1 area, ~21
rows = the 7-row viewport plus the look-ahead band). No CNN, no screenshot.

This replaced the old per-device self-learning model (`TerrainModel`): once the
client's tiling rule was known there is nothing to "learn" — every cell is a
direct table lookup, exact and instant, instead of slowly inferred from reveals.
"""
from __future__ import annotations

import io
import json
import os
from typing import Dict, List, Optional, Tuple

ROWS = 7
COLS = 6
AIR = 100
DIRT = 201
STONE = 202
PIT = 401

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLE_PATH = os.path.join(_REPO, "docs", "protocol", "mine_config_tables.json")

# A template grid is ROWS x COLS of {AIR, DIRT, STONE}.
Grid = Tuple[Tuple[int, ...], ...]


def _grid_rowmajor(flat: List[int]) -> Grid:
    return tuple(tuple(flat[r * COLS + c] for c in range(COLS)) for r in range(ROWS))


_TEMPLATES_BY_ID: Optional[Dict[int, Grid]] = None


def _templates_by_id() -> Dict[int, Grid]:
    """{template_id -> 7x6 row-major grid of {AIR,DIRT,STONE}}.

    Keyed by the template's own id (entry[0]) — exactly what an ``area_info``
    value holds — and NOT deduped (distinct ids can share a grid).
    """
    global _TEMPLATES_BY_ID
    if _TEMPLATES_BY_ID is None:
        with io.open(_TABLE_PATH, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        out: Dict[int, Grid] = {}
        for _k, ent in data["template"]["entries"].items():
            flat = list(ent[1])
            if len(flat) != ROWS * COLS:
                continue
            out[int(ent[0])] = _grid_rowmajor(flat)
        _TEMPLATES_BY_ID = out
    return _TEMPLATES_BY_ID


def terrain_at(depth: int, col: int, area_info: Dict[int, int]) -> Optional[int]:
    """Undug terrain {AIR=100, DIRT=201, STONE=202} for an absolute (depth, col),
    or ``None`` when ``area_info`` does not cover that depth's area / col is out of
    range. Pure config lookup — valid even for cells never dug or revealed."""
    c = int(col)
    if not (0 <= c < COLS):
        return None
    q = int(depth) + 6
    tid = (area_info or {}).get(q // ROWS)
    if tid is None:
        return None
    grid = _templates_by_id().get(int(tid))
    if grid is None:
        return None
    return grid[q % ROWS][c]


if __name__ == "__main__":  # tiny runnable self-check (no framework)
    ai = {44701: 13}  # 5558 live area_info entry
    assert terrain_at(312902, 0, ai) == DIRT, terrain_at(312902, 0, ai)
    assert terrain_at(312903, 2, ai) == STONE, terrain_at(312903, 2, ai)
    assert terrain_at(312902, 0, {}) is None      # no area_info -> unknown
    assert terrain_at(312902, 9, ai) is None      # col out of range
    print("mine_terrain static self-check OK")
