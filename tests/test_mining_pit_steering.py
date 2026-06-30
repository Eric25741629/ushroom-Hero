"""Regressions for two 5554 CDP-live mining fixes (2026-07-01):

1. board_to_grid bottom-row fill: missing bottom cells whose deterministic
   terrain is STONE/DIRT must project as solid unreachable_rock/dirt, NOT
   phantom-air unreachable_empty. The phantom air let v1 "open floor7" by
   digging the row above, which never scrolled -> it cleared whole rows.
2. _select_dig_step below-viewport ore steering: when map_pits has a pit below
   the 7-row viewport (v1 is blind to it), the executed step must follow
   pit_directed_next toward the ore column, not v1's pit-blind floor7-opener.
"""
from ws_token import mine_terrain
import ws_token.mining_adapter as ma
from ws_token.mining import MineBoard, MineBlock
from ws_token.mining_supervised import _select_dig_step


def _board(baseline, blocks=None, actives=(), area_info=None):
    return MineBoard(
        max_num=0, next_time=0, area=0, baseline=baseline,
        actives=list(actives), area_info=area_info or {},
        blocks=list(blocks or []), holes=[],
    )


def test_bottom_row_missing_cell_uses_terrain_not_phantom_air(monkeypatch):
    """A missing bottom-row cell with STONE terrain -> unreachable_rock (solid),
    so it cannot become a phantom floor7-opener via the row above."""
    baseline = 1000
    top = ma.viewport_top_depth(baseline)           # row 0 depth
    bottom_depth = top + (ma.GRID_ROWS - 1)          # row 6
    # one real active bottom cell (col 1) so the bottom-row special case fires
    active_bottom = ma.grid_pos_to_block_id(baseline, row=6, col=1)

    def fake_terrain(d, c, ai):
        if d == bottom_depth and c == 3:
            return mine_terrain.STONE                # missing bottom cell col 3
        return None

    monkeypatch.setattr(mine_terrain, "terrain_at", fake_terrain)
    grid = ma.board_to_grid(_board(baseline, actives=[active_bottom]))
    assert grid[6][3] == "unreachable_rock", grid[6]
    assert grid[6][3] != "unreachable_empty"


def test_bottom_row_unknown_terrain_stays_unreachable_empty(monkeypatch):
    """terrain_at None (unknown / empty area_info) keeps the conservative
    unreachable_empty fill so floor7 is not falsely opened."""
    baseline = 1000
    active_bottom = ma.grid_pos_to_block_id(baseline, row=6, col=1)
    monkeypatch.setattr(mine_terrain, "terrain_at", lambda d, c, ai: None)
    grid = ma.board_to_grid(_board(baseline, actives=[active_bottom]))
    # the non-active bottom cells stay unreachable_empty (unchanged behaviour)
    assert grid[6][3] == "unreachable_empty", grid[6]


def test_select_dig_step_steers_to_below_viewport_pit():
    """A pit two rows below the viewport bottom must pull the executed step to
    its column, overriding v1's pit-blind floor7-opener."""
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    pit_depth = top + 8                              # row 8 -> below viewport
    pit_bid = pit_depth * 100 + 2                    # col index 1 (x=2)
    pit = MineBlock(block_id=pit_bid, x=2, y=pit_depth,
                    config_id=401, count=1, is_reward=1)
    # frontier: bottom-row actives at col 1 (toward pit) and col 3 (v1 drift)
    col1 = ma.grid_pos_to_block_id(baseline, row=6, col=1)
    col3 = ma.grid_pos_to_block_id(baseline, row=6, col=3)
    board = _board(baseline, blocks=[pit], actives=[col1, col3])

    # v1 (pit-blind) would pick col3; steering must override to the pit column.
    v1_steps = [{"type": "dig", "block_id": col3, "row": 6, "col": 3}]
    step = _select_dig_step(board, v1_steps, hold_floor=False, grid=None)
    assert step is not None
    chosen = int(step["block_id"])
    assert chosen == int(ma.pit_directed_next(board)), (chosen, col1, col3)
    # and it heads to col 1 (the ore column), not v1's col 3
    assert chosen % 100 - 1 == 1, chosen


