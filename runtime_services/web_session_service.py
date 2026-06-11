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
            target_pid = config_manager.get_device_config(ip).get("online_check_target_pid")
            req_id = bot_state.submit_online_check_request(
                requester_ip=ip,
                checker_ip=checker_ip,
                target_pid=target_pid,
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
    """Serve pending cross-device online-check requests as a checker.

    Decoupled (2026-06-09): any device in the configured `online_check_checkers`
    list may serve requests (default: just emulator-5554, so legacy behaviour is
    unchanged). The check is **protocol-only** (friend list, no OCR): the
    requester's `target_pid` is read off the request, and
    `game_initialization.check_on_line_protocol_only` judges presence via this
    checker's web_h5 session.

    The `check_on_line_fn` / `cnn_model` params are kept for signature
    compatibility but no longer drive the check (no OCR fallback by design).
    """
    if not bot_state.is_online_check_checker(ip):
        return

    while True:
        req = bot_state.pop_online_check_request(ip)
        if not req:
            return

        req_id = req.get("id")
        requester_ip = req.get("requester_ip")
        target_pid = req.get("target_pid")
        try:
            logger_obj.info(
                f"[{ip}] processing online-check request from {requester_ip} "
                f"(req={req_id}, target_pid={target_pid})"
            )
            bot_state.update_state(
                ip,
                task="互檢中",
                step=f"處理 {requester_ip} 的上線檢查",
            )
            is_busy, reason = _run_checker_protocol_only(
                ip, target_pid, requester_ip, logger_obj
            )
            if is_busy is None:
                # Cannot determine on this checker (e.g. target not in friend
                # list). Fail so the requester retries and another checker can
                # claim it — never 放行 on an unknown result.
                logger_obj.info(
                    f"[{ip}] online-check 無法判定 (req={req_id}, reason={reason})，"
                    f"交給其他 checker / 下一輪重試"
                )
                bot_state.fail_online_check_request(
                    req_id, f"undetermined by {ip}: {reason}"
                )
                continue
            bot_state.complete_online_check_request(
                req_id,
                is_busy=is_busy,
                detail=f"checked by {ip} for requester={requester_ip}; reason={reason}",
            )
        except Exception as e:
            logger_obj.error(f"[{ip}] online-check request failed: req={req_id}, err={e}")
            bot_state.fail_online_check_request(req_id, str(e))


def _run_checker_protocol_only(ip, target_pid, requester_ip, logger_obj):
    """Resolve the target_pid then run the protocol-only check on this checker.

    Returns (is_busy, reason); is_busy is None when the result is undetermined.
    On error, conservatively returns (True, "check_failed:...") so the requester
    keeps waiting rather than 放行.
    """
    # Backward-compat: legacy requesters submitted without a target_pid (the old
    # check_on_line read it from the hardcoded emulator-5558 config). Fall back
    # to the requester's own online_check_target_pid so the 5558 path is intact.
    pid = target_pid
    if not pid:
        try:
            pid = config_manager.get_device_config(requester_ip).get("online_check_target_pid")
        except Exception:
            pid = None
    if not pid:
        # No way to do a protocol check → undetermined (let caller fail it).
        return None, "online_check_target_pid not set"

    threshold_sec = 60
    try:
        threshold_sec = int(
            config_manager.get_device_config(requester_ip).get(
                "online_check_threshold_sec", 60
            )
        )
    except Exception:
        threshold_sec = 60

    from game_initialization import check_on_line_protocol_only  # lazy import

    try:
        return check_on_line_protocol_only(ip, int(pid), threshold_sec)
    except Exception as e:
        logger_obj.warning(f"[{ip}] protocol-only online-check error: {e}; conservative → busy")
        return True, f"check_failed:{e}"


def initialize_runtime_device(
    ip: str,
    device_logger,
    connect_device_fn,
    before_web_device_start=None,
):
    device_cfg = config_manager.get_device_config(ip)
    backend_kind = str(device_cfg.get("backend", "adb")).strip().lower()
    checker_ip = str(device_cfg.get("startup_checker_ip", "")).strip() or "emulator-5554"
    require_checker_gate = bool(device_cfg.get("require_checker_before_web_start", False))
    has_manual_web_launch_request = bool(
        backend_kind == "web_h5" and bot_state.has_pending_web_launch_request(ip)
    )
    skip_online_check_once = False

    # Decoupled requester gate (2026-06-09): any web_h5 device that has an
    # online_check_target_pid (and is not itself a checker) goes through the
    # checker gate before starting. Default config only sets target_pid on
    # emulator-5558, so this is byte-for-byte the old 5558 behaviour; it also
    # generalizes to any future requester without re-hardcoding a serial.
    is_requester = (
        bool(device_cfg.get("online_check_target_pid"))
        and not bot_state.is_online_check_checker(ip)
    )
    if backend_kind == "web_h5" and is_requester:
        require_checker_gate = True
    if has_manual_web_launch_request and require_checker_gate:
        device_logger.info(f"[{ip}] manual web launch requested, skip checker gate")
        require_checker_gate = False

    if backend_kind == "web_h5" and require_checker_gate:
        wait_for_checker_gate_before_start(ip, device_logger, checker_ip=checker_ip)
        if is_requester:
            skip_online_check_once = True

    if (
        backend_kind == "web_h5"
        and not has_manual_web_launch_request
        and callable(before_web_device_start)
    ):
        # h5+ws 需要先跑純 WS，再啟動同帳號 H5；否則剛開的 H5 session 會被 WS 登入踢掉。
        before_web_device_start()

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
        # 手動開網頁預設強制使用可見視窗，避免裝置設定 web_headless=true 時
        # 面板「開啟網頁」沒有視窗可接管。
        force_headful = bool(payload.get("force_headful", True))

        bot_state.update_state(ip, task="手動操作", step="正在開啟網頁 / 等待手動操作")
        logger_obj.info(f"[{ip}] start web page for manual control")

        if clear_cookies_once:
            try:
                device_obj.clear_cookies()
                logger_obj.info(f"[{ip}] cleared web cookies before opening page")
            except Exception as clear_err:
                logger_obj.warning(f"[{ip}] clear_cookies failed: {clear_err}")

        device_obj.app_start(
            package_name="com.mxdzz.tw.and",
            use_monkey=True,
            force_headful=force_headful,
        )
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

        # 手動接管結束後，回到原本裝置設定的 headless 模式，避免改動持續到自動流程。
        if force_headful and manual_hold_until_closed:
            restore_fn = getattr(device_obj, "restore_configured_headless_session", None)
            if callable(restore_fn):
                try:
                    restore_fn(reason="manual web launch completed")
                except Exception as restore_err:
                    logger_obj.warning(
                        f"[{ip}] restore configured headless mode failed: {restore_err}"
                    )

        time.sleep(1)
    except ForceSleepRequested:
        raise
    except Exception as e:
        bot_state.complete_web_launch_request(ip, ok=False, error=str(e))
        bot_state.update_state(ip, task="手動操作失敗", step=str(e))
        logger_obj.warning(f"[{ip}] manual web launch failed: {e}")
    return True
