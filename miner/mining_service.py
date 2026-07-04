"""Mining service: screenshot -> classify -> plan -> execute loop."""
from __future__ import annotations

import collections
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import contextlib
import io
import copy

import uiautomator2 as u2
import img_tools
import bot_state
import config_manager

from miner.models.classifier import ClassifierCNN, load_cnn_model
from miner.planning.executor import (
    ExecutionResult,
    NoBoardChangeError,
    OutOfItemError,
    execute_plan_steps,
)
from miner.core.config import DEFAULT_CLASSES, HIT_TABLE
from miner.depth_tracker import DepthTracker
from miner.core.ocr_utils import check_pickaxe_count, check_drill_num, check_boom_num
from miner.core.ws_inventory import read_ws_prop_counts
from miner.planning.item_planner import find_tool_candidate
from miner.planning.planner import (
    base_label,
    enter_cost,
    is_empty as is_air,
)
from miner.planning.smart_planner import plan_smart
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4
from miner.rl.rl_recorder import RLRecorder
from miner.core.vision_utils import check_points
from utils.logging_utils import logger, setup_miner_logger
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR
from tools import click_white


# Cell labels that mean "no cell here" — used to count un-dug visible
# cells for cross-check against the protocol's `0x0c01.cells` list.
_EMPTY_CELL_LABELS = {
    "empty", "void", "dug_pit",
    "unreachable_empty", "unreachable_void",
}


def _log_inventory_validation(
    ip: str,
    pickaxe_count: float,
    items_available: Dict[str, int],
    miner_logger,
) -> None:
    """Cross-check OCR readings against latest captured 0x0402/0x0c11/0x4202.
    Best-effort — swallows all errors. Purely informational."""
    try:
        from utils.ws_validator import validate_against_captures
    except Exception:
        return
    try:
        ocr = {
            "pickaxe": int(pickaxe_count),
            "drill": int(items_available.get("drill", 0)),
            "bomb": int(items_available.get("bomb", 0)),
        }
        for r in validate_against_captures(
            ip, ocr, cmds=(0x0402, 0x0c11, 0x4202),
        ):
            miner_logger.info(
                f"[protocol-validate cmd=0x{r['cmd']:04x}] {r['summary']}"
            )
    except Exception as exc:
        miner_logger.debug(f"[protocol-validate inventory] skipped: {exc}")


def _log_board_validation(ip: str, board: List[List[str]], miner_logger) -> None:
    """Cross-check classifier board against latest 0x0c01 capture."""
    try:
        from utils.ws_validator import validate_board_against_classifier
    except Exception:
        return
    try:
        cnn_cells = sum(
            1 for row in board for c in row if c not in _EMPTY_CELL_LABELS
        )
        result = validate_board_against_classifier(ip, cnn_cells)
        if result.get("have_body"):
            miner_logger.info(f"[protocol-validate-board] {result['summary']}")
    except Exception as exc:
        miner_logger.debug(f"[protocol-validate board] skipped: {exc}")
from runtime_services.device_runtime_service import ForceSleepRequested

# 功能開關：規劃器是否允許使用鑽頭/炸彈道具。
USE_ITEMS: bool = True

# 連續拿到空 plan 的容忍上限，避免無限空轉。
_MAX_EMPTY_PLANS: int = 3

# 連續相同版面（非空 plan 卻毫無變化）的容忍上限 — 真正的「identical state」死結偵測。
_MAX_IDENTICAL_BOARDS: int = 3


def _identical_board_exceeded(cur_sig, prev_sig, count: int):
    """Return (new_count, tripped). Increments when the board signature is
    unchanged from the previous iteration; resets to 0 when it changes."""
    if prev_sig is not None and cur_sig == prev_sig:
        count += 1
    else:
        count = 0
    return count, count >= _MAX_IDENTICAL_BOARDS

