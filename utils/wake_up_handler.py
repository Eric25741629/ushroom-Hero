import logging
import os
import time
import random
from adb_operations import connect_u2_with_retries, ensure_on_launcher, get_battery_level

logger = logging.getLogger(__name__)
from device import close_notification
from game_initialization import check_on_line
import bot_state # 引入狀態管理
import config_manager # 引入設定管理
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    PhoneUnreachableError,
    WakeLoopInterrupted,
)

# 直連手機（fc65396d / 192.168）喚醒時等連線的上限秒數。喚醒窗只有 :00~:20，
# 不該為了等已帶出門的手機而吃掉整個窗 → 3 次 60 秒重試後放棄、降級純 WS。
PHONE_CONNECT_MAX_WAIT_SEC = 180


def _match_any(target: str, patterns) -> bool:
    """True iff `target` equals or contains any non-empty pattern."""
    for p in patterns:
        if not p:
            continue
        if p == target or p in target:
            return True
    return False


def _parse_hours(value) -> set:
    """Parse blackout-hour spec into a set of ints in 0..23.

    Accepts: list/tuple/set of digits, or a comma-separated string with optional
    `a-b` ranges. Examples: "7", "6,7,8", [6, 7].

    All resulting hours are normalised modulo 24 — previously the list path
    skipped this normalisation while the string path applied it, so a config
    of `[25]` and `"25"` would produce different results.
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {int(h) % 24 for h in value if str(h).isdigit()}
    out = set()
    for p in (s.strip() for s in str(value).split(',')):
        if not p:
            continue
        if p.isdigit():
            out.add(int(p))
        elif '-' in p:
            try:
                a, b = p.split('-', 1)
                out.update(range(int(a), int(b) + 1))
            except Exception:
                continue
    return {h % 24 for h in out}


def _check_skip_list(ip: str, mode, lst, logger, *, source: str) -> bool:
    """Apply a single skip-list source. Returns True iff this source decides to skip."""
    if not lst:
        return False
    if isinstance(lst, str):
        lst = [s.strip() for s in lst.split(',') if s.strip()]
    if mode and str(mode).lower() == 'whitelist':
        if _match_any(ip, lst):
            logger.info(f"[{ip}] {source} wake_skip_mode=whitelist 且匹配清單，跳過喚醒")
            return True
        return False  # whitelist with no match → don't skip; later sources can still skip
    # default: blacklist
    if _match_any(ip, lst):
        logger.info(f"[{ip}] {source} wake_skip_mode=blacklist 且匹配清單，跳過喚醒")
        return True
    return False


def _check_blackout_hours(ip: str, hours_value, now_hour, logger, *, source: str) -> bool:
    """True iff `now_hour` falls in the blackout-hours spec from `source`."""
    if not hours_value:
        return False
    hours = _parse_hours(hours_value)
    if now_hour is not None and now_hour in hours:
        logger.info(f"[{ip}] 現在時段 ({now_hour}) 在 {source} wake_blackout_hours，跳過喚醒")
        return True
    return False


def _should_skip_wake(ip: str, logger) -> bool:
    """Decide whether to skip the wake-up flow for `ip`.

    Sources checked in priority order:
      1) WAKE_SKIP_LIST + WAKE_SKIP_MODE env vars
      2) global config wake_skip_list / wake_skip_mode
      3) device config skip_wake (bool) and wake_blackout_hours
      4) blackout hours from env / global / device config
    Whitelist mode short-circuits to False if pattern doesn't match (so later
    sources don't override the explicit allow-list decision); blacklist falls
    through.
    """
    # 1) skip-list env vars
    env_mode = os.environ.get('WAKE_SKIP_MODE')
    env_list = os.environ.get('WAKE_SKIP_LIST')
    if env_list:
        if _check_skip_list(ip, env_mode, env_list, logger, source='WAKE_SKIP_MODE='):
            return True
        if env_mode and env_mode.lower() == 'whitelist':
            return False  # whitelist no-match = explicit don't-skip

    # 2) global config skip-list
    try:
        gcfg = config_manager.get_global_config()
        g_mode = gcfg.get('wake_skip_mode')
        g_list = gcfg.get('wake_skip_list')
        if g_list:
            if _check_skip_list(ip, g_mode, g_list, logger, source='global'):
                return True
            if g_mode and str(g_mode).lower() == 'whitelist':
                return False
    except Exception as e:
        logger.debug(f"取得 global config 時發生錯誤: {e}")

    # 3) device config skip_wake flag
    try:
        dev_cfg = config_manager.get_device_config(ip)
        if dev_cfg.get('skip_wake', False):
            logger.info(f"[{ip}] device config skip_wake=True，跳過喚醒")
            return True
    except Exception:
        pass

    # 4) blackout hours (env / global / device)
    try:
        now_hour = time.localtime().tm_hour
    except Exception:
        now_hour = None

    if _check_blackout_hours(ip, os.environ.get('WAKE_BLACKOUT_HOURS'), now_hour, logger, source='WAKE_BLACKOUT_HOURS env'):
        return True
    try:
        gcfg = config_manager.get_global_config()
        if _check_blackout_hours(ip, gcfg.get('wake_blackout_hours'), now_hour, logger, source='global'):
            return True
    except Exception:
        pass
    try:
        dev_cfg = config_manager.get_device_config(ip)
        if _check_blackout_hours(ip, dev_cfg.get('wake_blackout_hours'), now_hour, logger, source='device'):
            return True
    except Exception:
        pass

    return False


def _honor_dashboard_controls(ip: str) -> None:
    """Called from any long-running wait inside the wake-up flow.

    - pause: blocks until user un-pauses (`check_pause` handles blocking).
    - force-sleep: raises ForceSleepRequested so main loop catches and sleeps.
    - pending web-launch: raises WakeLoopInterrupted so main loop catches
      and re-evaluates the top-of-loop handlers (no forced sleep).
    """
    bot_state.check_pause(ip)
    if bot_state.check_force_sleep(ip):
        raise ForceSleepRequested(
            f"[{ip}] force sleep requested during wake-up wait"
        )
    if bot_state.has_pending_web_launch_request(ip):
        raise WakeLoopInterrupted(
            f"[{ip}] web-launch request received during wake-up wait"
        )

# Global lock for synchronization
_wakeup_lock = False

def get_lock_status():
    global _wakeup_lock
    return _wakeup_lock

def set_lock_status(status):
    global _wakeup_lock
    _wakeup_lock = status

def release_wakeup_lock(ip):
    """
    Releases the lock for specific devices if they are holding it.
    """
    global _wakeup_lock
    if 'emulator-5554' in ip or '3a8d31f2' in ip:
        _wakeup_lock = False


def _press_home_repeated(d, count: int = 3, delay_sec: float = 0.2) -> None:
    """連續按 Home，讓剛喚醒/剛關通知欄的手機有時間回到 launcher。"""
    press_fn = getattr(d, "press", None)
    if not callable(press_fn):
        return
    for _ in range(max(0, int(count))):
        press_fn("home")
        time.sleep(delay_sec)

def _wait_for_phone_connection(d, ip, logger, max_wait_sec=None):
    """Block until the direct-connect phone's u2 session answers again.

    掉線判離線 fix (2026-06-11)：原本這段是無限 while True 重試但完全不更新
    bot_state、也不理 dashboard 控制 → 手機帶出門後儀表板凍結。現在每輪：
    更新狀態（連線中斷）、honor 暫停/強制休眠/開網頁、嘗試重連，60 秒重試
    — 手機回到 wifi 後自動接上（保留自癒語義）。

    手機離線降級 (Task A)：`max_wait_sec` 給定時，超過上限就 raise
    PhoneUnreachableError，讓主迴圈跳過本輪 ADB 任務、降級為純 WS（喚醒前 WS
    階段已跑），照常進入對齊休眠；`max_wait_sec=None`（預設）維持無限重試的
    原行為，向後相容其他呼叫端/測試。

    deadline 檢查刻意放在 while 迴圈頂端（在 try 之外），否則 `except Exception`
    會吞掉 raise。`started` 在迴圈前設定，故首輪 elapsed≈0 一定先嘗試一次連線。
    """
    started = time.time()
    while True:
        if max_wait_sec is not None and (time.time() - started) >= max_wait_sec:
            mins = int((time.time() - started) // 60)
            bot_state.update_state(
                ip,
                task="連線中斷",
                step=f"手機連線逾時 {mins} 分鐘，放棄本輪 ADB，降級為純 WS",
            )
            logger.warning(
                f"[{ip}] 手機 ADB 連線逾時（已中斷 {mins} 分鐘），放棄本輪喚醒，降級純 WS"
            )
            raise PhoneUnreachableError(
                f"[{ip}] phone unreachable after {max_wait_sec}s — degrade to WS-only"
            )
        try:
            d.info.get('screenOn')
            return d
        except Exception as e:
            logger.error(f"[{ip}] 檢查螢幕狀態時發生錯誤: {e}")
            _honor_dashboard_controls(ip)
            mins = int((time.time() - started) // 60)
            bot_state.update_state(ip, task="連線中斷", step=f"手機連線失敗，60 秒後重試（已中斷 {mins} 分鐘）")
            try:
                new_d = connect_u2_with_retries(ip, logger=logger)
                if new_d is not None:
                    d = new_d
            except Exception as e2:
                logger.warning(f"[wake_up_handler] 例外: {e2}")
            time.sleep(60)

def handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader=None, skip_online_check_once: bool = False):
    """
    Handles the device wake-up, unlock, and synchronization logic.
    """
    global _wakeup_lock

    if _should_skip_wake(ip, logger):
        return d

    def _is_5554_busy_by_state() -> bool:
        """Fallback busy check for web_h5 backend to avoid cross-thread Playwright access."""
        states = bot_state.get_all_states()
        st = states.get('emulator-5554', {}) or {}
        status = str(st.get("status", "OFFLINE")).upper()
        if status == "OFFLINE":
            return False

        task = str(st.get("task", "") or "")
        step = str(st.get("step", "") or "")
        text = f"{task} {step}"

        # Explicit free/idle markers first.
        free_markers = ["休眠", "離線", "等待喚醒", "等待啟動", "thread exit"]
        if any(m in text for m in free_markers):
            return False

        # Explicit busy markers.
        busy_markers = ["喚醒中", "啟動", "挖礦", "任務", "戰鬥", "執行", "忙碌", "主頁面"]
        if any(m in text for m in busy_markers):
            return True

        # Heartbeat stale => treat as not busy.
        last_update = float(st.get("last_update", 0) or 0)
        if last_update > 0 and (time.time() - last_update) > 120:
            return False
        return True

    # --- 核心邏輯：requester 啟動前透過 checker 檢查帳號線上狀態 ---
    # 解耦 (2026-06-09): 任何設定了 online_check_target_pid 的 requester 都會走
    # 互檢；checker 不再寫死 5554（由 online_check_checkers 清單動態決定）。預設
    # 只有 5558 設了 target_pid → 行為與舊版相同。is_online_check_checker(ip) 用來
    # 避免 checker 自己對自己發起互檢。
    _online_check_target_pid = config_manager.get_device_config(ip).get('online_check_target_pid')
    _wants_online_check = (
        bool(_online_check_target_pid)
        and not skip_online_check_once
        and not bot_state.is_online_check_checker(ip)
    )
    if _wants_online_check:
        while True:
            # Honor dashboard controls at every loop entry so the user can
            # pause / force-sleep / request-web-launch without waiting for
            # the full online_check_interval_sec to elapse.
            _honor_dashboard_controls(ip)
            logger.info(f"[{ip}] 等待 checker 狀態檢查(check_on_line request)...")
            is_busy = True
            try:
                req_id = bot_state.submit_online_check_request(
                    requester_ip=ip,
                    target_pid=_online_check_target_pid,
                )
                # Protocol path takes < 1s; OCR fallback takes 30-50s. 60s timeout
                # comfortably covers both with margin.
                result = bot_state.wait_online_check_result(req_id, timeout_sec=60.0)
                status = str(result.get('status', 'pending'))
                if status == 'done':
                    is_busy = bool(result.get('result_busy', True))
                    logger.info(
                        f"[{ip}] checker online-check result: busy={is_busy}, detail={result.get('detail', '')}"
                    )
                elif status in ('pending', 'processing'):
                    logger.info(
                        f"[{ip}] checker online-check 尚未完成（status={status}），稍後重試"
                    )
                    is_busy = True
                else:
                    logger.warning(
                        f"[{ip}] checker online-check failed: status={status}, error={result.get('error', '')}"
                    )
                    is_busy = True
            except Exception as e:
                logger.error(f"[{ip}] 檢查 checker 狀態失敗: {e}")
                is_busy = True

            if not is_busy:
                logger.info(f"[{ip}] checker 狀態可放行，繼續喚醒")
                break

            wait_sec = int(config_manager.get_device_config(ip).get("online_check_interval_sec", 30))
            wait_sec = max(1, wait_sec)
            logger.info(f"[{ip}] 帳號在線中，{wait_sec} 秒後重新檢查")
            for remain in range(wait_sec, 0, -1):
                _honor_dashboard_controls(ip)
                bot_state.update_state(ip, task="等待放行", step=f"帳號在線中，{remain} 秒後重新檢查")
                time.sleep(1)

    # --- 直連設備喚醒流程 (fc65396d / 192.168) ---
    if 'fc65396d' in ip or '192.168' in ip:
        # 檢查電量
        battery_level = get_battery_level(ip, logger)
        if battery_level >= 0:
            logger.info(f"[{ip}] 當前電量: {battery_level}%")
            if battery_level < 20:
                logger.warning(f"[{ip}] 電量過低 ({battery_level}%)，跳過本次執行")
                bot_state.update_state(ip, task="跳過", step=f"電量過低 ({battery_level}%)，等待充電")
                return d
        else:
            logger.warning(f"[{ip}] 無法獲取電量資訊，繼續執行")

        logger.info(f"[{ip}] 檢查螢幕狀態...")

        d = _wait_for_phone_connection(d, ip, logger, max_wait_sec=PHONE_CONNECT_MAX_WAIT_SEC)

        while d.info.get('screenOn'):
            # 使用者實體操作手機時 bot 在此等螢幕關閉；同時honor儀表板控制，
            # 讓暫停/強制休眠/開網頁能中斷這段空轉（否則使用者按了沒反應）。
            _honor_dashboard_controls(ip)
            logger.warning(f"[{ip}] 偵測到螢幕開啟 (人為操作中)，每 5 秒自動檢測一次...")
            bot_state.update_state(ip, task="等待中", step="等待螢幕關閉 (人為操作中)")
            time.sleep(5)

        logger.info(f"[{ip}] 螢幕已關閉，開始執行自動喚醒邏輯...")
        bot_state.update_state(ip, task="喚醒中", step="正在執行解鎖...")
        
        d.unlock()
        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
        time.sleep(2)

        if not d.info.get('screenOn'):
            d.press("power")
            time.sleep(1)
            d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
            time.sleep(1)
        
    # 分流延遲
    if 'emulator-5556' in ip or 'emulator-5554' in ip:
        logger.info(f"[{ip}] 執行啟動分流，等待 5 分鐘...")
        deadline = time.time() + (60 * 5)
        while time.time() < deadline:
            if bot_state.is_online_check_checker(ip) and bot_state.has_pending_online_check_request(ip):
                logger.info(f"[{ip}] 偵測到 online-check 請求，提前結束分流等待")
                break
            if bot_state.check_skip_sleep(ip):
                logger.info(f"[{ip}] 收到 skip_sleep，提前結束分流等待")
                break
            time.sleep(1)

        # Checker 裝置若此時已有互檢請求，直接回主迴圈先處理 mailbox，
        # 不要繼續往下執行自己的 app_stop / 喚醒流程。
        if bot_state.is_online_check_checker(ip):
            if bot_state.has_pending_online_check_request(ip):
                logger.info(f"[{ip}] 偵測到互檢請求，先返回主迴圈處理 requester 上線檢查")
                return d
    elif '3a8d31f2' in ip:
        time.sleep(10)
    
    time.sleep(2)

    is_web_backend = getattr(d, "backend_kind", None) == "web_h5"
    if not is_web_backend:
        d.app_stop("com.mxdzz.tw.and")
    else:
        logger.info(f"[{ip}] web_h5 backend，略過 Android app_stop，避免關閉 Playwright 瀏覽器")
    
    if 'emulator-5560' in ip:
        time.sleep(30)
        
    # 通用的螢幕開啟檢查與解鎖
    while True:
        if d.info.get('screenOn'):
            break
        logger.info(f"[{ip}] 螢幕未開啟，嘗試解鎖...")
        d.unlock()
        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05)
        time.sleep(1)
        
    # 停止遊戲後必須等 Android 桌面真的成為前景；直連手機剛喚醒時
    # launcher 切換較慢，先連按 Home，再用 launcher 檢查守住順序。
    # web_h5（PlaywrightGameDevice）沒有 Android 桌面；且 app_stop 會關閉
    # Playwright 瀏覽器，因此整段 Android-only 清理都要略過。
    if not is_web_backend:
        _press_home_repeated(d)
        if not ensure_on_launcher(d, ip, logger):
            logger.warning(f"[{ip}] 喚醒後未能確認回到桌面，仍繼續後續啟動流程")

        close_notification(d)

        # close_notification 會拉下系統選單；處理 Messenger 前先確定選單已收起。
        _press_home_repeated(d)
        if not ensure_on_launcher(d, ip, logger):
            logger.warning(f"[{ip}] 清理通知後未能確認回到桌面，仍繼續關閉 Messenger")

        try:
            d.app_stop("com.facebook.orca")
        except Exception:
            pass

    return d
