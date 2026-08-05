"""Mining service: screenshot -> classify -> plan -> execute loop."""
from __future__ import annotations

import collections
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import contextlib
import io
import copy
import json
import uuid
from dataclasses import dataclass

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
from miner.core.ws_inventory import read_ws_below_rows, read_ws_prop_counts
from miner.planning.item_planner import find_tool_candidate
from miner.planning.planner import (
    base_label,
    enter_cost,
    is_empty as is_air,
)
from miner.planning.smart_planner import plan_smart
from miner.final_v1 import plan_final_v1
from miner.v3.planner import plan_v3
from miner.v4.planner import plan_v4
from miner.rl.rl_recorder import RLRecorder
from miner.core.vision_utils import check_points
from utils.logging_utils import logger, setup_miner_logger
from utils.mining_map_recorder import MiningMapRecorder
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

# WS/動畫驗證偶發短暫失敗時，先封鎖該落點並重規劃一次；只有同一道具在
# 未變版面連續失敗才整場停用。dig（鎬子）仍沿用 action-level blocked
# 契約，不套用這個 item-level 門檻。
_ITEM_FAILURE_BLACKLIST_LIMIT: int = 2


@dataclass
class MiningTelemetryCounters:
    """Cheap, session-scoped counters for the CNN/ADB mining service.

    The counters intentionally live at the service boundary.  They are copied
    into both the existing ``MiningTelemetry`` log lines and map-recorder JSON
    events, so adding observability does not replace or mutate the append-only
    execution event contract.
    """

    rounds: int = 0
    overlay_ocr_calls: int = 0
    screenshot_calls: int = 0
    classify_calls: int = 0
    shadow_calls: int = 0
    shadow_successes: int = 0
    shadow_elapsed_ms: float = 0.0
    shadow_skipped: int = 0
    shadow_failures: int = 0
    shadow_not_attempted: int = 0

    def snapshot(self, *, screenshots_per_round: int = 0) -> Dict[str, Any]:
        """Return cumulative fields plus legacy aliases.

        ``snapshot`` is kept for the append-only text log and old consumers.
        Machine-readable session summaries use :meth:`summary_fields`, while
        round events use :meth:`round_fields`; this prevents the old
        ``screenshots_per_round`` key from changing meaning between scopes.
        """
        sample_rate = (
            float(self.shadow_calls) / float(self.rounds)
            if self.rounds
            else 0.0
        )
        return {
            "overlay_ocr_calls": int(self.overlay_ocr_calls),
            "screenshot_calls": int(self.screenshot_calls),
            "classify_calls": int(self.classify_calls),
            "screenshots_per_round": int(screenshots_per_round),
            "screenshots_per_round_avg": round(
                float(self.screenshot_calls) / float(self.rounds)
                if self.rounds else 0.0,
                6,
            ),
            "shadow_calls": int(self.shadow_calls),
            "shadow_successes": int(self.shadow_successes),
            "shadow_elapsed_ms": round(float(self.shadow_elapsed_ms), 3),
            "shadow_sample_rate": round(sample_rate, 6),
            "shadow_skipped": int(self.shadow_skipped),
            "shadow_failures": int(self.shadow_failures),
            "shadow_not_attempted": int(self.shadow_not_attempted),
        }

    def summary_fields(self) -> Dict[str, Any]:
        """Return the canonical session-summary aggregates.

        The ``*_avg`` values are per mining round (except elapsed shadow time,
        which is per attempted shadow call).  Legacy cumulative keys are kept
        additively for existing log readers; ``screenshots_per_round`` is the
        historical average alias, never the total screenshot count.
        """
        rounds = int(self.rounds)
        rounds_f = float(rounds) if rounds else 0.0
        shadow_calls = int(self.shadow_calls)
        shadow_calls_f = float(shadow_calls) if shadow_calls else 0.0
        fields = {
            "screenshots_total": int(self.screenshot_calls),
            "screenshots_per_round_avg": round(
                float(self.screenshot_calls) / rounds_f if rounds else 0.0, 6
            ),
            "screenshot_calls_avg": round(
                float(self.screenshot_calls) / rounds_f if rounds else 0.0, 6
            ),
            "classify_calls_avg": round(
                float(self.classify_calls) / rounds_f if rounds else 0.0, 6
            ),
            "overlay_ocr_calls_avg": round(
                float(self.overlay_ocr_calls) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_calls_avg": round(
                float(self.shadow_calls) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_successes_avg": round(
                float(self.shadow_successes) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_failures_avg": round(
                float(self.shadow_failures) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_skipped_avg": round(
                float(self.shadow_skipped) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_elapsed_ms_avg": round(
                float(self.shadow_elapsed_ms) / shadow_calls_f
                if shadow_calls else 0.0,
                3,
            ),
            # Cumulative counters retained for additive compatibility.
            "screenshot_calls": int(self.screenshot_calls),
            "classify_calls": int(self.classify_calls),
            "overlay_ocr_calls": int(self.overlay_ocr_calls),
            "screenshots_per_round": round(
                float(self.screenshot_calls) / rounds_f if rounds else 0.0, 6
            ),
            "shadow_calls": shadow_calls,
            "shadow_successes": int(self.shadow_successes),
            "shadow_failures": int(self.shadow_failures),
            "shadow_skipped": int(self.shadow_skipped),
            "shadow_not_attempted": int(self.shadow_not_attempted),
            "shadow_elapsed_ms": round(float(self.shadow_elapsed_ms), 3),
            "shadow_sample_rate": round(
                float(self.shadow_calls) / rounds_f if rounds else 0.0, 6
            ),
        }
        return fields

    def round_fields(
        self,
        *,
        screenshot_start: int,
        classify_start: int,
        overlay_start: int,
        shadow_start: int,
        shadow_success_start: int,
        shadow_failure_start: int,
        shadow_skip_start: int,
        shadow_not_attempted_start: int,
        shadow_elapsed_start: float,
    ) -> Dict[str, Any]:
        """Return canonical per-round counters (``round_*`` namespace)."""
        shadow_calls = max(0, int(self.shadow_calls) - int(shadow_start))
        elapsed = max(
            0.0, float(self.shadow_elapsed_ms) - float(shadow_elapsed_start)
        )
        return {
            "round_screenshot_calls": max(0, int(self.screenshot_calls) - int(screenshot_start)),
            "round_classify_calls": max(0, int(self.classify_calls) - int(classify_start)),
            "round_overlay_ocr_calls": max(0, int(self.overlay_ocr_calls) - int(overlay_start)),
            "round_shadow_calls": shadow_calls,
            "round_shadow_successes": max(0, int(self.shadow_successes) - int(shadow_success_start)),
            "round_shadow_failures": max(0, int(self.shadow_failures) - int(shadow_failure_start)),
            "round_shadow_skipped": max(0, int(self.shadow_skipped) - int(shadow_skip_start)),
            "round_shadow_not_attempted": max(
                0, int(self.shadow_not_attempted) - int(shadow_not_attempted_start)
            ),
            "round_shadow_elapsed_ms": round(elapsed, 3),
            "round_shadow_sample_rate": 1.0 if shadow_calls else 0.0,
            # Legacy round alias; canonical consumers must use round_screenshot_calls.
            "screenshots_per_round": max(
                0, int(self.screenshot_calls) - int(screenshot_start)
            ),
        }