# OCR 鏟子驗證頻率（每 N 個 iter 對照一次）。從原本 3 拉長到 5 —
# 現在 count 由 executor 回傳的 ExecutionResult.shovels_used 增量扣減，
# OCR 只是漂移驗證。
_PICKAXE_OCR_VALIDATE_EVERY: int = 5

# OCR 漂移容忍 — 內部 count 與 OCR 相差 <= 這個值時不調整。允許小量
# 抖動（OCR 邊緣抖動、Reward 動畫殘留），超過則以 OCR 為準。
_PICKAXE_DRIFT_TOLERANCE: int = 2

# 正向漂移（OCR > 內部 count）的例行上限。收集礦坑會回饋鏟子，但 executor 的
# shovels_used 只扣挖掘、從不計入這些獎勵，於是內部 count 偏低、OCR（真值）偏高。
# 這在幾乎每次校驗都會發生（實測 66/68 次校正為 +3/+4），屬正常，僅記 INFO；
# 負向漂移（用得比追蹤的多）或過大的正向跳動才是真正 desync，維持 WARNING。
_PICKAXE_REWARD_DRIFT_MAX: int = 10


def _apply_partial(
    result: Optional[ExecutionResult],
    count: int,
    items_available: Dict[str, int],
    miner_logger,
) -> int:
    """Decrement internal counters by the resources an ExecutionResult
    reports consumed.

    Returns the new shovel `count`. Items are debited in place on
    ``items_available``. A ``None`` result (e.g. an older exception path
    that didn't plumb partial accounting) is treated as zero usage so
    callers don't have to special-case it.
    """
    if result is None:
        return count
    if result.shovels_used:
        new_count = max(0, count - int(result.shovels_used))
        miner_logger.info(
            f"[MiningService] 扣減鏟子 {count} -> {new_count} (executor 用掉 {result.shovels_used})"
        )
        count = new_count
    if result.drills_used:
        items_available["drill"] = max(0, items_available.get("drill", 0) - int(result.drills_used))
    if result.bombs_used:
        items_available["bomb"] = max(0, items_available.get("bomb", 0) - int(result.bombs_used))
    return count


def _reconcile_shovel_count(
    internal: int,
    ocr: Optional[int],
    tolerance: int = _PICKAXE_DRIFT_TOLERANCE,
) -> Tuple[int, str]:
    """Reconcile internal pickaxe counter against an OCR reading.

    Returns ``(new_count, kind)`` where ``kind`` is one of:

      * ``"ok"`` — within tolerance; internal value is kept.
      * ``"drift"`` — outside tolerance; OCR wins (counter snaps to OCR).
      * ``"ocr_unavailable"`` — OCR returned ``None`` (failure); internal
        kept as-is.

    Pure helper — no side effects, no logging. Logging is the caller's
    responsibility so this can be reused / tested in isolation.
    """
    if ocr is None:
        return internal, "ocr_unavailable"
    if abs(internal - ocr) <= tolerance:
        return internal, "ok"
    return ocr, "drift"


def _check_force_sleep(ip: str) -> None:
    if bot_state.check_force_sleep(ip):
        raise ForceSleepRequested(f"[{ip}] force sleep requested during mining")


def _dismiss_mining_overlay_if_needed(d: u2.Device, frame, miner_logger) -> bool:
    """Reuse existing OCR helpers to dismiss the mine title overlay."""
    try:
        # Limit OCR scan area to avoid false positives and extra OCR load.
        # Requested range: y=210..550 (full width).
        frame_h = frame.shape[0] if frame is not None else 0
        y0 = max(0, min(210, frame_h))
        y1 = max(y0, min(550, frame_h))
        roi = frame[y0:y1, :] if frame is not None and y1 > y0 else frame
        texts = img_tools.get_all_text(roi, max_servers=1)
    except Exception as exc:
        miner_logger.debug(f"[Mining OCR] overlay check failed: {exc}")
        return False

    if any("礦洞" in text for text in texts):
        miner_logger.info("[Mining OCR] detected '礦洞', clicking blank area")
        click_white(d)
        time.sleep(0.6)
        return True
    return False
