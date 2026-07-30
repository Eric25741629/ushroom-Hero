"""Carpark (車位) reconcile scheduler — Task 0.5 of the daily pipeline.

Extracted from new_main_v2._run_carpark_check_if_due as Phase 11C of the
slim-down plan. Same gating as redpack but stacks two additional config
keys: experimental_cocos_navigation must be on AND device cfg must have
`carpark.enabled: true`.
"""
from __future__ import annotations

import bot_state
import config_manager
from utils.logging_utils import logger


def _ws_owns_warehouse_claim(cfg: dict) -> bool:
    """WS 車位計畫啟用時，倉庫收益由 12846 負責領取。"""
    ws_cfg = cfg.get("ws_token") or {}
    plan_cfg = ws_cfg.get("carpark_plan") or {}
    return bool(ws_cfg.get("enabled") and plan_cfg.get("enabled"))


def run_carpark_check_if_due(d, ip: str) -> None:
    """Experimental: keep cross-server park deployment aligned to the
    daytime/nighttime targets via cocos UI clicks.

    Gating (same as redpack check):
        1. `experimental_cocos_navigation: true`
        2. `backend == web_h5`
        3. live Playwright page
        4. device cfg has `carpark.enabled: true`

    Other devices skip entirely — no cost, no behavior change.
    """
    from utils.cocos_navigator import _device_flag_enabled
    if not _device_flag_enabled(ip):
        return
    cfg = config_manager.get_device_config(ip) or {}
    if str(cfg.get("backend", "")).lower() != "web_h5":
        return
    carpark_cfg = cfg.get("carpark") or {}
    if not carpark_cfg.get("enabled"):
        return
    page = getattr(d, "_page", None)
    if page is None:
        return
    try:
        from utils.carpark_auto import reconcile
        from utils.carpark_click_recorder import (
            CarparkClickRecorder, set_recorder, clear_recorder,
        )
        from utils import pause_guard
        rec = CarparkClickRecorder(ip, run_tag="auto")
        set_recorder(rec)
        pause_guard.bind(ip=ip, page=page)
        try:
            summary = reconcile(
                page,
                carpark_cfg,
                claim_warehouse_rewards=not _ws_owns_warehouse_claim(cfg),
            )
        except pause_guard.TaskAborted as exc:
            logger.info(f"[{ip}] 車位檢查 aborted: {exc}")
            return
        finally:
            rec.close()
            clear_recorder()
            pause_guard.unbind()
    except Exception as e:
        logger.warning(f"[{ip}] 車位檢查 exception (non-fatal): {e}")
        return
    bot_state.update_state(ip, task="車位檢查",
                           step=f"snap={summary.get('snapshot')} tgt={summary.get('target')}")
    actions = summary.get("actions") or []
    if actions:
        logger.info(f"[{ip}] 車位檢查: {summary.get('snapshot')} → {summary.get('target')}; "
                    f"actions={actions}")
    else:
        logger.info(f"[{ip}] 車位檢查: 已對齊 {summary.get('snapshot')}")
