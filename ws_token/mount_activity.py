"""伺服器活動型別探測：坐騎衝刺只允許在同服活動真的開啟時執行。"""
from __future__ import annotations
from typing import Optional
from ws_token import relic_sprint
from ws_token.client import WSError, WSTimeoutError
ACT_TYPES: tuple[int, ...] = (9, 266)
def find_active_act_type(client, *, timeout: Optional[float] = None) -> Optional[int]:
    """向伺服器探測目前是否為坐騎衝刺，未開啟時回傳 None。"""
    for act_type in ACT_TYPES:
        try:
            snapshot = relic_sprint.read_sprint(client, act_type, timeout=timeout)
        except (WSTimeoutError, WSError):
            continue
        if snapshot.get("open"):
            return int(snapshot.get("act_type") or act_type)
    return None