def get_visual_board(board: List[List[str]]) -> str:
    """將棋盤轉成易讀的文字格狀圖。"""
    symbols = {
        "empty": ".",
        "void": ".",
        "dug_pit": ".",
        "unreachable_empty": "_",
        "unreachable_void": "_",
        "dirt": "D",
        "unreachable_dirt": "d",
        "rock": "R",
        "unreachable_rock": "r",
        "reachable_pit": "*",
        "unreachable_pit": "X"
    }
    
    C = len(board[0]) if board else 0
    header = "   " + " ".join([str(i) for i in range(C)])
    rows = [header]
    
    for i, row in enumerate(board):
        row_str = f"{i:2d} " + " ".join([symbols.get(cell, cell[:1]) for cell in row])
        rows.append(row_str)
    return "\n".join(rows)

def print_plan_result(logger_obj, title: str, result: Dict[str, Any], orig_board: List[List[str]]) -> None:
    """美化輸出規劃結果。"""
    logger_obj.info(f"\n[{title}]")
    logger_obj.info(result.get("message", ""))
    if not result.get("ok", False):
        return
    steps = result.get("steps")
    if steps:
        for i, step in enumerate(steps, 1):
            cost_val = step.get('step_cost', 0.0)
            msg = f"  Step {i}: {step['type']} -> {step['pos']}  cost={cost_val}"
            logger_obj.info(msg)


def _build_item_plan(candidate: Dict[str, Any]) -> Dict[str, Any]:
    tool = candidate["tool"]
    target = candidate["target"]
    return {
        "ok": True,
        "mode": "item_candidate",
        "message": (
            f"Use {tool} at {target} "
            f"(visible pits={candidate.get('gain', 0)}, "
            f"savings={candidate.get('effective_savings', candidate.get('savings', 0.0)):.1f})"
        ),
        "steps": [
            {
                "type": "use",
                "item": tool,
                "pos": target,
                "action": f"use_{tool}",
                "target": target,
                "step_cost": 0.0,
                "gain": candidate.get("gain", 0),
                "savings": candidate.get("effective_savings", candidate.get("savings", 0.0)),
                "dig_list": candidate.get("pits", []),
            }
        ],
    }


def _normalize_signature_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


def _board_signature(board: List[List[str]]) -> Tuple[Tuple[str, ...], ...]:
    return tuple(tuple(cell for cell in row) for row in board)


def _step_signature(step: Dict[str, Any]) -> Tuple[Any, Any, Any, Any, Any]:
    return (
        step.get("type"),
        step.get("item"),
        _normalize_signature_value(step.get("pos")),
        _normalize_signature_value(step.get("target")),
        step.get("action"),
    )


def _count_planned_item_uses(steps: List[Dict[str, Any]]) -> Dict[str, int]:
    planned: Dict[str, int] = {}
    for step in steps:
        item_name: Optional[str] = None
        if step.get("type") == "use" and step.get("item"):
            item_name = str(step.get("item"))
        else:
            action = str(step.get("action", ""))
            if action.startswith("use_"):
                item_name = action.split("_", 1)[1]
        if item_name:
            planned[item_name] = planned.get(item_name, 0) + 1
    return planned


