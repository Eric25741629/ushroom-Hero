"""Adapter: WS MineBoard <-> miner v4 planner grid + plan -> block_id steps.

Uses the **v4** planner (bounded rolling-horizon DFS), NOT the v1 A* default
that the screenshot/ADB runtime uses. The WS mining loop is a supervised
scroll loop: it needs the planner to keep emitting a no_pit progress dig so
the board scrolls. v1 returns an empty plan when there is no pit and floor7 is
already open, which stalls the loop; v4 (with its no_pit progress-dig
fallback) keeps advancing. See `mining_service.py` for the ADB path default.

Two layers, kept apart so the pure grid transform never imports the planner:

  board_to_grid(mine_board) -> List[List[str]]   (7x6 DEFAULT_CLASSES labels)
      Pure function. No miner import. Tested directly.
  plan(mine_board, inventory) -> steps           (calls plan_v4 via LAZY import)
      Builds the grid, runs the bounded DFS, maps each step's (row, col) back
      to a WS block_id.

LIVE-CALIBRATION (7fe98fc6 / 小寶 H5/CDP):

1. terrain enum is incomplete. Only 201/202/401 are live-verified. Any other
   config_id is mapped to "rock" (treated as a generic solid obstacle, cost 2)
   so the planner never crashes on an unknown block.
2. viewport / depth->row mapping. The 7-row planner window starts at
   ``baseline - 5``. For baseline=162388, the visible rows are
   y=162383..162389. Blocks outside that 7-row window are dropped.
3. column origin. ``p_mine_block.x`` is 1-indexed in the live captures
   (block_id = depth*100 + col, col in 1..6), so grid col = x - 1.
4. reachability. We cannot infer true reachability from a single board, so
   every pit is emitted as "reachable_pit" and solids as their plain
   (reachable) label. The planner's own reachability pass then refines it.
   A future calibration may downgrade buried pits to "unreachable_pit".
5. ``actives`` are valid dig targets. Live 0x0c01 may omit terrain features
   for some active cells, so viewport actives without a block feature are
   emitted as conservative "rock" instead of "empty"; otherwise the planner
   can falsely think row 6 is already open and return no progress step.
   When row 6 contains any active dig target, the other unknown row-6 cells are
   marked "unreachable_empty" rather than reachable "empty" for the same reason:
   pure WS absence is not proof of a visual floor-open air cell.
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

# terrain config_id -> planner label. Only 201/202/401 are live-verified;
# see gap #1. count is consulted to distinguish 1-hit vs >=2-hit rock.
TERRAIN_DIRT = 201
TERRAIN_STONE = 202
TERRAIN_PIT = 401


def _block_label(config_id: int, count: int, is_reward: int) -> str:
    """Map a WS block's terrain to a DEFAULT_CLASSES label."""
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


def viewport_top_depth(baseline: int) -> int:
    """Live H5 視窗頂端深度；baseline 不是 row 0，而是 row 5。"""
    return int(baseline) - (GRID_ROWS - 2)


def _project_board(mine_board: Any) -> tuple[List[List[str]], list[dict], list[dict]]:
    """Project WS board into planner grid and record what the projection drops."""
    grid: List[List[str]] = [[EMPTY for _ in range(GRID_COLS)] for _ in range(GRID_ROWS)]
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    top_depth = viewport_top_depth(baseline)
    dropped_blocks: list[dict] = []
    dropped_actives: list[dict] = []

    for block_id in getattr(mine_board, "actives", []) or []:
        try:
            cell_id = int(block_id)
        except (TypeError, ValueError):
            continue
        depth, game_col = divmod(cell_id, 100)
        row = depth - top_depth
        col = game_col - 1
        if not (0 <= row < GRID_ROWS) or not (0 <= col < GRID_COLS):
            reason = "outside_viewport" if not (0 <= row < GRID_ROWS) else "outside_cols"
            dropped_actives.append({
                "cell_id": cell_id, "row": row, "col": col, "reason": reason,
            })
            continue
        # actives 是 server 接受的可挖目標；terrain 缺失時用 rock 保守估成本。
        grid[row][col] = "rock"

    if any(cell != EMPTY for cell in grid[GRID_ROWS - 1]):
        for col, cell in enumerate(grid[GRID_ROWS - 1]):
            if cell == EMPTY:
                # 純 WS 沒有證據證明底列缺失格是可達空氣；避免誤判已下樓。
                grid[GRID_ROWS - 1][col] = "unreachable_empty"

    for blk in getattr(mine_board, "blocks", []) or []:
        row = int(blk.y) - top_depth          # depth offset from visible viewport top
        col = int(blk.x) - 1                   # x is 1-indexed in live captures
        if not (0 <= row < GRID_ROWS) or not (0 <= col < GRID_COLS):
            reasons: list[str] = []
            if not (0 <= row < GRID_ROWS):
                reasons.append("outside_viewport")
            if not (0 <= col < GRID_COLS):
                reasons.append("outside_cols")
            dropped_blocks.append({
                "block_id": int(getattr(blk, "block_id", 0) or 0),
                "x": int(getattr(blk, "x", 0) or 0),
                "y": int(getattr(blk, "y", 0) or 0),
                "row": row,
                "col": col,
                "config_id": int(getattr(blk, "config_id", 0) or 0),
                "count": int(getattr(blk, "count", 0) or 0),
                "reason": "+".join(reasons) or "unknown",
            })
            continue
        grid[row][col] = _block_label(blk.config_id, blk.count, blk.is_reward)
    return grid, dropped_blocks, dropped_actives


def board_to_grid(mine_board: Any) -> List[List[str]]:
    """Project a MineBoard's blocks into a 7x6 grid of planner labels.

    Pure: no planner import, no side effects. Viewport cells listed in
    ``actives`` default to conservative "rock" when no terrain feature exists.
    Blocks outside the 7-row viewport [baseline-5, baseline+2) or outside
    columns 1..6 are dropped (gap #2/#3).
    """
    grid, _dropped_blocks, _dropped_actives = _project_board(mine_board)
    return grid


def board_projection_trace(mine_board: Any) -> Dict[str, Any]:
    """Return log-friendly details of WS board -> planner grid projection."""
    grid, dropped_blocks, dropped_actives = _project_board(mine_board)
    return {
        "area": int(getattr(mine_board, "area", 0) or 0),
        "baseline": int(getattr(mine_board, "baseline", 0) or 0),
        "top_depth": viewport_top_depth(int(getattr(mine_board, "baseline", 0) or 0)),
        "actives_count": len(getattr(mine_board, "actives", []) or []),
        "blocks_count": len(getattr(mine_board, "blocks", []) or []),
        "holes_count": len(getattr(mine_board, "holes", []) or []),
        "area_info": dict(getattr(mine_board, "area_info", {}) or {}),
        "grid": grid,
        "dropped_blocks": dropped_blocks,
        "dropped_actives": dropped_actives,
    }


def grid_pos_to_block_id(baseline: int, row: int, col: int) -> int:
    """Inverse of the grid mapping: (row, col) -> WS block_id.

    block_id = depth*100 + game_col, depth = baseline - 5 + row, game_col = col + 1
    (col is the 0-indexed grid column; the live board uses 1-indexed cols).
    """
    depth = viewport_top_depth(baseline) + int(row)
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
