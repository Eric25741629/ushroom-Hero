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


# 互檢 gate 等待 checker 結果時的輪詢分片：把單一 60s 盲等切成 0.5s 一片，
# 讓「開啟網頁」/ force-sleep 最慢 ~0.5s 就能中斷，而非卡滿整段 timeout。
_GATE_RESULT_POLL_SEC = 0.5

# 互檢 gate 的總等待上限。超過就放棄本輪喚醒（不是無限等 checker 空閒），
# 避免排程在幾小時後才啟動、落在原本不該執行的時段。
GATE_MAX_WAIT_SEC = 30 * 60


def _web_launch_release_requested(ip: str, logger_obj, checker_ip: str) -> bool:
    """放行互檢 gate：使用者按「開啟瀏覽器」是最高優先的手動介入，絕不能卡在
    跨裝置互檢等待後面。回傳後 caller 會重讀 has_pending_web_launch_request，
    使 WS pre-phase 被跳過、瀏覽器立即開啟。"""
    if bot_state.has_pending_web_launch_request(ip):
        logger_obj.info(
            f"[{ip}] 手動開啟網頁請求進來，放行互檢 gate（{checker_ip} online-check）"
        )
        return True
    return False


def _wait_online_check_result_interruptible(
    ip: str,
    req_id: str,
    logger_obj,
    checker_ip: str,
    total_timeout_sec: float = 60.0,
):
    """等 checker 回應，但以 0.5s 分片輪詢，讓手動開網頁 / force-sleep 能即時中斷。

    回傳 result 快照；若期間使用者按了「開啟網頁」則回傳 None（caller 應放行）。
    force-sleep 期間照常拋出 ForceSleepRequested。
    """
    deadline = time.time() + max(_GATE_RESULT_POLL_SEC, float(total_timeout_sec))
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            # 逾時：回最後一張快照（多半仍 pending），由 caller 視為 incomplete→busy。
            return bot_state.wait_online_check_result(req_id, timeout_sec=0.1)
        slice_sec = min(_GATE_RESULT_POLL_SEC, remaining)
        result = bot_state.wait_online_check_result(req_id, timeout_sec=slice_sec)
        if str(result.get("status", "pending")) != "pending":
            return result
        if bot_state.check_force_sleep(ip):
            logger_obj.warning(
                f"[{ip}] force sleep requested while waiting for {checker_ip} online-check result"
            )
            raise ForceSleepRequested()
        if _web_launch_release_requested(ip, logger_obj, checker_ip):
            return None


def wait_for_checker_gate_before_start(
    ip: str,
    logger_obj,
    checker_ip: str = "emulator-5554",
    max_wait_sec: float = GATE_MAX_WAIT_SEC,
) -> None:
    deadline = time.time() + max(0.0, float(max_wait_sec))
    while True:
        if bot_state.check_force_sleep(ip):
            logger_obj.warning(f"[{ip}] force sleep requested while waiting for {checker_ip} online-check")
            raise ForceSleepRequested()
        if _web_launch_release_requested(ip, logger_obj, checker_ip):
            return
        logger_obj.info(f"[{ip}] waiting for {checker_ip} online-check...")
        is_busy = True
        try:
            target_pid = config_manager.get_device_role_id(ip)
            req_id = bot_state.submit_online_check_request(
                requester_ip=ip,
                checker_ip=checker_ip,
                target_pid=target_pid,
            )
            result = _wait_online_check_result_interruptible(ip, req_id, logger_obj, checker_ip)
            if result is None:
                # 手動開網頁，放行 gate。
                return
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
        except ForceSleepRequested:
            raise
        except Exception as e:
            logger_obj.error(f"[{ip}] online-check request to {checker_ip} failed: {e}")
            is_busy = True

        if not is_busy:
            logger_obj.info(f"[{ip}] {checker_ip} is free, continue web_h5 startup")
            return

        # 2026-07-21：checker 長時間忙碌時這裡曾卡 16 小時（web-002 03:00 claim 萬神
        # 排程 → 19:06 才拿到 gate → 在錯誤時段開瀏覽器）。逾時就放棄本輪喚醒，
        # 讓 caller 進休眠/結束執行緒，下次喚醒重新檢查時間窗口。
        if time.time() >= deadline:
            logger_obj.warning(
                f"[{ip}] {checker_ip} online-check gate 等待逾時 "
                f"({int(max_wait_sec)}s)，放棄本輪喚醒"
            )
            raise ForceSleepRequested(f"[{ip}] checker gate timeout")

        wait_sec = int(config_manager.get_device_config(ip).get("online_check_interval_sec", 30))
        wait_sec = max(1, wait_sec)
        logger_obj.info(f"[{ip}] checker busy, sleeping {wait_sec} sec before retry")
        for remain in range(wait_sec, 0, -1):
            if bot_state.check_force_sleep(ip):
                logger_obj.warning(f"[{ip}] force sleep requested during checker retry backoff")
                raise ForceSleepRequested()
            if _web_launch_release_requested(ip, logger_obj, checker_ip):
                return
            bot_state.update_state(ip, task="等待互檢", step=f"{checker_ip} 忙碌中，{remain} 秒後重試")
            time.sleep(1)


