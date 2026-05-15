import time

import bot_state
from utils.mumu_control import MuMuController, discover_control_exe


class ForceSleepRequested(Exception):
    """Raised when a device should stop current work and enter sleep immediately."""

    pass


class WakeLoopInterrupted(Exception):
    """Raised inside `handle_device_wakeup` when a pending web-launch request
    (or similar user action) needs the main loop to re-evaluate top-of-loop
    handlers before attempting wake-up again. The main loop should catch this
    and `continue` — do NOT put the device to sleep.
    """

    pass


CONNECT_FAILURE_COUNTS = {}
NON_RESTARTABLE_DEVICE_KEYWORDS = ("7fe98fc6", "fc65396d")
EMULATOR_RESTART_THRESHOLD = 1
EMU_RESTART_MARKERS = ("not online", "offline", "device offline", "cannot connect", "connection", "timeout", "closed")
DISABLE_EMULATOR_RESTART = True

_mumu_controller = None


def is_non_restartable_device(ip: str) -> bool:
    return any(key in (ip or "") for key in NON_RESTARTABLE_DEVICE_KEYWORDS)


def is_emulator_serial(ip: str) -> bool:
    return (ip or "").startswith("emulator-")


def is_recoverable_connect_error(msg: str) -> bool:
    text = (msg or "").lower()
    return any(marker in text for marker in EMU_RESTART_MARKERS)


def get_mumu_controller(logger_obj):
    global _mumu_controller
    if _mumu_controller is not None:
        return _mumu_controller
    exe = discover_control_exe()
    if not exe:
        logger_obj.warning("[Watchdog] 找不到 MuMuManager.exe，無法執行模擬器重啟")
        return None
    _mumu_controller = MuMuController(exe)
    return _mumu_controller


def reset_connect_failure(ip: str):
    CONNECT_FAILURE_COUNTS[ip] = 0


def handle_connect_failure(
    ip: str,
    exc: Exception,
    device_logger,
    running_threads: dict,
    logger_obj,
    refresh_adb_server_fn,
):
    msg = str(exc)
    fail_count = int(CONNECT_FAILURE_COUNTS.get(ip, 0)) + 1
    CONNECT_FAILURE_COUNTS[ip] = fail_count
    if DISABLE_EMULATOR_RESTART:
        device_logger.warning(f"[{ip}] emulator restart disabled, connect failure: {msg}")
        return

    if is_non_restartable_device(ip):
        device_logger.warning(f"[{ip}] 連線失敗第 {fail_count} 次（真機不執行重啟）: {msg}")
        return

    if is_emulator_serial(ip) and is_recoverable_connect_error(msg) and fail_count >= EMULATOR_RESTART_THRESHOLD:
        controller = get_mumu_controller(logger_obj)
        if controller is None:
            device_logger.warning(f"[{ip}] 達到重啟門檻但無可用 MuMu 控制器")
            return

        device_logger.warning(f"[{ip}] 連線異常連續 {fail_count} 次，嘗試重啟模擬器")
        action = controller.restart(ip)
        if action.ok:
            CONNECT_FAILURE_COUNTS[ip] = 0
            bot_state.record_emulator_restart(ip, reason="connect_failure")
            refresh_adb_server_fn(running_threads, logger_obj, hard_reset=True, exclude_device=ip)
            device_logger.info(f"[{ip}] 模擬器重啟成功: rc={action.returncode}, cost={action.duration_sec:.2f}s")
        else:
            device_logger.error(
                f"[{ip}] 模擬器重啟失敗: rc={action.returncode}, stderr={action.stderr or '<empty>'}"
            )


def sleep_until_wake_or_interrupt(ip: str, wake_ts: float, logger_obj) -> bool:
    """Returns True when interrupted early, False when wake_ts is reached."""
    while True:
        time.sleep(1)
        # Force-sleep requested while the device is already sleeping is
        # effectively a no-op: the device is already in the desired state.
        # Consume the flag and refresh the displayed state so the dashboard
        # doesn't get stuck on the transient "強制休眠" text set by
        # bot_state.request_force_sleep().
        if bot_state.check_force_sleep(ip):
            remain_sec = max(0, int(wake_ts - time.time()))
            wake_str = time.strftime("%H:%M", time.localtime(wake_ts))
            bot_state.update_state(
                ip,
                task="休眠中",
                step=f"強制休眠（裝置原已休眠） | 預計休眠 {remain_sec/60:.1f} 分鐘 (預計 {wake_str} 喚醒)",
                next_wake_at=wake_ts,
            )
            logger_obj.info(
                f"[{ip}] 收到強制休眠請求，但裝置已在休眠中，沿用原排程喚醒至 {wake_str}"
            )

        if bot_state.check_pause(ip):
            logger_obj.info(f"[{ip}] 偵測到從暫停中恢復，立即重啟初始化流程")
            return True

        if bot_state.check_skip_sleep(ip):
            logger_obj.info(f"[{ip}] 收到跳過休眠指令，立即喚醒")
            return True

        if bot_state.has_pending_web_launch_request(ip):
            logger_obj.info(f"[{ip}] 收到中控網頁啟動請求，提前喚醒處理手動模式")
            return True

        if ip == "emulator-5554":
            if bot_state.has_pending_online_check_request("emulator-5554"):
                logger_obj.info(f"[{ip}] 偵測到 emulator-5558 上線檢查請求，提前喚醒處理")
                return True

        if time.time() >= wake_ts:
            logger_obj.info(f"[{ip}] 已達對齊後喚醒時間，執行喚醒")
            return False
