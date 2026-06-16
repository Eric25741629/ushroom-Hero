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


def _planned_pickaxe_hits(step: Dict[str, Any], goods_id: int) -> int:
    if goods_id == mining.GOODS_PICKAXE and step.get("type") == "dig":
        return max(1, int(math.ceil(float(step.get("step_cost") or 1))))
    return 1


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
                    log: Optional[logging.Logger] = None) -> None:
    _log = log or logger
    if not _log.isEnabledFor(logging.INFO):
        return
    steps = list(plan_result.get("ws_steps", []))
    _log.info(
        "ws_mining plan step=%s inventory=%s message=%r steps=%s hold_floor=%s first_step=%s",
        step_index,
        dict(inventory),
        plan_result.get("message"),
        len(steps),
        plan_result.get("hold_floor"),
        steps[0] if steps else None,
    )


def _log_execute_trace(item: Dict[str, Any], *, step_index: int,
                       inventory: Dict[str, int],
                       log: Optional[logging.Logger] = None) -> None:
    _log = log or logger
    if not _log.isEnabledFor(logging.INFO):
        return
    _log.info(
        "ws_mining execute step=%s goods_id=%s block_id=%s confirmed=%s "
        "confirmation=%s refresh_attempts=%s inventory_after=%s error=%s",
        step_index,
        item.get("goods_id"),
        item.get("block_id"),
        item.get("confirmed"),
        item.get("confirmation"),
        item.get("refresh_attempts"),
        dict(inventory),
        item.get("error"),
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
    if confirm_by_refresh:
        deadline = time.monotonic() + max(0.0, float(refresh_timeout))
        while True:
            refresh_attempts += 1
            try:
                after_board = mining.read_board(client, timeout=timeout)
            except Exception as exc:  # pragma: no cover - live transport failure path
                confirmation = "refresh_failed"
                error = f"{type(exc).__name__}: {exc}"
                break
            if _board_signature(after_board) != _board_signature(before_board):
                confirmed = True
                confirmation = "confirmed_by_board_change"
                break
            confirmation = "unconfirmed_no_board_change"
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
    not an already-cleared cell. Live-verified rules (adb-fc65396d 2026-06-16):
      - active cell with NO block entry  -> undug dirt, diggable.
      - block with count>0               -> live pit / partially-hit cell, diggable.
      - pit/dirt block with count==0     -> already collected / passed, no-op.
      - stone (config 202) reads count==0 even when fresh (MINING_SCHEMA §6), so
        it is treated as diggable regardless of count.
    """
    if block_id not in actives:
        return False
    blk = block_by_id.get(block_id)
    if blk is None:
        return True
    if int(getattr(blk, "count", 0) or 0) > 0:
        return True
    return int(getattr(blk, "config_id", 0) or 0) == mining.TERRAIN_STONE


def _select_dig_step(
    board: Any,
    plan_steps,
    *,
    hold_floor: bool = False,
    grid=None,
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
    actives = {int(a) for a in (getattr(board, "actives", None) or [])}
    block_by_id = {int(b.block_id): b for b in (getattr(board, "blocks", None) or [])}

    for step in plan_steps:
        bid = step.get("block_id")
        if bid is not None and _is_diggable(actives, block_by_id, int(bid)):
            return step
    if not plan_steps:
        return None

    cands = [bid for bid in actives if _is_diggable(actives, block_by_id, bid)]
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

    seen = tracker.has_item(mining.GOODS_PICKAXE)
    inventory = dict(tracker.as_props())
    if not seen:
        inventory["pickaxe"] = _SEED_UNKNOWN_PICKAXE
    initial_inventory = dict(inventory)
    plans: list[Dict[str, Any]] = []
    candidate_steps: list[Dict[str, Any]] = []
    executed: list[Dict[str, Any]] = []
    limit = max(0, int(max_steps))
    stopped_reason = "max_steps"

    current_board = mining.read_board(client, timeout=timeout)
    _log_board_trace(current_board, inventory, phase="initial", step_index=0, log=_wlog)
    for _idx in range(limit):
        # 開瀏覽器請求優先：每步前讓出。已確認的挖步是伺服器端已落地，
        # 續做時讀當前 board 接續，不會重複。
        if should_abort is not None and should_abort():
            raise WSRunAborted("挖礦中途收到中斷請求（開啟瀏覽器）")
        if seen and int(inventory.get("pickaxe", 0)) <= 0:
            stopped_reason = "pickaxe_empty"
            break

        plan_result = mining_adapter.plan(
            current_board,
            inventory,
            max_depth=max_depth,
        )
        plans.append(plan_result)
        _log_plan_trace(plan_result, inventory, step_index=_idx, log=_wlog)
        step = _select_dig_step(
            current_board,
            plan_result.get("ws_steps", []),
            hold_floor=bool(plan_result.get("hold_floor")),
            grid=plan_result.get("grid"),
        )
        if step is None:
            stopped_reason = "no_steps"
            break

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
            _log_execute_trace(item, step_index=_idx, inventory=inventory, log=_wlog)
            stopped_reason = "unconfirmed"
            break

        _decrement_inventory(
            inventory,
            int(item["goods_id"]),
            int(item.get("hits") or 1),
        )
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
