"""Supervised pure-WS mining bridge.

This module connects ``ws_token.mining_adapter.plan`` output to
``home_mine_use_goods`` calls, but keeps live mutation behind explicit caller
flags. Defaults are dry-run and single-step because every executed step consumes
real mining resources.
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from typing import Any, Callable, Dict, Optional

from ws_token import mining, mining_adapter
from ws_token.abort import WSRunAborted
from ws_token.client import WSGameClient
from ws_token.creds import load_creds
from utils.mining_map_recorder import MiningMapRecorder

logger = logging.getLogger(__name__)


class LiveMiningBlocked(PermissionError):
    """Raised when a planned step is not allowed for supervised live execution."""


def _board_signature(board: Any) -> tuple:
    """Small stable signature for refresh confirmation."""
    blocks = []
    for blk in getattr(board, "blocks", []) or []:
        blocks.append((
            int(getattr(blk, "block_id", 0) or 0),
            int(getattr(blk, "x", 0) or 0),
            int(getattr(blk, "y", 0) or 0),
            int(getattr(blk, "config_id", 0) or 0),
            int(getattr(blk, "count", 0) or 0),
            int(getattr(blk, "is_reward", 0) or 0),
        ))
    holes = []
    for hole in getattr(board, "holes", []) or []:
        holes.append((
            int(getattr(hole, "config_id", 0) or 0),
            int(getattr(hole, "last_num", 0) or 0),
            int(getattr(hole, "max_num", 0) or 0),
            int(getattr(hole, "hole_num", 0) or 0),
        ))
    return (
        int(getattr(board, "area", 0) or 0),
        int(getattr(board, "baseline", 0) or 0),
        tuple(sorted(int(a) for a in (getattr(board, "actives", []) or []))),
        tuple(sorted(blocks)),
        tuple(sorted(holes)),
    )


def _board_confirmation(before: Any, after: Any, step: Dict[str, Any]) -> Optional[str]:
    """歸因盤面變化：baseline_changed / target_changed / footprint_changed，無變化 None。"""
    if _board_signature(after) == _board_signature(before):
        return None
    if int(getattr(after, "baseline", 0) or 0) != int(getattr(before, "baseline", 0) or 0):
        return "baseline_changed"
    bid = int(step.get("block_id") or 0)

    def _target_sig(board: Any):
        for blk in getattr(board, "blocks", []) or []:
            if int(getattr(blk, "block_id", 0) or 0) == bid:
                return (int(getattr(blk, "count", 0) or 0),
                        int(getattr(blk, "config_id", 0) or 0))
        return None

    if _target_sig(before) != _target_sig(after):
        return "target_changed"
    if step.get("type") != "use":
        return None

    from miner.core.mechanics import get_drill_affected_cells
    from ws_token import mining_adapter
    top = mining_adapter.viewport_top_depth(int(getattr(before, "baseline", 0) or 0))
    depth, game_col = divmod(bid, 100)
    row, col = depth - top, game_col - 1
    if step.get("item") == "bomb":
        # 炸彈可延伸畫面外，確認足跡使用絕對座標，不套 7 列裁切。
        footprint_ids = {
            (depth + dr) * 100 + col + dc + 1
            for dr, dc in (
                *((dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)),
                (-2, 0), (2, 0), (0, -2), (0, 2),
            )
            if 0 <= col + dc < mining_adapter.GRID_COLS
        }
    elif step.get("item") == "drill":
        rel_cells = get_drill_affected_cells(row, col, mining_adapter.GRID_ROWS,
                                             mining_adapter.GRID_COLS)
        footprint_ids = {(top + r) * 100 + c + 1 for r, c in rel_cells}
    else:
        return None

    def _block_sigs(board: Any):
        return {
            int(getattr(blk, "block_id", 0) or 0): (
                int(getattr(blk, "count", 0) or 0),
                int(getattr(blk, "config_id", 0) or 0),
            )
            for blk in (getattr(board, "blocks", []) or [])
        }

    before_sigs, after_sigs = _block_sigs(before), _block_sigs(after)
    if any(before_sigs.get(cell_id) != after_sigs.get(cell_id)
           for cell_id in footprint_ids):
        return "footprint_changed"
    return None


def _inventory_from_tracker(tracker: "mining.InventoryTracker",
                            fallback: Dict[str, int]) -> Dict[str, int]:
    """以 tracker（0x0401 seed + 0x0402 delta）覆蓋本地估計；未見過的 item 保留 fallback。"""
    names = {
        "pickaxe": mining.GOODS_PICKAXE,
        "drill": mining.GOODS_DRILL,
        "bomb": mining.GOODS_BOMB,
    }
    merged = dict(fallback)
    for name, item_id in names.items():
        if tracker.has_item(item_id):
            merged[name] = int(tracker.counts[item_id])
    return merged


def _planned_pickaxe_hits(step: Dict[str, Any], goods_id: int) -> int:
    if goods_id == mining.GOODS_PICKAXE and step.get("type") == "dig":
        return max(1, int(math.ceil(float(step.get("step_cost") or 1))))
    return 1


def _inventory_delta_confirms(
    before: Optional[Dict[str, int]],
    after: Optional[Dict[str, int]],
    goods_id: int,
) -> bool:
    """只把目標道具現量下降當成 WS 動作成功。

    盤面快照可能因動畫／短暫 WS 斷線沒有更新，但 0x0402 consume push
    已抵達；此時下降的目標道具是最可靠的歸因訊號。整張 dict 不同（例如
    同步到獎勵增加）不能單獨算成功，避免把無關庫存變化誤認成挖掘成功。
    """
    if before is None or after is None:
        return False
    key_by_goods = {
        mining.GOODS_PICKAXE: "pickaxe",
        mining.GOODS_DRILL: "drill",
        mining.GOODS_BOMB: "bomb",
    }
    key = key_by_goods.get(int(goods_id))
    if key is None:
        return False
    try:
        return int(after.get(key, 0)) < int(before.get(key, 0))
    except (TypeError, ValueError):
        return False


def _decrement_inventory(inventory: Dict[str, int], goods_id: int, hits: int) -> None:
    key_by_goods = {
        mining.GOODS_PICKAXE: "pickaxe",
        mining.GOODS_DRILL: "drill",
        mining.GOODS_BOMB: "bomb",
    }
    key = key_by_goods.get(goods_id)
    if key is None or key not in inventory:
        return
    inventory[key] = max(0, int(inventory.get(key, 0)) - int(hits))


def _format_grid(grid: list) -> str:
    return " | ".join(",".join(str(cell) for cell in row) for row in grid)


def _log_board_trace(board: Any, inventory: Dict[str, int], *,
                     phase: str, step_index: int,
                     log: Optional[logging.Logger] = None) -> None:
    """Log WS raw board projection so live runs can diagnose filtered layout."""
    _log = log or logger
    if not _log.isEnabledFor(logging.INFO):
        return
    trace = mining_adapter.board_projection_trace(board)
    _log.info(
        "ws_mining board phase=%s step=%s area=%s baseline=%s top_depth=%s "
        "actives=%s blocks=%s holes=%s inventory=%s area_info=%s grid=%s "
        "dropped_blocks=%s dropped_block_details=%s dropped_actives=%s "
        "dropped_active_details=%s",
        phase,
        step_index,
        trace["area"],
        trace["baseline"],
        trace["top_depth"],
        trace["actives_count"],
        trace["blocks_count"],
        trace["holes_count"],
        dict(inventory),
        trace["area_info"],
        _format_grid(trace["grid"]),
        len(trace["dropped_blocks"]),
        trace["dropped_blocks"][:12],
        len(trace["dropped_actives"]),
        trace["dropped_actives"][:12],
    )


def _log_plan_trace(plan_result: Dict[str, Any], inventory: Dict[str, int],
                    *, step_index: int,
                    log: Optional[logging.Logger] = None,
                    planner_version: str = "v1",
                    shadow_planner_version: str = "") -> None:
    _log = log or logger
    if not _log.isEnabledFor(logging.INFO):
        return
    steps = list(plan_result.get("ws_steps", []))
    _log.info(
        "ws_mining plan step=%s inventory=%s message=%r steps=%s hold_floor=%s first_step=%s "
        "primary_planner=%s shadow_planner=%s planner_source=%s score_breakdown=%s "
        "elapsed_ms=%s search_depth=%s explored_nodes=%s budget_hit=%s shadow=%s",
        step_index,
        dict(inventory),
        plan_result.get("message"),
        len(steps),
        plan_result.get("hold_floor"),
        steps[0] if steps else None,
        plan_result.get("planner_name", planner_version),
        shadow_planner_version,
        plan_result.get("planner_source", "planner"),
        plan_result.get("score_breakdown"),
        plan_result.get("elapsed_ms"),
        plan_result.get("search_depth"),
        plan_result.get("explored_nodes"),
        plan_result.get("budget_hit"),
        plan_result.get("shadow"),
    )


def _log_execute_trace(item: Dict[str, Any], *, step_index: int,
                       inventory: Dict[str, int],
                       log: Optional[logging.Logger] = None) -> None:
    _log = log or logger
    if not _log.isEnabledFor(logging.INFO):
        return
    _log.info(
        "ws_mining execute step=%s goods_id=%s block_id=%s confirmed=%s "
        "confirmation=%s refresh_attempts=%s inventory_after=%s error=%s "
        "tracker_inventory_after=%s rejection_reason=%s",
        step_index,
        item.get("goods_id"),
        item.get("block_id"),
        item.get("confirmed"),
        item.get("confirmation"),
        item.get("refresh_attempts"),
        dict(inventory),
        item.get("error"),
        item.get("inventory_after"),
        None if item.get("confirmed") else item.get("confirmation"),
    )


def step_goods_id(
    step: Dict[str, Any],
    *,
    allow_bomb: bool = False,
    allow_drill: bool = False,
) -> int:
    """Map one planner ``ws_step`` to the mining goods id used by 0x0c03.

    炸彈/鑽頭都需要明確 allow flag；預設只允許鎬子。
    """
    step_type = step.get("type")
    if step_type == "dig":
        return mining.GOODS_PICKAXE
    if step_type != "use":
        raise LiveMiningBlocked(f"unsupported mining step type: {step_type!r}")

    item = step.get("item")
    if item == "bomb":
        if not allow_bomb:
            raise LiveMiningBlocked("bomb step requires allow_bomb=True")
        return mining.GOODS_BOMB
    if item == "drill":
        if not allow_drill:
            raise LiveMiningBlocked("drill step requires allow_drill=True")
        return mining.GOODS_DRILL
    raise LiveMiningBlocked(f"unsupported mining item step: {item!r}")


def execute_plan_step(
    client: WSGameClient,
    step: Dict[str, Any],
    *,
    allow_bomb: bool = False,
    allow_drill: bool = False,
    timeout: Optional[float] = None,
    before_board: Optional[Any] = None,
    confirm_by_refresh: bool = True,
    refresh_timeout: float = 6.0,
    refresh_interval: float = 0.75,
    before_inventory: Optional[Dict[str, int]] = None,
    inventory_reader: Optional[Callable[[], Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Execute one planned WS step via send-only ``home_mine_use_goods``.

    0x0c03 may not reply even when the server accepts the mutation. We therefore
    send one action, refresh the board, and let the caller re-plan from the new
    snapshot. ``step_cost`` is reported as ``planned_hits`` but is not blindly
    repeated because unknown active terrain is deliberately cost-conservative.
    """
    block_id = step.get("block_id")
    if block_id is None:
        raise ValueError(f"missing block_id in ws step: {step!r}")
    block_id = int(block_id)
    goods_id = step_goods_id(step, allow_bomb=allow_bomb, allow_drill=allow_drill)
    planned_hits = _planned_pickaxe_hits(step, goods_id)
    if confirm_by_refresh and before_board is None:
        before_board = mining.read_board(client, timeout=timeout)

    mining.send_dig(client, goods_id, block_id)

    after_board = None
    confirmed = False
    confirmation = "sent_unconfirmed"
    error = None
    refresh_attempts = 0
    inventory_after: Optional[Dict[str, int]] = None
    if confirm_by_refresh:
        deadline = time.monotonic() + max(0.0, float(refresh_timeout))
        while True:
            refresh_attempts += 1
            try:
                after_board = mining.read_board(client, timeout=timeout)
            except Exception as exc:  # pragma: no cover - live transport failure path
                confirmation = "refresh_failed"
                error = f"{type(exc).__name__}: {exc}"
                # WinError 10038 and similar transient refresh failures can
                # happen after the server already accepted 0x0c03.  Keep the
                # inventory-delta success contract instead of discarding the
                # consume push merely because 0x0c01 could not be read.
                if inventory_reader is not None:
                    try:
                        inventory_after = inventory_reader()
                    except Exception:
                        inventory_after = None
                    if _inventory_delta_confirms(
                        before_inventory, inventory_after, goods_id
                    ):
                        confirmed = True
                        confirmation = "inventory_changed"
                break
            board_conf = _board_confirmation(before_board, after_board, step)
            if inventory_reader is None:
                # legacy 語意不變：只看盤面 signature
                if board_conf:
                    confirmed = True
                    confirmation = board_conf
                    break
                confirmation = "unconfirmed_no_board_change"
            else:
                # inventory-aware：盤面歸因（target/footprint/baseline）優先，
                # 盤面快照延遲時可用庫存變化歸因成功；兩者皆無 = unchanged。
                if board_conf:
                    confirmed = True
                    confirmation = board_conf
                    break
                inventory_after = inventory_reader()
                if _inventory_delta_confirms(
                    before_inventory, inventory_after, goods_id
                ):
                    confirmed = True
                    confirmation = "inventory_changed"
                    break
                confirmation = "unchanged"
            if time.monotonic() >= deadline:
                break
            if refresh_interval > 0:
                time.sleep(float(refresh_interval))
    return {
        "step": dict(step),
        "goods_id": goods_id,
        "block_id": block_id,
        "hits": 1,
        "planned_hits": planned_hits,
        "raw_replies": [],
        "raw_reply": None,
        "after_board": after_board,
        "confirmed": confirmed,
        "confirmation": confirmation,
        "error": error,
        "refresh_attempts": refresh_attempts,
        "inventory_after": inventory_after,
    }