class _TelemetryDeviceProxy:
    """Delegate a device while counting screenshots made by the executor."""

    def __init__(self, device: u2.Device, counters: MiningTelemetryCounters):
        self._device = device
        self._counters = counters

    def screenshot(self, *args, **kwargs):
        self._counters.screenshot_calls += 1
        return self._device.screenshot(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._device, name)


class _TelemetryClassifierProxy:
    """Delegate a classifier while counting executor board classifications."""

    def __init__(self, classifier: ClassifierCNN, counters: MiningTelemetryCounters):
        self._classifier = classifier
        self._counters = counters

    def classify_board(self, *args, **kwargs):
        self._counters.classify_calls += 1
        return self._classifier.classify_board(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._classifier, name)


def _counted_screenshot(
    d: u2.Device,
    counters: Optional[MiningTelemetryCounters],
    *args,
    **kwargs,
):
    if counters is not None:
        counters.screenshot_calls += 1
    return d.screenshot(*args, **kwargs)


def _telemetry_fields(
    counters: MiningTelemetryCounters,
    *,
    screenshots_per_round: int = 0,
) -> str:
    """Render stable key/value fields for the append-only text log."""
    fields = counters.snapshot(screenshots_per_round=screenshots_per_round)
    return (
        "overlay_ocr_calls=%d screenshot_calls=%d classify_calls=%d "
        "screenshots_per_round=%d screenshots_per_round_avg=%.6f "
        "shadow_calls=%d shadow_successes=%d shadow_elapsed_ms=%.3f "
        "shadow_sample_rate=%.6f shadow_skipped=%d shadow_failures=%d "
        "shadow_not_attempted=%d "
        "overlay_source=service"
        % (
            fields["overlay_ocr_calls"], fields["screenshot_calls"],
            fields["classify_calls"], fields["screenshots_per_round"],
            fields["screenshots_per_round_avg"],
            fields["shadow_calls"], fields["shadow_successes"],
            fields["shadow_elapsed_ms"], fields["shadow_sample_rate"],
            fields["shadow_skipped"], fields["shadow_failures"],
            fields["shadow_not_attempted"],
        )
    )


def _json_telemetry_payload(
    counters: MiningTelemetryCounters,
    event: str,
    *,
    screenshots_per_round: int = 0,
    **extra: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema": "mining_telemetry_v1",
        "event": event,
        "overlay_source": "service",
        # Keep common identity fields present and JSON-stable on every event.
        "session_id": None,
        "device_id": None,
        "planner": None,
        "shadow_planner": None,
    }
    payload.update(counters.snapshot(screenshots_per_round=screenshots_per_round))
    payload.update(extra)
    payload["session_id"] = (
        str(payload["session_id"]) if payload.get("session_id") is not None else None
    )
    payload["device_id"] = (
        str(payload["device_id"]) if payload.get("device_id") is not None else None
    )
    for key in ("planner", "shadow_planner"):
        value = payload.get(key)
        payload[key] = str(value) if value is not None and str(value) else None
    return payload


