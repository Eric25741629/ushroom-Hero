"""Mining service: screenshot -> classify -> plan -> execute loop."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple
import contextlib
import io
import copy

import uiautomator2 as u2
import img_tools
import bot_state

from miner.models.classifier import ClassifierCNN, load_cnn_model
from miner.planning.executor import execute_plan_steps
from miner.core.config import DEFAULT_CLASSES, HIT_TABLE
from miner.core.ocr_utils import check_pickaxe_count, check_drill_num, check_boom_num
from miner.planning.item_planner import find_tool_candidate
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


def plan_greedy_with_rewards(board: List[List[str]]) -> Dict[str, Any]:
    """相容性包裝：舊 demo 若還在呼叫時，回退到最小成本規劃。"""
    return plan_min_cost_to_floor7(board)


def plan_collect_all_mines_then_descend_v2(board: List[List[str]]) -> Dict[str, Any]:
    """相容性包裝：舊 demo 若還在呼叫時，回退到最小成本規劃。"""
    return plan_min_cost_to_floor7(board)


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


def run(
    d: u2.Device,
    ip: str,
    clf: ClassifierCNN,
    rl_recorder: Optional[RLRecorder] = None,
    max_duration_minutes: float = 6.0,
) -> None:
    """主挖礦流程：截圖 → 分類 → 規劃 → 執行，並支援逾時與鏟子檢查。"""
    # 建立挖礦專屬 Logger
    miner_logger = setup_miner_logger(ip)
    
    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    miner_logger.info(f"⏱️ 開始挖礦，時間限制: {max_duration_minutes} 分鐘 (設備 {ip})")

    count = check_pickaxe_count(d)
    if count < 5:
        miner_logger.info("鏟子數量過少，停止挖礦")
        return

    items_available = {"drill": 0, "bomb": 0}
    # 道具一旦連續多輪 OCR 為 0，就直接停止再算那個道具，省掉無效規劃。
    item_blacklist: set[str] = set()
    zero_streaks = {"drill": 0, "bomb": 0}
    zero_streak_limit = 2

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
                zero_streaks[name] += 1
                miner_logger.info(
                    f"[ITEM STATUS] {name} OCR=0，連續 {zero_streaks[name]} 輪，當輪起停止規劃此道具"
                )
                if zero_streaks[name] >= zero_streak_limit:
                    item_blacklist.add(name)
                    miner_logger.info(
                        f"[ITEM STATUS] {name} 已連續歸零，加入黑名單，後續不再計算此道具"
                    )
            else:
                if zero_streaks[name] > 0:
                    miner_logger.info(f"[ITEM STATUS] {name} 數量恢復為 {value}，解除歸零計數")
                zero_streaks[name] = 0

    def _log_item_status() -> None:
        miner_logger.info(
            f"[ITEM STATUS] items_available={items_available}, "
            f"zero_streaks={zero_streaks}, blacklist={sorted(item_blacklist)}"
        )

    history_states = []
    history_actions = []
    stuck_limit = 3

    retry_count = 0
    iterations = 0
    overlay_check_attempted = False
    while count >= 1:
        _check_force_sleep(ip)
        if time.time() - start_time > max_duration_seconds:
            miner_logger.info(f"⏳ 已達到時間限制 ({max_duration_minutes} 分鐘)，停止挖礦")
            break

        iterations += 1
        # Shared frame for this loop: pickaxe/item OCR + board classification.
        shared_frame = d.screenshot(format="opencv")
        if not overlay_check_attempted:
            overlay_check_attempted = True
            if _dismiss_mining_overlay_if_needed(d, shared_frame, miner_logger):
                shared_frame = d.screenshot(format="opencv")
        if iterations % 3 == 0:
            real_count = check_pickaxe_count(d, frame=shared_frame)
            miner_logger.info(f"🔄 定期校正鏟子數量: {count:.1f} -> {real_count}")
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
        tool_candidate = find_tool_candidate(board, items_available=current_items) if USE_ITEMS else None
        if tool_candidate:
            miner_logger.info(
                "[MiningService] using item candidate: "
                f"{tool_candidate['tool']} at {tool_candidate['target']} "
                f"gain={tool_candidate.get('gain', 0)} "
                f"savings={tool_candidate.get('effective_savings', tool_candidate.get('savings', 0.0)):.1f}"
            )
            plan = _build_item_plan(tool_candidate)
        else:
            plan = plan_smart(board, shovels=count, items=current_items)

        _check_force_sleep(ip)

        if not plan.get("ok"):
            miner_logger.warning(f"❌ 規劃失敗: {plan.get('message', '未知錯誤')}")
            miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}, 底層開啟: {plan.get('floor7_open', '?')}")
            continue
        
        if not plan.get("steps"):
            # 沒有步驟時，輸出詳細診斷資訊
            msg = plan.get('message', '????')
            miner_logger.warning(f"⚠️  無挖礦步驟 ({msg})")
            miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}")
            miner_logger.warning(f"  底層開啟: {plan.get('floor7_open', '?')}")
            miner_logger.warning(f"  花費成本: {plan.get('total_cost', '?')}")
            
            from miner.planning.planner import is_empty as is_air
            R, C = len(board), len(board[0])
            air_count = sum(1 for row in board for cell in row if is_air(cell))
            unreachable_air = sum(1 for row in board for cell in row if is_air(cell) and isinstance(cell, str) and cell.startswith("unreachable_"))
            reachable_air = air_count - unreachable_air
            
            miner_logger.warning(f"  📊 空氣識別統計:")
            miner_logger.warning(f"     - 可達空氣: {reachable_air}")
            miner_logger.warning(f"     - 不可達空氣: {unreachable_air}")
            miner_logger.warning(f"     - 總空氣數: {air_count}")
            
            # 列出所有不同的標籤類型
            import collections
            label_counts = collections.Counter()
            for row in board:
                for cell in row:
                    label_counts[cell] += 1
            miner_logger.warning(f"  📋 棋盤標籤分佈: {dict(label_counts)}")
            
            if air_count == 0:
                miner_logger.error("❌ 致命問題：棋盤找不到任何空氣！分類器可能有問題")
            
            continue

        # ...（死循環偵測）...
        if len(history_states) == stuck_limit:
            if len(set(history_states)) == 1 and len(set(history_actions)) == 1:
                # ...
                miner_logger.error("❌ 偵測到死循環！")
                break

        print_plan_result(miner_logger, "智能規劃 (SmartPlanner)", plan, board)
        deadline = start_time + max_duration_seconds
        _check_force_sleep(ip)
        execute_plan_steps(d, clf, board, plan["steps"], rl_recorder=rl_recorder, deadline=deadline)
        # ...



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