def plan_current_board(
    client: WSGameClient,
    inventory: Dict[str, int],
    *,
    execute: bool = False,
    max_steps: int = 1,
    allow_bomb: bool = False,
    allow_drill: bool = False,
    timeout: Optional[float] = None,
    max_depth: Optional[int] = None,
) -> Dict[str, Any]:
    """Read the current board, run the planner, and optionally execute steps."""
    board = mining.read_board(client, timeout=timeout)
    plan_result = mining_adapter.plan(board, inventory, max_depth=max_depth)
    limit = max(0, int(max_steps))
    plans = [plan_result]
    candidate_steps = list(plan_result.get("ws_steps", []))[:limit] if not execute else []

    executed = []
    if execute:
        current_board = board
        current_plan = plan_result
        remaining_inventory = dict(inventory)
        for idx in range(limit):
            steps = list(current_plan.get("ws_steps", []))
            if not steps:
                break
            step = steps[0]
            candidate_steps.append(step)
            item = execute_plan_step(
                client,
                step,
                allow_bomb=allow_bomb,
                allow_drill=allow_drill,
                timeout=timeout,
                before_board=current_board,
            )
            executed.append(item)
            if not item.get("confirmed"):
                break
            _decrement_inventory(remaining_inventory, int(item["goods_id"]), int(item["hits"]))
            current_board = item.get("after_board")
            if current_board is None:
                break
            if idx + 1 < limit:
                current_plan = mining_adapter.plan(
                    current_board,
                    remaining_inventory,
                    max_depth=max_depth,
                )
                plans.append(current_plan)

    return {
        "board": board,
        "plan": plan_result,
        "plans": plans,
        "candidate_steps": candidate_steps,
        "executed": executed,
    }


