"""Adapter: WS MineBoard <-> miner v1 planner grid + plan -> block_id steps.

Uses the **v1** planner (`miner.planning.smart_planner.plan_smart`, whole-board
A*) — the same default as the screenshot/ADB runtime — because it has the
highest score/shovel-efficiency on the canonical sim (v1 3711 vs v4 1649).
The WS mining loop is a supervised scroll loop that needs the planner to keep
emitting a no_pit progress dig so the board scrolls; v1 used to return an
EMPTY plan once pits were gone + floor7 was open (why WS previously ran v4),
but smart_planner's descent-dig fallback fixed that, so v1 now keeps emitting
no_pit progress digs like v4 did. See the inline comment in `plan()`.

Two layers, kept apart so the pure grid transform never imports the planner:

  board_to_grid(mine_board) -> List[List[str]]   (7x6 DEFAULT_CLASSES labels)
      Pure function. No miner import. Tested directly.
  plan(mine_board, inventory) -> steps           (calls plan_smart via LAZY import)
      Builds the grid, runs the v1 A*, maps each step's (row, col) back
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

from ws_token import mine_terrain  # deterministic undug-terrain lookup (no learning)

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


def _project_board(mine_board: Any) -> tuple[List[List[str]], list[dict], list[dict]]:
    """Project WS board into planner grid and record what the projection drops.

    Undug terrain comes from ``mine_terrain.terrain_at(depth, col, area_info)`` —
    a deterministic lookup into ``configMine_template`` keyed by the board's own
    ``area_info`` (0x0c01 field-6). This is exact for every cell whose area is in
    ``area_info`` (the viewport always is), so undug actives are labelled
    dirt/rock precisely and OFF-FRONTIER occluded cells (which WS never reports)
    are filled as solid ``unreachable_dirt/rock`` instead of wrongly defaulting to
    passable air. When ``area_info`` does not cover a depth (very rare in-viewport)
    the lookup returns ``None`` and we keep the safe legacy defaults (dirt / air).
    WS truth always wins (dug/pit/active set first).
    """
    grid: List[List[str]] = [[EMPTY for _ in range(GRID_COLS)] for _ in range(GRID_ROWS)]
    baseline = int(getattr(mine_board, "baseline", 0) or 0)
    top_depth = viewport_top_depth(baseline)
    area_info = getattr(mine_board, "area_info", None) or {}
    dropped_blocks: list[dict] = []
    dropped_actives: list[dict] = []
    ws_known: set[tuple[int, int]] = set()  # cells WS actually reports (active/block)

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
        # active 且無 block feature = 未挖格。地形由 area_info 決定論查表得知:
        # STONE(202)→rock(成本 2)、AIR(100)→empty(空氣口袋,可穿越成本 0)、
        # 其餘(dirt/未涵蓋)→dirt(保守、可挖成本 1)。
        # AIR 必須標 empty 而非 dirt:5554 CDP live 對拍(CNN 像素 vs WS,2026-07-01)
        # 證實 active 且 terrain_at==AIR 的格在畫面上就是已開的空氣口袋,舊版盲標 dirt
        # 會灌水盤面密度→A* 成本失真→下挖軌跡偏掉(使用者回報「軌跡很奇怪」根因)。
        t = mine_terrain.terrain_at(depth, col, area_info)
        if t == mine_terrain.STONE:
            label = "rock"
        elif t == mine_terrain.AIR:
            label = EMPTY
        else:
            label = "dirt"
        grid[row][col] = label
        ws_known.add((row, col))

    if any(cell != EMPTY for cell in grid[GRID_ROWS - 1]):
        bottom_depth = top_depth + (GRID_ROWS - 1)
        for col, cell in enumerate(grid[GRID_ROWS - 1]):
            if cell != EMPTY:
                continue
            # 底列缺格：先查決定論地形表。STONE/DIRT → 不可達「實體」，AIR/未涵蓋(None)
            # → 保守標 unreachable_empty。
            # 為何不能一律 unreachable_empty：unreachable_empty 是「空氣但不可達」，v1 的
            # reachability pass 只要挖開正上方那格就把它曝光成「可達空氣」→ 判定 floor7 開
            # (cost 1)。但底列缺格其實多半是「未傳輸的實體地形」(不在 actives)，挖了上一列
            # 不會真的捲動。於是 v1 一路挑「上一列假開口」逐 col 挖、把整列 r5 挖空才碰到真正
            # 進 frontier 的底列格(5554 CDP live 2026-07-01「挖空整列 r5」根因)。改用 terrain_at
            # 把已知實體標 unreachable_rock/dirt 後，假開口消失，v1 只能挖真實 active 底列格 →
            # 一挖就捲。AIR/None 仍標 unreachable_empty：純 WS 無法證明該空氣可達，維持「不誤判
            # 已下樓」(hold_floor 測試用空 area_info → None → 行為不變)。
            t = mine_terrain.terrain_at(bottom_depth, col, area_info)
            if t == mine_terrain.STONE:
                grid[GRID_ROWS - 1][col] = "unreachable_rock"
            elif t == mine_terrain.DIRT:
                grid[GRID_ROWS - 1][col] = "unreachable_dirt"
            else:
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
        ws_known.add((row, col))

    # Occluded fill: viewport cells WS never reported (not active, not a block)
    # default to EMPTY=air — wrong, the planner would walk through them. Fill solid
    # ones from the deterministic template so off-frontier pockets read as walls.
    # Template AIR(100)/uncovered(None) stay EMPTY (genuinely passable air).
    for r in range(GRID_ROWS):
        depth = top_depth + r
        for c in range(GRID_COLS):
            if (r, c) in ws_known or grid[r][c] != EMPTY:
                continue
            t = mine_terrain.terrain_at(depth, c, area_info)
            if t == mine_terrain.DIRT:
                grid[r][c] = "unreachable_dirt"
            elif t == mine_terrain.STONE:
                grid[r][c] = "unreachable_rock"

    return grid, dropped_blocks, dropped_actives


def board_to_grid(mine_board: Any) -> List[List[str]]:
    """Project a MineBoard's blocks into a 7x6 grid of planner labels.

    Pure: no planner import, no side effects. Undug terrain (actives + occluded
    off-frontier cells) is filled deterministically from ``configMine_template``
    via the board's ``area_info`` (see ``_project_board``). Blocks outside the
    7-row viewport [baseline-5, baseline+2) or outside columns 1..6 are dropped.
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


