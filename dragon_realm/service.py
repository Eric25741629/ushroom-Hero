"""龍骸聖域迴圈協調。

read_state -> decide -> dispatch -> 等下一個更新。終止：planner STOP、
wall-clock/action 預算、dead-loop（連續 WAIT 無進展）。

``run_loop`` 為純邏輯（吃任意 client 介面 + 注入 sleep），可單測。
``run(ip, d)`` 是 runtime 入口：建 DragonClient + pause_guard + 錯誤截圖。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from dragon_realm import constants as C
from dragon_realm.planner import Action, Prefs, decide
from dragon_realm.state import DragonState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopLimits:
    max_actions: int = 200          # 動作預算（含探索）
    max_wait_iters: int = 30        # 連續 WAIT 上限 -> dead-loop
    wait_sleep_sec: float = 3.0     # WAIT 時的輪詢間隔
    sleep_fn: Optional[Callable[[float], None]] = None


@dataclass
class LoopReport:
    stop_reason: str = ""
    actions: int = 0
    waits: int = 0


def run_loop(client, config, prefs: Prefs, limits: LoopLimits) -> LoopReport:
    sleep = limits.sleep_fn or (lambda s: __import__("time").sleep(s))
    report = LoopReport()
    consecutive_wait = 0
    while True:
        state = client.read_state()
        action = decide(state, config, prefs)

        if action.kind == C.A_STOP:
            report.stop_reason = action.reason
            return report

        if action.kind == C.A_WAIT:
            consecutive_wait += 1
            report.waits += 1
            if consecutive_wait >= limits.max_wait_iters:
                report.stop_reason = "deadloop"
                return report
            sleep(limits.wait_sleep_sec)
            continue

        consecutive_wait = 0
        client.dispatch(action)
        report.actions += 1
        if report.actions >= limits.max_actions:
            report.stop_reason = "budget_exhausted"
            return report


def run(ip: str, d) -> LoopReport:
    """Runtime 入口（H5 only）。adb 後端直接回 aborted。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.info("[dragon_realm] %s -- H5 only (no _page), skip", ip)
        return LoopReport(stop_reason="not_h5")

    from dragon_realm.client import DragonClient
    import config_manager
    from utils import pause_guard
    from utils.screenshot_helpers import save_error_screenshot

    dev = config_manager.get_device_config(ip)
    my_role_id = int(dev.get("dragon_realm_role_id", 0))
    prefs = Prefs(
        my_role_id=my_role_id,
        assist_teammates=bool(dev.get("dragon_realm_assist", True)),
        auto_open_box=bool(dev.get("dragon_realm_open_box", True)),
    )

    client = DragonClient(page, my_role_id)
    pause_guard.bind(ip=ip, page=page)
    try:
        if not client.install():
            logger.warning("[dragon_realm] %s -- netManager 不可用，略過", ip)
            return LoopReport(stop_reason="no_netmanager")
        config = _load_config(client)
        return run_loop(_StateAdapter(client, my_role_id), config, prefs, LoopLimits())
    except Exception:
        logger.exception("[dragon_realm] %s -- 迴圈異常", ip)
        save_error_screenshot(d, ip, "dragon_realm", "dragon_realm exception")
        return LoopReport(stop_reason="error")
    finally:
        pause_guard.unbind()


class _StateAdapter:
    """把 DragonClient.read_raw() 包成 run_loop 期望的 read_state()/dispatch()。"""

    def __init__(self, client, my_role_id: int):
        self._client = client
        self._role = my_role_id

    def read_state(self) -> DragonState:
        return DragonState.from_raw(
            self._client.read_raw().get("raw") or {"activity_open": False},
            self._role,
        )

    def dispatch(self, action: Action) -> None:
        self._client.dispatch(action)


def _load_config(client):
    """讀一次 config KV（靜態）。實作對照 DRAGON_REALM_SCHEMA.md 的 accessor。"""
    from dragon_realm.config_loader import load_dragon_config
    return load_dragon_config(client)