# Seed count when the login 0x0402 snapshot never delivered the pickaxe (axe)
# count (it doesn't — the axe count is the goods count for gtid 4001, surfaced
# live only via the 0x0402 consume push that follows each dig). A positive seed
# lets the planner emit steps; the first consume push then supplies the real
# remaining count. Verified live on adb-fc65396d 2026-06-16.
_SEED_UNKNOWN_PICKAXE = 999


def _is_diggable(actives: set, block_by_id: Dict[int, Any], block_id: int) -> bool:
    """Whether the server will accept an axe dig on ``block_id`` right now.

    A target is diggable iff it is on the server frontier (``actives``) AND it is
    not an already-dug cell. Live-verified rules (CDP dig 2026-06-20, 小寶):
      - active cell with NO block entry  -> undug dirt, diggable.
      - block with count>0               -> undug / live cell (dirt/stone/pit),
        diggable; digging returns a 0x0c03 reply, spends a pickaxe, and the cell
        becomes a count==0 air block.
      - block with count==0              -> ALREADY DUG (air). Digging is a
        confirmed no-op (0x0c03 NO reply, board unchanged, no pickaxe), for BOTH
        201 and 202. So it is NOT a valid dig target.

    The earlier "stone (202) is diggable regardless of count" rule was wrong (it
    rested on a mistaken "fresh stone reads count==0" note) and sent wasted no-op
    digs at already-dug stone; a fresh 202 actually carries count>0.
    """
    if block_id not in actives:
        return False
    blk = block_by_id.get(block_id)
    if blk is None:
        return True
    return int(getattr(blk, "count", 0) or 0) > 0


