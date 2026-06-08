"""Adapter: WS MineBoard <-> miner v4 planner grid + plan -> block_id steps.

Two layers, kept apart so the pure grid transform never imports the planner:

  board_to_grid(mine_board) -> List[List[str]]   (7x6 DEFAULT_CLASSES labels)
      Pure function. No miner import. Tested directly.
  plan(mine_board, inventory) -> steps           (calls plan_v4 via LAZY import)
      Builds the grid, runs the bounded DFS, maps each step's (row, col) back
      to a WS block_id.

LIVE-CALIBRATION GAPS (all marked # live-confirm; reasonable defaults, no
magic-number guessing):

1. terrain enum is incomplete. Only 201/202/401 are 5554-verified. Any other
   config_id is mapped to "rock" (treated as a generic solid obstacle, cost 2)
   so the planner never crashes on an unknown block. # live-confirm
2. viewport / depth->row mapping. We assume the 7-row planner window starts at
   ``baseline`` (depth == baseline -> row 0) and grows downward, mirroring
   the 7-row scroll viewport in miner/core/config GRID_CFG (H=7). Blocks
   outside [baseline, baseline+7) are dropped. The real board may key the
   viewport off a different anchor (e.g. the deepest reachable row). # live-confirm
3. column origin. ``p_mine_block.x`` is 1-indexed in the live captures
   (block_id = depth*100 + col, col in 1..6), so grid col = x - 1. # live-confirm
4. reachability. We cannot infer true reachability from a single board, so
   every pit is emitted as "reachable_pit" and solids as their plain
   (reachable) label. The planner's own reachability pass then refines it.
   A future calibration may downgrade buried pits to "unreachable_pit". # live-confirm
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Mirror miner.core.config GRID_CFG (H=7, W=6) and DEFAULT_CLASSES labels,
# duplicated here so board_to_grid stays import-free of the miner package.
GRID_ROWS = 7
GRID_COLS = 6

EMPTY = "empty"

# terrain config_id -> planner label. Only 201/202/401 are 5554-verified;
# see gap #1. count is consulted to distinguish 1-hit vs >=2-hit rock.
TERRAIN_STONE = 201
TERRAIN_DIRT = 202
TERRAIN_PIT = 401


def _block_label(config_id: int, count: int, is_reward: int) -> str:
    """Map a WS block's terrain to a DEFAULT_CLASSES label. # live-confirm enum."""
    if config_id == TERRAIN_PIT or is_reward:
        # Single-snapshot reachability is unknown -> assume reachable; the
        # planner's reachability pass refines it (gap #4).
        return "reachable_pit"
    if config_id == TERRAIN_DIRT:
        return "dirt"
    if config_id == TERRAIN_STONE:
        # 石頭 needs >=2 hits; a partially-dug stone with 1 hit left is a
        # one_hit_rock to the planner cost model.
        return "one_hit_rock" if count <= 1 else "rock"
    # Unknown terrain -> treat as a generic solid obstacle (gap #1).
    logger.debug("ws_token mining_adapter: unknown config_id=%s -> rock", config_id)
    return "rock"


def board_to_grid(mine_board: Any) -> List[List[str]]:
    """Project a MineBoard's blocks into a 7x6 grid of planner labels.

    Pure: no planner import, no side effects. Cells with no block default to
    "empty". Blocks outside the 7-row viewport [baseline, baseline+7) or
    outside columns 1..6 are dropped (gap #2/#3).
    """
    grid: List[List[str]] = [[EMPTY for _ in range(GRID_COLS)] for _ in range(GRID_ROWS)]
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    for blk in getattr(mine_board, "blocks", []) or []:
        row = int(blk.y) - baseline           # depth offset from viewport top
        col = int(blk.x) - 1                   # x is 1-indexed in live captures
        if not (0 <= row < GRID_ROWS) or not (0 <= col < GRID_COLS):
            continue
        grid[row][col] = _block_label(blk.config_id, blk.count, blk.is_reward)
    return grid


def grid_pos_to_block_id(baseline: int, row: int, col: int) -> int:
    """Inverse of the grid mapping: (row, col) -> WS block_id.

    block_id = depth*100 + game_col, depth = baseline + row, game_col = col + 1
    (col is the 0-indexed grid column; the live board uses 1-indexed cols).
    """
    depth = int(baseline) + int(row)
    game_col = int(col) + 1
    return depth * 100 + game_col


def plan(mine_board: Any, inventory: Optional[Dict[str, int]] = None,
         *, max_depth: Optional[int] = None) -> Dict[str, Any]:
    """Build the grid, run plan_v4, and translate steps back to block_ids.

    ``plan_v4`` is imported lazily so importing this module never risks pulling
    the miner CNN stack. (Verified safe: ``from miner.v4.planner import plan_v4``
    does not import torch/cv2, but the lazy import keeps the contract robust.)

    Returns the raw plan_v4 dict augmented with ``ws_steps``: a list of
    ``{"goods_id"-less} step + {"block_id", "row", "col"}`` so a (human-driven)
    executor can map each planner move to a home_mine_use_goods target.
    """
    from miner.v4.planner import plan_v4  # lazy: keep import cost off module load

    grid = board_to_grid(mine_board)
    inv = inventory or {}
    shovels = float(inv.get("pickaxe", 0))
    items = {"drill": int(inv.get("drill", 0)), "bomb": int(inv.get("bomb", 0))}

    kwargs: Dict[str, Any] = {"shovels": shovels, "items": items}
    if max_depth is not None:
        kwargs["max_depth"] = max_depth
    result = plan_v4(grid, **kwargs)

    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    ws_steps: List[Dict[str, Any]] = []
    for step in result.get("steps", []):
        pos = step.get("pos") or step.get("target")
        if not pos:
            continue
        row, col = int(pos[0]), int(pos[1])
        enriched = dict(step)
        enriched["row"] = row
        enriched["col"] = col
        enriched["block_id"] = grid_pos_to_block_id(baseline, row, col)
        ws_steps.append(enriched)
    result["ws_steps"] = ws_steps
    return result
