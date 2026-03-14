import time
import random
from adb_operations import connect_u2_with_retries
from device import close_nofication
from game_initialization import check_on_line
import bot_state # 引入狀態管理
import config_manager # 引入設定管理

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

def handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader):
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

    # --- 核心邏輯：5558 啟動前透過 5554 檢查帳號線上狀態 ---
    if 'emulator-5558' in ip:
        while True:
            logger.info(f"[{ip}] 準備執行，先借用 emulator-5554 檢查帳號是否正在線上...")
            is_busy = True
            try:
                # 獲取 5554 的設備鎖，確保檢查時 5554 不會亂動
                with bot_state.get_device_lock('emulator-5554'):
                    logger.info(f"[{ip}] 已鎖定 5554，開始執行實體 check_on_line...")
                    # check_on_line 內部會連接到 5554 並操作
                    # 回傳 False 代表 "pass" (帳號不在線，安全)
                    # 回傳 True 代表 "busy" (帳號在線，不安全)
                    is_busy = check_on_line(Cnn_model, easyocr_reader)
            except Exception as e:
                logger.error(f"[{ip}] 借用 5554 檢查時發生異常: {e}")
                is_busy = True # 發生錯誤時保守起見視為忙碌
            
            if not is_busy:
                logger.info(f"[{ip}] 檢查通過：帳號目前不在線上，5558 準備啟動任務。")
                break
            else:
                # 獲取設定的休眠時間 (分鐘)
                wait_min = config_manager.get_device_config(ip).get("online_check_interval", 5)
                logger.info(f"[{ip}] 帳號正在線上 (via 5554)，5558 避讓休眠 {wait_min} 分鐘...")
                bot_state.update_state(ip, task="等待中", step=f"帳號在線避讓中 ({wait_min}分鐘)")
                time.sleep(wait_min * 60)

    # --- 喚醒與解鎖手機 (fc65396d / 實體手機) ---
    if 'fc65396d' in ip or '192.168' in ip:
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
        time.sleep(60 * 5)
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
