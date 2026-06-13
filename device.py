import logging
import numpy as np
import uiautomator2 as u2
import time
import subprocess

from utils.main_page_guard import is_main_page_with_popup
from runtime_services.device_runtime_service import ForceSleepRequested, WakeLoopInterrupted

logger = logging.getLogger(__name__)


class device:
    def __init__(self, device: u2.Device):
        self.device = device

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        max_attempts = 10  # 避免死循环
        attempts = 0
        while attempts < max_attempts:
            img = self.device.screenshot(format='opencv')
            if img is None:
                raise ValueError("Failed to capture screenshot")

            if is_main_page_with_popup(img):
                self.device.click(509, 56)
                time.sleep(1)
                attempts += 1
                continue
            break

        if attempts == max_attempts:
            raise RuntimeError(
                "Maximum attempts reached without capturing valid screenshot")
        return img
def get_adb_devices():

    """
    Retrieves a list of connected Android devices using ADB.

    Returns:
        A list of device serial numbers (strings).  Returns an empty list
        if ADB is not found or no devices are connected.
    """
    try:
        # Run the adb devices command and capture the output
        result = subprocess.check_output(['adb', 'devices'], universal_newlines=True)

        # Parse the output to extract device serial numbers
        devices = []
        for line in result.splitlines():
            parts = line.split('\t')
            if len(parts) == 2 and parts[1] == 'device':
                devices.append(parts[0])
        return devices
    except FileNotFoundError:
        logger.error("ADB not found. Please ensure ADB is installed and in your system's PATH.")
        return []
    except subprocess.CalledProcessError as e:
        logger.error(f"ADB command failed with error: {e}")
        return []
    
def close_notification(d):
    """
    清理通知與 Messenger 氣泡等遮擋物 (僅適用於 Android/ADB 後端)
    """
    # web_h5 (Playwright) 後端沒有系統通知欄/Messenger 氣泡，且 PlaywrightGameDevice
    # 沒有 __call__，會在 d(packageNameMatches=...) 觸發 'object is not callable'。
    # 注意：不能只靠 hasattr('open_quick_settings')，因為 PlaywrightGameDevice 有同名 stub。
    if getattr(d, "backend_kind", None) == "web_h5":
        return
    if not hasattr(d, 'open_quick_settings'):
        return
    try:
        # 1. 處理系統設定 (方向鎖定/勿擾)
        try:
            d.open_quick_settings()
            time.sleep(1)
            if not d.xpath('//*[@content-desc="方向鎖定"]').info.get("checked"):
                d.xpath('//*[@content-desc="方向鎖定"]').click()   
            if not d.xpath('//*[@content-desc="勿擾模式"]').info.get("checked"):
                d.xpath('//*[@content-desc="勿擾模式"]').click()  
            d.click(0.71, 0.016) # 點擊空白處返回
            # 先確保通知欄/快捷選單收起，再偵測 Messenger；否則會在下拉選單前景
            # 狀態下誤判並嘗試掃氣泡。
            press_fn = getattr(d, "press", None)
            if callable(press_fn):
                press_fn("home")
                time.sleep(0.5)
        except (ForceSleepRequested, WakeLoopInterrupted):
            # 暫停/開網頁/強制休眠是控制流訊號，必須即時 unwind，不可降級為 warning。
            raise
        except Exception as e:
            logger.warning(f"[device] 例外: {e}")

        # 2. 處理 Messenger 氣泡 (包含雙開版本)
        # 遍歷所有元件，尋找包名包含 facebook.orca 的元件
        # 氣泡通常是一個浮動的 FrameLayout 或 ImageView
        screen_info = d.info
        screen_w = screen_info.get("displayWidth", 540)
        screen_h = screen_info.get("displayHeight", 960)
        
        # 嘗試尋找 Messenger 相關元件 (模糊匹配包名)
        fb_elements = d(packageNameMatches=r".*facebook\.orca.*")
        if fb_elements.exists:
            logger.info("偵測到 Messenger 相關氣泡/視窗")
            for el in fb_elements:
                try:
                    bounds = el.info.get("bounds")
                    if bounds:
                        mid_x = (bounds['left'] + bounds['right']) / 2
                        mid_y = (bounds['top'] + bounds['bottom']) / 2
                        
                        # 往螢幕底部中央滑動 (關閉氣泡)
                        logger.debug(f"掃除氣泡於: ({mid_x}, {mid_y}) -> 底部")
                        d.swipe(mid_x, mid_y, screen_w / 2, screen_h - 10, duration=0.2)
                        time.sleep(0.5)
                except (ForceSleepRequested, WakeLoopInterrupted):
                    raise
                except Exception as e:
                    logger.warning(f"[device] 例外: {e}")
                    continue
                    
    except (ForceSleepRequested, WakeLoopInterrupted):
        raise
    except Exception as e:
        logger.error(f"清理遮擋物時發生錯誤: {e}")
def open_notification(d):
    # Android/ADB 專用；web_h5 後端直接略過 (同 close_notification)。
    if getattr(d, "backend_kind", None) == "web_h5":
        return
    if not hasattr(d, 'open_quick_settings'):
        return
    try:
        d.open_quick_settings()
        if not d.xpath('//*[@content-desc="方向鎖定"]').info.get("checked"):
            d.xpath('//*[@content-desc="方向鎖定"]').click()   
        if d.xpath('//*[@content-desc="勿擾模式"]').info.get("checked"):
            d.xpath('//*[@content-desc="勿擾模式"]').click()  
        d.click(0.71, 0.016)
        time.sleep(1)
    except Exception as e:
        logger.error(f"An error occurred: {e}")

# Backward-compatible aliases
close_nofication = close_notification
open_nofication = open_notification