def _select_dig_step(
    board: Any,
    plan_steps,
    *,
    hold_floor: bool = False,
    grid=None,
    exclude=None,
    inventory: Optional[Dict[str, int]] = None,
    allow_bomb: bool = False,
    allow_drill: bool = False,
) -> Optional[Dict[str, Any]]:
    """Pick the next server-valid step from the planner output.

    The v5 planner orders moves by value but can propose unreachable (non-active)
    or already-collected (count==0) pit blocks, which the server silently rejects
    (board unchanged). So take the planner's highest-value step whose target is
    actually diggable. If the planner proposed steps but NONE are diggable (e.g.
    it keeps targeting already-dug pits), fall back to the deepest diggable
    frontier cell to drive the board downward and reveal new pits. An empty plan
    is respected (return None) — the planner sees nothing worth digging.

    When ``hold_floor=True`` (visible pits exist), the fallback only considers
    diggable cells whose axe-dig would NOT open floor-7. Pass ``grid`` (the raw
    7×6 planner grid from the plan result) to enable per-candidate simulation;
    without it the fallback skips non-pit cells entirely to stay conservative.
    """
    plan_steps = list(plan_steps or ())
    inventory_known = inventory is not None and "pickaxe" in inventory
    inventory = inventory or {}
    excl = {int(x) for x in (exclude or ())}
    actives = {int(a) for a in (getattr(board, "actives", None) or [])}
    block_by_id = {int(b.block_id): b for b in (getattr(board, "blocks", None) or [])}

    # Below-viewport ore steering. v1 plans on a 7-row grid and is BLIND to pits
    # below the viewport (mining_adapter.map_pits row >= GRID_ROWS). When such ore
    # exists, v1's floor7-opener drifts to an arbitrary column and the ore needs an
    # extra horizontal traverse once it scrolls into reach. pit_directed_next runs a
    # terrain-cost Dijkstra from EVERY uncollected pit (incl. below-viewport) back to
    # the frontier, so it steers the shaft straight down the ore's column. Prefer it
    # over v1's pit-blind step here. Skipped under hold_floor (protecting a row-0 pit
    # must not trigger a scroll) — the existing hold_floor-safe pit_directed call
    # below still runs in that case. 5554 CDP live 2026-07-01: pit @d120829 c1 was
    # being bypassed for a c2 floor7-opener.
    if not hold_floor:
        below_pit = any(p["row"] >= mining_adapter.GRID_ROWS
                        for p in mining_adapter.map_pits(board))
        if below_pit:
            # 道具優先(只在「1 道具 > ~3 鎬」時出手):鑽頭沿礦的 column 一次清開下挖井、
            # 炸彈一砲收 ≥2 顆礦(且能炸到視窗下方)。道具珍貴、鎬會回,所以門檻設在省 ≥3 鎬;
            # prop_step_for_pit 內部已套此門檻 + active 落點規則。其落點仍需 server-diggable。
            prop = mining_adapter.prop_step_for_pit(
                board, inventory, allow_bomb=allow_bomb, allow_drill=allow_drill)
            # props target a count==0 AIR cell (空地/挖完礦洞), so DON'T run the
            # solid-cell _is_diggable gate (it would reject every valid placement).
            if prop is not None and int(prop["block_id"]) not in excl:
                return prop
            if inventory_known and int(inventory.get("pickaxe", 0) or 0) <= 0:
                return None
            steer = mining_adapter.pit_directed_next(board, exclude=excl)
            if steer is not None and _is_diggable(actives, block_by_id, int(steer)):
                return {"type": "dig", "block_id": int(steer), "step_cost": 1.0}

    for step in plan_steps:
        if (inventory_known and step.get("type") == "dig"
                and int(inventory.get("pickaxe", 0) or 0) <= 0):
            continue
        bid = step.get("block_id")
        if bid is not None and int(bid) not in excl and _is_diggable(actives, block_by_id, int(bid)):
            return step
    if not plan_steps:
        return None

    if inventory_known and int(inventory.get("pickaxe", 0) or 0) <= 0:
        return None
    cands = [bid for bid in actives if bid not in excl and _is_diggable(actives, block_by_id, bid)]
    if not cands:
        return None

    if hold_floor:
        if grid is not None:
            from miner.v3.board import floor7_open
            from miner.v3.actions import apply_dig
            baseline = int(getattr(board, "baseline", 0) or 0)
            top_depth = mining_adapter.viewport_top_depth(baseline)
            grid_cols = len(grid[0]) if grid else 0
            safe_cands = []
            for bid in cands:
                depth, game_col = divmod(bid, 100)
                row = depth - top_depth
                col = game_col - 1
                if not (0 <= row < len(grid)) or not (0 <= col < grid_cols):
                    safe_cands.append(bid)
                    continue
                work = [gr[:] for gr in grid]
                apply_dig(work, (row, col))
                if not floor7_open(work):
                    safe_cands.append(bid)
            # safety valve: if EVERY candidate would scroll, allow original set
            cands = safe_cands or cands
        else:
            # no grid available → only allow pits (never use deepest-frontier fallback)
            pit_cands = [
                bid for bid in cands
                if block_by_id.get(bid) is not None
                and (int(getattr(block_by_id[bid], "config_id", 0) or 0) == mining.TERRAIN_PIT
                     or int(getattr(block_by_id[bid], "is_reward", 0) or 0))
            ]
            cands = pit_cands or cands

    # Pit-directed steering: prefer the frontier cell that begins the cheapest
    # dig-path to the nearest uncollected pit (incl. below-viewport map_pits),
    # via terrain-cost Dijkstra. Keeps the shaft heading for the reward instead of
    # "deepest frontier, lowest col" (the root of 繞著礦坑挖 + 礦堆到 r=0). Only used
    # when its target is a server-valid, hold-floor-safe candidate; else fall
    # through to the deepest-frontier _key below.
    cand_set = set(cands)
    target = mining_adapter.pit_directed_next(board, exclude=excl)
    if target is not None and target in cand_set:
        return {"type": "dig", "block_id": target, "step_cost": 1.0}

    def _key(bid: int):
        blk = block_by_id.get(bid)
        depth, col = divmod(bid, 100)
        is_pit = blk is not None and (
            int(getattr(blk, "config_id", 0) or 0) == mining.TERRAIN_PIT
            or int(getattr(blk, "is_reward", 0) or 0))
        return (0 if is_pit else 1, -depth, col)  # pits first, then deepest frontier

    return {"type": "dig", "block_id": sorted(cands, key=_key)[0], "step_cost": 1.0}


