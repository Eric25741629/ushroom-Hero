"""Multi-step prop-combo planner for pure-WS mining (prop_combo_for_pits).

Upgrades the old single-shot bomb-priority selector to a bounded DFS over prop
sequences (bomb/drill, len <= max_props) that maximises JOINT pit coverage. The
public ``prop_step_for_pit`` keeps its exact signature/shape and now returns the
combo's first step. Lightweight SimpleNamespace fake boards (block fields
x/y/count/config_id/is_reward + board baseline/actives/area_info/blocks) so the
tests never import the real device/Playwright stack.

Coordinate facts (mirrors mining_adapter): block.x is 1-indexed (col = x - 1),
block_id = depth*100 + x, prop placement goes on an AIR cell (count == 0), a pit
is count > 0 with config_id == TERRAIN_PIT or is_reward. TERRAIN_PIT is read from
the module, never hard-coded.
"""
from types import SimpleNamespace

import ws_token.mining_adapter as ma

PIT = ma.TERRAIN_PIT
DIRT = ma.TERRAIN_DIRT
BASELINE = 1000
TOP = ma.viewport_top_depth(BASELINE)  # depth of grid row 0


def _pit(depth, gcol):
    """Uncollected pit (count > 0). gcol is the 1-indexed game column (block.x)."""
    return SimpleNamespace(block_id=depth * 100 + gcol, x=gcol, y=depth,
                           config_id=PIT, count=1, is_reward=1)


def _air(depth, gcol):
    """Already-dug air cell (count == 0) — a valid prop placement."""
    return SimpleNamespace(block_id=depth * 100 + gcol, x=gcol, y=depth,
                           config_id=DIRT, count=0, is_reward=0)


def _board(blocks, *, baseline=BASELINE, actives=(), area_info=None):
    return SimpleNamespace(baseline=baseline, actives=list(actives),
                           area_info=area_info or {}, blocks=list(blocks),
                           holes=[])


def _combo(board, inv, *, allow_bomb=True, allow_drill=True, **kw):
    return ma.prop_combo_for_pits(board, inv, allow_bomb=allow_bomb,
                                  allow_drill=allow_drill, **kw)


# --- 1. parity: a single bomb covering 2 pits ---------------------------------

def test_single_bomb_two_pits_returns_bomb_step():
    place = _air(TOP + 5, 2)                       # col 1
    board = _board([place, _pit(TOP + 6, 2), _pit(TOP + 6, 3)])  # both in 3x3
    combo = _combo(board, {"bomb": 5, "drill": 5})
    assert len(combo) == 1 and combo[0]["item"] == "bomb"
    assert int(combo[0]["block_id"]) == place.block_id


# --- 2. bug-fix regression: drill(3) must beat bomb(2) as the first step -------

def test_drill_beats_bomb_when_it_collects_more():
    # One column (col 1) with 3 pits; a bomb reaches only the top 2 (rows within
    # +-2 of placement), a drill clears the whole column downward -> 3.
    #   depth   col1
    #   TOP+2   .air.
    #   TOP+3   pit
    #   TOP+4   pit
    #   ...
    #   TOP+7   pit   <- out of bomb reach, in drill reach
    place = _air(TOP + 2, 2)
    board = _board([place, _pit(TOP + 3, 2), _pit(TOP + 4, 2), _pit(TOP + 7, 2)])
    combo = _combo(board, {"bomb": 5, "drill": 5})
    # old code returned a bomb (bomb-priority bug); combo compares joint coverage.
    assert combo[0]["item"] == "drill"


# --- 3. greedy first pick != combo-optimal first pick -------------------------

def test_greedy_first_differs_from_combo_first():
    # Two columns, 2 pits each. A single "straddling" bomb grabs 3 (both tops +
    # one bottom via the cross), but leaves each column with a lone pit that no
    # second prop can pick up -> greedy(bomb) total 3. Two drills clear both
    # columns fully -> total 4, so the combo-optimal FIRST step is a drill, not
    # the greedy bomb.
    #        col0        col1
    # TOP+5  air         air
    # TOP+6  pit         pit
    # TOP+7  pit         pit
    d = TOP + 5
    board = _board([
        _air(d, 1), _air(d, 2),
        _pit(d + 1, 1), _pit(d + 2, 1),   # col 0
        _pit(d + 1, 2), _pit(d + 2, 2),   # col 1
    ])
    combo = _combo(board, {"bomb": 5, "drill": 5})
    # greedy (single max hit, bomb-priority) would open with a bomb; the joint
    # optimum opens with the col-0 drill.
    assert combo[0]["item"] == "drill" and combo[0]["col"] == 0
    assert len(combo) >= 2


