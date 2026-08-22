"""將規劃結果轉換為實際點擊操作的相關函式。"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:  # 僅在型別檢查時才載入，避免循環匯入與硬性依賴
    import uiautomator2 as uiauto

    DeviceLike = uiauto.Device
    from miner.models.classifier import ClassifierCNN
else:  # pragma: no cover - 測試環境僅需輕量別名
    DeviceLike = Any

from miner.core.config import GRID_CFG, HIT_TABLE
from miner.core.mechanics import get_bomb_affected_cells, get_drill_affected_cells


@dataclass
class ExecutionResult:
    """Resource accounting for one ``execute_plan_steps`` invocation.

    The mining loop uses this to decrement its internal pickaxe / item
    counters incrementally, so OCR can be downgraded from authoritative
    source to validator.

    Fields
    ------
    shovels_used:
        Total shovel cost of cells where the executor actually issued
        ``tap_cell`` (regardless of whether classify_board later
        confirmed the cell as empty — the game consumed the shovel as
        soon as the click landed). Equals ``enter_cost(label_before)``
        summed across attempted cells.
    drills_used / bombs_used:
        Count of item activations dispatched (0 or 1 per call — the
        executor returns after each item use so the planner can re-plan
        from the new board).
    steps_completed:
        Number of plan steps that ran to completion. For dig steps that
        terminated via floor7 / verify_fail this counts the partially-
        completed step.
    terminated_reason:
        ``None`` if the executor ran the full plan, otherwise one of
        ``"deadline"``, ``"floor7"``, ``"verify_fail"``,
        ``"item_placement_invalid"``, ``"item_used"``,
        ``"no_board_change"``, ``"out_of_item"``.
    """

    shovels_used: int = 0
    drills_used: int = 0
    bombs_used: int = 0
    steps_completed: int = 0
    terminated_reason: Optional[str] = None
    pickaxe_count_after: Optional[int] = None
    verification_events: List[Dict[str, Any]] = field(default_factory=list)


class ItemPlacementError(Exception):
    pass


class OutOfItemError(Exception):
    def __init__(
        self,
        item_type: str,
        live_count: int,
        partial_result: Optional[ExecutionResult] = None,
    ):
        self.item_type = item_type
        self.live_count = int(live_count)
        # Resources consumed before the exception. None when the
        # exception bubbles up from a deeper helper that doesn't yet
        # plumb the accounting.
        self.partial_result = partial_result
        super().__init__(f"{item_type} unavailable (live_count={live_count})")


class NoBoardChangeError(Exception):
    def __init__(
        self,
        step: Dict[str, Any],
        reason: str,
        item_type: Optional[str] = None,
        board_before: Optional[List[List[str]]] = None,
        board_after: Optional[List[List[str]]] = None,
        partial_result: Optional[ExecutionResult] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
    ):
        self.step = step
        self.reason = reason
        self.item_type = item_type
        self.board_before = board_before
        self.board_after = board_after
        # Resources consumed before the exception — see ExecutionResult.
        self.partial_result = partial_result
        # H5 前置檢查／伺服器回覆細節，供 telemetry 與偵錯使用。
        self.diagnostics = diagnostics
        super().__init__(reason)


from .planner import base_label, enter_cost
PLACEABLE_MATERIALS = {"empty", "dug_pit"}

from miner.core.vision_utils import check_points
from miner.core.ocr_utils import check_drill_num, check_boom_num
from miner.core.ws_inventory import read_ws_mine_board, read_ws_prop_counts
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


def wait_frame_stable(
    d: DeviceLike,
    roi: Optional[Tuple[int, int, int, int]] = None,
    poll_interval: float = 0.2,
    max_wait: float = 2.0,
    diff_threshold: float = 2.0,
    min_wait: float = 0.3,
) -> Any:
    """等到畫面動畫結束為止 — 兩張相鄰 poll 的平均像素差低於 threshold 就算穩定。

    - `min_wait` 先硬等一段（給點擊事件抵達設備的時間），再開始輪詢。
    - `roi` = (y0, y1, x0, x1)，只比對這個區域（例如只盯棋盤 + 彈窗區）。
      傳 None 時比對整張 frame。
    - `max_wait` 是自 `min_wait` 之後的最大額外等待。
    - 回傳最後一張截圖，方便呼叫端重用。
    """
    if min_wait > 0:
        time.sleep(min_wait)

    def _extract(frame):
        if roi is None:
            return frame
        y0, y1, x0, x1 = roi
        return frame[y0:y1, x0:x1]

    prev = _extract(d.screenshot(format="opencv"))
    started = time.time()
    last_frame = prev
    while time.time() - started < max_wait:
        time.sleep(poll_interval)
        curr_full = d.screenshot(format="opencv")
        curr = _extract(curr_full)
        if curr.shape != prev.shape:
            prev = curr
            last_frame = curr_full
            continue
        diff = cv2.absdiff(prev, curr)
        if float(np.mean(diff)) < diff_threshold:
            return curr_full
        prev = curr
        last_frame = curr_full
    return last_frame


def tap_cell(d: DeviceLike, r: int, c: int, hits: int, wait_ms: int = 150) -> None:
    """在指定格子重複點擊指定次數，並等到動畫結束才回傳。"""
    x, y = cell_center_xy(r, c)
    for _ in range(hits):
        d.click(x + random.randint(-10, 10), y + random.randint(-10, 10))
        d.sleep(wait_ms / 1000.0)
    # Adaptive wait for the dig animation / reward popup to finish rather
    # than a fixed 0.5-1.0s tail. Rock/dirt stabilise in ~0.4s; pit reward
    # popups need 1-2s. ROI caps the compare region to board + bottom popup.
    wait_frame_stable(
        d,
        roi=(200, 960, 0, 540),
        poll_interval=0.2,
        max_wait=2.0,
        diff_threshold=2.0,
        min_wait=0.3,
    )


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


def get_live_item_count(d: DeviceLike, item_type: str) -> int:
    """執行前讀道具現量；web_h5 優先 WS，拿不到才退回 OCR。"""
    if item_type not in {"drill", "bomb"}:
        return 0
    ws_counts = read_ws_prop_counts(d)
    if ws_counts is not None:
        return int(ws_counts.get(item_type, 0))
    frame = d.screenshot(format="opencv")
    if item_type == "drill":
        return int(check_drill_num(d, frame=frame))
    if item_type == "bomb":
        return int(check_boom_num(d, frame=frame))
    return 0


def _h5_page(d: DeviceLike) -> Any:
    """裝置使用 H5 時回傳 Playwright page。"""
    inner = getattr(d, "_d", d)
    return getattr(inner, "_page", None)


def _raise_h5_board_unavailable(
    d: DeviceLike,
    step: Dict[str, Any],
    partial_result: ExecutionResult,
    before_board: Any,
    *,
    item_type: Optional[str] = None,
) -> None:
    """H5 缺少 authoritative 狀態時，不退回像素點擊。"""
    if before_board is not None:
        return
    if _h5_page(d) is None:
        return
    raise NoBoardChangeError(
        step=step,
        reason="H5 authoritative 0x0c01 board unavailable; skip pixel fallback",
        item_type=item_type,
        partial_result=partial_result,
        diagnostics={
            "phase": "h5_preflight",
            "validation": "board_unavailable",
        },
    )


def _dispatch_h5_ws_action(
    d: DeviceLike,
    before_board: Any,
    step: Dict[str, Any],
    *,
    hits: int = 1,
) -> bool:
    """H5 直接呼叫 JavaScript 挖礦控制器；非 H5/無 page 回 ``False``。"""
    page = _h5_page(d)
    if page is None:
        return False
    from ws_token.mining_h5_executor import H5MiningExecutor
    from ws_token.mining_adapter import grid_pos_to_block_id

    r, c = step["target"]
    block_id = int(step.get("block_id") or grid_pos_to_block_id(
        int(getattr(before_board, "baseline", 0) or 0), r, c
    ))

    if step.get("type") == "dig":
        from ws_token.mining_supervised import _is_diggable

        actives = {
            int(value) for value in (getattr(before_board, "actives", None) or [])
        }
        blocks = {
            int(getattr(block, "block_id")): block
            for block in (getattr(before_board, "blocks", None) or [])
            if getattr(block, "block_id", None) is not None
        }
        block = blocks.get(block_id)
        block_count = None if block is None else int(getattr(block, "count", 0) or 0)
        if not _is_diggable(actives, blocks, block_id):
            validation = "not_active" if block_id not in actives else "already_dug"
            details = {
                "phase": "h5_preflight",
                "validation": validation,
                "block_id": block_id,
                "baseline": int(getattr(before_board, "baseline", 0) or 0),
                "active_count": len(actives),
                "block_count": block_count,
            }
            raise NoBoardChangeError(
                step=step,
                reason=(
                    f"H5 伺服器前緣拒絕目標 {block_id}: "
                    f"{validation} count={block_count}"
                ),
                diagnostics=details,
            )

    h5 = H5MiningExecutor(page)
    response: Any
    if step.get("type") == "use":
        if step.get("item") == "drill":
            response = h5.use_drill(block_id)
        elif step.get("item") == "bomb":
            response = h5.use_bomb(block_id)
        else:
            return False
    else:
        for index in range(max(1, int(hits))):
            response = h5.use_pickaxe(block_id)
            if isinstance(response, dict) and response.get("ok") is False:
                break
            if index + 1 < hits:
                time.sleep(0.25)
    if isinstance(response, dict) and response.get("ok") is False:
        diagnostics = {
            "phase": "h5_server_response",
            "block_id": block_id,
            "response_cmd": response.get("response_cmd"),
            "error_code": response.get("error_code"),
            "raw_body_hex": response.get("raw_body_hex"),
        }
        raise NoBoardChangeError(
            step=step,
            reason=(
                f"H5 伺服器拒絕 block_id={block_id} "
                f"error_code={response.get('error_code')}"
            ),
            item_type=step.get("item") if step.get("type") == "use" else None,
            diagnostics=diagnostics,
        )
    return True


def verify_cell_empty(
    d: DeviceLike,
    clf: "ClassifierCNN",
    r: int,
    c: int,
    max_retry: int = 3,
    error_threshold: float = 0.9,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """重新截圖後確認指定格子是否成功被挖空。

    遊戲規則上 dug_pit 等同於 empty，可視為已挖空。
    因此這裡接受 base_label in {"empty", "dug_pit"} 都算成功，
    避免挖完礦洞後又多點一次。
    """
    attempts = []
    for _ in range(max_retry):
        passed, _ = check_points(d.screenshot(format="opencv"))
        if passed:
            d.click(444 + random.randint(-10, 10), 107 + random.randint(-10, 10))
            continue
        img2 = d.screenshot(format="opencv")
        board2, confidences2 = clf.classify_board(img2, save_samples=False)
        confidence = confidences2[r][c]
        new_label = base_label(board2[r][c])
        attempts.append({"label": new_label, "confidence": float(confidence)})
        if new_label in ("empty", "dug_pit"):
            if details is not None:
                details.update({"source": "cnn", "attempts": attempts})
            return True
        if confidence < error_threshold:
            # 使用者回饋：信心度低於閾值時，不要點擊空白處，而是儲存錯誤樣本
            print(f"    ⚠️ 信心度過低 ({r},{c}): {confidence:.4f} < {error_threshold}")
            # 如果信心度介於 0.6 到 0.8 之間，儲存樣本 (由 ClassifierCNN.classify_board 負責，
            # 這裡只需要呼叫，但目前 verify_cell_empty 的 classify_board 參數 save_samples=False)
            # 因此這裡我們選擇忽略點擊空白處的行為。
            continue
    if details is not None:
        details.update({"source": "cnn", "attempts": attempts})
    return False


def _verify_ws_action(
    d: DeviceLike,
    before_board: Any,
    step: Dict[str, Any],
    before_inventory: Optional[Dict[str, int]],
    *,
    max_retry: int = 4,
) -> Dict[str, Any]:
    """以 0x0c01 + 0x0401 驗證 H5 動作，僅接受可歸因變化。"""
    from ws_token.mining_supervised import _board_confirmation

    after_board = before_board
    after_inventory = before_inventory
    confirmation = None
    for attempt in range(1, max_retry + 1):
        after_board = read_ws_mine_board(d)
        after_inventory = read_ws_prop_counts(d)
        if after_board is not None:
            confirmation = _board_confirmation(before_board, after_board, step)
        if confirmation:
            break
        item = step.get("item", "pickaxe")
        key = "pickaxe" if step.get("type") == "dig" else item
        if (before_inventory is not None and after_inventory is not None
                and int(after_inventory.get(key, 0)) < int(before_inventory.get(key, 0))):
            confirmation = f"{key}_inventory_changed"
            break
        if attempt < max_retry:
            time.sleep(0.25)
    # ``read_ws_mine_board`` deliberately swallows transport exceptions and
    # returns None.  Preserve that distinction in telemetry so the caller can
    # treat a transient refresh failure as blocked/retryable rather than as a
    # permanent item failure.  Inventory delta (checked above) still wins.
    if confirmation is None and after_board is None:
        confirmation = "refresh_failed"
    return {
        # ``refresh_failed`` is telemetry for a retryable verification miss,
        # not a positive confirmation.  Inventory/board confirmations remain
        # successful as before.
        "success": bool(confirmation) and confirmation != "refresh_failed",
        "source": "ws",
        "confirmation": confirmation,
        "attempts": attempt,
        "inventory_before": before_inventory,
        "inventory_after": after_inventory,
    }


def execute_plan_steps(
    d: DeviceLike,
    clf: "ClassifierCNN",
    board: List[List[str]],
    steps: List[Dict[str, Any]],
    rl_recorder: Optional[RLRecorder] = None,
    deadline: Optional[float] = None,
) -> ExecutionResult:
    """逐步執行規劃結果；支援挖路、採礦、下樓與道具使用。

    Returns an :class:`ExecutionResult` summarising resources consumed —
    the mining loop uses it to decrement its internal counters instead of
    relying on a full OCR re-read after each plan. On exceptions
    (``NoBoardChangeError``, ``OutOfItemError``) the partial accounting
    is attached as ``exc.partial_result`` so the caller can still credit
    shovels / items consumed before the failure.
    """
    acc = ExecutionResult()
    try:
        for i, step in enumerate(steps, 1):
            if deadline and time.time() > deadline:
                print(f"    [Executor] 超時 (deadline={deadline})，停止執行剩餘步驟")
                acc.terminated_reason = "deadline"
                return acc

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
                ws_board_before = read_ws_mine_board(d)
                ws_inventory_before = read_ws_prop_counts(d) if ws_board_before is not None else None
                _raise_h5_board_unavailable(
                    d, step, acc, ws_board_before, item_type=item_type
                )
                target_label = board[r][c]
                if not is_placeable_label(target_label):
                    print(
                        f"    ⚠️ 無法在 ({r},{c}) 放置 {item_type}，目前格子為 {target_label}，僅允許 empty/dug_pit，停止並重新規劃"
                    )
                    acc.terminated_reason = "item_placement_invalid"
                    return acc
                live_count = get_live_item_count(d, item_type)
                if live_count <= 0:
                    print(
                        f"    [Executor] live inventory check failed for {item_type}: "
                        f"count={live_count}, abort item step"
                    )
                    # No item consumed yet — partial accounting unchanged.
                    raise OutOfItemError(item_type, live_count, partial_result=acc)
                print(f"  - 於 ({r},{c}) 使用 {item_type}")
                h5_dispatched = _dispatch_h5_ws_action(
                    d, ws_board_before, step
                ) if ws_board_before is not None else False
                if not h5_dispatched:
                    select_item(d, item_type)
                    tap_cell(d, r, c, 1, wait_ms=500)
                # Item is consumed by the game as soon as the click lands —
                # record it now so an unsuccessful board change (rare, lag /
                # misclick) still debits the item.
                if item_type == "drill":
                    acc.drills_used += 1
                elif item_type == "bomb":
                    acc.bombs_used += 1
                # Items (bomb 3×3+cross, drill full column) trigger the
                # longest animations in the game — explosion + chain shatter
                # + reward popups. Wait for the frame to settle instead of
                # a fixed 0.8s sleep.
                if h5_dispatched:
                    board_after_use = [row[:] for row in board]
                else:
                    img_after_use = wait_frame_stable(
                        d,
                        roi=(200, 960, 0, 540),
                        poll_interval=0.25,
                        max_wait=3.0,
                        diff_threshold=2.0,
                        min_wait=0.5,
                    )
                    board_after_use, _ = clf.classify_board(img_after_use, save_samples=False)
                ws_event = None
                if ws_board_before is not None:
                    ws_event = _verify_ws_action(
                        d, ws_board_before, step, ws_inventory_before
                    )
                    acc.verification_events.append(ws_event)
                if ws_event is not None and not ws_event["success"]:
                    acc.terminated_reason = "no_board_change"
                    raise NoBoardChangeError(
                        step=step,
                        reason=f"{item_type} made no attributable WS change",
                        item_type=item_type,
                        board_before=[row[:] for row in board],
                        board_after=[row[:] for row in board_after_use],
                        partial_result=acc,
                    )
                if ws_event is not None:
                    after_inv = ws_event["inventory_after"]
                    if after_inv is not None and ws_inventory_before is not None:
                        used = max(
                            0,
                            int(ws_inventory_before.get(item_type, 0))
                            - int(after_inv.get(item_type, 0)),
                        )
                        if item_type == "drill":
                            acc.drills_used = used
                        elif item_type == "bomb":
                            acc.bombs_used = used
                if ws_event is None and board_after_use == board:
                    acc.terminated_reason = "no_board_change"
                    raise NoBoardChangeError(
                        step=step,
                        reason=f"{item_type} made no board change",
                        item_type=item_type,
                        board_before=[row[:] for row in board],
                        board_after=[row[:] for row in board_after_use],
                        partial_result=acc,
                    )

                hit_pit = False
                H, W = GRID_CFG["H"], GRID_CFG["W"]
                affected = (
                    get_bomb_affected_cells(r, c, H, W)
                    if item_type == "bomb"
                    else get_drill_affected_cells(r, c, H, W)
                )
                for ar, ac in affected:
                    before_label = board[ar][ac]
                    after_label = board_after_use[ar][ac]
                    if "pit" in before_label and after_label == "empty":
                        hit_pit = True
                        break

                board[:] = [row[:] for row in board_after_use]
                if hit_pit and not h5_dispatched:
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
                acc.steps_completed += 1
                acc.terminated_reason = "item_used"
                return acc

            step_board_before = [row[:] for row in board]
            cell_events: List[Dict[str, Any]] = []
            for (r, c) in step["dig_list"]:
                ws_board_before = read_ws_mine_board(d)
                ws_inventory_before = read_ws_prop_counts(d) if ws_board_before is not None else None
                _raise_h5_board_unavailable(d, step, acc, ws_board_before)
                label = board[r][c]
                hits = required_hits(label)
                cell_cost = int(enter_cost(label) or 0)
                print(f"  - 挖 ({r},{c}) {label} → 需要點擊 {hits} 次")
                cell_event: Dict[str, Any] = {
                    "row": r,
                    "col": c,
                    "label_before": label,
                    "material": material_of(label),
                    "required_hits": hits,
                    "enter_cost": cell_cost,
                }
                if hits > 0:
                    h5_dispatched = _dispatch_h5_ws_action(
                        d, ws_board_before, step, hits=hits
                    ) if ws_board_before is not None else False
                    if not h5_dispatched:
                        tap_cell(d, r, c, hits, wait_ms=1000)
                    if ws_board_before is None:
                        # ADB 無 authoritative 庫存，維持保守點擊成本估算。
                        acc.shovels_used += cell_cost
                    if "pit" in label and "dug" not in label and not h5_dispatched:
                        print(f"    [Executor] 挖掘礦洞 ({r},{c})，執行兩次確認點擊 (394, 152)")
                        d.click(394, 152)
                        time.sleep(0.3)
                        d.click(394, 152)
                        # Wait for reward popup (coin +shovel animation) to
                        # finish before any subsequent screenshot.
                        wait_frame_stable(
                            d,
                            roi=(200, 960, 0, 540),
                            poll_interval=0.25,
                            max_wait=2.5,
                            diff_threshold=2.0,
                            min_wait=0.5,
                        )
                    if not h5_dispatched:
                        check_points(d.screenshot(format="opencv"))

                if r < 6:
                    if ws_board_before is not None:
                        ws_event = _verify_ws_action(
                            d, ws_board_before, step, ws_inventory_before
                        )
                        acc.verification_events.append(ws_event)
                        cell_event.update({
                            "verify_source": "ws",
                            "confirmation": ws_event["confirmation"],
                            "inventory_before": ws_event["inventory_before"],
                            "inventory_after": ws_event["inventory_after"],
                        })
                        success = ws_event["success"]
                        after_inv = ws_event["inventory_after"]
                        if after_inv is not None:
                            acc.pickaxe_count_after = int(after_inv.get("pickaxe", 0))
                            if ws_inventory_before is not None:
                                acc.shovels_used += max(
                                    0,
                                    int(ws_inventory_before.get("pickaxe", 0))
                                    - acc.pickaxe_count_after,
                                )
                    else:
                        verify_details: Dict[str, Any] = {}
                        success = verify_cell_empty(
                            d, clf, r, c, max_retry=2, details=verify_details
                        )
                        cell_event.update(verify_details)
                    if not success and ws_board_before is None:
                        print(f"    驗證未成功，補點一次 ({r},{c})")
                        tap_cell(d, r, c, 1)
                        # The retry click is one extra shovel regardless of
                        # the cell's enter_cost.
                        acc.shovels_used += 1
                        success = verify_cell_empty(
                            d, clf, r, c, max_retry=1, details=cell_event
                        )
                    cell_event["verify_success"] = success
                    acc.verification_events.append(dict(cell_event))
                    if not success:
                        print(f"    ⚠️ 挖掘驗證失敗 ({r},{c})，停止執行剩餘步驟")
                        cell_events.append(cell_event)
                        # The dig did not empty the target. If the WHOLE board is
                        # also unchanged, this action is futile on this board —
                        # surface it as NoBoardChangeError so the mining loop
                        # blacklists it and re-plans, instead of returning silent
                        # "progress" that lets the same plan repeat forever
                        # (regression: 7fe98fc6 row-0 unreachable-pit spin).
                        board_now, _ = clf.classify_board(
                            d.screenshot(format="opencv"), save_samples=False
                        )
                        if board_now == step_board_before:
                            acc.terminated_reason = "no_board_change"
                            raise NoBoardChangeError(
                                step=step,
                                reason=f"dig at ({r},{c}) verify failed, board unchanged",
                                board_before=step_board_before,
                                board_after=[row[:] for row in board_now],
                                partial_result=acc,
                            )
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
                                    "board_after": [row[:] for row in board_now],
                                    "terminated": "verify_fail",
                                }
                            )
                        # Count this step as partially attempted so the
                        # caller knows real work happened.
                        acc.steps_completed += 1
                        acc.terminated_reason = "verify_fail"
                        return acc
                else:
                    if ws_board_before is not None:
                        ws_event = _verify_ws_action(
                            d, ws_board_before, step, ws_inventory_before
                        )
                        acc.verification_events.append(ws_event)
                        cell_event.update({
                            "verify_source": "ws",
                            "confirmation": ws_event["confirmation"],
                            "inventory_before": ws_event["inventory_before"],
                            "inventory_after": ws_event["inventory_after"],
                            "verify_success": ws_event["success"],
                        })
                        after_inv = ws_event["inventory_after"]
                        if after_inv is not None:
                            acc.pickaxe_count_after = int(after_inv.get("pickaxe", 0))
                            if ws_inventory_before is not None:
                                acc.shovels_used += max(
                                    0,
                                    int(ws_inventory_before.get("pickaxe", 0))
                                    - acc.pickaxe_count_after,
                                )
                        if not ws_event["success"]:
                            acc.terminated_reason = "no_board_change"
                            raise NoBoardChangeError(
                                step=step,
                                reason=f"floor7 dig at ({r},{c}) made no attributable WS change",
                                board_before=step_board_before,
                                partial_result=acc,
                            )
                    print(f"    第七層格子 ({r},{c}) 跳過驗證")
                    print(f"    ⚠️ 觸發下樓，停止執行剩餘路徑，請重新規劃")
                    # Row-6 dig triggers a full scroll + new-row-generation
                    # animation — the longest in the whole game. Main loop
                    # will re-screenshot immediately after we return, so wait
                    # for the scroll to land before exiting.
                    if ws_board_before is None:
                        wait_frame_stable(
                            d,
                            roi=(200, 960, 0, 540),
                            poll_interval=0.3,
                            max_wait=3.5,
                            diff_threshold=2.0,
                            min_wait=0.7,
                        )
                    cell_event["verify_success"] = True
                    cell_events.append(cell_event)
                    acc.verification_events.append(dict(cell_event))
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
                    acc.steps_completed += 1
                    acc.terminated_reason = "floor7"
                    return acc
                if ws_board_before is None:
                    img_after_dig = d.screenshot(format="opencv")
                    board_after_dig, _ = clf.classify_board(img_after_dig, save_samples=False)
                else:
                    board_after_dig = [row[:] for row in board]
                if ws_board_before is None and board_after_dig == step_board_before:
                    acc.terminated_reason = "no_board_change"
                    raise NoBoardChangeError(
                        step=step,
                        reason=f"dig at ({r},{c}) made no board change",
                        board_before=step_board_before,
                        board_after=[row[:] for row in board_after_dig],
                        partial_result=acc,
                    )
                board[:] = [row[:] for row in board_after_dig]
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
                        "board_after": [row[:] for row in board],
                    }
                )
            acc.steps_completed += 1
        return acc
    except (ItemPlacementError, NoBoardChangeError, OutOfItemError):
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
    "ExecutionResult",
    "NoBoardChangeError",
    "OutOfItemError",
    "ItemPlacementError",
]
