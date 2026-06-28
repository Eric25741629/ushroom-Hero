"""Deterministic terrain lookup must reproduce the frontend's undug terrain exactly.

Rule (reverse-engineered live, verified on 2 accounts 54/54):
    q = depth + 6 ; area = q // 7 ; tpl_row = q % 7
    terrain[depth][col] = configMine_template[ area_info[area] ][tpl_row][col]
"""
import io
import json
import os

import pytest

from ws_token.mine_terrain import terrain_at, AIR, DIRT, STONE

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ws_mine_terrain_boards.json")


def _boards():
    with io.open(_FIX, encoding="utf-8-sig") as fh:
        return json.load(fh)["boards"]


@pytest.mark.parametrize("board", _boards(), ids=lambda b: b["device"])
def test_terrain_at_reproduces_frontend_undug_terrain(board):
    top = board["top"]
    area_info = {int(k): int(v) for k, v in board["area_info"].items()}
    pits = {tuple(p) for p in board["pits"]}
    checked = 0
    for row_s, cells in board["rows"].items():
        depth = top + int(row_s)
        for col_s, terrain in cells.items():
            col = int(col_s)
            if (depth, col) in pits:
                continue  # pit cell: terrain is overlaid by WS, not the template
            got = terrain_at(depth, col, area_info)
            assert got == terrain, (
                f"{board['device']} depth={depth} col={col}: "
                f"expected {terrain}, got {got}"
            )
            checked += 1
    assert checked >= 20  # both boards have plenty of solid cells


def test_terrain_at_returns_none_outside_area_info():
    # area_info only carries current +/-1 area; far depths are unknown.
    area_info = {44701: 13}
    far_depth = 312902 + 7 * 50  # area 44751-ish, not in area_info
    assert terrain_at(far_depth, 0, area_info) is None


def test_terrain_at_formula_indices():
    # area = (depth+6)//7, tpl_row = (depth+6)%7; value=template id.
    # depth 312902 -> q=312908 -> area 44701, row (312908%7).
    area_info = {44701: 13}
    # 5558 r0 c0 = dirt (verified live)
    assert terrain_at(312902, 0, area_info) == DIRT
    # values returned are raw template enum
    assert {AIR, DIRT, STONE} == {100, 201, 202}


def test_board_to_grid_uses_static_terrain_for_undug_cells():
    """board_to_grid must label undug actives + occluded cells from the template
    (via area_info), not blanket 'dirt'/'air'. Real 5558 area_info."""
    from ws_token.mining import MineBoard
    from ws_token.mining_adapter import board_to_grid

    area_info = {44700: 19, 44701: 13, 44702: 14}
    # active stone cell at depth 312903 col2 (5558 r1 c2 = stone, verified live)
    active_id = 312903 * 100 + (2 + 1)
    board = MineBoard(
        max_num=120, next_time=0, area=44701, baseline=312907,
        actives=[active_id], area_info=area_info, blocks=[], holes=[],
    )
    grid = board_to_grid(board)
    # undug active over a STONE template cell -> "rock", not "dirt"
    assert grid[1][2] == "rock", grid[1]
    # occluded cell (not active, not a block) over a DIRT template cell ->
    # solid wall, NOT passable air
    assert grid[0][0] == "unreachable_dirt", grid[0]


def test_board_to_grid_without_area_info_keeps_legacy_defaults():
    """No area_info (e.g. malformed board) -> static lookup returns None ->
    undug active = 'dirt', occluded = 'empty' (legacy safe behaviour)."""
    from ws_token.mining import MineBoard
    from ws_token.mining_adapter import board_to_grid

    active_id = 312903 * 100 + (2 + 1)
    board = MineBoard(
        max_num=120, next_time=0, area=44701, baseline=312907,
        actives=[active_id], area_info={}, blocks=[], holes=[],
    )
    grid = board_to_grid(board)
    assert grid[1][2] == "dirt"
    assert grid[0][0] == "empty"