def pit_directed_next(mine_board: Any, exclude: Any = None) -> Optional[int]:
    """Block_id of the diggable frontier cell that begins the cheapest dig-path to
    the nearest uncollected pit, or ``None`` when no pit is in the known region.

    Why this and not "deepest frontier, lowest col": the old fallback descended a
    shaft blind to where the reward is, so pits drifted up to row 0 uncollected
    and the dig "circled" the ore. Here we run a multi-source Dijkstra from every
    uncollected pit (including below-viewport ``map_pits``) back across the now-
    deterministic terrain — entering a cell costs its dig cost (dug/air 0, dirt 1,
    stone 2) — and pick the frontier cell with the smallest distance. So the shaft
    heads straight for the reward column through the cheapest terrain, and a
    reachable pit is pursued before it can scroll out of view. Tiebreak: deepest
    then lowest col (keep descending). Pure: terrain via ``mine_terrain``, no
    planner import. The executed cell is always a real ``active`` (server-valid).
    """
    import heapq

    actives = {int(a) for a in (getattr(mine_board, "actives", None) or [])}
    if not actives:
        return None
    excl = {int(x) for x in (exclude or ())}
    area_info = getattr(mine_board, "area_info", None) or {}

    block_by_pos: Dict[tuple, Any] = {}
    pits: List[tuple] = []
    for blk in getattr(mine_board, "blocks", []) or []:
        pos = (int(blk.y), int(blk.x) - 1)
        block_by_pos[pos] = blk
        if int(getattr(blk, "count", 0) or 0) > 0 and (
            int(getattr(blk, "config_id", 0) or 0) == TERRAIN_PIT
            or int(getattr(blk, "is_reward", 0) or 0)
        ):
            pits.append(pos)
    if not pits:
        return None

    def _diggable(bid: int) -> bool:
        if bid in excl:
            return False
        d, gc = divmod(bid, 100)
        blk = block_by_pos.get((d, gc - 1))
        return blk is None or int(getattr(blk, "count", 0) or 0) > 0

    active_pos = {(d, gc - 1): bid
                  for bid in actives if _diggable(bid)
                  for d, gc in [divmod(bid, 100)]}
    if not active_pos:
        return None

    depths = [p[0] for p in pits] + [p[0] for p in active_pos]
    dmin, dmax = min(depths), max(depths)

    def _cost(d: int, c: int) -> int:
        blk = block_by_pos.get((d, c))
        if blk is not None and int(getattr(blk, "count", 0) or 0) == 0:
            return 0  # already dug -> air
        t = mine_terrain.terrain_at(d, c, area_info)
        if t == mine_terrain.AIR:
            return 0
        if t == mine_terrain.STONE:
            return 2
        return 1  # dirt / undug-unknown default (every cell is diggable)

    INF = float("inf")
    dist: Dict[tuple, float] = {}
    pq: list = []
    for p in pits:
        dist[p] = 0
        heapq.heappush(pq, (0, p[0], p[1]))
    while pq:
        dc, d, c = heapq.heappop(pq)
        if dc > dist.get((d, c), INF):
            continue
        for nd, nc in ((d - 1, c), (d + 1, c), (d, c - 1), (d, c + 1)):
            if not (0 <= nc < GRID_COLS) or not (dmin <= nd <= dmax):
                continue
            nv = dc + _cost(nd, nc)
            if nv < dist.get((nd, nc), INF):
                dist[(nd, nc)] = nv
                heapq.heappush(pq, (nv, nd, nc))

    best_key = None
    best_bid = None
    for (d, c), bid in active_pos.items():
        dd = dist.get((d, c))
        if dd is None:
            continue
        key = (dd, -d, c)  # nearest pit, then deepest, then lowest col
        if best_key is None or key < best_key:
            best_key, best_bid = key, bid
    return best_bid