def resolve_skip_online_check_once(
    ip: str,
    backend_kind: str,
    *,
    initial_skip: bool = False,
) -> bool:
    """決定本輪喚醒是否可略過在線互檢。"""
    if str(backend_kind).strip().lower() != "web_h5":
        return bool(initial_skip)
    try:
        cfg = config_manager.get_device_config(ip)
        ws_enabled = bool((cfg.get("ws_token") or {}).get("enabled", False))
        if not ws_enabled:
            return bool(initial_skip)
        return bool(bot_state.get_ws_h5_handoff_ok(ip))
    except Exception:
        return False


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
        # Gate 可能因等待期間使用者按了「開啟網頁」而放行。重讀 live flag，
        # 讓底下的 WS pre-phase 被跳過、瀏覽器立即開啟（與「進函式前就 pending」
        # 的 fast-path 一致）。
        if bot_state.has_pending_web_launch_request(ip):
            has_manual_web_launch_request = True

    if (
        backend_kind == "web_h5"
        and not has_manual_web_launch_request
        and callable(before_web_device_start)
    ):
        # h5+ws 需要先跑純 WS，再啟動同帳號 H5；否則剛開的 H5 session 會被 WS 登入踢掉。
        before_web_device_start()
        # 暫停閘：WS 跑完、開 H5 瀏覽器前。使用者暫停時「不啟動瀏覽器」（而非開了再暫停）
        # → block 於 check_pause 到恢復；恢復後重跑 WS 續做（其 ledger），再回檢查是否又被
        # 暫停。手動開網頁請求優先放行（要開瀏覽器給人接管）。
        while bot_state.is_paused(ip):
            bot_state.check_pause(ip)
            if bot_state.has_pending_web_launch_request(ip):
                break
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
        # Browser is now open — publish the truth so the dashboard toggle flips to
        # 關閉網頁 immediately (without waiting for the hold loop's first tick).
        bot_state.set_web_browser_open(ip, True)

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
                # Publish each tick so the dashboard toggle flips back to 開啟網頁
                # the moment the user closes the browser window (≤1s).
                bot_state.set_web_browser_open(ip, still_open)

                if bot_state.check_web_close(ip):
                    logger_obj.info(f"[{ip}] manual hold close-browser requested")
                    close_fn = getattr(device_obj, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception as close_err:
                            logger_obj.warning(
                                f"[{ip}] close browser during manual hold failed: {close_err}"
                            )
                    bot_state.set_web_browser_open(ip, False)
                    bot_state.update_state(ip, task="手動操作", step="已關閉瀏覽器")
                    break

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

        # 手動頁面通常是在休眠期間被中控提前喚醒；結束接管後要立即跑一次
        # WS，讓農場打工等可自動修復的狀態先被重新確認，不要等原定喚醒時間。
        if manual_hold_until_closed:
            bot_state.set_skip_sleep(ip)
            logger_obj.info(f"[{ip}] 手動操作結束，跳過原定休眠並立即恢復自動檢查")

        time.sleep(1)
    except ForceSleepRequested:
        raise
    except Exception as e:
        bot_state.complete_web_launch_request(ip, ok=False, error=str(e))
        bot_state.update_state(ip, task="手動操作失敗", step=str(e))
        logger_obj.warning(f"[{ip}] manual web launch failed: {e}")
    return True
