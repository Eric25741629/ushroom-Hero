"""同伴衝刺的伺服器進度探測。"""
from __future__ import annotations
from typing import Optional
from ws_token import relic_sprint, skill_sprint
from ws_token.client import WSError, WSTimeoutError
ACT_TYPES: tuple[int, ...] = (7, 268)
def read_progress(client, *, timeout: Optional[float] = None) -> dict:
    """讀取目前開啟的同伴衝刺；協議結構與技能衝刺相同。"""
    for act_type in ACT_TYPES:
        try:
            snapshot = relic_sprint.read_sprint(client, act_type, timeout=timeout)
        except (WSTimeoutError, WSError):
            continue
        if snapshot.get("open"):
            return {"open": True, "act_type": int(snapshot.get("act_type") or act_type),
                    "draws": max(0, int(snapshot.get("accrued", 0) or 0)),
                    "rounds": list(snapshot.get("rounds") or ()),
                    "claimable_rounds": list(snapshot.get("claimable_rounds") or ()),
                    "tasks": list(snapshot.get("tasks") or ())}
    return {"open": False}
def claim_completed_rounds(client, progress: dict, *, timeout=None) -> list[int]:
    """同伴衝刺沿用技能/遺物衝刺的四輪領獎結構。"""
    return skill_sprint.claim_completed_rounds(client, progress, timeout=timeout)
