import time

import bot_state
import config_manager
from device_wrapper import MonitoredDevice, close_all_web_devices, create_web_device_if_enabled
from runtime_services.device_runtime_service import ForceSleepRequested


LOGIN_CONFLICT_SLEEP_SEC = 30 * 60


def shutdown_web_devices(logger_obj) -> None:
    try:
        close_all_web_devices(logger_obj=logger_obj)
    except Exception as e:
        logger_obj.warning(f"[System] close_all_web_devices failed: {e}")


def mark_login_conflict_sleep(ip: str, sleep_sec: int = LOGIN_CONFLICT_SLEEP_SEC) -> float:
    wake_ts = time.time() + sleep_sec
    wake_time_str = time.strftime("%H:%M", time.localtime(wake_ts))
    bot_state.update_state(
        ip,
        task="休眠中",
        step=f"偵測到異地登入，已進入避讓休眠 (預計 {wake_time_str} 喚醒)",
        next_wake_at=wake_ts,
    )
    return wake_ts


def check_on_line_detail(cnn_model, logger_obj, check_on_line_fn, check_ip: str = "emulator-5554"):
    try:
        is_busy = bool(check_on_line_fn(cnn_model))
        reason = "account_online" if is_busy else "account_offline"
        return is_busy, reason
    except Exception as e:
        logger_obj.warning(f"[{check_ip}] check_on_line_detail fallback due to error: {e}")
        return True, f"check_failed:{e}"


def wait_for_checker_gate_before_start(
    ip: str,
    logger_obj,
    checker_ip: str = "emulator-5554",
) -> None:
    while True:
        if bot_state.check_force_sleep(ip):
            logger_obj.warning(f"[{ip}] force sleep requested while waiting for {checker_ip} online-check")
            raise ForceSleepRequested()
        logger_obj.info(f"[{ip}] waiting for {checker_ip} online-check...")
        is_busy = True
        try:
            req_id = bot_state.submit_online_check_request(
                requester_ip=ip,
                checker_ip=checker_ip,
            )
            result = bot_state.wait_online_check_result(req_id, timeout_sec=60.0)
            status = str(result.get("status", "pending"))
            if status == "done":
                is_busy = bool(result.get("result_busy", True))
                logger_obj.info(
                    f"[{ip}] {checker_ip} online-check result: busy={is_busy}, detail={result.get('detail', '')}"
                )
            else:
                logger_obj.warning(
                    f"[{ip}] {checker_ip} online-check incomplete: status={status}, error={result.get('error', '')}"
                )
                is_busy = True
        except Exception as e:
            logger_obj.error(f"[{ip}] online-check request to {checker_ip} failed: {e}")
            is_busy = True

        if not is_busy:
            logger_obj.info(f"[{ip}] {checker_ip} is free, continue web_h5 startup")
            return

        wait_sec = int(config_manager.get_device_config(ip).get("online_check_interval_sec", 30))
        wait_sec = max(1, wait_sec)
        logger_obj.info(f"[{ip}] checker busy, sleeping {wait_sec} sec before retry")
        for remain in range(wait_sec, 0, -1):
            if bot_state.check_force_sleep(ip):
                logger_obj.warning(f"[{ip}] force sleep requested during checker retry backoff")
                raise ForceSleepRequested()
            bot_state.update_state(ip, task="等待互檢", step=f"{checker_ip} 忙碌中，{remain} 秒後重試")
            time.sleep(1)


def process_online_check_requests(ip: str, cnn_model, logger_obj, check_on_line_fn) -> None:
    if ip != "emulator-5554":
        return

    while True:
        req = bot_state.pop_online_check_request("emulator-5554")
        if not req:
            return

        req_id = req.get("id")
        requester_ip = req.get("requester_ip")
        try:
            logger_obj.info(f"[emulator-5554] processing online-check request from {requester_ip} (req={req_id})")
            bot_state.update_state(
                "emulator-5554",
                task="互檢中",
                step=f"處理 {requester_ip} 的上線檢查",
            )
            is_busy, reason = check_on_line_detail(cnn_model, logger_obj, check_on_line_fn, check_ip="emulator-5554")
            bot_state.complete_online_check_request(
                req_id,
                is_busy=is_busy,
                detail=f"checked by emulator-5554 for requester={requester_ip}; reason={reason}",
            )
        except Exception as e:
            logger_obj.error(f"[emulator-5554] online-check request failed: req={req_id}, err={e}")
            bot_state.fail_online_check_request(req_id, str(e))


