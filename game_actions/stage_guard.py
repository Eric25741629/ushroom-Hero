"""Stage-guard helpers used by main() and the daily task pipeline.

Extracted from new_main_v2.py (Phase 3 of slim-down plan). Provides:

- `LoginConflictError`: raised when 異地登錄 is detected mid-flow; caller
  is expected to put the device into the login-conflict sleep window.
- `get_stage_with_check`: wrap `resolve_stage_until_stable` and convert
  the "異地登錄" stage into a hard stop + exception.
- `_run_at_main_page`: run a task callable only when the current stage
  is 主頁面; otherwise record a mismatch screenshot.
"""
from __future__ import annotations

from typing import Optional

import bot_state
import config_manager  # noqa: F401  (kept for parity; original module imports it)
from game_initialization import resolve_stage_until_stable
from runtime_services.web_session_service import mark_login_conflict_sleep
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch

# reward is imported lazily inside get_stage_with_check — reward_manager's
# transitive deps (tools → adb_operations.tap_device) pull in heavy adb
# wiring at import time, which breaks tests that stub adb_operations
# before loading this module. Callers invoke get_stage_with_check at
# runtime only, so the lazy path is fine.


class LoginConflictError(Exception):
    """自定義異常：用於處理異地登錄並終止當前喚醒 session"""
    pass


def get_stage_with_check(d, ip, Cnn_model, img=None):
    """
    使用與啟動流程相同的狀態判斷器。
    先清掉已知首頁彈窗，再回傳穩定 stage。

    Web H5 uses the strict Cocos state contract. A known non-home state and a
    Cocos probe failure are both returned explicitly; neither enters the ADB
    OCR resolver. ADB devices keep the existing resolver unchanged.
    """
    from utils.page_detector import H5State, probe_h5_state

    h5_result = probe_h5_state(d, ip)
    if h5_result.state is not H5State.ADB_LEGACY:
        if h5_result.state is H5State.H5_MAIN:
            logger.info(f"[{ip}] stage via cocos state: 主頁面")
            return "主頁面"
        if h5_result.state is H5State.H5_STATE_UNAVAILABLE:
            logger.warning(
                f"[{ip}] Web H5 stage unavailable，停止目前任務，"
                f"禁止 OCR fallback: {h5_result.reason or 'unknown'}"
            )
            return h5_result.legacy_stage()
        if h5_result.state is H5State.H5_NON_HOME:
            logger.info(
                f"[{ip}] Web H5 非首頁狀態，停止目前任務: "
                f"{getattr(h5_result.page_state, 'value', None)}"
            )
            return h5_result.legacy_stage()

        # 已知前景 popup 仍交給既有 resolver 處理；resolver 內的 get_stage
        # 也只會重做 Cocos probe，不會回到 OCR。
        logger.info(
            f"[{ip}] Web H5 已知 popup，使用 Cocos stage resolver: "
            f"{getattr(h5_result.page_state, 'value', None)}"
        )

    from game_actions.reward_manager import reward  # lazy — see module header
    stage = resolve_stage_until_stable(
        d,
        ip,
        Cnn_model=Cnn_model,
        reward_fn=reward,
        logger=logger,
        img=img,
    )
    if stage == "異地登錄":
        logger.warning(f"[{ip}] 全域偵測到異地登錄，強制停止遊戲")
        d.app_stop("com.mxdzz.tw.and")
        mark_login_conflict_sleep(ip)
        raise LoginConflictError("偵測到異地登錄")
    return stage


def _run_at_main_page(
    d,
    ip: str,
    Cnn_model,
    task_name: str,
    mismatch_reason: str,
    fn,
    *,
    step: str = "執行中",
    log: Optional[str] = None,
) -> str:
    """Fetch stage, run fn() on main page, else log mismatch. Returns stage."""
    stage = get_stage_with_check(d, ip, Cnn_model)
    if stage == "主頁面":
        if log:
            bot_state.update_state(ip, task=task_name, step=step, log=log)
        else:
            bot_state.update_state(ip, task=task_name, step=step)
        fn()
    else:
        log_main_page_mismatch(d, ip, stage, task_name, mismatch_reason)
    return stage
