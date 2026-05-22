"""Redpack (紅包) check scheduler — Task 0 of the daily pipeline.

Extracted from new_main_v2._run_redpack_check_if_due as Phase 11C of the
slim-down plan. Gating + behavior preserved exactly: web_h5 backend with
a live Playwright page only; ADB-backend and headless web_h5 sessions
skip with zero cost.
"""
from __future__ import annotations

import bot_state
import config_manager
from utils.logging_utils import logger


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
    cfg = config_manager.get_device_config(ip) or {}
    if str(cfg.get("backend", "")).lower() != "web_h5":
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
