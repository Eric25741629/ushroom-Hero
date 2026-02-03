import time
import random
from adb_operations import connect_u2_with_retries
from device import close_nofication
from game_initialization import check_on_line

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
    Logic moved from main loop end.
    """
    global _wakeup_lock
    if 'emulator-5554' in ip or '3a8d31f2' in ip:
        _wakeup_lock = False

def handle_device_wakeup(d, ip, logger, Cnn_model, easyocr_reader):
    """
    Handles the device wake-up, unlock, and synchronization logic.
    Returns the device object (d), which might be re-connected.
    """
    global _wakeup_lock

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
                except Exception as e2:
                    logger.error(f"[{ip}] 重新連線失敗: {e2}")
                time.sleep(60)  # 等待 60 秒後重試
        while True:
            try:
                if d.info.get('screenOn'):
                    logger.info(f"[{ip}] 偵測到螢幕為開啟狀態，可能正在使用中。等待 5 秒後重試...")
                    time.sleep(5)
                if not d.info.get('screenOn'):
                    break
            except Exception as e:
                logger.warning(f"[{ip}] 檢查螢幕狀態時發生錯誤: {e}")
                time.sleep(60)  # 等待 60 秒後重試
                try:
                    d = connect_u2_with_retries(ip, logger=logger)
                    logger.warning(f"[{ip}] 重新連接設備成功")
                except Exception as e2:
                    logger.warning(f"[{ip}] 重新連接設備失敗: {e2}")
        if not d.info.get('screenOn'):
            logger.info(f"[{ip}] 螢幕為關閉狀態，執行標準喚醒與解鎖...")
            d.unlock()  # 使用 uiautomator2 的標準解鎖方法
            d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
            d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
            time.sleep(2)  # 等待解鎖動畫完成

            # 再次確認螢幕是否成功開啟
            if not d.info.get('screenOn'):
                logger.warning(f"[{ip}] 標準解鎖失敗，嘗試備用方案: Power鍵 + 上滑")
                d.press("power")  # 按下電源鍵
                time.sleep(1)
                d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1) # 從下往上滑動
                time.sleep(1)
        else:
            logger.info(f"[{ip}] 螢幕已是開啟狀態。")
        
    if 'emulator-5556' in ip or '3a8d31f2' in ip or 'emulator-5554' in ip :
        logger.info(f"[{ip}] 休眠5分鐘後繼續")
        time.sleep(60*5)
    time.sleep(2)
    
    #打開chrome
    if 'fc65396d' in ip or '192.168' in ip:
        close_nofication(d)
    time.sleep(1)
    
    d.app_stop("com.mxdzz.tw.and")    
    
    if 'emulator-5558' in ip :
        while _wakeup_lock == True:
            time.sleep(1)
            logger.warning(f"[{ip}] 等待解鎖")
        d.app_stop("com.mxdzz.tw.and")    
        _wakeup_lock = True # 問題點1：此處將 lock 設為 True。
        for i in range(60):
            if check_on_line(Cnn_model, easyocr_reader):
                break
            time.sleep(5*60) 
        _wakeup_lock = False

    # 問題點2：其他執行緒 (ip 為 '3a8d31f2' 或 'emulator-5554') 會檢查 lock
    while(('3a8d31f2' in ip or 'emulator-5554' in ip) and _wakeup_lock == True ):
        logger.warning("等待解鎖") # 將持續印出此訊息
        time.sleep(3)
        
    if 'emulator-5554' in ip or '3a8d31f2' in ip:
        _wakeup_lock = True    
        
    if 'emulator-5560' in ip:
        time.sleep(30*1)
        
    while(1):
        if d.info.get('screenOn') == True:
            break;
        logger.info("螢幕未開啟，嘗試解鎖")
        d.unlock()
        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05) # 從下往上滑動
        d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05) # 從下往上滑動
        time.sleep(1)
        
    d.press("home")
    d.press("home")
    d.press("home")
    
    return d