def _identical_board_exceeded(cur_sig, prev_sig, count: int):
    """Return (new_count, tripped). Increments when the board signature is
    unchanged from the previous iteration; resets to 0 when it changes."""
    if prev_sig is not None and cur_sig == prev_sig:
        count += 1
    else:
        count = 0
    return count, count >= _MAX_IDENTICAL_BOARDS


def _record_item_failure(
    item_type: str,
    streaks: Dict[str, int],
    *,
    limit: int = _ITEM_FAILURE_BLACKLIST_LIMIT,
) -> Tuple[int, bool]:
    """增加道具驗證失敗計數，回傳 ``(次數, 是否達黑名單門檻)``。

    這個純 helper 讓 WS/非 WS 的測試都能鎖定同一個「首次只 retry、第二次
    才 blacklist」契約；呼叫端仍負責把落點加入 blocked set 及實際停用道具。
    """
    count = int(streaks.get(item_type, 0) or 0) + 1
    streaks[item_type] = count
    return count, count >= max(1, int(limit))

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
    if result.pickaxe_count_after is not None:
        new_count = max(0, int(result.pickaxe_count_after))
        miner_logger.info(
            f"[MiningService] WS 同步鏟子現量 {count} -> {new_count}"
        )
        count = new_count
    elif result.shovels_used:
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


def _cnn_valid_targets(board: List[List[str]]) -> Set[Tuple[Any, ...]]:
    """Action-aware keys for every executor-accepted visible dig / item placement."""
    from miner.v3.board import is_frontier_diggable, is_reachable_air
    targets: Set[Tuple[Any, ...]] = set()
    for r in range(len(board)):
        for c in range(len(board[r])):
            if is_frontier_diggable(board, r, c):
                targets.add(("dig", "pickaxe", r, c))
            if is_reachable_air(board[r][c]):
                targets.add(("use", "bomb", r, c))
                targets.add(("use", "drill", r, c))
    return targets


def _blocked_action_keys(blocked_actions: Set[Tuple[Any, ...]]) -> Set[Tuple[Any, ...]]:
    """Convert existing blocked signatures (type,item,pos,target,action) to ActionKey."""
    keys: Set[Tuple[Any, ...]] = set()
    for sig in blocked_actions:
        kind = sig[0] if len(sig) > 0 else None
        item = sig[1] if len(sig) > 1 else None
        pos = None
        for cand in sig[2:4]:
            if isinstance(cand, (tuple, list)) and len(cand) == 2:
                pos = cand
                break
        if kind not in ("dig", "use") or pos is None:
            continue
        item_name = str(item) if (kind == "use" and item) else "pickaxe"
        keys.add((kind, item_name, int(pos[0]), int(pos[1])))
    return keys


