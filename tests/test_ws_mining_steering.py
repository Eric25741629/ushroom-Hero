"""Pit-directed steering: dig toward the cheapest path to the nearest pit
(incl. below-viewport map_pits), not 'deepest frontier, lowest col'.

This is the fix for both observed bugs:
  - 繞著礦坑挖 : the shaft now heads straight for the reward column.
  - 礦堆到 r=0 : reachable pits are pursued before they scroll off the top.
"""
from ws_token.mining import MineBoard, MineBlock
from ws_token.mining_adapter import pit_directed_next


def _board(baseline, actives, blocks, area_info=None):
    return MineBoard(
        max_num=120, next_time=0, area=0, baseline=baseline,
        actives=list(actives), area_info=area_info or {}, blocks=list(blocks), holes=[],
    )


def _bid(depth, col):
    return depth * 100 + (col + 1)


def test_steers_toward_pit_column_not_deepest_lowest_col():
    # pit sits below the viewport at col 4; frontier spans cols 0,1,4,5 at depth 101.
    pit = MineBlock(block_id=_bid(110, 4), x=5, y=110, config_id=401, count=1, is_reward=0)
    actives = [_bid(101, 0), _bid(101, 1), _bid(101, 4), _bid(101, 5)]
    board = _board(100, actives, [pit])
    bid = pit_directed_next(board)
    depth, gcol = divmod(bid, 100)
    assert depth == 101 and (gcol - 1) == 4, (depth, gcol - 1)


def test_picks_nearer_of_two_pits():
    # two pits; col-1 pit is much closer to the col-1 frontier than the col-5 pit.
    pits = [
        MineBlock(block_id=_bid(104, 1), x=2, y=104, config_id=401, count=1, is_reward=0),
        MineBlock(block_id=_bid(115, 5), x=6, y=115, config_id=401, count=1, is_reward=0),
    ]
    actives = [_bid(101, 1), _bid(101, 5)]
    board = _board(100, actives, pits)
    bid = pit_directed_next(board)
    depth, gcol = divmod(bid, 100)
    assert (gcol - 1) == 1, gcol - 1  # head for the closer (col-1) pit


def test_none_when_no_uncollected_pit():
    actives = [_bid(101, 0), _bid(101, 3)]
    board = _board(100, actives, [])
    assert pit_directed_next(board) is None


def test_collected_pit_ignored():
    collected = MineBlock(block_id=_bid(110, 4), x=5, y=110, config_id=401, count=0, is_reward=0)
    board = _board(100, [_bid(101, 0)], [collected])
    assert pit_directed_next(board) is None


def test_select_dig_step_routes_through_pit_directed():
    """_select_dig_step's fallback must pick the pit-directed cell, not the old
    deepest-frontier-lowest-col cell, when the planner step is not diggable."""
    from ws_token.mining_supervised import _select_dig_step

    pit = MineBlock(block_id=_bid(110, 5), x=6, y=110, config_id=401, count=1, is_reward=0)
    # (102,0) is DEEPER (old _key would pick it); (101,5) is on the cheap path to
    # the col-5 pit (pit-directed should pick it).
    actives = [_bid(101, 5), _bid(102, 0)]
    board = _board(100, actives, [pit])
    # a planner step that is NOT a server-valid frontier cell -> forces the fallback
    plan_steps = [{"type": "dig", "block_id": _bid(50, 2)}]
    step = _select_dig_step(board, plan_steps, hold_floor=False, grid=None, exclude=set())
    assert step is not None
    depth, gcol = divmod(step["block_id"], 100)
    assert depth == 101 and (gcol - 1) == 5, (depth, gcol - 1)