def test_select_dig_step_no_pit_keeps_v1_step():
    """No below-viewport pit -> steering is inert, v1's step is honoured."""
    baseline = 1000
    col3 = ma.grid_pos_to_block_id(baseline, row=6, col=3)
    board = _board(baseline, actives=[col3])
    v1_steps = [{"type": "dig", "block_id": col3, "row": 6, "col": 3}]
    step = _select_dig_step(board, v1_steps, hold_floor=False, grid=None)
    assert int(step["block_id"]) == col3


# --- prop_step_for_pit: 1 道具 > ~3 鎬 = 一砲收 >=2 顆礦 -----------------------
# Props go on an AIR cell (count==0 空地/挖完礦洞), NOT a solid frontier cell.

def _pit(depth, gcol):  # uncollected pit (count>0), gcol = 1-indexed game col
    return MineBlock(block_id=depth * 100 + gcol, x=gcol, y=depth,
                     config_id=401, count=1, is_reward=1)


def _air(depth, gcol):  # dug cell (count==0) = valid prop placement
    return MineBlock(block_id=depth * 100 + gcol, x=gcol, y=depth,
                     config_id=201, count=0, is_reward=0)


def test_prop_bomb_fires_for_two_pits_in_one_blast():
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    place = _air(top + 5, 1)                       # air cell center, col 0
    pits = [_pit(top + 6, 1), _pit(top + 6, 2)]    # both in the 3x3 blast
    board = _board(baseline, blocks=[place, *pits])
    step = ma.prop_step_for_pit(board, {"bomb": 5, "drill": 5},
                                allow_bomb=True, allow_drill=True)
    assert step["type"] == "use" and step["item"] == "bomb", step
    assert int(step["block_id"]) == place.block_id


def test_prop_drill_fires_for_two_pits_in_column_when_bomb_off():
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    place = _air(top + 4, 2)                        # air cell in col 1
    pits = [_pit(top + 5, 2), _pit(top + 6, 2)]     # both col 1, at/below placement
    board = _board(baseline, blocks=[place, *pits])
    step = ma.prop_step_for_pit(board, {"bomb": 0, "drill": 5},
                                allow_bomb=True, allow_drill=True)
    assert step["type"] == "use" and step["item"] == "drill", step
    assert int(step["block_id"]) == place.block_id


def test_prop_skipped_for_single_pit():
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    board = _board(baseline, blocks=[_air(top + 5, 1), _pit(top + 6, 1)])
    assert ma.prop_step_for_pit(board, {"bomb": 5, "drill": 5},
                                allow_bomb=True, allow_drill=True) is None


def test_prop_skipped_without_air_placement():
    """>=2 pits but no count==0 cell to place on -> can't use a prop."""
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    board = _board(baseline, blocks=[_pit(top + 6, 1), _pit(top + 6, 2)])
    assert ma.prop_step_for_pit(board, {"bomb": 5, "drill": 5},
                                allow_bomb=True, allow_drill=True) is None


def test_prop_gated_off_when_not_allowed():
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    board = _board(baseline, blocks=[_air(top + 5, 1),
                                     _pit(top + 6, 1), _pit(top + 6, 2)])
    assert ma.prop_step_for_pit(board, {"bomb": 5, "drill": 5},
                                allow_bomb=False, allow_drill=False) is None


def test_select_dig_step_prefers_prop_over_pickaxe():
    """Below-viewport 2-pit blast -> _select_dig_step returns the bomb, not a dig."""
    baseline = 1000
    top = ma.viewport_top_depth(baseline)
    place = _air(top + 6, 1)                        # air cell at the bottom, col 0
    pits = [_pit(top + 7, 1), _pit(top + 7, 2)]     # row 7 = below viewport, in blast
    board = _board(baseline, blocks=[place, *pits])
    step = _select_dig_step(board, [], hold_floor=False, grid=None,
                            inventory={"bomb": 5}, allow_bomb=True, allow_drill=False)
    assert step is not None and step.get("type") == "use", step
    assert step["item"] == "bomb" and int(step["block_id"]) == place.block_id
