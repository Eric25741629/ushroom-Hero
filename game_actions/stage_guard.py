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

    Experimental: web_h5 devices with `experimental_cocos_navigation: true`
    get a cocos-tree fast-path that confirms "主頁面" in single-digit ms
    instead of ~1-3s of OCR. Non-main states still go through legacy OCR
    (so 異地登錄/車位倉庫/家族戰/公告 etc. continue to be detected).
    """
    from utils.page_detector import try_detect_main_page_fast
    fast_stage = try_detect_main_page_fast(d, ip)
    if fast_stage:
        logger.info(f"[{ip}] stage via cocos fast-path: {fast_stage}")
        return fast_stage

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
