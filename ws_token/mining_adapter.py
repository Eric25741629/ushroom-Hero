"""Adapter: WS MineBoard <-> miner v5 planner grid + plan -> block_id steps.

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

# terrain config_id -> planner label. Only 201/202/401 are live-verified
# (see gap #1). p_mine_block.f5 ("count") is NOT consulted: its semantic is
# unverified (MINING_SCHEMA §6) and live capture (2026-06-15) shows it is 0 on
# a fresh stone and only goes 0->1 AFTER the first hit, so it is not "hits
# remaining" — using it would mislabel every fresh stone as a 1-hit rock.
TERRAIN_DIRT = 201
TERRAIN_STONE = 202
TERRAIN_PIT = 401


def _block_label(config_id: int, is_reward: int, count: int) -> str:
    """Map a WS block's terrain + dig-state to a DEFAULT_CLASSES label.

    ``count`` semantics — LIVE-verified via CDP dig 2026-06-20 (小寶, BOTH 201 & 202):
      count == 0 → the cell is ALREADY DUG (air). Digging it is a confirmed no-op
                   (0x0c03 sends NO reply, the board does not change, no pickaxe is
                   spent). ``config_id`` is then only the *historical* terrain, so
                   the cell must project as empty/air — NOT as solid dirt/rock.
      count  > 0 → undug / live cell: 201=dirt, 202=stone(rock), 401=pit.

    The old code was count-blind and labelled dug (count==0) cells as solid
    dirt/rock, so a mostly-dug board projected "dense" and the planner mis-planned
    / wasted pickaxes (the dense-vs-empty cognition bug). The earlier "fresh stone
    reads count==0" note was wrong: a live CDP dig of a fresh 202 returned count==1,
    and digging a count==0 cell was a verified no-op.
    """
    if int(count) <= 0:
        return EMPTY  # already-dug air; config_id is historical only
    if config_id == TERRAIN_PIT or is_reward:
        # Single-snapshot reachability is unknown -> assume reachable; the
        # planner's reachability pass refines it (gap #4).
        return "reachable_pit"
    if config_id == TERRAIN_DIRT:
        return "dirt"
    if config_id == TERRAIN_STONE:
        return "rock"
    # Unknown terrain -> treat as a generic solid obstacle (gap #1).
    logger.debug("ws_token mining_adapter: unknown config_id=%s -> rock", config_id)
    return "rock"


def viewport_top_depth(baseline: int) -> int:
    """Live H5 視窗頂端深度；baseline 不是 row 0，而是 row 5。"""
    return int(baseline) - (GRID_ROWS - 2)


def map_pits(mine_board: Any) -> List[Dict[str, int]]:
    """全地圖（非只 7 列視窗）的未採集礦坑 — look-ahead 用。

    伺服器送的是比可見 7 列「棋盤」更高的「地圖」：未採集礦坑(待發現礦洞)會出現在
    視窗上下好幾列(實測 baseline-3 .. +17)，但 `board_to_grid` 把盤面裁成 rows 0-6、
    其餘當 outside_viewport 丟掉，planner 因此看不到即將到來的礦。這裡從原始 blocks 撈出
    每個未採集礦坑(config 401 / is_reward 且 count>0)，附上相對視窗頂端的 row：
      row > 6  → 視窗下方(即將捲到的 upcoming 礦)
      0..6     → 視窗內
      row < 0  → 已捲過(passed；通常已收不到)
    讓 planner 能朝即將到來的礦規劃下挖，而不是盲目下挖。純 WS、不需 CNN。
    """
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    top = viewport_top_depth(baseline)
    out: List[Dict[str, int]] = []
    for blk in getattr(mine_board, "blocks", []) or []:
        if int(getattr(blk, "count", 0) or 0) <= 0:
            continue
        if not (int(getattr(blk, "config_id", 0) or 0) == TERRAIN_PIT
                or int(getattr(blk, "is_reward", 0) or 0)):
            continue
        out.append({
            "row": int(blk.y) - top,   # rel to viewport top; >6 = 下方/upcoming
            "col": int(blk.x) - 1,
            "depth": int(blk.y),
            "count": int(blk.count),
        })
    out.sort(key=lambda d: (d["row"], d["col"]))
    return out


def has_uncollected_row0_pit(mine_board: Any) -> bool:
    """row-0（視窗頂端深度）是否還有「未採集且仍可挖」的礦坑（從原始 blocks 判定）。

    判定 hold_floor 必須同時看 block.count 與 actives：
      - count：已採集礦坑 (count==0) 視覺上已空、即使被捲走也無損，不該觸發 hold_floor。
        grid 標籤層 (_block_label) 刻意不看 count（估成本用，把所有 401/is_reward 都標
        reachable_pit），所以這裡直接讀原始 MineBoard.blocks，只認 row 0
        (y == viewport_top_depth) 上 config_id==401 / is_reward 且 count>0 的 block。
        若不這麼判，已採集的 row-0 礦坑會讓 hold_floor 永久 True（手機fc 鎬子卡 118/118）。
      - actives：礦坑必須在伺服器可挖前緣 (actives) 上才值得守。被挖出的空洞越過、卡在
        視窗頂列的礦坑 count>0 但不在 actives（伺服器拒挖）—守它只會讓監督迴圈狂挖開不了
        floor-7 的深層格、燒光鎬子，礦坑照樣收不到並捲走（7fe98fc6 2026-06-20 浪費根因）。
        挖不到的坑就放行捲動，捲走成本=1 挖步而非 ~26 把鎬子。
    """
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    top_depth = viewport_top_depth(baseline)
    actives = {int(a) for a in (getattr(mine_board, "actives", []) or [])}
    for blk in getattr(mine_board, "blocks", []) or []:
        if int(getattr(blk, "y", 0) or 0) != top_depth:
            continue
        if int(getattr(blk, "count", 0) or 0) <= 0:
            continue
        if not (int(getattr(blk, "config_id", 0) or 0) == TERRAIN_PIT
                or int(getattr(blk, "is_reward", 0) or 0)):
            continue
        if int(getattr(blk, "block_id", 0) or 0) in actives:
            return True
    return False


def _project_board(mine_board: Any, terrain: Any = None) -> tuple[List[List[str]], list[dict], list[dict]]:
    """Project WS board into planner grid and record what the projection drops.

    ``terrain`` (optional ``mine_terrain.TerrainModel``): WS never sends the type
    of an undug cell, so undug actives default to "dirt". When a learned terrain
    model reconstructs that cell as STONE (202) we upgrade it to "rock" so the
    planner can cost it correctly / bomb dense stone. DIRT/AIR/unknown keep the
    safe "dirt" default — the override only ever *adds* stone knowledge.
    """
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
        # active 且無 block feature = 未挖泥土 (CDP dig 2026-06-20 + MINING_SCHEMA L204
        # "active 無 block entry = 未挖泥土" + user)。舊版填 "rock" 把未挖泥土當石頭、
        # 成本高估，也讓盤面更顯「實心」。實際未挖格大多是泥土；石頭/礦會帶 count>0 block。
        label = "dirt"
        if terrain is not None:
            # 202 == STONE (ws_token.mine_terrain.STONE); literal to keep this
            # pure projection import-free.
            if terrain.terrain_at(depth, col) == 202:
                label = "rock"
        grid[row][col] = label

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
        grid[row][col] = _block_label(blk.config_id, blk.is_reward, blk.count)
    return grid, dropped_blocks, dropped_actives


def board_to_grid(mine_board: Any, terrain: Any = None) -> List[List[str]]:
    """Project a MineBoard's blocks into a 7x6 grid of planner labels.

    Pure: no planner import, no side effects. Undug ``actives`` default to
    "dirt"; an optional learned ``terrain`` model upgrades cells it has
    reconstructed as stone (see ``_project_board``). Blocks outside the 7-row
    viewport [baseline-5, baseline+2) or outside columns 1..6 are dropped.
    """
    grid, _dropped_blocks, _dropped_actives = _project_board(mine_board, terrain)
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
         *, max_depth: Optional[int] = None, terrain: Any = None) -> Dict[str, Any]:
    """Build the grid, run the planner (v4), and translate steps back to block_ids.

    Returns the plan dict augmented with:
      ``ws_steps``: list of step + {block_id, row, col}.
      ``hold_floor``: True when row-0 has uncollected pits and floor-7 is still closed.
        Steps that would open floor-7 are removed from ws_steps under hold_floor
        so the bot cannot scroll row-0 pits out of the viewport.
        Pits in rows 1-6 survive a scroll (they move up, staying visible) and do
        not trigger hold_floor.
      ``grid``: the raw 7×6 planner grid, used by the fallback in _select_dig_step.
    """
    from miner.planning.smart_planner import plan_smart  # lazy: keep import cost off module load
    from miner.v3.board import floor7_open
    from miner.v3.actions import apply_dig, apply_bomb, apply_drill

    # Self-learning terrain: feed this board's dug cells (their real config_id)
    # into the model, then project with stone-aware undug cells. No-op if no model.
    if terrain is not None:
        try:
            terrain.observe_board(mine_board)
        except Exception:
            pass
    grid = board_to_grid(mine_board, terrain)
    inv = inventory or {}
    shovels = float(inv.get("pickaxe", 0))
    items = {"drill": int(inv.get("drill", 0)), "bomb": int(inv.get("bomb", 0))}

    # WS path uses v1 (whole-board A*) — highest score/shovel-efficiency on the
    # canonical sim (v1 3711 vs v4 1649). v1 used to return an EMPTY plan once
    # pits were gone + floor7 open (why WS previously used v4); that's fixed by
    # smart_planner's descent-dig fallback, so v1 now keeps emitting a no_pit
    # progress dig like v4 did. `max_depth` is a v4-only DFS knob — ignored here.
    result = plan_smart(grid, shovels=shovels, items=items)

    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    ws_steps: List[Dict[str, Any]] = []
    for step in result.get("steps", []):
        pos = step.get("pos") or step.get("target")
        if not pos:
            continue
        sr, sc = int(pos[0]), int(pos[1])
        enriched = dict(step)
        enriched["row"] = sr
        enriched["col"] = sc
        enriched["block_id"] = grid_pos_to_block_id(baseline, sr, sc)
        ws_steps.append(enriched)

    # hold-floor: row 0 的「未採集」礦坑捲動後會離開視窗 → veto 會觸發捲動的步。
    # row 1-6 的礦坑捲動後上移一列仍可見，不算風險；已採集 (count==0) 礦坑捲走無損，
    # 不觸發 hold_floor。必須看 block.count，所以從原始 board 判定，不能用不看 count 的
    # grid 標籤（_block_label 把已採集 401 也標 reachable_pit → 永久 hold_floor 死結）。
    hold_floor = has_uncollected_row0_pit(mine_board) and not floor7_open(grid)
    if hold_floor:
        safe: List[Dict[str, Any]] = []
        for step in ws_steps:
            work = [gr[:] for gr in grid]
            stype = step.get("type")
            sr, sc = int(step["row"]), int(step["col"])
            if stype == "dig":
                apply_dig(work, (sr, sc))
            elif stype == "use":
                it = step.get("item")
                if it == "bomb":
                    apply_bomb(work, (sr, sc))
                elif it == "drill":
                    apply_drill(work, (sr, sc))
                else:
                    safe.append(step)
                    continue
            else:
                safe.append(step)
                continue
            if not floor7_open(work):
                safe.append(step)
        ws_steps = safe

    result["ws_steps"] = ws_steps
    result["hold_floor"] = hold_floor
    result["grid"] = grid
    # 全地圖 look-ahead：視窗下方即將到來的未採集礦坑（伺服器有送、舊版被裁掉）。
    result["map_pits"] = map_pits(mine_board)
    return result