def _bomb_blast_cells(depth: int, col: int) -> set:
    """Absolute (depth, col) cells a bomb at (depth,col) clears: 3x3 + cross±2.

    Mirrors miner.core.mechanics.get_bomb_affected_cells but in ABSOLUTE board
    coords (no 7-row clamp), so the blast that reaches BELOW the viewport bottom is
    represented — that below-frontier reach is the bomb's unique value (a pickaxe
    can only hit the frontier). Columns clamp to 0..5; depth is unbounded.
    """
    cells = {(depth + dd, col + dc) for dd in (-1, 0, 1) for dc in (-1, 0, 1)}
    cells |= {(depth + 2, col), (depth - 2, col), (depth, col + 2), (depth, col - 2)}
    return {(d, c) for d, c in cells if 0 <= c < GRID_COLS}


def prop_step_for_pit(board: Any, inventory: Optional[Dict[str, int]], *,
                      allow_bomb: bool, allow_drill: bool,
                      min_pits: int = 2) -> Optional[Dict[str, Any]]:
    """A bomb/drill step ONLY when one prop collects >= ``min_pits`` uncollected pits
    in a single use — the unambiguous "一個道具 > 2.5-3 個鎬子" case (each pit would
    else need its own descent-and-dig). Pure; returns None otherwise.

    Deliberately conservative (props are precious, pickaxes regenerate): we do NOT
    spend a prop to clear plain terrain — for pure descent a prop saves no pickaxes
    (each scroll only needs the one bottom cell dug; cleared extras scroll away). The
    win is collecting MULTIPLE ore at once.

    Placement: props go on an AIR cell — 空地 or 挖完的礦洞 (a count==0 dug block) —
    NOT a solid frontier cell (live-verified: a bomb on a solid active cell is
    rejected; on a count==0 cell it consumes and clears its blast).
    - bomb: an air cell whose 3x3 + cross±2 blast (which reaches BELOW the viewport —
      a pickaxe can't) covers >= min_pits pits.
    - drill: an air cell whose column (placement depth downward) holds >= min_pits pits.

    Returns the planner-style ``use`` step (type/item/block_id). The block_id is the
    count==0 placement cell, so the caller must NOT gate it through the solid-cell
    ``_is_diggable`` check.
    """
    inv = inventory or {}
    blocks = getattr(board, "blocks", None) or []
    pits = {(int(b.y), int(b.x) - 1) for b in blocks
            if int(getattr(b, "count", 0) or 0) > 0
            and (int(getattr(b, "config_id", 0) or 0) == TERRAIN_PIT
                 or int(getattr(b, "is_reward", 0) or 0))}
    if len(pits) < min_pits:
        return None
    # placement cells: props go on AIR — 空地 or 挖完的礦洞 (count==0 dug block),
    # NOT a solid frontier cell (live-verified 5554 2026-07-01: bomb on a solid active
    # cell is rejected/no-consume; on a count==0 cell it consumes + clears the blast).
    air_cells = [(int(b.y), int(b.x) - 1) for b in blocks
                 if int(getattr(b, "count", 0) or 0) == 0]
    if not air_cells:
        return None
    top = viewport_top_depth(int(getattr(board, "baseline", 0) or 0))

    def _bid(depth: int, col: int) -> int:
        return depth * 100 + col + 1

    # bomb: an air cell whose 3x3+cross±2 blast (reaches below the viewport) covers
    # >= min_pits pits. Footprint live-verified to match _bomb_blast_cells.
    if allow_bomb and int(inv.get("bomb", 0) or 0) > 0:
        best = None
        for (ad, ac) in air_cells:
            hit = len(_bomb_blast_cells(ad, ac) & pits)
            if hit >= min_pits and (best is None or hit > best[0]):
                best = (hit, ad, ac)
        if best is not None:
            _, ad, ac = best
            return {"type": "use", "item": "bomb", "block_id": _bid(ad, ac),
                    "row": ad - top, "col": ac, "step_cost": 2.99}

    # drill: an air cell whose column (downward) holds >= min_pits pits.
    if allow_drill and int(inv.get("drill", 0) or 0) > 0:
        best = None
        for (ad, ac) in air_cells:
            hit = sum(1 for (pd, pc) in pits if pc == ac and pd >= ad)
            if hit >= min_pits and (best is None or hit > best[0]):
                best = (hit, ad, ac)
        if best is not None:
            _, ad, ac = best
            return {"type": "use", "item": "drill", "block_id": _bid(ad, ac),
                    "row": ad - top, "col": ac, "step_cost": 2.99}
    return None


def plan(mine_board: Any, inventory: Optional[Dict[str, int]] = None,
         *, max_depth: Optional[int] = None) -> Dict[str, Any]:
    """Build the grid, run the planner (v1 A*), and translate steps back to block_ids.

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

    grid = board_to_grid(mine_board)
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