# --- 4. chaining: bomb opens new air that a later drill needs ------------------

def test_chaining_bomb_then_drill_uses_freed_air():
    # Only ONE initial air cell (col 2). A bomb there clears 2 pits AND turns its
    # blast footprint into air; one of those freed cells (col 4, the cross reach)
    # lets a drill clear a deep col-4 column that no bomb could reach.
    #        col1  col2   col3  col4
    # d      .     AIR    .     (freed by bomb cross)
    # d+1    pit   .      pit   .
    # d+3    .     .      .     pit
    # d+4    .     .      .     pit
    d = TOP + 3
    board = _board([
        _air(d, 3),                       # col 2, the only initial air
        _pit(d + 1, 2), _pit(d + 1, 4),   # col 1 & col 3, inside bomb 3x3
        _pit(d + 3, 5), _pit(d + 4, 5),   # col 4, deep -> only a drill reaches
    ])
    combo = _combo(board, {"bomb": 5, "drill": 5})
    assert len(combo) == 2
    assert combo[0]["item"] == "bomb"
    assert combo[1]["item"] == "drill" and combo[1]["col"] == 4
    # the drill placement sits on a cell the bomb freed (col 4, depth d)
    assert int(combo[1]["block_id"]) == d * 100 + 5


# --- 5. a sub-min-pits prop is never folded into a combo -----------------------

def test_low_yield_prop_excluded_even_if_total_higher():
    # A bomb clears 2 pits; a lone extra pit sits in its own column so any drill
    # there would hit only 1 (< min_pits). Joint total would be 3, but the drill
    # is inadmissible, so the combo is the bomb alone.
    d = TOP + 4
    board = _board([
        _air(d, 2),                        # bomb placement (col 1)
        _pit(d + 1, 1), _pit(d + 1, 2),    # bomb 3x3 -> 2 pits
        _air(d, 5), _pit(d + 1, 5),        # col 4: only 1 pit -> drill hit 1
    ])
    combo = _combo(board, {"bomb": 5, "drill": 5})
    assert len(combo) == 1 and combo[0]["item"] == "bomb"


# --- 6. inventory bounds the sequence -----------------------------------------

def test_inventory_limits_sequence_length():
    # Two bomb-able clusters, but only ONE bomb in stock and no drills.
    d = TOP + 5
    board = _board([
        _air(d, 1), _air(d, 4),
        _pit(d + 1, 1), _pit(d + 1, 2),    # cluster A (bomb @ col0)
        _pit(d + 1, 4), _pit(d + 1, 5),    # cluster B (bomb @ col3)
    ])
    combo = _combo(board, {"bomb": 1, "drill": 0})
    assert len(combo) == 1
    assert all(s["item"] == "bomb" for s in combo)


# --- 7. gating / empty inputs -------------------------------------------------

def test_bomb_disallowed_excludes_bombs():
    d = TOP + 4
    board = _board([_air(d, 2), _pit(d + 1, 2), _pit(d + 2, 2)])  # drill column
    combo = _combo(board, {"bomb": 5, "drill": 5}, allow_bomb=False)
    assert combo is not None
    assert all(s["item"] != "bomb" for s in combo)


def test_both_disallowed_returns_none():
    d = TOP + 4
    board = _board([_air(d, 2), _pit(d + 1, 1), _pit(d + 1, 2)])
    assert _combo(board, {"bomb": 5, "drill": 5},
                  allow_bomb=False, allow_drill=False) is None


def test_no_pits_or_no_air_returns_none():
    d = TOP + 4
    no_air = _board([_pit(d + 1, 1), _pit(d + 1, 2)])            # 2 pits, no air
    assert _combo(no_air, {"bomb": 5, "drill": 5}) is None
    no_pit = _board([_air(d, 2)])                                 # air, no pit
    assert _combo(no_pit, {"bomb": 5, "drill": 5}) is None


# --- 8. prop_step_for_pit shape is unchanged ----------------------------------

def test_prop_step_for_pit_shape_unchanged():
    place = _air(TOP + 5, 2)
    board = _board([place, _pit(TOP + 6, 2), _pit(TOP + 6, 3)])
    step = ma.prop_step_for_pit(board, {"bomb": 5, "drill": 5},
                                allow_bomb=True, allow_drill=True)
    assert set(step) >= {"type", "item", "block_id", "row", "col", "step_cost"}
    assert step["type"] == "use"
    assert step["row"] == (TOP + 5) - ma.viewport_top_depth(BASELINE)
    assert int(step["block_id"]) == place.block_id
