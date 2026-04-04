"""將規劃結果轉換為實際點擊操作的相關函式。"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # 僅在型別檢查時才載入，避免循環匯入與硬性依賴
    import uiautomator2 as uiauto

    DeviceLike = uiauto.Device
    from miner.models.classifier import ClassifierCNN
else:  # pragma: no cover - 測試環境僅需輕量別名
    DeviceLike = Any

from miner.core.config import GRID_CFG, HIT_TABLE
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells
class ItemPlacementError(Exception):
    pass
from .planner import base_label, enter_cost
PLACEABLE_MATERIALS = {"empty", "dug_pit"}

from miner.core.vision_utils import check_points
from miner.rl.rl_recorder import RLRecorder
from tools import click_white

Coordinate = Tuple[int, int]


# ===== 道具按鈕座標（請依實機調整） =====
# 下面是範例值，建議你實際在模擬器量一次，再改成正確座標。
DRILL_BTN_XY: Coordinate = (160, 910)    # 左邊鑽頭按鈕
PICKAXE_BTN_XY: Coordinate = (270, 910)  # 中間鏟子按鈕
BOMB_BTN_XY: Coordinate = (370, 910)     # 右邊炸彈按鈕


def cell_center_xy(r: int, c: int) -> Coordinate:
    """計算盤面第 r 列、第 c 欄格子的中心點座標。"""
    H, W = GRID_CFG["H"], GRID_CFG["W"]
    x0, y0, x1, y1 = GRID_CFG["x0"], GRID_CFG["y0"], GRID_CFG["x1"], GRID_CFG["y1"]
    cell_w = int(round((x1 - x0) / W))
    cell_h = int(round((y1 - y0) / H))
    cx = x0 + c * cell_w + cell_w // 2
    cy = y0 + r * cell_h + cell_h // 2
    return cx, cy


def material_of(label: str) -> str:
    return base_label(label)


def required_hits(label: str) -> int:
    return HIT_TABLE.get(material_of(label), 0)


def is_placeable_label(label: str) -> bool:
    """道具只能放在空地（empty/dug_pit）。

    Use base_label so that 'unreachable_empty' is treated as 'empty',
    keeping planner and executor consistent.
    """
    return base_label(label) in PLACEABLE_MATERIALS


def tap_cell(d: DeviceLike, r: int, c: int, hits: int, wait_ms: int = 150) -> None:
    """在指定格子重複點擊指定次數。"""
    x, y = cell_center_xy(r, c)
    for _ in range(hits):
        d.click(x + random.randint(-10, 10), y + random.randint(-10, 10))
        d.sleep(wait_ms / 1000.0)
    time.sleep(0.5 + random.random() * 0.5)


def select_item(d: DeviceLike, item_type: str) -> None:
    """選取炸彈/鑽頭/鏟子按鈕。

    目前版本遊戲會在道具放置完成後自動切回鏟子，
    因此只需要在放置前切換一次即可。

    item_type 目前預期為 "drill"、"bomb" 或 "pickaxe"。
    """
    if item_type == "drill":
        x, y = DRILL_BTN_XY
    elif item_type == "bomb":
        x, y = BOMB_BTN_XY
    elif item_type == "pickaxe":
        x, y = PICKAXE_BTN_XY
    else:
        print(f"[WARN] 未知道具 {item_type}，不切換")
        return

    print(f"[select_item] 切換道具為 {item_type}，點擊座標=({x},{y})")
    d.click(x, y)
    # 動畫稍長，適度多等一下，避免誤觸
    d.sleep(0.5)


def verify_cell_empty(
    d: DeviceLike,
    clf: "ClassifierCNN",
    r: int,
    c: int,
    max_retry: int = 3,
    error_threshold: float = 0.9,
) -> bool:
    """重新截圖後確認指定格子是否成功被挖空。

    遊戲規則上 dug_pit 等同於 empty，可視為已挖空。
    因此這裡接受 base_label in {"empty", "dug_pit"} 都算成功，
    避免挖完礦洞後又多點一次。
    """
    for _ in range(max_retry):
        passed, _ = check_points(d.screenshot(format="opencv"))
        if passed:
            d.click(444 + random.randint(-10, 10), 107 + random.randint(-10, 10))
            continue
        img2 = d.screenshot()
        board2, confidences2 = clf.classify_board(img2, save_samples=False)
        confidence = confidences2[r][c]
        if confidence < error_threshold:
            # 使用者回饋：信心度低於閾值時，不要點擊空白處，而是儲存錯誤樣本
            print(f"    ⚠️ 信心度過低 ({r},{c}): {confidence:.4f} < {error_threshold}")
            # 如果信心度介於 0.6 到 0.8 之間，儲存樣本 (由 ClassifierCNN.classify_board 負責，
            # 這裡只需要呼叫，但目前 verify_cell_empty 的 classify_board 參數 save_samples=False)
            # 因此這裡我們選擇忽略點擊空白處的行為。
            continue
        new_label = base_label(board2[r][c])
        if new_label in ("empty", "dug_pit"):
            return True
    return False


def execute_plan_steps(
    d: DeviceLike,
    clf: "ClassifierCNN",
    board: List[List[str]],
    steps: List[Dict[str, Any]],
    rl_recorder: Optional[RLRecorder] = None,
    deadline: Optional[float] = None,
) -> None:
    """逐步執行規劃結果；支援挖路、採礦、下樓與道具使用。"""
    try:
        for i, step in enumerate(steps, 1):
            if deadline and time.time() > deadline:
                print(f"    [Executor] 超時 (deadline={deadline})，停止執行剩餘步驟")
                return

            # Normalize SmartPlanner output to match Executor expectations
            if "type" in step and "pos" in step:
                step["target"] = step["pos"]
                if step["type"] == "use":
                    step["action"] = f"use_{step['item']}"
                elif step["type"] == "dig":
                    step["action"] = "dig"
                    step["dig_list"] = [step["pos"]]

            msg = f"\n[執行 Step {i}] {step['action']} -> {step['target']}  預期成本={step.get('step_cost', 0)}  預期收益={step.get('gain')}"
            if "savings" in step:
                msg += f"  savings={step['savings']}"
            print(msg)

            if step["action"].startswith("use_"):
                item_type = step["action"].split("_", 1)[1]
                r, c = step["target"]
                target_label = board[r][c]
                if not is_placeable_label(target_label):
                    print(
                        f"    ⚠️ 無法在 ({r},{c}) 放置 {item_type}，目前格子為 {target_label}，僅允許 empty/dug_pit，停止並重新規劃"
                    )
                    return
                select_item(d, item_type)
                print(f"  - 於 ({r},{c}) 使用 {item_type}")
                tap_cell(d, r, c, 1, wait_ms=500)
                # 標記受影響區域 unreachable_pit -> dug_pit，reachable_pit -> empty，其他障礙 -> empty (粗略更新以利後續規劃更快)
                H, W = GRID_CFG["H"], GRID_CFG["W"]
                affected = (
                    get_bomb_affected_cells(r, c, H, W)
                    if item_type == "bomb"
                    else get_drill_affected_cells(r, c, H, W)
                )
                hit_pit = False
                for (ar,ac) in affected:
                    lbl = board[ar][ac]
                    if "pit" in lbl and "dug" not in lbl:
                        hit_pit = True
                    if lbl == "reachable_pit" or lbl == "unreachable_pit":
                        # 直接挖開得獎勵（獎勵已在規劃階段計入），標記 empty
                        board[ar][ac] = "empty"
                    elif base_label(lbl) not in ("empty","dug_pit","pit","void"):
                        board[ar][ac] = "empty"
                
                if hit_pit:
                    print(f"    [Executor] 道具 {item_type} 炸到礦洞，執行兩次確認點擊 + 兩次點空白處")
                    d.click(394, 152)
                    time.sleep(0.3)
                    d.click(394, 152)
                    time.sleep(0.3)
                    click_white(d)
                    click_white(d)
                    time.sleep(0.5)

                print("    使用道具後更新局部盤面(含 unreachable_pit->empty)，停止執行，將重新規劃")
                time.sleep(2.5)
                return

            step_board_before = [row[:] for row in board]
            cell_events: List[Dict[str, Any]] = []
            for (r, c) in step["dig_list"]:
                label = board[r][c]
                hits = required_hits(label)
                print(f"  - 挖 ({r},{c}) {label} → 需要點擊 {hits} 次")
                cell_event: Dict[str, Any] = {
                    "row": r,
                    "col": c,
                    "label_before": label,
                    "material": material_of(label),
                    "required_hits": hits,
                    "enter_cost": enter_cost(label),
                }
                if hits > 0:
                    tap_cell(d, r, c, hits, wait_ms=1000)
                    if "pit" in label and "dug" not in label:
                        print(f"    [Executor] 挖掘礦洞 ({r},{c})，執行兩次確認點擊 (394, 152)")
                        d.click(394, 152)
                        time.sleep(0.3)
                        d.click(394, 152)
                        time.sleep(0.5)
                    check_points(d.screenshot(format="opencv"))

                if r < 6:
                    success = verify_cell_empty(d, clf, r, c, max_retry=2)
                    if not success:
                        print(f"    驗證未成功，補點一次 ({r},{c})")
                        tap_cell(d, r, c, 1)
                        success = verify_cell_empty(d, clf, r, c, max_retry=1)
                    cell_event["verify_success"] = success
                    if not success:
                        print(f"    ⚠️ 挖掘驗證失敗 ({r},{c})，停止執行剩餘步驟")
                        cell_events.append(cell_event)
                        if rl_recorder:
                            rl_recorder.record_transition(
                                {
                                    "step_index": i,
                                    "plan_action": step["action"],
                                    "target": step["target"],
                                    "step_cost_expected": step.get("step_cost"),
                                    "gain_expected": step.get("gain"),
                                    "cell_events": cell_events,
                                    "board_before": step_board_before,
                                    "board_after": None,
                                    "terminated": "verify_fail",
                                }
                            )
                        return
                else:
                    print(f"    第七層格子 ({r},{c}) 跳過驗證")
                    print(f"    ⚠️ 觸發下樓，停止執行剩餘路徑，請重新規劃")
                    cell_event["verify_success"] = True
                    cell_events.append(cell_event)
                    if rl_recorder:
                        rl_recorder.record_transition(
                            {
                                "step_index": i,
                                "plan_action": step["action"],
                                "target": step["target"],
                                "step_cost_expected": step.get("step_cost"),
                                "gain_expected": step.get("gain"),
                                "cell_events": cell_events,
                                "board_before": step_board_before,
                                "board_after": None,
                                "terminated": "floor7",
                            }
                        )
                    return
                cell_events.append(cell_event)

            img2 = d.screenshot()
            board, _ = clf.classify_board(img2, save_samples=False)
            if rl_recorder:
                rl_recorder.record_transition(
                    {
                        "step_index": i,
                        "plan_action": step["action"],
                        "target": step["target"],
                        "step_cost_expected": step.get("step_cost"),
                        "gain_expected": step.get("gain"),
                        "cell_events": cell_events,
                        "board_before": step_board_before,
                        "board_after": [row[:] for row in board],
                    }
                )
    except ItemPlacementError:
        raise
    except Exception as exc:  # pragma: no cover - 方便偵錯
        import sys

        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = exc_tb.tb_frame.f_code.co_filename if exc_tb else "<unknown>"
        line = exc_tb.tb_lineno if exc_tb else -1
        print(f"[執行錯誤] {fname} 第 {line} 行: {exc}")
        raise


__all__ = [
    "cell_center_xy",
    "material_of",
    "required_hits",
    "tap_cell",
    "verify_cell_empty",
    "execute_plan_steps",
]
