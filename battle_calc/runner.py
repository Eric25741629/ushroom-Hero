# -*- coding: utf-8 -*-
"""一場戰鬥：block →（A 已點開戰）→ 取 combat → sim → send result。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from utils.logging_utils import logger

from .config import coerce_battle_mode
from .page_hooks import (
    clear_combat,
    install_hooks,
    send_result,
    set_block_result,
    take_combat,
)
from .simulate import result_body_from_sim, simulate_on_page, simulate_remote


def resolve_mode(cfg: Optional[dict], key: str = "arena_battle_mode") -> str:
    cfg = cfg or {}
    return coerce_battle_mode(cfg.get(key, "animation"))


def run_sim_path(
    page: Any,
    mode: str,
    battle_mode: str,
    *,
    ip: str = "",
    timeout_s: float = 25.0,
    global_cfg: Optional[dict] = None,
    clear_first: bool = True,
) -> Dict[str, Any]:
    """假定呼叫端已觸發開戰。battle_mode 為 local_sim / remote_calc。

    clear_first=False：開戰前已 clear，點擊後立刻呼叫時不要清掉剛到的 combat。
    Returns dict: ok, sim, result_body, path used, err?
    """
    if battle_mode not in ("local_sim", "remote_calc"):
        return {"ok": False, "err": f"not a sim path: {battle_mode}"}

    install_hooks(page)
    set_block_result(page, True)
    if clear_first:
        clear_combat(page, mode)
    try:
        combat = take_combat(page, mode, timeout_s=timeout_s)
        if not combat:
            return {"ok": False, "err": "timeout waiting combat s2c"}
        if combat.get("code") not in (0, None):
            return {"ok": False, "err": f"combat code={combat.get('code')}", "combat": combat}

        if battle_mode == "remote_calc":
            sim = simulate_remote(mode, combat, global_cfg=global_cfg)
            if not sim.get("ok"):
                logger.warning("[%s] battle_calc remote fail → local_sim: %s", ip, sim.get("err"))
                sim = simulate_on_page(page, mode, combat)
        else:
            sim = simulate_on_page(page, mode, combat)

        if not sim.get("ok"):
            return {"ok": False, "err": sim.get("err") or "sim failed", "sim": sim}

        body = result_body_from_sim(mode, combat, sim)
        sent = send_result(page, mode, body)
        if not sent.get("ok"):
            return {"ok": False, "err": sent.get("err") or "send failed", "sim": sim}

        logger.info(
            "[%s] battle_calc %s/%s ok ms=%s result=%s wid=%s precent=%s",
            ip,
            mode,
            battle_mode,
            sim.get("ms"),
            sim.get("result"),
            sim.get("wid"),
            sim.get("precent"),
        )
        return {"ok": True, "sim": sim, "result_body": body, "combat": combat, "path": battle_mode}
    finally:
        set_block_result(page, False)


def enforce_gap(last_ts: float, gap_sec: float) -> float:
    """若距 last_ts 不足 gap_sec 則 sleep；回傳新的 now。"""
    now = time.monotonic()
    if last_ts > 0:
        wait = gap_sec - (now - last_ts)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
    return now
