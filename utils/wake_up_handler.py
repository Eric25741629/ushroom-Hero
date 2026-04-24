import time
import random
from adb_operations import connect_u2_with_retries, get_battery_level
from device import close_nofication
from game_initialization import check_on_line
import bot_state # 引入狀態管理
import config_manager # 引入設定管理
from runtime_services.device_runtime_service import (
    ForceSleepRequested,
    WakeLoopInterrupted,
)


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

def handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader=None, skip_online_check_once: bool = False):
    """
    Handles the device wake-up, unlock, and synchronization logic.
    """
    global _wakeup_lock
    # 決定是否跳過喚醒的邏輯：
    # 優先順序：環境變數 -> global config -> device config (skip_wake)
    import os

    def _match_any(target, patterns):
        for p in patterns:
            if not p:
                continue
            if p == target or p in target:
                return True
        return False

    def should_skip_wake(ip_str: str) -> bool:
        # 1) 環境變數
        mode = os.environ.get('WAKE_SKIP_MODE')  # 'whitelist' 或 'blacklist'
        lst = os.environ.get('WAKE_SKIP_LIST')  # 逗號分隔的字串或子字串
        if lst:
            patterns = [s.strip() for s in lst.split(',') if s.strip()]
            if mode and mode.lower() == 'whitelist':
                if _match_any(ip_str, patterns):
                    logger.info(f"[{ip_str}] WAKE_SKIP_MODE=whitelist 且匹配清單，跳過喚醒")
                    return True
                return False
            else:
                # 默認為 blacklist
                if _match_any(ip_str, patterns):
                    logger.info(f"[{ip_str}] WAKE_SKIP_MODE=blacklist 且匹配清單，跳過喚醒")
                    return True

        # 2) global config
        try:
            gcfg = config_manager.get_global_config()
            g_mode = gcfg.get('wake_skip_mode')
            g_list = gcfg.get('wake_skip_list') or []
            if isinstance(g_list, str):
                g_list = [s.strip() for s in g_list.split(',') if s.strip()]
            if g_list:
                if g_mode and g_mode.lower() == 'whitelist':
                    if _match_any(ip_str, g_list):
                        logger.info(f"[{ip_str}] global wake_skip_mode=whitelist 且匹配清單，跳過喚醒")
                        return True
                    return False
                else:
                    if _match_any(ip_str, g_list):
                        logger.info(f"[{ip_str}] global wake_skip_mode=blacklist 且匹配清單，跳過喚醒")
                        return True
        except Exception as e:
            logger.debug(f"取得 global config 時發生錯誤: {e}")

        # 3) device config: 單一裝置可設定 skip_wake = True
        try:
            dev_cfg = config_manager.get_device_config(ip_str)
            if dev_cfg.get('skip_wake', False):
                logger.info(f"[{ip_str}] device config skip_wake=True，跳過喚醒")
                return True
        except Exception:
            pass

        # 4) 喚醒黑名單時段 (env / global / device)
        def _parse_hours(value):
            if value is None:
                return set()
            if isinstance(value, (list, tuple, set)):
                return {int(h) for h in value if str(h).isdigit()}
            s = str(value)
            parts = [p.strip() for p in s.split(',') if p.strip()]
            out = set()
            for p in parts:
                if p.isdigit():
                    out.add(int(p))
                elif '-' in p:
                    try:
                        a, b = p.split('-', 1)
                        a = int(a); b = int(b)
                        out.update(range(a, b+1))
                    except Exception:
                        continue
            return {h % 24 for h in out}

        now_hour = None
        try:
            import time as _time
            now_hour = _time.localtime().tm_hour
        except Exception:
            now_hour = None

        # env var WAKE_BLACKOUT_HOURS (e.g. "7" or "6,7,8" or "22-6")
        env_hours = os.environ.get('WAKE_BLACKOUT_HOURS')
        if env_hours:
            hours = _parse_hours(env_hours)
            if now_hour is not None and now_hour in hours:
                logger.info(f"[{ip_str}] 現在時段 ({now_hour}) 在 WAKE_BLACKOUT_HOURS，跳過喚醒")
                return True

        # global config wake_blackout_hours
        try:
            gcfg = config_manager.get_global_config()
            g_hours = gcfg.get('wake_blackout_hours')
            if g_hours:
                hours = _parse_hours(g_hours)
                if now_hour is not None and now_hour in hours:
                    logger.info(f"[{ip_str}] 現在時段 ({now_hour}) 在 global wake_blackout_hours，跳過喚醒")
                    return True
        except Exception:
            pass

        # device config wake_blackout_hours
        try:
            dev_cfg = config_manager.get_device_config(ip_str)
            d_hours = dev_cfg.get('wake_blackout_hours')
            if d_hours:
                hours = _parse_hours(d_hours)
                if now_hour is not None and now_hour in hours:
                    logger.info(f"[{ip_str}] 現在時段 ({now_hour}) 在 device wake_blackout_hours，跳過喚醒")
                    return True
        except Exception:
            pass

        return False

    if should_skip_wake(ip):
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

    # --- 核心邏輯：5558 啟動前透過 5554 檢查帳號線上狀態 ---
    if 'emulator-5558' in ip and not skip_online_check_once:
        while True:
            # Honor dashboard controls at every loop entry so the user can
            # pause / force-sleep / request-web-launch without waiting for
            # the full online_check_interval to elapse.
            _honor_dashboard_controls(ip)
            logger.info(f"[{ip}] 5558 等待 5554 狀態檢查(check_on_line request)...")
            is_busy = True
            try:
                bot_state.activate_online_check_priority(
                    requester_ip=ip,
                    checker_ip='emulator-5554',
                    ttl_sec=180.0,
                )
                req_id = bot_state.submit_online_check_request(
                    requester_ip=ip,
                    checker_ip='emulator-5554',
                )
                result = bot_state.wait_online_check_result(req_id, timeout_sec=150.0)
                status = str(result.get('status', 'pending'))
                if status == 'done':
                    is_busy = bool(result.get('result_busy', True))
                    logger.info(
                        f"[{ip}] 5554 online-check result: busy={is_busy}, detail={result.get('detail', '')}"
                    )
                else:
                    logger.warning(
                        f"[{ip}] 5554 online-check timeout/failed: status={status}, error={result.get('error', '')}"
                    )
                    is_busy = True
            except Exception as e:
                logger.error(f"[{ip}] 檢查 5554 狀態失敗: {e}")
                is_busy = True
            finally:
                bot_state.release_online_check_priority(
                    requester_ip=ip,
                    checker_ip='emulator-5554',
                )

            if not is_busy:
                logger.info(f"[{ip}] 5554 狀態可放行，5558 繼續喚醒")
                break

            wait_min = config_manager.get_device_config(ip).get("online_check_interval", 5)
            wait_sec = max(1, int(float(wait_min) * 60))
            logger.info(f"[{ip}] 5558 在線中，{wait_sec} 秒後重新檢查")
            for remain in range(wait_sec, 0, -1):
                # Every second, give the dashboard a chance to abort this
                # wait — pause / force-sleep / web-launch must not have to
                # wait the full `online_check_interval` minutes to take effect.
                _honor_dashboard_controls(ip)
                bot_state.update_state(ip, task="等待放行", step=f"5558在線中，{remain} 秒後重新檢查")
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

        while True:
            try:
                d.info.get('screenOn')
                break
            except Exception as e:
                logger.error(f"[{ip}] 檢查螢幕狀態時發生錯誤: {e}")
                try:
                    d = connect_u2_with_retries(ip, logger=logger)
                except:
                    pass
                time.sleep(60)

        while d.info.get('screenOn'):
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
            if 'emulator-5554' in ip and bot_state.has_pending_online_check_request('emulator-5554'):
                logger.info(f"[{ip}] 偵測到 5558 的 online-check 請求，提前結束分流等待")
                break
            if bot_state.check_skip_sleep(ip):
                logger.info(f"[{ip}] 收到 skip_sleep，提前結束分流等待")
                break
            time.sleep(1)

        # Checker 裝置若此時已有互檢請求，直接回主迴圈先處理 mailbox，
        # 不要繼續往下執行自己的 app_stop / 喚醒流程。
        if 'emulator-5554' in ip:
            if (
                bot_state.has_pending_online_check_request('emulator-5554')
                or bot_state.is_online_check_priority_active('emulator-5554')
            ):
                logger.info(f"[{ip}] 偵測到互檢請求，先返回主迴圈處理 emulator-5558 上線檢查")
                return d
    elif '3a8d31f2' in ip:
        time.sleep(10)
    
    time.sleep(2)
    
    if 'fc65396d' in ip or '192.168' in ip:
        close_nofication(d)
    
    d.app_stop("com.mxdzz.tw.and")    
    
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
        
    d.press("home")
    d.press("home")
    d.press("home")
    
    return d