def mine_until_pickaxe_empty(
    client: WSGameClient,
    tracker: mining.InventoryTracker,
    *,
    allow_bomb: bool = False,
    allow_drill: bool = False,
    max_steps: int = 200,
    timeout: Optional[float] = None,
    max_depth: Optional[int] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    device_id: Optional[str] = None,
    planner_version: str = "v1",
    shadow_planner_version: str = "",
) -> Dict[str, Any]:
    """Re-plan and execute one confirmed dig at a time until pickaxes reach 0.

    The pickaxe ("axe") count is NOT delivered by the 0x0402 login snapshot — it
    is the goods count for gtid 4001, surfaced live only via the 0x0402 consume
    push (9800001) that follows each dig (verified live 2026-06-16, fc). So rather
    than skip when the count is unknown, we seed a positive count so the planner
    emits steps, then adopt the authoritative remaining count from the first
    consume push (``tracker``). Each dig targets a server-VALID frontier cell
    (see ``_select_dig_step``); the planner can propose unreachable / already-
    collected pits, which the server rejects, so the executed step is filtered to
    a diggable target with a frontier fallback.
    """
    # 設定裝置專屬 ws_mining log（若有提供 device_id）
    _wlog: Optional[logging.Logger] = None
    if device_id:
        try:
            from utils.logging_utils import get_or_create_ws_mining_logger
            _wlog = get_or_create_ws_mining_logger(device_id)
        except Exception:
            pass

    # 每帳號挖礦地圖記錄（純 WS 路徑：21 列已知盤 + WS baseline authoritative depth）。
    map_recorder = MiningMapRecorder.for_device(device_id, "ws") if device_id else None

    def _map_snapshot(board_obj):
        """回傳 (depth, visible_7列, below_列或None)；失敗回 None（不得中斷挖礦）。"""
        try:
            trace = mining_adapter.board_projection_trace(board_obj)
            visible = trace["grid"]
            depth = int(trace["top_depth"])
            below = None
            try:
                full = mining_adapter.build_final_v1_input(board_obj)["board"]
                if len(full) > len(visible):
                    below = full[len(visible):]
            except Exception:
                below = None
            return depth, visible, below
        except Exception:
            return None

    def _record_ws_round(board_obj, plan_result, item, tried_any):
        if map_recorder is None or not map_recorder.enabled:
            return
        snap = _map_snapshot(board_obj)
        if snap is None:
            return
        depth, visible, below = snap
        confirmed = bool(item and item.get("confirmed"))
        goods = int(item["goods_id"]) if (item and item.get("goods_id") is not None) else None
        reason = (item.get("confirmation") if item
                  else ("unconfirmed" if tried_any else "no_steps"))
        exec_dict = {
            "ok": confirmed,
            "reason": reason,
            "shovels": 1 if confirmed and goods == mining.GOODS_PICKAXE else 0,
            "bombs": 1 if confirmed and goods == mining.GOODS_BOMB else 0,
            "drills": 1 if confirmed and goods == mining.GOODS_DRILL else 0,
        }
        steps = [item["step"]] if item else list(plan_result.get("ws_steps", []))[:1]
        # WS depth 由 baseline 決定，authoritative → 不 uncertain。
        map_recorder.round(depth=depth, uncertain=False, board=visible, below=below,
                           steps=steps, exec=exec_dict, inv=dict(inventory))

    # Undug terrain is reconstructed deterministically inside mining_adapter via
    # mine_terrain.terrain_at(depth, col, area_info) — no per-device learning,
    # no cache, no CNN. The board's own area_info indexes configMine_template.
    is_final_v1 = str(planner_version or "v1").strip().lower() == "final_v1"
    seen = tracker.has_item(mining.GOODS_PICKAXE)
    if is_final_v1 and not seen:
        # final_v1 只信 authoritative inventory（0x0401 seed + 0x0402 delta）；
        # 沒看過 4001 現量就不猜、不 seed，直接 skip（保留 ADB/v1 後備）。
        return {
            "initial_inventory": tracker.as_props(),
            "final_inventory": tracker.as_props(),
            "plans": [], "candidate_steps": [], "executed": [],
            "stopped_reason": "inventory_unknown",
            "skipped": "pickaxe 4001 missing from authoritative inventory",
        }
    inventory = dict(tracker.as_props())
    if not seen:
        inventory["pickaxe"] = _SEED_UNKNOWN_PICKAXE  # v1 相容路徑限定
    initial_inventory = dict(inventory)
    plans: list[Dict[str, Any]] = []
    candidate_steps: list[Dict[str, Any]] = []
    executed: list[Dict[str, Any]] = []
    limit = max(0, int(max_steps))
    stopped_reason = "max_steps"

    # dug-pit 身分側表：單次挖礦 session 生命週期（此函式每次執行建一個），跨輪記憶
    # 「先前為活躍礦坑、後續 count==0」的格。WS 21 列重建把已採集礦坑投影成 empty，
    # 只有把這些格補標回 dug_pit，final_v1 的 pit_clusters 才能在實機保住 cluster 身分。
    # 僅 final_v1 消費此資訊，故只在 final_v1 分支傳入 plan()（v1 路徑 dug_pit 無作用）。
    dug_pit_session = mining_adapter.DugPitTracker()

    current_board = mining.read_board(client, timeout=timeout)
    _log_board_trace(current_board, inventory, phase="initial", step_index=0, log=_wlog)
    if map_recorder is not None:
        map_recorder.start(planner=planner_version, inv=dict(inventory))
    for _idx in range(limit):
        # 開瀏覽器請求優先：每步前讓出。已確認的挖步是伺服器端已落地，
        # 續做時讀當前 board 接續，不會重複。
        if should_abort is not None and should_abort():
            raise WSRunAborted("挖礦中途收到中斷請求（開啟瀏覽器）")
        if seen and int(inventory.get("pickaxe", 0)) <= 0:
            stopped_reason = "pickaxe_empty"
            break

        if is_final_v1:
            # 每輪規劃前以 authoritative tracker 覆蓋本地估計（consume + gain）
            inventory = _inventory_from_tracker(tracker, inventory)
        # planner kwargs 只在非預設時傳遞，維持既有 v1 呼叫簽名（與測試 fake）不變
        _plan_kwargs: Dict[str, Any] = {}
        if is_final_v1:
            _plan_kwargs["planner_version"] = "final_v1"
            _plan_kwargs["session"] = dug_pit_session
        if shadow_planner_version:
            _plan_kwargs["shadow_planner_version"] = shadow_planner_version
        plan_result = mining_adapter.plan(
            current_board,
            inventory,
            max_depth=max_depth,
            **_plan_kwargs,
        )
        plans.append(plan_result)
        _log_plan_trace(plan_result, inventory, step_index=_idx, log=_wlog,
                        planner_version=planner_version,
                        shadow_planner_version=shadow_planner_version)

        # 同一盤面內逐個候選嘗試：被伺服器拒挖（unconfirmed、版面不變）的目標只
        # 加入本盤黑名單後改試下一個可達格，不再因「第一步失敗」就中止整輪挖礦。
        # 這正是 hold_floor 盤面挑到被上方石頭擋住的深層 frontier 格 → 拒挖 →
        # 之前 digs=1 就 break、整輪挖 0 的根因。候選有限，不會無限送 dig。
        rejected: set = set()
        item = None
        tried_any = False
        while True:
            if should_abort is not None and should_abort():
                raise WSRunAborted("挖礦中途收到中斷請求（開啟瀏覽器）")
            step = _select_dig_step(
                current_board,
                plan_result.get("ws_steps", []),
                hold_floor=bool(plan_result.get("hold_floor")),
                grid=plan_result.get("grid"),
                exclude=rejected,
                inventory=inventory,
                allow_bomb=allow_bomb,
                allow_drill=allow_drill,
            )
            if step is None:
                break
            candidate_steps.append(step)
            tried_any = True
            _exec_kwargs: Dict[str, Any] = {}
            if is_final_v1:
                # 庫存變化也可歸因確認（盤面快照延遲時）
                _exec_kwargs["before_inventory"] = dict(inventory)
                _exec_kwargs["inventory_reader"] = (
                    lambda: _inventory_from_tracker(tracker, inventory))
            item = execute_plan_step(
                client,
                step,
                allow_bomb=allow_bomb,
                allow_drill=allow_drill,
                timeout=timeout,
                before_board=current_board,
                **_exec_kwargs,
            )
            executed.append(item)
            if item.get("confirmed"):
                break
            _log_execute_trace(item, step_index=_idx, inventory=inventory, log=_wlog)
            rejected.add(int(step["block_id"]))
            item = None

        _record_ws_round(current_board, plan_result, item, tried_any)

        if item is None:
            # 本盤所有可挖候選都試過仍無 confirmed dig。完全沒挖步=no_steps，
            # 有送過但都被拒=unconfirmed（兩者都讓 confirmed_digs==0 標 skipped）。
            stopped_reason = "unconfirmed" if tried_any else "no_steps"
            break

        _decrement_inventory(
            inventory,
            int(item["goods_id"]),
            int(item.get("hits") or 1),
        )
        if is_final_v1:
            # authoritative 覆蓋：本地扣抵只是估計，tracker 的 consume/gain 才是真值
            inventory = _inventory_from_tracker(tracker, inventory)
        # Adopt the authoritative remaining count the moment the first consume
        # push lands (only relevant when we started from a seed).
        if not seen and tracker.has_item(mining.GOODS_PICKAXE):
            seen = True
            inventory["pickaxe"] = tracker.pickaxe
        _log_execute_trace(item, step_index=_idx, inventory=inventory, log=_wlog)
        if seen and int(inventory.get("pickaxe", 0)) <= 0:
            stopped_reason = "pickaxe_empty"
            break

        next_board = item.get("after_board")
        if next_board is None:
            stopped_reason = "missing_after_board"
            break
        current_board = next_board
        _log_board_trace(current_board, inventory, phase="after_execute",
                         step_index=_idx, log=_wlog)
    else:
        if seen and int(inventory.get("pickaxe", 0)) <= 0:
            stopped_reason = "pickaxe_empty"

    _summary_log = _wlog or logger
    _summary_log.info(
        "ws_mining summary: stopped=%s digs=%s hold_floor_rounds=%s "
        "pickaxe %s→%s drill %s→%s bomb %s→%s",
        stopped_reason,
        len(executed),
        sum(1 for p in plans if p.get("hold_floor")),
        initial_inventory.get("pickaxe"), inventory.get("pickaxe"),
        initial_inventory.get("drill"), inventory.get("drill"),
        initial_inventory.get("bomb"), inventory.get("bomb"),
    )

    if map_recorder is not None:
        map_recorder.end(totals={"stopped": stopped_reason, "digs": len(executed)})

    result: Dict[str, Any] = {
        "initial_inventory": initial_inventory,
        "final_inventory": inventory,
        "plans": plans,
        "candidate_steps": candidate_steps,
        "executed": executed,
        "stopped_reason": stopped_reason,
    }
    # 實質沒挖到（0 個 confirmed dig 且卡在 no_steps/unconfirmed）→ 標 "skipped"
    # sentinel，讓 ws_phase._substantive_done 不把「挖礦/Oracle」記為完成、保留 ADB
    # 後備。判定用 confirmed_digs==0（非 executed==[]）：unconfirmed step 也會被 append
    # 進 executed（confirmed 檢查之前），死結時長度為 1。pickaxe_empty（沒鏟可挖）
    # 仍算完成（ADB 也挖不了），不標 skipped。
    confirmed_digs = sum(1 for it in executed if it.get("confirmed"))
    if confirmed_digs == 0 and stopped_reason in ("no_steps", "unconfirmed"):
        result["skipped"] = f"no dig confirmed (stopped={stopped_reason})"
    return result


