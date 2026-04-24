"""Per-device startup-sleep handler (Phase 6 extraction).

Wait a few minutes after process start before firing the first wake for
specific emulator slots, to stagger initialization I/O.

The budget table and process-start timestamp are module-level so tests
can monkeypatch them. Renamed from private `_STARTUP_SLEEP_SEC_BY_DEVICE`
and `_PROCESS_START_TS` to public `STARTUP_SLEEP_SEC_BY_DEVICE` /
`PROCESS_START_TS` for patchability; `new_main_v2.py` re-exports both.
"""
from __future__ import annotations

import time

import bot_state


STARTUP_SLEEP_SEC_BY_DEVICE: dict[str, int] = {
    "emulator-5554": 3 * 60,
    "emulator-5556": 3 * 60,
    "emulator-5560": 3 * 60,
}

PROCESS_START_TS: float = time.time()


def _handle_startup_sleep(ip: str, device_logger) -> None:
    startup_sleep_sec = int(STARTUP_SLEEP_SEC_BY_DEVICE.get(ip, 0) or 0)
    elapsed_sec = int(time.time() - PROCESS_START_TS)
    remaining_startup_sleep = max(0, startup_sleep_sec - elapsed_sec)
    if remaining_startup_sleep > 0:
        for remain in range(remaining_startup_sleep, 0, -1):
            if ip == "emulator-5554":
                if (
                    bot_state.has_pending_online_check_request("emulator-5554")
                    or bot_state.is_online_check_priority_active("emulator-5554")
                ):
                    device_logger.info(f"[{ip}] 收到 emulator-5558 上線檢查請求，提前結束啟動休眠")
                    break
            if bot_state.has_pending_web_launch_request(ip):
                device_logger.info(f"[{ip}] 收到中控啟動請求，提前結束啟動休眠")
                break
            bot_state.update_state(ip, task="啟動後休眠", step=f"{remain} 秒後開始執行")
            time.sleep(1)
