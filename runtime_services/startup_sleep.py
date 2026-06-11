"""Per-device startup-sleep handler (Phase 6 extraction).

Wait a few minutes after process start before firing the first wake, to
stagger initialization I/O (分流). The per-device delay is *derived from
the same 分流 schedule* used for sleep-cycle wakes (``wake_hour_parity`` +
``wake_minute_offset``): devices launch one ``startup_stagger_sec`` slot
apart, ordered even-before-odd then minute-offset :00 -> :05 -> :15, so
all devices come up one-at-a-time instead of clumping at process start.

``STARTUP_SLEEP_SEC_BY_DEVICE`` is kept as an explicit per-device override
(empty by default = pure config derivation). The override table, gap, and
process-start timestamp are module-level so tests can monkeypatch them.
`new_main_v2.py` re-exports the table and ``PROCESS_START_TS``.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import bot_state
import config_manager
from runtime_services.wake_override_service import apply_manual_wake_override


# Explicit per-device overrides (seconds). Empty by default: every device's
# startup delay is derived from its 分流 config. Populate to pin a specific
# device to a fixed startup delay regardless of the schedule.
STARTUP_SLEEP_SEC_BY_DEVICE: dict[str, int] = {}

# Default seconds between consecutive device launches at startup. Overridable
# via bot_config global.compute.startup_stagger_sec.
DEFAULT_STARTUP_STAGGER_SEC: int = 120

PROCESS_START_TS: float = time.time()


def _parity_rank(value) -> int:
    """Order key for hour-parity: even -> 0, odd -> 1, unset/other -> 2.

    ``bool`` is rejected (treated as unset) so True/False don't pose as 1/0.
    """
    if isinstance(value, bool):
        return 2
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "even":
            return 0
        if v == "odd":
            return 1
        return 2
    if isinstance(value, int) and value in (0, 1):
        return value
    return 2


def _offset_rank(value) -> int:
    """Order key for minute offset: a non-negative int, else 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def compute_stagger_order(devices: Dict[str, dict]) -> List[str]:
    """Deterministic startup launch order for ``devices``.

    Sorted by (parity rank even<odd<unset, minute offset ascending, ip) so
    the order matches the 分流 schedule and is stable across restarts.
    """
    def _key(item):
        ip, cfg = item
        cfg = cfg or {}
        return (
            _parity_rank(cfg.get("wake_hour_parity")),
            _offset_rank(cfg.get("wake_minute_offset")),
            ip,
        )

    return [ip for ip, _ in sorted(devices.items(), key=_key)]


def _resolve_gap_sec() -> int:
    try:
        compute = config_manager.get_global_config().get("compute", {}) or {}
        return int(compute.get("startup_stagger_sec", DEFAULT_STARTUP_STAGGER_SEC))
    except Exception:
        return DEFAULT_STARTUP_STAGGER_SEC


def resolve_startup_stagger_sec(
    ip: str,
    *,
    gap_sec: Optional[int] = None,
    devices: Optional[Dict[str, dict]] = None,
) -> int:
    """Resolve the startup delay (seconds) for ``ip``.

    Precedence: an explicit ``STARTUP_SLEEP_SEC_BY_DEVICE`` entry wins;
    otherwise the delay is ``rank * gap_sec`` where ``rank`` is the device's
    position in :func:`compute_stagger_order`. Unknown devices and a
    non-positive gap resolve to 0 (no startup delay).
    """
    if ip in STARTUP_SLEEP_SEC_BY_DEVICE:
        return int(STARTUP_SLEEP_SEC_BY_DEVICE.get(ip, 0) or 0)

    if gap_sec is None:
        gap_sec = _resolve_gap_sec()
    if gap_sec <= 0:
        return 0

    if devices is None:
        try:
            devices = config_manager.load_config().get("devices", {}) or {}
        except Exception:
            return 0

    order = compute_stagger_order(devices)
    if ip not in order:
        return 0
    return order.index(ip) * int(gap_sec)


def _handle_startup_sleep(ip: str, device_logger) -> None:
    startup_sleep_sec = int(resolve_startup_stagger_sec(ip) or 0)
    elapsed_sec = int(time.time() - PROCESS_START_TS)
    remaining_startup_sleep = max(0, startup_sleep_sec - elapsed_sec)
    if remaining_startup_sleep > 0:
        wake_ts = time.time() + remaining_startup_sleep
        while remaining_startup_sleep > 0:
            next_wake_ts, should_wake_now = apply_manual_wake_override(
                ip, wake_ts, device_logger, task="啟動後休眠"
            )
            if should_wake_now:
                break
            if next_wake_ts != wake_ts:
                wake_ts = next_wake_ts
                remaining_startup_sleep = max(0, int(wake_ts - time.time()))
                if remaining_startup_sleep <= 0:
                    break
            if bot_state.is_online_check_checker(ip):
                if (
                    bot_state.has_pending_online_check_request(ip)
                    or bot_state.is_online_check_priority_active(ip)
                ):
                    device_logger.info(f"[{ip}] 收到 requester 上線檢查請求，提前結束啟動休眠")
                    break
            if bot_state.has_pending_web_launch_request(ip):
                device_logger.info(f"[{ip}] 收到中控啟動請求，提前結束啟動休眠")
                break
            bot_state.update_state(
                ip,
                task="啟動後休眠",
                step=f"{remaining_startup_sleep} 秒後開始執行",
                next_wake_at=wake_ts,
            )
            time.sleep(1)
            remaining_startup_sleep -= 1
