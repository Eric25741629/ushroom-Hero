"""單一 client backend 任務的 registry executor adapter。

W9 的任務只允許由 Web H5 執行。這裡只做延遲委派，讓 registry loop 能在
選定 backend 後真正消費 executor；due、登入與頁面流程仍由既有 scheduler
負責。
"""
from __future__ import annotations

from typing import Any, Callable


def run_dragon_realm(
    device: Any,
    ip: str,
    *,
    action: Callable[[], Any] | None = None,
) -> Any:
    """執行龍骸聖域的 H5 client action。"""
    if action is not None:
        return action()
    from game_actions.dragon_realm_scheduler import run_dragon_realm_if_due

    return run_dragon_realm_if_due(ip, device)


def run_fannaoxiao(
    device: Any,
    ip: str,
    *,
    action: Callable[[], Any] | None = None,
) -> Any:
    """執行煩惱消的 H5 client action。"""
    if action is not None:
        return action()
    from game_actions.fannaoxiao_scheduler import run_fannaoxiao_if_due

    return run_fannaoxiao_if_due(device, ip)


__all__ = ["run_dragon_realm", "run_fannaoxiao"]
