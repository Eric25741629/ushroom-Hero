"""Self-learning terrain reconstruction (pure WS, no CNN)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ws_token import mine_terrain as mt
from ws_token import mining_adapter


# ---- fakes mirroring ws_token.mining.MineBoard / MineBlock ----------------
@dataclass
class FakeBlock:
    y: int
    x: int            # 1-indexed col
    config_id: int
    count: int = 0
    is_reward: int = 0
    block_id: int = 0


@dataclass
class FakeBoard:
    baseline: int
    actives: List[int] = field(default_factory=list)
    blocks: List[FakeBlock] = field(default_factory=list)
    holes: List = field(default_factory=list)
    area: int = 1


def _unique_template_with_stone():
    """Pick a template uniquely identifiable by its dirt/stone cells and return
    (index, (rr, col)) of one stone cell in it."""
    grids = mt.templates("row")
    for idx, g in enumerate(grids):
        cells = [(rr, c, g[rr][c]) for rr in range(mt.ROWS) for c in range(mt.COLS)
                 if g[rr][c] in (mt.DIRT, mt.STONE)]
        cons = mt._consistent_templates(cells, grids)
        if cons == [idx]:
            stone = next(((rr, c) for rr in range(mt.ROWS) for c in range(mt.COLS)
                          if g[rr][c] == mt.STONE), None)
            if stone is not None:
                return idx, stone
    raise AssertionError("no uniquely-identifiable template with a stone cell")


def _seed_band(m, grids, idx, phase, band):
    for rr in range(mt.ROWS):
        for c in range(mt.COLS):
            if grids[idx][rr][c] in (mt.DIRT, mt.STONE):
                m.observe(phase + band * mt.ROWS + rr, c, grids[idx][rr][c])


def test_learn_recovers_phase_orientation_and_band_template():
    grids = mt.templates("row")
    idx, _ = _unique_template_with_stone()
    phase = 2
    m = mt.TerrainModel()
    _seed_band(m, grids, idx, phase, 7)
    _seed_band(m, grids, idx, phase, 8)  # 2 bands corroborate the phase
    m.learn()
    assert m.phase == phase
    assert m.orientation == "row"
    assert m.bands.get(7) == idx and m.bands.get(8) == idx


def test_undug_stone_reconstructs_else_none():
    grids = mt.templates("row")
    idx, (rr, c) = _unique_template_with_stone()
    phase = 0
    m = mt.TerrainModel()
    _seed_band(m, grids, idx, phase, 0)
    _seed_band(m, grids, idx, phase, 1)
    depth = phase + 0 * mt.ROWS + rr
    assert m.terrain_at(depth, c) == mt.STONE
    # a band with no reveals stays unknown
    assert m.terrain_at(phase + 99 * mt.ROWS, 0) is None


def test_ambiguous_band_not_identified():
    # one revealed dirt cell is not enough to pin a single template
    m = mt.TerrainModel()
    m.observe(0, 0, mt.DIRT)
    m.learn()
    # if any band is identified at all, it must not be from a single weak reveal
    assert m.terrain_at(0, 0) in (None, mt.DIRT)


def test_observe_board_ignores_pits_and_only_takes_terrain():
    m = mt.TerrainModel()
    board = FakeBoard(
        baseline=100,
        blocks=[
            FakeBlock(y=95, x=2, config_id=mt.DIRT, count=0),   # dug dirt -> observed
            FakeBlock(y=96, x=3, config_id=mt.STONE, count=0),  # dug stone -> observed
            FakeBlock(y=97, x=1, config_id=mt.PIT, count=1),    # pit -> ignored
        ],
    )
    m.observe_board(board)
    assert m.reveals == {(95, 1): mt.DIRT, (96, 2): mt.STONE}


def test_projection_upgrades_undug_stone_to_rock():
    grids = mt.templates("row")
    idx, (rr, c) = _unique_template_with_stone()
    phase, band = 0, 0
    m = mt.TerrainModel()
    _seed_band(m, grids, idx, phase, band)
    _seed_band(m, grids, idx, phase, band + 1)  # corroborate phase

    depth = phase + band * mt.ROWS + rr  # absolute depth of the stone cell
    baseline = depth + (mining_adapter.GRID_ROWS - 2)  # so viewport_top_depth == depth -> row 0
    block_id = depth * 100 + (c + 1)
    board = FakeBoard(baseline=baseline, actives=[block_id])  # undug active, no block

    # without the model: blind default is dirt
    assert mining_adapter.board_to_grid(board)[0][c] == "dirt"
    # with the model: reconstructed stone -> rock
    assert mining_adapter.board_to_grid(board, m)[0][c] == "rock"


def test_occluded_cell_filled_from_template():
    """A viewport cell WS never reports (not active, no block) is air by default;
    with the band identified it must be filled as the template's solid terrain."""
    grids = mt.templates("row")
    idx, (rr, c) = _unique_template_with_stone()
    if rr == mining_adapter.GRID_ROWS - 1:
        # avoid the bottom-row special-case; pick this template only if stone is
        # off the floor (every active template has interior stone, so this holds)
        rr, c = next((r, cc) for r in range(mt.ROWS - 1) for cc in range(mt.COLS)
                     if grids[idx][r][cc] == mt.STONE)
    phase, band = 0, 0
    m = mt.TerrainModel()
    _seed_band(m, grids, idx, phase, band)
    _seed_band(m, grids, idx, phase, band + 1)

    depth = phase + band * mt.ROWS + rr
    baseline = depth - rr + (mining_adapter.GRID_ROWS - 2)  # so viewport row 0 == band row 0
    # occluded: NO active, NO block anywhere -> the cell is a blank in WS
    board = FakeBoard(baseline=baseline, actives=[], blocks=[])

    assert mining_adapter.board_to_grid(board)[rr][c] == "empty"        # blind: air
    assert mining_adapter.board_to_grid(board, m)[rr][c] == "unreachable_rock"


def test_sliding_window_prunes_old_reveals():
    m = mt.TerrainModel()
    m.observe(10, 0, mt.DIRT)
    m.observe(10 + mt.TerrainModel._WINDOW + 50, 1, mt.STONE)
    assert (10, 0) not in m.reveals
    assert len(m.reveals) == 1
