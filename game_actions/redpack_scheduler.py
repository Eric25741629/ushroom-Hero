"""Redpack (紅包) check scheduler — Task 0 of the daily pipeline.

Extracted from new_main_v2._run_redpack_check_if_due as Phase 11C of the
slim-down plan. Gating + behavior preserved exactly: web_h5 backend with
a live Playwright page only; ADB-backend and headless web_h5 sessions
skip with zero cost.
"""
from __future__ import annotations

import bot_state
import config_manager
from game_actions.scheduler_policy import SchedulerPolicy
from utils.logging_utils import logger


def _redpack_enabled(ip: str) -> bool:
    cfg = config_manager.get_device_config(ip) or {}
    return str(cfg.get("backend", "")).lower() == "web_h5"


_POLICY = SchedulerPolicy(enabled_hook=_redpack_enabled)


def _is_enabled(ip: str) -> bool:
    return _POLICY.is_enabled(ip, get_device_config=config_manager.get_device_config)


def _is_due(ip: str) -> bool:
    # 紅包狀態由 WS 即時查詢，沒有本地完成記錄。
    return _POLICY.is_due(ip)


def _mark_done(ip: str) -> None:
    _POLICY.mark_done(ip)


def run_redpack_check_if_due(d, ip: str) -> None:
    """Claim any pending 紅包 via WS API.

    Runs on every device with `backend == web_h5` and a live Playwright
    page. ADB-backend devices and web_h5 devices without an attached page
    skip entirely (no cost, no behavior change).

    Two-stage detection (`utils.redpack_detector.claim_all_pending`):
        1. send 0x2605, parse list (~50-150ms one WS roundtrip)
        2. try grab on each via 0x2603
    Failures and "already-claimed" errors are logged but non-fatal.
    """
    if not _is_enabled(ip) or not _is_due(ip):
        return
    page = getattr(d, "_page", None)
    if page is None:
        return
    try:
        from utils.redpack_detector import claim_all_pending
        claimed, results = claim_all_pending(page)
    except Exception as e:
        logger.warning(f"[{ip}] 紅包檢查發生例外 (non-fatal): {e}")
        return

    if not results:
        logger.info(f"[{ip}] 紅包檢查: gate off (無未讀)")
        return

    bot_state.update_state(ip, task="紅包檢查", step=f"嘗試 {len(results)} 個")
    summary = []
    for r in results:
        if r.success:
            summary.append(f"OK#{r.bag_id}")
        else:
            ec = r.error_code if r.error_code is not None else "?"
            summary.append(f"ERR{ec}#{r.bag_id}")
    logger.info(
        f"[{ip}] 紅包檢查: claimed={claimed}/{len(results)} | {', '.join(summary)}"
    )
