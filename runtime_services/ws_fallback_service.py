"""Offline pure-WS fallback round for ADB phones (ws_token.offline_fallback).

When an ADB phone is unreachable during device init, the legacy behavior is
``handle_connect_failure`` + ``set_offline`` + ``return`` — the thread dies and
no WS task runs until the next scan respawns it. For devices that opt into
``ws_token.offline_fallback``, ``new_main_v2.main`` instead loops:

    while connect fails and offline_fallback on:
        run one WS phase round (idle reward / lamp / mining via ws_phase)
        aligned sleep (same hourly parity schedule as normal)
        retry initialize_runtime_device

Host gate: master + worker sync the same bot_config.json over the NAS, so both
hosts would otherwise inject/run the fallback — and the "wrong" host's hourly
WS login would kick the phone session while the right host is driving it
normally. ``ws_fallback_host_allowed`` resolves which single host may run it:
``ws_token.fallback_host`` set -> only the matching hostname (case-insensitive);
unset -> only the host whose resolved global mode is "master".

The round lives here as a small, light-to-import helper so it can be unit
tested (and imported by device_scan_service) without dragging in heavy import
trees — ``run_sleep_cycle`` is loaded lazily (mirrors ws_runner_service's
``_load_run_device`` seam). Any exception inside the round is swallowed and
logged so the device thread never dies on a bad WS pass; a failed sleep cycle
additionally applies a floor sleep so the caller's retry-``continue`` can never
hot-spin WS logins.

Flag default is off → every other device's behavior is byte-for-byte unchanged.
"""
from __future__ import annotations

import time
from typing import Callable

import bot_state
import config_manager

# run_sleep_cycle 失敗時的保底睡眠，避免外層 continue 變成緊迴圈狂打 WS 登入。
_SLEEP_FAILURE_FLOOR_SEC = 60


def _load_run_sleep_cycle():
    """Lazy seam: keeps this module light to import (sleep_service pulls in
    game_initialization / web_session_service). Tests monkeypatch this."""
    from runtime_services.sleep_service import run_sleep_cycle
    return run_sleep_cycle


def _is_login_conflict_error(exc: BaseException) -> bool:
    """識別 WS-first 控制訊號，避免被 offline fallback 的保底 catch 吞掉。"""
    try:
        from game_actions.stage_guard import LoginConflictError
    except Exception:  # noqa: BLE001 — 一般 fallback 錯誤維持輕量處理
        return False
    return isinstance(exc, LoginConflictError)


def ws_fallback_host_allowed(ws_cfg) -> bool:
    """True when THIS host is the one allowed to run the offline WS fallback.

    ``fallback_host`` set -> hostname must match (case-insensitive).
    ``fallback_host`` unset/empty -> only the resolved-mode=="master" host
    (mode from ``get_global_config()``, i.e. after host_settings overrides).
    """
    ws_cfg = ws_cfg or {}
    fallback_host = str(ws_cfg.get("fallback_host") or "").strip()
    if fallback_host:
        return config_manager.get_hostname().strip().lower() == fallback_host.lower()
    try:
        mode = str(config_manager.get_global_config().get("mode", "master")).strip().lower()
    except Exception:
        return False
    return mode == "master"


def should_ws_fallback(ip: str, backend_kind: str) -> bool:
    """True when this device should take the offline pure-WS wait path.

    Gated on the backend actually in use (``backend_kind`` is authoritative —
    web_h5 never qualifies) AND ``ws_token.enabled`` AND
    ``ws_token.offline_fallback`` AND the host gate (double insurance with the
    scan-injection gate: a worker that briefly saw the serial on adb and
    spawned a thread must NOT enter the fallback loop after disconnect).
    """
    if str(backend_kind).strip().lower() != "adb":
        return False
    try:
        cfg = config_manager.get_device_config(ip) or {}
    except Exception:
        return False
    ws_cfg = cfg.get("ws_token") or {}
    if not (bool(ws_cfg.get("enabled", False)) and bool(ws_cfg.get("offline_fallback", False))):
        return False
    return ws_fallback_host_allowed(ws_cfg)


def run_ws_fallback_wait_round(
    ip: str,
    logger_obj,
    *,
    run_ws_phase_fn: Callable[[str, object], object],
    enable_dungeon_manager: bool,
    run_offline_tasks_fn: Callable[[str, object], object] | None = None,
) -> None:
    """Run one offline WS round: status update -> WS phase -> aligned sleep.

    ``run_ws_phase_fn`` is injected (new_main_v2._run_ws_phase_for_wake) so the
    heavy ws_phase import stays out of this module. Never raises: a failed WS
    pass or sleep is logged and the thread continues to the next retry.
    """
    try:
        bot_state.update_state(
            ip, task="手機離線", step="WS 備援掛機中，等待手機回線"
        )
    except Exception as exc:  # status update is best-effort
        logger_obj.warning(f"[{ip}] WS 備援狀態更新失敗（忽略）: {exc}")

    try:
        run_ws_phase_fn(ip, logger_obj)
    except Exception as exc:
        if _is_login_conflict_error(exc):
            # 異地登入必須回到 new_main 的 30 分鐘 runtime policy；不可照一般
            # WS 失敗繼續本輪休眠，否則控制訊號會遺失。
            raise
        logger_obj.warning(f"[{ip}] WS 備援階段未預期錯誤（忽略，照常排程休眠）: {exc}")

    if run_offline_tasks_fn is not None:
        try:
            run_offline_tasks_fn(ip, logger_obj)
        except Exception as exc:
            logger_obj.warning(f"[{ip}] 離線 WS 排程任務失敗（忽略，照常休眠）: {exc}")

    try:
        _load_run_sleep_cycle()(
            ip,
            logger_obj,
            sleep_policy="phone_offline_ws_only",
            sleep_reason="手機離線 WS 備援",
            enable_dungeon_manager=enable_dungeon_manager,
        )
    except Exception as exc:
        logger_obj.warning(
            f"[{ip}] WS 備援休眠週期失敗（保底睡 {_SLEEP_FAILURE_FLOOR_SEC}s 防緊迴圈）: {exc}"
        )
        time.sleep(_SLEEP_FAILURE_FLOOR_SEC)
