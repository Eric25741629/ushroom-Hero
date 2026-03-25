"""Mining service: screenshot -> classify -> plan -> execute loop."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple
import contextlib
import io
import copy

import uiautomator2 as u2

from miner.models.classifier import ClassifierCNN, load_cnn_model
from miner.planning.executor import execute_plan_steps
from miner.core.config import DEFAULT_CLASSES, HIT_TABLE
from miner.core.ocr_utils import check_pickaxe_count, check_drill_num, check_boom_num
from miner.planning.planner import (
    base_label,
    enter_cost,
    plan_min_cost_to_floor7,
)
from miner.planning.smart_planner import plan_smart
from miner.rl.rl_recorder import RLRecorder
from miner.core.vision_utils import check_points
from utils.logging_utils import logger, setup_miner_logger
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR

# Feature toggle: whether planner can use drill/bomb items.
USE_ITEMS: bool = True
def get_visual_board(board: List[List[str]]) -> str:
    """Render board as an easy-to-read text grid."""
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
    """Pretty-print planner output."""
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


def run(
    d: u2.Device,
    ip: str,
    clf: ClassifierCNN,
    rl_recorder: Optional[RLRecorder] = None,
    max_duration_minutes: float = 6.0,
) -> None:
    """Main mining workflow with timeout and shovel checks."""
    # ??????????Logger
    miner_logger = setup_miner_logger(ip)
    
    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    miner_logger.info(f"??? ????音?????? {max_duration_minutes} ?? (?獢?? {ip})")

    count = check_pickaxe_count(d)
    if count < 5:
        miner_logger.info("pickaxe count too low, stop mining")
        return

    items_available = {"drill": 0, "bomb": 0}
    # ...
    item_blacklist: set[str] = set()
    zero_streak_limit = 3

    def refresh_item_inventory(shared_frame=None) -> None:
        if not USE_ITEMS:
            items_available["drill"] = 0
            items_available["bomb"] = 0
            return
        latest_counts = {
            "drill": check_drill_num(d, frame=shared_frame),
            "bomb": check_boom_num(d, frame=shared_frame),
        }
        for name, value in latest_counts.items():
            if name in item_blacklist:
                items_available[name] = 0
                continue
            items_available[name] = value
            if value == 0:
                # ...
                pass

    def _log_item_status() -> None:
        miner_logger.info(f"[ITEM STATUS] items_available={items_available}")

    history_states = []
    history_actions = []
    stuck_limit = 3

    retry_count = 0
    iterations = 0
    while count >= 1:
        if time.time() - start_time > max_duration_seconds:
            miner_logger.info(f"time limit reached ({max_duration_minutes} min), stop mining")
            break

        iterations += 1
        # Shared frame for this loop: pickaxe/item OCR + board classification.
        shared_frame = d.screenshot(format="opencv")
        if iterations % 3 == 0:
            real_count = check_pickaxe_count(d, frame=shared_frame)
            miner_logger.info(f"?? ????鈭亦腦?????: {count:.1f} -> {real_count}")
            count = real_count
            if count < 1:
                break

        refresh_item_inventory(shared_frame)
        _log_item_status()

        # ...
        board, _ = clf.classify_board(shared_frame, save_samples=True)
        board_str = get_visual_board(board)
        miner_logger.info(f"\n[MiningService] Current Board:\n{board_str}")

        current_items = items_available.copy() if USE_ITEMS else {'drill': 0, 'bomb': 0}
        plan = plan_smart(board, shovels=count, items=current_items)

        if not plan.get("ok"):
            miner_logger.warning(f"????????: {plan.get('message', '??????')}")
            miner_logger.warning(f"  ?????? {plan.get('remaining_pits', '?')}, ?????: {plan.get('floor7_open', '?')}")
            continue
        
        if not plan.get("steps"):
            # ????? - ???????∟??
            msg = plan.get('message', '????')
            miner_logger.warning(f"??  ????????({msg})")
            miner_logger.warning(f"  ?????? {plan.get('remaining_pits', '?')}")
            miner_logger.warning(f"  ?????: {plan.get('floor7_open', '?')}")
            miner_logger.warning(f"  ???摮?: {plan.get('total_cost', '?')}")
            
            from miner.planning.planner import is_empty as is_air
            R, C = len(board), len(board[0])
            air_count = sum(1 for row in board for cell in row if is_air(cell))
            unreachable_air = sum(1 for row in board for cell in row if is_air(cell) and isinstance(cell, str) and cell.startswith("unreachable_"))
            reachable_air = air_count - unreachable_air
            
            miner_logger.warning(f"  ?? ???????嚗?:")
            miner_logger.warning(f"     - ??????? {reachable_air}")
            miner_logger.warning(f"     - ????? {unreachable_air}")
            miner_logger.warning(f"     - ??????: {air_count}")
            
            # ??????????
            import collections
            label_counts = collections.Counter()
            for row in board:
                for cell in row:
                    label_counts[cell] += 1
            miner_logger.warning(f"  ?? ?????: {dict(label_counts)}")
            
            if air_count == 0:
                miner_logger.error("fatal: no air cell found in board classification")
            
            continue

        # ... (???????? ...
        if len(history_states) == stuck_limit:
            if len(set(history_states)) == 1 and len(set(history_actions)) == 1:
                # ...
                miner_logger.error("detected stuck loop")
                break

        print_plan_result(miner_logger, "??? (SmartPlanner)", plan, board)
        deadline = start_time + max_duration_seconds
        execute_plan_steps(d, clf, board, plan["steps"], rl_recorder=rl_recorder, deadline=deadline)
        # ...



    if rl_recorder:
        rl_recorder.flush()
        summary = rl_recorder.summary()
        print(f"\n[RL ??] ??{summary['total']} ???????: {summary['log_path']}")



def demo_plan_print(board: List[List[str]]) -> None:
    """Demo helper to print multiple planning strategies."""
    result_a = plan_min_cost_to_floor7(board)
    print_plan_result("min_cost_to_floor7", result_a, board)

    result_b = plan_greedy_with_rewards(board)
    print_plan_result("greedy_with_rewards", result_b, board)

    result_c = plan_collect_all_mines_then_descend_v2(board)
    print_plan_result("collect_all_then_descend", result_c, board)


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
    "print_plan_result",
    "demo_plan_print",
    "execute_plan_steps",
    "check_pickaxe_count",
    "check_points",
    "DEFAULT_CLASSES",
]