def _compute_shadow_plan(
    board: List[List[str]],
    shovels: float,
    items: Dict[str, int],
    blocked_actions: Set[Tuple[Any, ...]],
    shadow_version: str,
    miner_logger,
    known_board: Optional[List[List[str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Shadow 只計算與記錄；任何失敗不得中斷挖礦主流程。"""
    if shadow_version != "final_v1":
        return None
    started = time.perf_counter()
    try:
        # valid_targets 只來自可見 7 列（executor 可點擊範圍）；
        # known_board 只擴充規劃視野（web_h5 由 WS 重建下方地形）
        valid = _cnn_valid_targets(board) - _blocked_action_keys(blocked_actions)
        result = plan_final_v1(known_board if known_board is not None else board,
                               shovels=shovels, items=items,
                               visible_rows=7, valid_targets=valid)
        return {
            "ok": True,
            "planner": "final_v1",
            "first_step": (result.get("steps") or [None])[0],
            "score_breakdown": result.get("score_breakdown"),
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            "budget_hit": result.get("budget_hit", False),
        }
    except Exception as exc:  # shadow must never interrupt mining
        miner_logger.warning("[MiningService] shadow planner final_v1 failed: %s", exc)
        return {
            "ok": False, "planner": "final_v1", "error": str(exc),
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }


def _dispatch_planner(
    board: List[List[str]],
    shovels: float,
    items: Dict[str, int],
    blocked_actions: Set[Tuple[Any, ...]],
    planner_version: str,
    miner_logger,
    depth: Optional[int] = None,
    device: Optional[str] = None,
    known_board: Optional[List[List[str]]] = None,
) -> Tuple[Dict[str, Any], str]:
    """Route to the correct planner version and return (plan, plan_title)."""
    if planner_version == "final_v1":
        plan = None
        try:
            # valid_targets 只來自可見 7 列；known_board（web_h5 WS 下方地形）
            # 只擴充規劃視野，v1/v3/v4 與 fallback 一律維持 7 列
            valid_targets = _cnn_valid_targets(board) - _blocked_action_keys(blocked_actions)
            plan = plan_final_v1(known_board if known_board is not None else board,
                                 shovels=shovels, items=items,
                                 visible_rows=7, valid_targets=valid_targets)
        except Exception as exc:
            miner_logger.warning("[MiningService] final_v1 planner failed, fallback to v1: %s", exc)
        if plan is not None and plan.get("steps"):
            plan["planner_name"] = "final_v1"
            plan["planner_source"] = "planner"
            return plan, "Final V1 規劃 (bounded beam)"
        fallback, _title = _dispatch_planner(
            board, shovels, items, blocked_actions, "v1", miner_logger,
            depth=depth, device=device,
        )
        # planner_name 必須反映實際執行的規劃器；fallback 的來源另以
        # planner_source 保留，避免 KPI 把 v1 輪次誤算成 final_v1。
        fallback["planner_name"] = "v1"
        fallback["planner_source"] = "final_v1_fallback"
        return fallback, "V1 規劃 (final_v1 fallback)"
    if planner_version == "v4":
        plan = plan_v4(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V4 規劃 (Miner V4, 3-step bounded)"
    if planner_version == "v3":
        plan = plan_v3(board, shovels=shovels, items=items, blocked_actions={sig[:3] for sig in blocked_actions})
        return plan, "V3 規劃 (Miner V3)"
    # v1 default (v2 removed 2026-06-05: violated <300ms on 18.8% of real boards)
    tool_candidate = find_tool_candidate(board, items_available=items) if USE_ITEMS else None
    if tool_candidate:
        # item_planner predates action-level blocked signatures and otherwise
        # would return the same failed落點 forever on an unchanged board.
        candidate_step = {
            "type": "use",
            "item": tool_candidate.get("tool"),
            "pos": tool_candidate.get("target"),
            "action": f"use_{tool_candidate.get('tool')}",
            "target": tool_candidate.get("target"),
        }
        if _step_signature(candidate_step) in blocked_actions:
            tool_candidate = None
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
    # fallback 輪次的 KPI 應歸到實際執行的 v1，而非設定中的 final_v1。
    effective_planner = str(plan.get("planner_name") or planner_version)
    shadow = plan.get("shadow")
    if shadow is not None:
        miner_logger.info(f"[MiningService] shadow_plan={shadow}")
    if effective_planner not in {"v3", "v4", "final_v1"}:
        return
    depth_str = "?" if depth is None else str(depth)
    miner_logger.info(
        "[MiningService] planner=%s depth=%s calc_ms=%.3f result_ms=%s nodes=%s steps=%s strategy=%s"
        % (
            effective_planner,
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
        miner_logger.info(f"[MiningService] planner={effective_planner} stats={stats}")
    if blocked_count:
        miner_logger.info(
            "[MiningService] planner=%s blocked_actions=%s" % (effective_planner, blocked_count)
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
    telemetry: Optional[MiningTelemetryCounters] = None,
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
        live_frame = _counted_screenshot(d, telemetry, format="opencv")
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
    telemetry = MiningTelemetryCounters()
    session_id = uuid.uuid4().hex
    device_id = str(ip) if ip is not None else None
    fatal_totals: Optional[Dict[str, Any]] = None
    summary_emitted = False
    map_recorder = None
    map_recorder_ended = False
    round_active = False
    # Start with safe defaults so a config-loader exception still gets a
    # complete, typed session_start/exception/session_summary sequence.
    device_cfg: Dict[str, Any] = {}
    planner_version = "v1"
    shadow_version: Optional[str] = None
    mining_save_samples = False

    def _safe_end_map_recorder(totals: Optional[Dict[str, Any]]) -> None:
        """End a recorder at most once, including a partially-started one."""
        nonlocal map_recorder_ended
        if map_recorder is None or map_recorder_ended:
            return
        map_recorder_ended = True
        try:
            map_recorder.end(totals=totals)
        except Exception as exc:
            miner_logger.warning("[MiningService] map recorder 收尾失敗: %s", exc)

    def _emit_exception_event(phase: str, exc: BaseException) -> None:
        """Emit one standalone exception event without changing mining flow."""
        miner_logger.info(
            json.dumps(
                _json_telemetry_payload(
                    telemetry,
                    "exception",
                    scope="session",
                    session_id=session_id,
                    device_id=device_id,
                    planner=planner_version,
                    shadow_planner=shadow_version,
                    phase=phase,
                    round=telemetry.rounds if round_active else None,
                    error=f"{type(exc).__name__}: {exc}",
                    rounds=telemetry.rounds,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def _emit_session_summary(stopped_reason: str = "completed") -> None:
        """Emit exactly one cumulative summary for this mining session."""
        nonlocal summary_emitted
        if summary_emitted:
            return
        summary_emitted = True
        summary_fields = telemetry.summary_fields()
        miner_logger.info(
            "[MiningTelemetry] event=session_summary rounds=%d %s"
            % (telemetry.rounds, _telemetry_fields(telemetry))
        )
        payload = _json_telemetry_payload(
            telemetry,
            "session_summary",
            # ``summary_fields`` contains canonical averages and additive old
            # aliases.  In particular screenshots_total is never conflated
            # with the legacy average screenshots_per_round key.
            **summary_fields,
            scope="session",
            session_id=session_id,
            device_id=device_id,
            planner=planner_version,
            shadow_planner=shadow_version,
            rounds=telemetry.rounds,
            stopped_reason=stopped_reason,
        )
        miner_logger.info(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _emit_session_start() -> None:
        miner_logger.info(
            json.dumps(
                _json_telemetry_payload(
                    telemetry,
                    "session_start",
                    scope="session",
                    session_id=session_id,
                    device_id=device_id,
                    planner=planner_version,
                    shadow_planner=shadow_version,
                    backend=str(device_cfg.get("backend", "adb")),
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    try:
        loaded_cfg = config_manager.get_device_config(ip)
        device_cfg = dict(loaded_cfg or {})
    except ForceSleepRequested:
        _emit_session_start()
        _emit_session_summary("force_sleep")
        raise
    except Exception as exc:
        fatal_totals = {
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        }
        _emit_session_start()
        _emit_exception_event("config_preflight", exc)
        miner_logger.exception("[MiningService] 設定前置檢查例外，停止挖礦: %s", exc)
        _emit_session_summary("exception")
        return

    # Default planner is v1 (A*). Real-board eval at the recalibrated 3.6%
    # density (docs/.../2026-06-18-...top-pileup-fix.md): v1 is the most
    # shovel-efficient and highest-scoring of all planners; v3 is cluster-aware;
    # v4 is a bounded 3-step DFS. v5 (priors-driven) and v2 were removed.
    # Override per-device with `mining_planner_version` in config.
    planner_version = str(device_cfg.get("mining_planner_version", "v1") or "v1").strip().lower()
    shadow_value = str(device_cfg.get("mining_shadow_planner_version", "") or "").strip().lower()
    shadow_version = shadow_value or None
    mining_save_samples = bool(device_cfg.get("mining_save_samples", False))
    if planner_version not in {"v1", "v3", "v4", "final_v1"}:
        planner_version = "v1"
    if shadow_version not in {"", "final_v1"}:
        shadow_version = None

    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    miner_logger.info(f"[MiningService] 開始挖礦，時間限制: {max_duration_minutes} 分鐘 (設備 {ip})")
    miner_logger.info(f"[MiningService] planner_version={planner_version}")
    miner_logger.info(f"[MiningService] mining_save_samples={mining_save_samples}")
    _emit_session_start()

    try:
        count = check_pickaxe_count(d)
    except ForceSleepRequested:
        _emit_session_summary("force_sleep")
        raise
    except Exception as exc:
        fatal_totals = {
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        }
        _emit_exception_event("session_start", exc)
        miner_logger.exception("[MiningService] session-start 前置檢查例外，停止挖礦: %s", exc)
        _emit_session_summary("exception")
        return
    if count < 5:
        miner_logger.info("鏟子數量過少，停止挖礦")
        _emit_session_summary("insufficient_pickaxe")
        return

    items_available: Dict[str, int] = {"drill": 0, "bomb": 0}
    item_blacklist: Set[str] = set()
    zero_streaks: Dict[str, int] = {"drill": 0, "bomb": 0}
    item_failure_streaks: Dict[str, int] = {}
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

    # 每帳號挖礦地圖記錄（session JSONL + 累積 global_map）。for_device 讀
    # mining_map_record（預設開），停用時完全不建檔；所有呼叫 fail-safe。
    try:
        map_recorder = MiningMapRecorder.for_device(ip, str(device_cfg.get("backend", "adb")))
        map_recorder.start(
            planner=planner_version,
            depth_base=depth_tracker.depth,
            inv={"pickaxe": count, **items_available},
        )
    except ForceSleepRequested:
        _safe_end_map_recorder(None)
        _emit_session_summary("force_sleep")
        raise
    except Exception as exc:
        fatal_totals = {
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        }
        _emit_exception_event("session_start", exc)
        miner_logger.exception("[MiningService] map recorder 啟動例外，停止挖礦: %s", exc)
        _safe_end_map_recorder(fatal_totals)
        _emit_session_summary("exception")
        return
    # Only the executor receives proxies; the rest of the service keeps the
    # original uiautomator2 objects, while all executor screenshots/classifier
    # calls are included in the same session counters.
    execution_device = _TelemetryDeviceProxy(d, telemetry)
    execution_clf = _TelemetryClassifierProxy(clf, telemetry)

    def _exec_counts(res) -> Dict[str, int]:
        return {
            "shovels": int(getattr(res, "shovels_used", 0) or 0),
            "bombs": int(getattr(res, "bombs_used", 0) or 0),
            "drills": int(getattr(res, "drills_used", 0) or 0),
            "verification": list(getattr(res, "verification_events", []) or []),
        }

    def _record_map_round(steps, exec_dict) -> None:
        # 讀 loop 內即時 locals（board/known_board/count/...）。recorder 內部已吞例外。
        below = known_board[len(board):] if known_board else None
        exec_payload = dict(exec_dict or {})
        exec_payload.update(
            telemetry.snapshot(
                screenshots_per_round=max(0, telemetry.screenshot_calls - round_screenshot_start)
            )
        )
        exec_payload.setdefault("planner", plan.get("planner_name", planner_version))
        if plan.get("planner_source"):
            exec_payload.setdefault("planner_source", plan["planner_source"])
        map_recorder.round(
            depth=depth_tracker.depth,
            uncertain=depth_tracker.last_uncertain,
            board=board,
            below=below,
            steps=steps,
            exec=exec_payload,
            inv={"pickaxe": count, **items_available},
        )

    iterations = 0
    round_screenshot_start = 0
    round_classify_start = 0
    round_overlay_start = 0
    round_shadow_start = 0
    round_shadow_success_start = 0
    round_shadow_failure_start = 0
    round_shadow_skip_start = 0
    round_shadow_not_attempted_start = 0
    round_shadow_elapsed_start = 0.0
    round_active = False
    round_shadow_accounted = False
    consecutive_empty_plans = 0
    identical_board_count = 0
    prev_exec_sig = None
    stopped_reason = "completed"
    round_event_logged = False

    def _account_shadow_missing() -> None:
        """Close shadow accounting for an active round before re-raising.

        A configured shadow that never returned a result is still one dispatch
        attempt with a skipped result.  Disabled shadows are explicitly marked
        ``not_attempted`` so calls/result invariants remain aggregatable.
        """
        nonlocal round_shadow_accounted
        if not round_active or round_shadow_accounted:
            return
        round_shadow_accounted = True
        if shadow_version:
            telemetry.shadow_calls += 1
            telemetry.shadow_skipped += 1
        else:
            telemetry.shadow_not_attempted += 1

    def _log_round_telemetry(status: str = "unknown", error: Optional[str] = None) -> None:
        """Write one machine-parseable observation event for every round."""
        nonlocal round_event_logged, round_active
        try:
            active_planner = plan.get("planner_name", planner_version)
        except (NameError, UnboundLocalError):
            active_planner = planner_version
        round_fields = telemetry.round_fields(
            screenshot_start=round_screenshot_start,
            classify_start=round_classify_start,
            overlay_start=round_overlay_start,
            shadow_start=round_shadow_start,
            shadow_success_start=round_shadow_success_start,
            shadow_failure_start=round_shadow_failure_start,
            shadow_skip_start=round_shadow_skip_start,
            shadow_not_attempted_start=round_shadow_not_attempted_start,
            shadow_elapsed_start=round_shadow_elapsed_start,
        )
        miner_logger.info(
            "[MiningTelemetry] event=round round=%d status=%s planner=%s %s"
            % (
                iterations,
                status,
                active_planner,
                _telemetry_fields(
                    telemetry,
                    screenshots_per_round=max(0, telemetry.screenshot_calls - round_screenshot_start),
                ),
            )
        )
        miner_logger.info(
            json.dumps(
                _json_telemetry_payload(
                    telemetry,
                    "round",
                    scope="round",
                    session_id=session_id,
                    device_id=device_id,
                    planner=active_planner,
                    shadow_planner=shadow_version or None,
                    round=iterations,
                    status=status,
                    error=error,
                    **round_fields,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        round_event_logged = True
        # A round is closed once its machine-readable event is emitted.  If a
        # later cleanup path raises, it must not double-account this round.
        round_active = False
    try:
        while count >= 1:
            _check_force_sleep(ip)
            if time.time() - start_time > max_duration_seconds:
                miner_logger.info(f"[MiningService] 已達到時間限制 ({max_duration_minutes} 分鐘)，停止挖礦")
                break

            iterations += 1
            telemetry.rounds += 1
            round_screenshot_start = telemetry.screenshot_calls
            round_classify_start = telemetry.classify_calls
            round_overlay_start = telemetry.overlay_ocr_calls
            round_shadow_start = telemetry.shadow_calls
            round_shadow_success_start = telemetry.shadow_successes
            round_shadow_failure_start = telemetry.shadow_failures
            round_shadow_skip_start = telemetry.shadow_skipped
            round_shadow_not_attempted_start = telemetry.shadow_not_attempted
            round_shadow_elapsed_start = telemetry.shadow_elapsed_ms
            round_active = True
            round_shadow_accounted = False
            round_event_logged = False
            # Shared frame for this loop: pickaxe/item OCR + board classification.
            shared_frame = _counted_screenshot(d, telemetry, format="opencv")
            # Count the service-level OCR probe even when it finds no overlay;
            # this is the expensive call P1-09 asks us to quantify.
            telemetry.overlay_ocr_calls += 1
            # 礦洞彈窗在「挖到 pit」後可能被遮蓋，executor 的本地清理不一定夠 —
            # 每輪重新做一次 OCR 檢查並 click_white，避免主迴圈被卡在彈窗上。
            if _dismiss_mining_overlay_if_needed(d, shared_frame, miner_logger):
                shared_frame = _counted_screenshot(d, telemetry, format="opencv")
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

            telemetry.classify_calls += 1
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
            if last_board_signature is not None and state_signature != last_board_signature:
                if blocked_action_signatures:
                    miner_logger.info("[MiningService] 版面已變化，清空非法操作封鎖清單")
                    blocked_action_signatures.clear()
                # 「連續失敗」只在同一個未變版面內計算；真正有進度後，
                # 下一次驗證失敗應重新視為第一次，而非沿用舊盤面的計數。
                item_failure_streaks.clear()
            last_board_signature = state_signature

            current_items = items_available.copy() if USE_ITEMS else {"drill": 0, "bomb": 0}
            # web_h5：由 WS 0x0c01 重建視窗下方已知地形，餵給 final_v1（主或 shadow）。
            # adb 拿不到下方狀況，read_ws_below_rows 回 [] → 維持純 7 列 CNN 視野。
            known_board: Optional[List[List[str]]] = None
            if "final_v1" in (planner_version, shadow_version):
                below_rows = read_ws_below_rows(d)
                if below_rows:
                    known_board = [list(row) for row in board] + below_rows
                    miner_logger.info(
                        f"[MiningService] WS 已知地形擴充 7+{len(below_rows)} 列 (web_h5)"
                    )
            plan_started_at = time.perf_counter()
            plan, plan_title = _dispatch_planner(
                board, count, current_items, blocked_action_signatures, planner_version, miner_logger,
                depth=depth_tracker.depth, device=ip, known_board=known_board,
            )
            plan_elapsed_ms = (time.perf_counter() - plan_started_at) * 1000.0
            # Shadow：同一盤面/庫存快照額外計算，只記錄不執行；失敗只留 log。
            shadow_payload = None
            if shadow_version:
                round_shadow_accounted = True
                telemetry.shadow_calls += 1
                shadow_started_at = time.perf_counter()
                try:
                    try:
                        shadow_payload = _compute_shadow_plan(
                            board, count, current_items, blocked_action_signatures, shadow_version, miner_logger,
                            known_board=known_board,
                        )
                    except Exception as exc:
                        # Shadow 只做觀測；實作例外不可中止主挖礦迴圈。
                        miner_logger.warning("[MiningService] shadow planner wrapper failed: %s", exc)
                        shadow_payload = {
                            "ok": False,
                            "planner": shadow_version,
                            "error": str(exc),
                        }
                finally:
                    telemetry.shadow_elapsed_ms += (
                        time.perf_counter() - shadow_started_at
                    ) * 1000.0
                if shadow_payload is None:
                    # 已配置但沒有結果，算一次 skipped；calls 仍代表真正嘗試過。
                    telemetry.shadow_skipped += 1
                elif shadow_payload.get("ok") is True:
                    telemetry.shadow_successes += 1
                else:
                    telemetry.shadow_failures += 1
            else:
                # 未啟用的輪次不是嘗試；維持 calls/result invariant，另記缺席輪次。
                round_shadow_accounted = True
                telemetry.shadow_not_attempted += 1
            if shadow_payload is not None:
                plan["shadow"] = shadow_payload
            _log_planner_stats(plan, planner_version, plan_elapsed_ms, len(blocked_action_signatures), miner_logger, depth_tracker.depth)

            _check_force_sleep(ip)

            if not plan.get("ok"):
                miner_logger.warning(f"[Mining] 規劃失敗: {plan.get('message', '未知錯誤')}")
                miner_logger.warning(f"  剩餘寶箱: {plan.get('remaining_pits', '?')}, 底層開啟: {plan.get('floor7_open', '?')}")
                _record_map_round([], {"ok": False, "reason": "plan_failed"})
                _log_round_telemetry("plan_failed")
                consecutive_empty_plans += 1
                if consecutive_empty_plans >= _MAX_EMPTY_PLANS:
                    miner_logger.warning(
                        f"[MiningService] 連續 {consecutive_empty_plans} 次取得空 plan，中止挖礦迴圈"
                    )
                    break
                continue

            if not plan.get("steps"):
                _diagnose_empty_plan(board, plan, miner_logger)
                _record_map_round([], {"ok": False, "reason": "no_steps"})
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
                                execution_device, execution_clf, board,
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
                            _log_round_telemetry("forced_descent")
                            continue
                consecutive_empty_plans += 1
                _log_round_telemetry("no_steps")
                if consecutive_empty_plans >= _MAX_EMPTY_PLANS:
                    miner_logger.warning(
                        f"[MiningService] 連續 {consecutive_empty_plans} 次取得空 plan，中止挖礦迴圈"
                    )
                    break
                continue

            # 拿到 non-empty plan，歸零空 plan 計數
            consecutive_empty_plans = 0

            if _verify_items_pre_execution(
                d, plan, items_available, item_blacklist, zero_streaks, zero_streak_limit, miner_logger,
                telemetry=telemetry,
            ):
                _log_round_telemetry("item_replan")
                continue

            print_plan_result(miner_logger, plan_title, plan, board)
            deadline = start_time + max_duration_seconds
            _check_force_sleep(ip)
            # 動作前 authoritative inventory 快照（telemetry 用；spec Telemetry 段要求
            # CNN/ADB 路徑與 WS 同水準記錄執行確認/拒絕原因/前後庫存）
            inv_before = {"pickaxe": count, **items_available}
            try:
                exec_result = execute_plan_steps(
                    execution_device, execution_clf, board, plan["steps"],
                    rl_recorder=rl_recorder, deadline=deadline
                )
            except NoBoardChangeError as exc:
                count = _apply_partial(exc.partial_result, count, items_available, miner_logger)
                action_signature = _step_signature(exc.step)
                if exc.item_type:
                    # 道具驗證可能只是 refresh_failed／動畫延遲。先封鎖
                    # 這個落點並重規劃；同一未變版面連續第二次才停用
                    # 整個道具，避免單次 WinError 10038 誤殺本場道具。
                    blocked_action_signatures.add(action_signature)
                    failures, should_blacklist = _record_item_failure(
                        exc.item_type, item_failure_streaks
                    )
                    if should_blacklist:
                        item_blacklist.add(exc.item_type)
                        items_available[exc.item_type] = 0
                        zero_streaks[exc.item_type] = max(
                            zero_streaks.get(exc.item_type, 0), zero_streak_limit
                        )
                        miner_logger.warning(
                            f"[MiningService] {exc.item_type} 連續 {failures} 次驗證失敗，"
                            "加入黑名單直到下次挖礦重置"
                        )
                    else:
                        miner_logger.info(
                            f"[MiningService] {exc.item_type} 驗證暫時失敗（第 {failures} 次），"
                            f"封鎖落點並重試：{action_signature}"
                        )
                else:
                    blocked_action_signatures.add(action_signature)
                    miner_logger.warning(f"[MiningService] 鎬子操作後版面未變，將操作加入黑名單直到版面變化: {action_signature}")
                miner_logger.info(
                    "[MiningTelemetry] planner=%s exec=rejected reason=no_board_change "
                    "step=%s item=%s inv_before=%s inv_after=%s %s"
                % (plan.get("planner_name", planner_version), action_signature, exc.item_type or "-",
                       inv_before, {"pickaxe": count, **items_available},
                       _telemetry_fields(
                           telemetry,
                           screenshots_per_round=max(0, telemetry.screenshot_calls - round_screenshot_start),
                       ))
                )
                _record_map_round(
                    plan.get("steps", []),
                    {"ok": False, "reason": "no_board_change", **_exec_counts(exc.partial_result)},
                )
                _log_round_telemetry("no_board_change")
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
                miner_logger.info(
                    "[MiningTelemetry] planner=%s exec=rejected reason=out_of_item "
                    "item=%s live_count=%s inv_before=%s inv_after=%s %s"
                % (plan.get("planner_name", planner_version), exc.item_type, exc.live_count,
                       inv_before, {"pickaxe": count, **items_available},
                       _telemetry_fields(
                           telemetry,
                           screenshots_per_round=max(0, telemetry.screenshot_calls - round_screenshot_start),
                       ))
                )
                _record_map_round(
                    plan.get("steps", []),
                    {"ok": False, "reason": "out_of_item", **_exec_counts(exc.partial_result)},
                )
                _log_round_telemetry("out_of_item")
                continue

            # Plan executed cleanly — credit shovels / items consumed.
            count = _apply_partial(exec_result, count, items_available, miner_logger)
            # Any successful use of an item breaks its consecutive failure
            # streak.  Keep dig/action blocking semantics independent.
            for item_name in _count_planned_item_uses(plan.get("steps", [])):
                item_failure_streaks.pop(item_name, None)
            miner_logger.info(
                "[MiningTelemetry] planner=%s exec=ok steps_planned=%d steps_completed=%s "
                "terminated=%s shovels_used=%s bombs_used=%s drills_used=%s "
                "inv_before=%s inv_after=%s verification=%s %s"
            % (plan.get("planner_name", planner_version), len(plan["steps"]), exec_result.steps_completed,
                   exec_result.terminated_reason or "-", exec_result.shovels_used,
                   exec_result.bombs_used, exec_result.drills_used,
                   inv_before, {"pickaxe": count, **items_available},
                   exec_result.verification_events,
                   _telemetry_fields(
                       telemetry,
                       screenshots_per_round=max(0, telemetry.screenshot_calls - round_screenshot_start),
                   ))
            )
            _record_map_round(
                plan["steps"],
                {"ok": True, "reason": exec_result.terminated_reason or "ok",
                 **_exec_counts(exec_result)},
            )
            _log_round_telemetry("ok")

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

    except ForceSleepRequested as exc:
        # 強制休眠是上層喚醒策略的控制訊號，不可被全域保險吞掉。
        # Active rounds still need a complete exception + round telemetry pair.
        stopped_reason = "force_sleep"
        _account_shadow_missing()
        _emit_exception_event("main_loop", exc)
        if iterations and round_active and not round_event_logged:
            _log_round_telemetry(
                "exception",
                error=f"{type(exc).__name__}: {exc}",
            )
        raise
    except Exception as exc:
        stopped_reason = "exception"
        fatal_totals = {
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        }
        _account_shadow_missing()
        _emit_exception_event("main_loop", exc)
        if iterations and round_active and not round_event_logged:
            _log_round_telemetry(
                "exception",
                error=f"{type(exc).__name__}: {exc}",
            )
        miner_logger.exception("[MiningService] 主迴圈未預期例外，停止挖礦: %s", exc)
    finally:
        # 無論正常停止或主迴圈例外，都要完成兩個 recorder 的收尾。
        _emit_session_summary(stopped_reason)
        # Keep the recorder's existing end/totals contract untouched;
        # telemetry is already present in every round JSON event and in
        # the standalone session-summary event above.
        _safe_end_map_recorder(fatal_totals)
        if rl_recorder:
            try:
                rl_recorder.flush()
                summary = rl_recorder.summary()
                print(f"\n[RL 記錄] 共 {summary['total']} 筆事件，檔案: {summary['log_path']}")
            except Exception as exc:
                miner_logger.warning("[MiningService] RL recorder 收尾失敗: %s", exc)

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
    "MiningTelemetryCounters",
    "_dismiss_mining_overlay_if_needed",
    "print_plan_result",
    "execute_plan_steps",
    "check_pickaxe_count",
    "check_points",
    "DEFAULT_CLASSES",
]