def initialize_runtime_device(ip: str, device_logger, connect_device_fn):
    device_cfg = config_manager.get_device_config(ip)
    backend_kind = str(device_cfg.get("backend", "adb")).strip().lower()
    checker_ip = str(device_cfg.get("startup_checker_ip", "")).strip() or "emulator-5554"
    require_checker_gate = bool(device_cfg.get("require_checker_before_web_start", False))
    has_manual_web_launch_request = bool(
        backend_kind == "web_h5" and bot_state.has_pending_web_launch_request(ip)
    )
    skip_online_check_once = False

    if ip == "emulator-5558" and backend_kind == "web_h5":
        require_checker_gate = True
    if has_manual_web_launch_request and require_checker_gate:
        device_logger.info(f"[{ip}] manual web launch requested, skip checker gate")
        require_checker_gate = False

    if backend_kind == "web_h5" and require_checker_gate:
        wait_for_checker_gate_before_start(ip, device_logger, checker_ip=checker_ip)
        if ip == "emulator-5558":
            skip_online_check_once = True

    web_device = create_web_device_if_enabled(ip, cfg=device_cfg, logger_obj=device_logger)
    if web_device is not None:
        device_logger.info(f"[{ip}] backend=web_h5, url={device_cfg.get('web_url', '')}")
        return web_device, MonitoredDevice(web_device, ip), backend_kind, skip_online_check_once

    device_obj = connect_device_fn(ip, logger=device_logger)
    return device_obj, MonitoredDevice(device_obj, ip), backend_kind, skip_online_check_once


def handle_pending_web_launch(ip: str, device_obj, backend_kind: str, logger_obj) -> bool:
    web_launch_req = bot_state.consume_web_launch_request(ip)
    if web_launch_req is None or backend_kind != "web_h5":
        return False

    try:
        payload = dict(web_launch_req.get("payload") or {})
        clear_cookies_once = bool(payload.get("clear_cookies_once", False))
        manual_hold_until_closed = bool(payload.get("manual_hold_until_closed", False))

        bot_state.update_state(ip, task="手動操作", step="正在開啟網頁 / 等待手動操作")
        logger_obj.info(f"[{ip}] start web page for manual control")

        if clear_cookies_once:
            try:
                device_obj.clear_cookies()
                logger_obj.info(f"[{ip}] cleared web cookies before opening page")
            except Exception as clear_err:
                logger_obj.warning(f"[{ip}] clear_cookies failed: {clear_err}")

        device_obj.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
        bot_state.complete_web_launch_request(ip, ok=True, message="web page opened")

        if manual_hold_until_closed:
            logger_obj.info(f"[{ip}] manual hold enabled, waiting for page to close")
            while True:
                if bot_state.check_force_sleep(ip):
                    logger_obj.warning(f"[{ip}] force sleep requested during manual web hold")
                    raise ForceSleepRequested()

                still_open = False
                try:
                    alive_fn = getattr(device_obj, "is_alive", None)
                    still_open = bool(alive_fn()) if callable(alive_fn) else False
                except Exception:
                    still_open = False

                if bot_state.check_manual_release(ip):
                    logger_obj.info(f"[{ip}] manual release requested, resume automation")
                    bot_state.update_state(ip, task="手動操作", step="手動操作已結束，準備恢復")
                    break

                if not still_open:
                    logger_obj.info(f"[{ip}] web session closed, resume automation")
                    bot_state.update_state(ip, task="手動操作", step="網頁已關閉")
                    break

                bot_state.update_state(ip, task="手動操作", step="等待手動操作中")
                time.sleep(1)

        time.sleep(1)
    except ForceSleepRequested:
        raise
    except Exception as e:
        bot_state.complete_web_launch_request(ip, ok=False, error=str(e))
        bot_state.update_state(ip, task="手動操作失敗", step=str(e))
        logger_obj.warning(f"[{ip}] manual web launch failed: {e}")
    return True