def _format_step(step: Dict[str, Any]) -> str:
    return (
        f"{step.get('type')}:{step.get('item', 'pickaxe')} "
        f"pos=({step.get('row')},{step.get('col')}) "
        f"block_id={step.get('block_id')} cost={step.get('step_cost')}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--pickaxe-count", type=int, default=None)
    parser.add_argument("--drill-count", type=int, default=0)
    parser.add_argument("--bomb-count", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-bomb", action="store_true")
    parser.add_argument(
        "--confirm-live-dig",
        action="store_true",
        help="required with --execute; confirms real mining items may be consumed",
    )
    args = parser.parse_args(argv)

    if args.execute and not args.confirm_live_dig:
        print("[mining] --execute requires --confirm-live-dig", flush=True)
        return 2

    creds = load_creds(args.device)
    client = WSGameClient(creds)
    tracker = mining.InventoryTracker()
    client.set_push_handler(tracker.on_push)
    info = client.connect()
    print(
        f"[mining] device={args.device} login code={info['code']} "
        f"role_id={info['role_id']}",
        flush=True,
    )
    try:
        inventory = tracker.as_props()
        if args.pickaxe_count is not None:
            inventory["pickaxe"] = int(args.pickaxe_count)
        inventory["drill"] = int(args.drill_count)
        inventory["bomb"] = int(args.bomb_count)

        result = plan_current_board(
            client,
            inventory,
            execute=args.execute,
            max_steps=args.max_steps,
            allow_bomb=args.allow_bomb,
            timeout=8.0,
            max_depth=args.max_depth,
        )
        board = result["board"]
        plan_result = result["plan"]
        print(
            f"[mining] board area={board.area} baseline={board.baseline} "
            f"blocks={len(board.blocks)} actives={len(board.actives)} "
            f"inventory={inventory}",
            flush=True,
        )
        print(f"[mining] plan: {plan_result.get('message')}", flush=True)
        for step in result["candidate_steps"]:
            print(f"  candidate {_format_step(step)}", flush=True)
        for item in result["executed"]:
            print(
                f"  executed goods_id={item['goods_id']} "
                f"block_id={item['block_id']} hits={item['hits']}/{item['planned_hits']} "
                f"confirmation={item['confirmation']}",
                flush=True,
            )
            if item.get("error"):
                print(f"    error={item['error']}", flush=True)
        if args.execute and any(not item.get("confirmed") for item in result["executed"]):
            return 3
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