def _dispatch_planner(
    board: List[List[str]],
    shovels: float,
    items: Dict[str, int],
    blocked_actions: Set[Tuple[Any, ...]],
    planner_version: str,
    miner_logger,
    depth: Optional[int] = None,
    device: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """Route to the correct planner version and return (plan, plan_title)."""
    if planner_version == "v4":
        plan = plan_v4(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V4 規劃 (Miner V4, 3-step bounded)"
    if planner_version == "v3":
        plan = plan_v3(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V3 規劃 (Miner V3)"
    # v1 default (v2 removed 2026-06-05: violated <300ms on 18.8% of real boards)
    tool_candidate = find_tool_candidate(board, items_available=items) if USE_ITEMS else None
    if tool_candidate:
        miner_logger.info(
            "[MiningService] using item candidate: "
            f"{tool_candidate['tool']} at {tool_candidate['target']} "
            f"gain={tool_candidate.get('gain', 0)} "
            f"savings={tool_candidate.get('effective_savings', tool_candidate.get('savings', 0.0)):.1f}"
        )
        return _build_item_plan(tool_candidate), "智能規劃 (SmartPlanner)"
    return plan_smart(board, shovels=shovels, items=items), "智能規劃 (SmartPlanner)"


def _log_planner_stats(
    plan: Dict[str, Any],
    planner_version: str,
    plan_elapsed_ms: float,
    blocked_count: int,
    miner_logger,
    depth: Optional[int] = None,
) -> None:
    if planner_version not in {"v3", "v4"}:
        return
    depth_str = "?" if depth is None else str(depth)
    miner_logger.info(
        "[MiningService] planner=%s depth=%s calc_ms=%.3f result_ms=%s nodes=%s steps=%s strategy=%s"
        % (
            planner_version,
            depth_str,
            plan_elapsed_ms,
            plan.get("elapsed_ms", "?"),
            plan.get("explored_nodes", "?"),
            len(plan.get("steps", [])),
            plan.get("strategy_class", "?"),
        )
    )
    stats = plan.get("stats")
    if stats:
        miner_logger.info(f"[MiningService] planner={planner_version} stats={stats}")
    if blocked_count:
        miner_logger.info(
            "[MiningService] planner=%s blocked_actions=%s" % (planner_version, blocked_count)
        )


def _diagnose_empty_plan(board: List[List[str]], plan: Dict[str, Any], miner_logger) -> None:
    msg = plan.get("message", "????")
    miner_logger.warning(f"[Mining] 無挖礦步驟 ({msg})")
    miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}")
    miner_logger.warning(f"  底層開啟: {plan.get('floor7_open', '?')}")
    miner_logger.warning(f"  花費成本: {plan.get('total_cost', '?')}")

    air_count = sum(1 for row in board for cell in row if is_air(cell))
    unreachable_air = sum(
        1 for row in board for cell in row
        if is_air(cell) and isinstance(cell, str) and cell.startswith("unreachable_")
    )
    miner_logger.warning(f"  空氣識別統計:")
    miner_logger.warning(f"     - 可達空氣: {air_count - unreachable_air}")
    miner_logger.warning(f"     - 不可達空氣: {unreachable_air}")
    miner_logger.warning(f"     - 總空氣數: {air_count}")

    label_counts = collections.Counter(cell for row in board for cell in row)
    miner_logger.warning(f"  棋盤標籤分佈: {dict(label_counts)}")
    if air_count == 0:
        miner_logger.error("[Mining] 致命問題：棋盤找不到任何空氣！分類器可能有問題")


def _forced_descent_dig(board):
    """Pick the deepest reachable non-pit frontier cell so digging it drives
    the viewport toward a scroll. Used to escape a board where the only
    remaining pits are uncollectable (blacklisted / sealed-pocket reachable).
    Returns (r, c) or None when no diggable non-pit frontier exists."""
    from miner.v3.board import is_frontier_diggable, is_pit
    rows = len(board)
    cols = len(board[0]) if board else 0
    best = None  # (depth_row, col)
    for r in range(rows):
        for c in range(cols):
            if is_pit(board[r][c]):
                continue
            if is_frontier_diggable(board, r, c):
                if best is None or r > best[0]:
                    best = (r, c)
    return best


def _verify_items_pre_execution(
    d: u2.Device,
    plan: Dict[str, Any],
    items_available: Dict[str, int],
    item_blacklist: Set[str],
    zero_streaks: Dict[str, int],
    zero_streak_limit: int,
    miner_logger,
) -> bool:
    """Live-check item counts before execution. Returns True if replanning is needed."""
    planned_item_uses = _count_planned_item_uses(plan.get("steps", []))
    if not planned_item_uses:
        return False

    # web_h5: authoritative drill/bomb現量 from WS (0x0401); browser-screenshot
    # bomb OCR mis-reads 0. adb / WS-unavailable falls back to OCR.
    ws_counts = read_ws_prop_counts(d)
    if ws_counts is not None:
        live_counts = {"drill": ws_counts["drill"], "bomb": ws_counts["bomb"]}
    else:
        live_frame = d.screenshot(format="opencv")
        live_counts = {
            "drill": check_drill_num(d, frame=live_frame),
            "bomb": check_boom_num(d, frame=live_frame),
        }
    needs_replan = False
    for item_name, need_count in planned_item_uses.items():
        live_count = int(live_counts.get(item_name, 0))
        cached_count = int(items_available.get(item_name, 0))
        if live_count < cached_count:
            miner_logger.info(f"[ITEM STATUS] live 校正 {item_name}: {cached_count} -> {live_count}")
            items_available[item_name] = live_count
        if live_count < need_count:
            miner_logger.warning(
                f"[ITEM STATUS] 規劃需 {item_name} x{need_count}，但 live={live_count}，"
                "加入黑名單並重新規劃"
            )
            item_blacklist.add(item_name)
            items_available[item_name] = 0
            zero_streaks[item_name] = max(zero_streaks.get(item_name, 0), zero_streak_limit)
            needs_replan = True
    return needs_replan


def run(
    d: u2.Device,
    ip: str,
    clf: ClassifierCNN,
    rl_recorder: Optional[RLRecorder] = None,
    max_duration_minutes: float = 6.0,
) -> None:
    """主挖礦流程：截圖 → 分類 → 規劃 → 執行，並支援逾時與鏟子檢查。"""
    miner_logger = setup_miner_logger(ip)
    device_cfg = config_manager.get_device_config(ip)
    # Default planner is v1 (A*). Real-board eval at the recalibrated 3.6%
    # density (docs/.../2026-06-18-...top-pileup-fix.md): v1 is the most
    # shovel-efficient and highest-scoring of all planners; v3 is cluster-aware;
    # v4 is a bounded 3-step DFS. v5 (priors-driven) and v2 were removed.
    # Override per-device with `mining_planner_version` in config.
    planner_version = str(device_cfg.get("mining_planner_version", "v1")).strip().lower()
    mining_save_samples = bool(device_cfg.get("mining_save_samples", False))
    if planner_version not in {"v1", "v3", "v4"}:
        planner_version = "v1"

    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    miner_logger.info(f"[MiningService] 開始挖礦，時間限制: {max_duration_minutes} 分鐘 (設備 {ip})")
    miner_logger.info(f"[MiningService] planner_version={planner_version}")
    miner_logger.info(f"[MiningService] mining_save_samples={mining_save_samples}")

    count = check_pickaxe_count(d)
    if count < 5:
        miner_logger.info("鏟子數量過少，停止挖礦")
        return

    items_available: Dict[str, int] = {"drill": 0, "bomb": 0}
    item_blacklist: Set[str] = set()
    zero_streaks: Dict[str, int] = {"drill": 0, "bomb": 0}
    zero_streak_limit = 2

    def refresh_item_inventory(shared_frame=None) -> None:
        if not USE_ITEMS:
            items_available["drill"] = 0
            items_available["bomb"] = 0
            return
        # web_h5: read authoritative drill/bomb現量 from WS (0x0401). The browser
        # screenshot bomb OCR mis-reads 0, which blacklists bombs and kills the
        # drill->bomb combo (5554 live: bomb=930, OCR=0). adb / WS-unavailable
        # falls back to OCR.
        ws_counts = read_ws_prop_counts(d)
        if ws_counts is not None:
            item_counts = {"drill": ws_counts["drill"], "bomb": ws_counts["bomb"]}
        else:
            item_counts = {
                "drill": check_drill_num(d, frame=shared_frame),
                "bomb": check_boom_num(d, frame=shared_frame),
            }
        for name, value in item_counts.items():
            if name in item_blacklist:
                items_available[name] = 0
                continue
            items_available[name] = value
            if value == 0:
                zero_streaks[name] += 1
                miner_logger.info(
                    f"[ITEM STATUS] {name} OCR=0，連續 {zero_streaks[name]} 輪，當輪起停止規劃此道具"
                )
                if zero_streaks[name] >= zero_streak_limit:
                    item_blacklist.add(name)
                    miner_logger.info(f"[ITEM STATUS] {name} 已連續歸零，加入黑名單，後續不再計算此道具")
            else:
                if zero_streaks[name] > 0:
                    miner_logger.info(f"[ITEM STATUS] {name} 數量恢復為 {value}，解除歸零計數")
                zero_streaks[name] = 0

    # 同一個非法操作或無效道具在同版面下只會被嘗試一次；直到版面真的變化才清空。
    blocked_action_signatures: Set[Tuple[Any, Any, Any, Any, Any]] = set()
    last_board_signature: Optional[Tuple[Tuple[str, ...], ...]] = None

    # 追蹤本 session 捲動深度（純觀測 telemetry，寫進 miner.log / plan stats）。
    depth_tracker = DepthTracker()
    prev_board: Optional[List[List[str]]] = None

    iterations = 0
    consecutive_empty_plans = 0
    identical_board_count = 0
    prev_exec_sig = None
    while count >= 1:
        _check_force_sleep(ip)
        if time.time() - start_time > max_duration_seconds:
            miner_logger.info(f"[MiningService] 已達到時間限制 ({max_duration_minutes} 分鐘)，停止挖礦")
            break

        iterations += 1
        # Shared frame for this loop: pickaxe/item OCR + board classification.
        shared_frame = d.screenshot(format="opencv")
        # 礦洞彈窗在「挖到 pit」後可能被遮蓋，executor 的本地清理不一定夠 —
        # 每輪重新做一次 OCR 檢查並 click_white，避免主迴圈被卡在彈窗上。
        if _dismiss_mining_overlay_if_needed(d, shared_frame, miner_logger):
            shared_frame = d.screenshot(format="opencv")
        # Internal counter is the source of truth; OCR is a periodic
        # validator. `allow_none=True` lets us distinguish "OCR says X"
        # from "OCR is broken" — failure no longer pretends the count is
        # 20.
        if iterations % _PICKAXE_OCR_VALIDATE_EVERY == 0:
            ocr_count = check_pickaxe_count(d, frame=shared_frame, allow_none=True)
            new_count, kind = _reconcile_shovel_count(count, ocr_count)
            if kind == "ok":
                miner_logger.info(
                    f"[MiningService] 鏟子 OCR 驗證 ok: internal={count}, ocr={ocr_count}"
                )
            elif kind == "drift":
                drift = ocr_count - count
                if 0 < drift <= _PICKAXE_REWARD_DRIFT_MAX:
                    # 例行正向漂移：礦坑獎勵未計入內部計數。以 OCR 為準，記 INFO。
                    miner_logger.info(
                        f"[MiningService] 鏟子正向漂移 {drift:+d}（礦坑獎勵未計入內部計數）"
                        f", 以 OCR 為準: {count} -> {ocr_count}"
                    )
                else:
                    # 負向或過大正向漂移 = 真正 desync，維持 WARNING。
                    miner_logger.warning(
                        f"[MiningService] 鏟子漂移 {drift:+d} 超過容忍 "
                        f"±{_PICKAXE_DRIFT_TOLERANCE}, 以 OCR 為準: {count} -> {ocr_count}"
                    )
            else:  # ocr_unavailable
                miner_logger.info(
                    f"[MiningService] 鏟子 OCR 不可用, 沿用內部 count={count}"
                )
            count = new_count
            if count < 1:
                break

        refresh_item_inventory(shared_frame)
        miner_logger.info(
            f"[ITEM STATUS] items_available={items_available}, "
            f"zero_streaks={zero_streaks}, blacklist={sorted(item_blacklist)}"
        )
        _log_inventory_validation(ip, count, items_available, miner_logger)

        board, _ = clf.classify_board(shared_frame, save_samples=mining_save_samples)
        miner_logger.info(f"\n[MiningService] Current Board:\n{get_visual_board(board)}")
        _log_board_validation(ip, board, miner_logger)
        shifted = depth_tracker.update(board)
        if depth_tracker.last_uncertain:
            miner_logger.info(
                f"[MiningService] depth={depth_tracker.depth} (scroll uncertain)"
            )
        else:
            miner_logger.info(
                f"[MiningService] depth={depth_tracker.depth} (+{shifted})"
            )
        prev_board = board
        state_signature = _board_signature(board)
        if last_board_signature is not None and state_signature != last_board_signature and blocked_action_signatures:
            miner_logger.info("[MiningService] 版面已變化，清空非法操作封鎖清單")
            blocked_action_signatures.clear()
        last_board_signature = state_signature

        current_items = items_available.copy() if USE_ITEMS else {"drill": 0, "bomb": 0}
        plan_started_at = time.perf_counter()
        plan, plan_title = _dispatch_planner(
            board, count, current_items, blocked_action_signatures, planner_version, miner_logger,
            depth=depth_tracker.depth, device=ip,
        )
        plan_elapsed_ms = (time.perf_counter() - plan_started_at) * 1000.0
        _log_planner_stats(plan, planner_version, plan_elapsed_ms, len(blocked_action_signatures), miner_logger, depth_tracker.depth)

        _check_force_sleep(ip)

        if not plan.get("ok"):
            miner_logger.warning(f"[Mining] 規劃失敗: {plan.get('message', '未知錯誤')}")
            miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}, 底層開啟: {plan.get('floor7_open', '?')}")
            consecutive_empty_plans += 1
            if consecutive_empty_plans >= _MAX_EMPTY_PLANS:
                miner_logger.warning(
                    f"[MiningService] 連續 {consecutive_empty_plans} 次取得空 plan，中止挖礦迴圈"
                )
                break
            continue

        if not plan.get("steps"):
            _diagnose_empty_plan(board, plan, miner_logger)
            # If reachable pits remain but the planner can't collect them
            # (blacklisted / sealed-pocket reachable), don't just count toward
            # abort — scroll past them so mining continues productively.
            # NOTE: gate on remaining_pits, NOT `count` — `count` is the pickaxe
            # count (the loop condition is `while count >= 1`), so it is almost
            # always true and would NOT mean "pits remain".
            if int(plan.get("remaining_pits", 0) or 0) > 0:
                descent = _forced_descent_dig(board)
                if descent is not None:
                    miner_logger.warning(
                        f"[MiningService] 空 plan 但仍有礦無法採集，強制下挖 {descent} 推進下樓"
                    )
                    try:
                        descent_result = execute_plan_steps(
                            d, clf, board,
                            [{"type": "dig", "action": "dig", "dig_list": [descent],
                              "target": descent}],
                            rl_recorder=rl_recorder, deadline=start_time + max_duration_seconds,
                        )
                    except NoBoardChangeError as exc:
                        # even forced descent did nothing — fall through to abort
                        blocked_action_signatures.add(_step_signature(exc.step))
                    else:
                        # Credit the pickaxe(s) the forced dig consumed, like every
                        # other execute_plan_steps call — otherwise internal count
                        # drifts high until the next OCR reconcile.
                        count = _apply_partial(descent_result, count, items_available, miner_logger)
                        consecutive_empty_plans = 0
                        continue
            consecutive_empty_plans += 1
            if consecutive_empty_plans >= _MAX_EMPTY_PLANS:
                miner_logger.warning(
                    f"[MiningService] 連續 {consecutive_empty_plans} 次取得空 plan，中止挖礦迴圈"
                )
                break
            continue

        # 拿到 non-empty plan，歸零空 plan 計數
        consecutive_empty_plans = 0

        if _verify_items_pre_execution(
            d, plan, items_available, item_blacklist, zero_streaks, zero_streak_limit, miner_logger
        ):
            continue

        print_plan_result(miner_logger, plan_title, plan, board)
        deadline = start_time + max_duration_seconds
        _check_force_sleep(ip)
        try:
            exec_result = execute_plan_steps(
                d, clf, board, plan["steps"], rl_recorder=rl_recorder, deadline=deadline
            )
        except NoBoardChangeError as exc:
            count = _apply_partial(exc.partial_result, count, items_available, miner_logger)
            action_signature = _step_signature(exc.step)
            if exc.item_type:
                item_blacklist.add(exc.item_type)
                items_available[exc.item_type] = 0
                zero_streaks[exc.item_type] = max(zero_streaks.get(exc.item_type, 0), zero_streak_limit)
                miner_logger.warning(f"[MiningService] {exc.item_type} 使用後版面未變，加入黑名單直到下次挖礦重置")
            else:
                blocked_action_signatures.add(action_signature)
                miner_logger.warning(f"[MiningService] 鎬子操作後版面未變，將操作加入黑名單直到版面變化: {action_signature}")
            continue
        except OutOfItemError as exc:
            count = _apply_partial(exc.partial_result, count, items_available, miner_logger)
            item_blacklist.add(exc.item_type)
            items_available[exc.item_type] = 0
            zero_streaks[exc.item_type] = max(zero_streaks.get(exc.item_type, 0), zero_streak_limit)
            miner_logger.warning(
                f"[MiningService] live item check failed for {exc.item_type}: "
                f"count={exc.live_count}; blacklist for current mining run"
            )
            continue

        # Plan executed cleanly — credit shovels / items consumed.
        count = _apply_partial(exec_result, count, items_available, miner_logger)

        # Identical-state deadlock guard: if executing a non-empty plan left
        # the board unchanged for too many iterations in a row, abort instead
        # of spinning (Task A1 normally blacklists first; this is the backstop).
        identical_board_count, tripped = _identical_board_exceeded(
            state_signature, prev_exec_sig, identical_board_count
        )
        prev_exec_sig = state_signature
        if tripped:
            miner_logger.warning(
                f"[MiningService] 版面連續 {identical_board_count} 次無變化（非空 plan），"
                f"判定死結，中止挖礦迴圈"
            )
            break

    if rl_recorder:
        rl_recorder.flush()
        summary = rl_recorder.summary()
        print(f"\n[RL 記錄] 共 {summary['total']} 筆事件，檔案: {summary['log_path']}")



if __name__ == "__main__":  # pragma: no cover - manual trigger only
    ip = "adb-fc65396d-4LPqmI._adb-tls-connect._tcp"
    device = u2.connect(ip)
    model, classes, device_obj = load_cnn_model()
    classifier = ClassifierCNN(
        model=model,
        classes=classes,

        device=device_obj,
        dataset_root=DATASET_LOW_CONFIDENCE_DIR_STR,
    )
    run(device, ip, classifier)
    #python -m miner.mining_service

__all__ = [
    "run",
    "_dismiss_mining_overlay_if_needed",
    "print_plan_result",
    "execute_plan_steps",
    "check_pickaxe_count",
    "check_points",
    "DEFAULT_CLASSES",
]
