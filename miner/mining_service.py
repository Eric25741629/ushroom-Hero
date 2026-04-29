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
from miner.planning.executor import NoBoardChangeError, OutOfItemError, execute_plan_steps
from miner.core.config import DEFAULT_CLASSES, HIT_TABLE
from miner.core.ocr_utils import check_pickaxe_count, check_drill_num, check_boom_num
from miner.planning.item_planner import find_tool_candidate
from miner.planning.planner import (
    base_label,
    enter_cost,
    is_empty as is_air,
    plan_min_cost_to_floor7,
)
from miner.planning.smart_planner import plan_smart
from miner.v2.planner import plan_v2
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4
from miner.rl.rl_recorder import RLRecorder
from miner.core.vision_utils import check_points
from utils.logging_utils import logger, setup_miner_logger
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR
from tools import click_white
from runtime_services.device_runtime_service import ForceSleepRequested

# 功能開關：規劃器是否允許使用鑽頭/炸彈道具。
USE_ITEMS: bool = True


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
) -> Tuple[Dict[str, Any], str]:
    """Route to the correct planner version and return (plan, plan_title)."""
    if planner_version == "v4":
        plan = plan_v4(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V4 規劃 (Miner V4, 5-step bounded)"
    if planner_version == "v3":
        plan = plan_v3(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V3 規劃 (Miner V3)"
    if planner_version == "v2":
        plan = plan_v2(board, shovels=shovels, items=items, blocked_actions=blocked_actions)
        return plan, "V2 規劃 (Miner V2)"
    # v1 default
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
) -> None:
    if planner_version not in {"v2", "v3", "v4"}:
        return
    miner_logger.info(
        "[MiningService] planner=%s calc_ms=%.3f result_ms=%s nodes=%s steps=%s strategy=%s"
        % (
            planner_version,
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
    # Default planner is v4 (planner-eval skill 2026-04-29 — v4 leads v1/v3
    # on every cluster-completion metric while staying under the 300 ms wall
    # budget). Override per-device with `mining_planner_version` in config.
    planner_version = str(device_cfg.get("mining_planner_version", "v4")).strip().lower()
    mining_save_samples = bool(device_cfg.get("mining_save_samples", False))
    if planner_version not in {"v1", "v2", "v3", "v4"}:
        planner_version = "v4"

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
        for name, value in {
            "drill": check_drill_num(d, frame=shared_frame),
            "bomb": check_boom_num(d, frame=shared_frame),
        }.items():
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

    iterations = 0
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
        if iterations % 3 == 0:
            real_count = check_pickaxe_count(d, frame=shared_frame)
            miner_logger.info(f"[MiningService] 定期校正鏟子數量: {count:.1f} -> {real_count}")
            count = real_count
            if count < 1:
                break

        refresh_item_inventory(shared_frame)
        miner_logger.info(
            f"[ITEM STATUS] items_available={items_available}, "
            f"zero_streaks={zero_streaks}, blacklist={sorted(item_blacklist)}"
        )

        board, _ = clf.classify_board(shared_frame, save_samples=mining_save_samples)
        miner_logger.info(f"\n[MiningService] Current Board:\n{get_visual_board(board)}")
        state_signature = _board_signature(board)
        if last_board_signature is not None and state_signature != last_board_signature and blocked_action_signatures:
            miner_logger.info("[MiningService] 版面已變化，清空非法操作封鎖清單")
            blocked_action_signatures.clear()
        last_board_signature = state_signature

        current_items = items_available.copy() if USE_ITEMS else {"drill": 0, "bomb": 0}
        plan_started_at = time.perf_counter()
        plan, plan_title = _dispatch_planner(
            board, count, current_items, blocked_action_signatures, planner_version, miner_logger
        )
        plan_elapsed_ms = (time.perf_counter() - plan_started_at) * 1000.0
        _log_planner_stats(plan, planner_version, plan_elapsed_ms, len(blocked_action_signatures), miner_logger)

        _check_force_sleep(ip)

        if not plan.get("ok"):
            miner_logger.warning(f"[Mining] 規劃失敗: {plan.get('message', '未知錯誤')}")
            miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}, 底層開啟: {plan.get('floor7_open', '?')}")
            continue

        if not plan.get("steps"):
            _diagnose_empty_plan(board, plan, miner_logger)
            continue

        if _verify_items_pre_execution(
            d, plan, items_available, item_blacklist, zero_streaks, zero_streak_limit, miner_logger
        ):
            continue

        print_plan_result(miner_logger, plan_title, plan, board)
        deadline = start_time + max_duration_seconds
        _check_force_sleep(ip)
        try:
            execute_plan_steps(d, clf, board, plan["steps"], rl_recorder=rl_recorder, deadline=deadline)
        except NoBoardChangeError as exc:
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
            item_blacklist.add(exc.item_type)
            items_available[exc.item_type] = 0
            zero_streaks[exc.item_type] = max(zero_streaks.get(exc.item_type, 0), zero_streak_limit)
            miner_logger.warning(
                f"[MiningService] live item check failed for {exc.item_type}: "
                f"count={exc.live_count}; blacklist for current mining run"
            )
            continue



    if rl_recorder:
        rl_recorder.flush()
        summary = rl_recorder.summary()
        print(f"\n[RL 記錄] 共 {summary['total']} 筆事件，檔案: {summary['log_path']}")



def demo_plan_print(board: List[List[str]]) -> None:
    """示範輸出規劃結果，方便除錯。"""
    result_a = plan_min_cost_to_floor7(board)
    print_plan_result(logger, "最小鏟子成本", result_a, board)


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
    "demo_plan_print",
    "execute_plan_steps",
    "check_pickaxe_count",
    "check_points",
    "DEFAULT_CLASSES",
]
