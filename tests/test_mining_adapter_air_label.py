"""board_to_grid must label an AIR active frontier cell as empty, not dirt.

Regression for the 5554 CDP cross-check (2026-07-01): active cells with no block
whose deterministic template terrain is AIR are open air pockets (CNN pixel-
verified), but the old code blanket-labelled every non-STONE active as "dirt",
inflating board density and distorting the A* descent trajectory.
"""
from ws_token import mine_terrain
import ws_token.mining_adapter as ma
from ws_token.mining import MineBoard


def _board(baseline, actives, area_info=None, blocks=None):
    return MineBoard(
        max_num=0, next_time=0, area=0, baseline=baseline,
        actives=list(actives), area_info=area_info or {},
        blocks=list(blocks or []), holes=[],
    )


def test_air_active_cell_is_empty_not_dirt(monkeypatch):
    baseline = 100
    depth = ma.viewport_top_depth(baseline)  # row 0
    air_bid = depth * 100 + 2                 # col index 1
    monkeypatch.setattr(
        mine_terrain, "terrain_at",
        lambda d, c, ai: mine_terrain.AIR if (d == depth and c == 1) else None,
    )
    grid = ma.board_to_grid(_board(baseline, [air_bid]))
    assert grid[0][1] == "empty", grid[0][1]


def test_stone_dirt_active_unchanged(monkeypatch):
    baseline = 100
    depth = ma.viewport_top_depth(baseline)

    def fake(d, c, ai):
        if d != depth:
            return None
        return {0: mine_terrain.AIR, 1: mine_terrain.STONE, 2: mine_terrain.DIRT}.get(c)

    monkeypatch.setattr(mine_terrain, "terrain_at", fake)
    grid = ma.board_to_grid(_board(
        baseline, [depth * 100 + 1, depth * 100 + 2, depth * 100 + 3]))
    assert grid[0][0] == "empty"   # AIR
    assert grid[0][1] == "rock"    # STONE
    assert grid[0][2] == "dirt"    # DIRT
