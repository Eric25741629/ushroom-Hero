"""Pure-WS device loop for the ws_token backend.

Devices with ``use_ws_runner: true`` (or ``backend: "ws_token"``) run here
instead of the ADB/Playwright daily pipeline. The loop:

  - never touches ADB / Playwright (no device init, no game launch),
  - on each wake calls :func:`ws_token.runner.run_device` once and logs the
    :class:`~ws_token.runner.RunReport`,
  - reuses the existing sleep/wake/pause/force-sleep machinery
    (:func:`runtime_services.sleep_service.run_sleep_cycle`,
    :func:`runtime_services.startup_sleep._handle_startup_sleep`,
    ``bot_state`` pause / force-sleep) so schedule parity, minute-offset and
    dashboard controls apply identically to the other backends.

Heavy / runtime-only modules (``ws_token.runner``, the sleep services) are
imported lazily inside the functions so this module stays cheap to import for
unit tests — tests monkeypatch the lazy hooks below.

The cycle is split into :func:`run_ws_device_cycle` (ONE wake → run → report,
no sleep) so it can be unit-tested in isolation, and :func:`run_ws_device_loop`
(the full while-loop with sleep + control handling) which ``new_main_v2.main``
dispatches to.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import bot_state
import config_manager

logger = logging.getLogger(__name__)


def _load_run_device():
    """Lazy import of ws_token.runner.run_device (heavy / WS deps)."""
    from ws_token.runner import run_device
    return run_device


def run_ws_device_cycle(ip: str, cfg: Any, logger_obj) -> Optional[Any]:
    """Run ONE ws_token pass for ``ip`` and log the report.

    Pure of any sleep/loop concerns so it can be unit-tested directly. Returns
    the :class:`~ws_token.runner.RunReport` (or ``None`` if run_device raised).
    ``cfg`` is the device config (a ``DeviceConfig`` or plain dict — only
    ``.get`` is used). A ``login_ok=False`` report is logged as a warning;
    refresh is intentionally NOT attempted here (out of scope for this wiring).
    """
    spend = bool(cfg.get("ws_token_spend", False))
    sweep_list = cfg.get("ws_token_sweep_list") or None

    run_device = _load_run_device()
    bot_state.update_state(ip, task="WS 任務", step="正在執行 ws_token 每日任務")
    try:
        report = run_device(ip, spend=spend, sweep_list=sweep_list)
    except Exception as exc:  # noqa: BLE001 — one bad pass must not kill the thread
        logger_obj.error(f"[{ip}] ws_token run_device 例外: {exc}", exc_info=True)
        bot_state.update_state(ip, task="WS 任務失敗", step=f"run_device 例外: {exc}")
        return None

    login_ok = bool(getattr(report, "login_ok", False))
    tasks = getattr(report, "tasks", {}) or {}
    errors = getattr(report, "errors", {}) or {}
    if not login_ok:
        # ticket missing / login failed — refresh strategy is a later task.
        logger_obj.warning(
            f"[{ip}] ws_token 登入失敗 (login_ok=False, errors={list(errors)})；"
            f"ticket 可能失效，本次跳過（刷新策略後續另做）"
        )
        bot_state.update_state(ip, task="WS 登入失敗", step=f"errors={list(errors)}")
    else:
        logger_obj.info(
            f"[{ip}] ws_token 完成: spend={spend} tasks_ok={list(tasks)} errors={list(errors)}"
        )
        bot_state.update_state(
            ip,
            task="WS 任務完成",
            step=f"tasks_ok={list(tasks)} errors={list(errors)}",
        )
    return report


def run_ws_device_loop(ip: str, logger_obj) -> None:
    """Full pure-WS device loop for ``ip``.

    Mirrors the control surface of the legacy main loop — startup stagger,
    pause, force-sleep, aligned sleep/wake — but with NO device init and NO
    game launch. Lazy-imports the sleep services so importing this module stays
    light for unit tests.
    """
    from runtime_services.device_runtime_service import ForceSleepRequested
    from runtime_services.sleep_service import run_sleep_cycle
    from runtime_services.startup_sleep import _handle_startup_sleep

    bot_state.init_device(ip)
    try:
        _handle_startup_sleep(ip, logger_obj)

        while True:
            force_sleep_now = False
            sleep_policy = "aligned_window"
            sleep_reason = "常規對齊喚醒 (ws_token)"
            try:
                # Respect dashboard force-sleep before doing any work.
                if bot_state.check_force_sleep(ip):
                    raise ForceSleepRequested("force sleep requested from dashboard")
                # Respect pause: block here until resumed. WS devices have no
                # browser to open, so a pending web-launch request is irrelevant
                # — check_pause already returns on that, we simply loop back.
                bot_state.check_pause(ip)
                if bot_state.check_force_sleep(ip):
                    raise ForceSleepRequested("force sleep requested during pause")

                # Re-read config each wake so live toggles (spend/sweep) apply.
                cfg = config_manager.get_device_config(ip)
                run_ws_device_cycle(ip, cfg, logger_obj)
            except ForceSleepRequested as e:
                force_sleep_now = True
                sleep_policy = "force_sleep"
                sleep_reason = "強制休眠"
                logger_obj.warning(f"[{ip}] ws_token 迴圈收到強制休眠請求: {e}")

            enable_dungeon_manager = bool(
                config_manager.get_device_config(ip).get("enable_dungeon_manager", True)
            )
            run_sleep_cycle(
                ip,
                logger_obj,
                force_sleep_now=force_sleep_now,
                sleep_policy=sleep_policy,
                sleep_reason=sleep_reason,
                enable_dungeon_manager=enable_dungeon_manager,
            )
    except Exception as e:  # noqa: BLE001
        logger_obj.error(f"[{ip}] ws_token 迴圈未預期錯誤: {e}", exc_info=True)
        bot_state.update_state(ip, log=f"ws_token 異常中斷: {e}")
    finally:
        bot_state.set_offline(ip, reason="ws_token thread exit")
