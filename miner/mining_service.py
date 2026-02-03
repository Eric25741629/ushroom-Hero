"""協調各模組：負責實際挖礦流程、規劃列印與 CLI 入口。"""
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

# 若為 False，整個程式會忽略道具（炸彈/鑽頭）相關的規劃與使用，僅使用鏟子
USE_ITEMS: bool = True  # 改為 True 以啟用新演算法的道具功能

def get_visual_board(board: List[List[str]]) -> str:
    """將 board 轉為視覺化的符號字串，增加座標軸"""
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
    """在終端機列出規劃結果，方便人工檢查。"""
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
    """主流程：重複截圖→分類→規劃→執行，直到鏟子或時間耗盡。"""
    # 初始化挖礦專屬 Logger
    miner_logger = setup_miner_logger(ip)
    
    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    miner_logger.info(f"⏱️ 開始挖礦，時間限制: {max_duration_minutes} 分鐘 (設備 {ip})")

    count = check_pickaxe_count(d)
    if count < 5:
        miner_logger.info("鏟子數量過少，停止挖礦")
        return

    # 道具庫存與備援機制
    items_available = {"drill": 0, "bomb": 0}
    # ...
    item_blacklist: set[str] = set()
    zero_streak_limit = 3

    def refresh_item_inventory() -> None:
        if not USE_ITEMS:
            items_available["drill"] = 0
            items_available["bomb"] = 0
            return
        latest_counts = {
            "drill": check_drill_num(d),
            "bomb": check_boom_num(d),
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
            miner_logger.info(f"⏳ 已達到時間限制 ({max_duration_minutes} 分鐘)，停止挖礦")
            break

        iterations += 1
        if iterations % 3 == 0:
            real_count = check_pickaxe_count(d)
            miner_logger.info(f"🔄 定期校正鏟子數量: {count:.1f} -> {real_count}")
            count = real_count
            
            # 定期更新道具數量
            refresh_item_inventory()
            _log_item_status()
            
            if count < 1:
                break

        refresh_item_inventory()
        _log_item_status()

        # ...
        img_pillow = d.screenshot()
        board, _ = clf.classify_board(img_pillow, save_samples=True)
        board_str = get_visual_board(board)
        miner_logger.info(f"\n[MiningService] Current Board:\n{board_str}")

        current_items = items_available.copy() if USE_ITEMS else {'drill': 0, 'bomb': 0}
        plan = plan_smart(board, shovels=count, items=current_items)

        if not plan.get("ok") or not plan.get("steps"):
            # ...
            miner_logger.info("無規劃或已完成")
            continue

        # ... (死循環偵測) ...
        if len(history_states) == stuck_limit:
            if len(set(history_states)) == 1 and len(set(history_actions)) == 1:
                # ...
                miner_logger.error("❌ 偵測到死循環！")
                break

        print_plan_result(miner_logger, "智能規劃 (SmartPlanner)", plan, board)
        deadline = start_time + max_duration_seconds
        execute_plan_steps(d, clf, board, plan["steps"], rl_recorder=rl_recorder, deadline=deadline)
        # ...



    if rl_recorder:
        rl_recorder.flush()
        summary = rl_recorder.summary()
        print(f"\n[RL 記錄] 共 {summary['total']} 筆事件，檔案: {summary['log_path']}")



def demo_plan_print(board: List[List[str]]) -> None:
    """示範如何輸出不同規劃結果，方便除錯。"""
    result_a = plan_min_cost_to_floor7(board)
    print_plan_result("最小鏟子成本", result_a, board)

    result_b = plan_greedy_with_rewards(board)
    print_plan_result("貪婪採礦", result_b, board)

    result_c = plan_collect_all_mines_then_descend_v2(board)
    print_plan_result("收集所有礦", result_c, board)


if __name__ == "__main__":  # pragma: no cover - 手動觸發用
    ip = "adb-fc65396d-4LPqmI._adb-tls-connect._tcp"
    device = u2.connect(ip)
    model, classes, device_obj = load_cnn_model()
    classifier = ClassifierCNN(
        model=model,
        classes=classes,

        device=device_obj,
        dataset_root="dataset/low_confidence",
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
