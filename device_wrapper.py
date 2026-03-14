import time
import bot_state
import logging

logger = logging.getLogger(__name__)

class MonitoredDevice:
    """
    uiautomator2 Device 的包裝類別。
    攔截關鍵動作 (如 click)，在動作前自動檢查暫停狀態並回報進度。
    """
    def __init__(self, original_d, ip: str):
        self._d = original_d
        self._ip = ip

    def click(self, x, y):
        """點擊前先檢查是否暫停"""
        bot_state.check_pause(self._ip)
        # 可以選擇回報更細的步驟 (但會增加 log 量)
        # bot_state.update_state(self._ip, step=f"點擊 ({x}, {y})")
        return self._d.click(x, y)

    def screenshot(self, *args, **kwargs):
        """截圖前也檢查暫停，確保不會在暫停時瘋狂截圖"""
        bot_state.check_pause(self._ip)
        return self._d.screenshot(*args, **kwargs)

    def swipe(self, *args, **kwargs):
        bot_state.check_pause(self._ip)
        return self._d.swipe(*args, **kwargs)

    def app_stop(self, pkg_name=None, *args, **kwargs):
        """Stop an app. Accepts either positional `pkg_name` or keyword `package_name`."""
        if pkg_name is None:
            pkg_name = kwargs.pop('package_name', None)
        if pkg_name is None:
            raise TypeError("app_stop() missing 1 required positional argument: 'pkg_name'")
        # Call underlying device; prefer keyword for compatibility
        try:
            return self._d.app_stop(pkg_name, *args, **kwargs)
        except TypeError:
            return self._d.app_stop(package_name=pkg_name, *args, **kwargs)

    def app_start(self, pkg_name=None, *args, **kwargs):
        """Start an app. Accepts either positional `pkg_name` or keyword `package_name`."""
        if pkg_name is None:
            pkg_name = kwargs.pop('package_name', None)
        if pkg_name is None:
            raise TypeError("app_start() missing 1 required positional argument: 'pkg_name'")
        try:
            return self._d.app_start(pkg_name, *args, **kwargs)
        except TypeError:
            return self._d.app_start(package_name=pkg_name, *args, **kwargs)

    def __getattr__(self, name):
        """
        委派 (Delegation): 
        如果呼叫的方法在本類別中沒定義 (例如 .xpath, .info, .press)，
        則自動轉發給原始的 u2 設備物件。
        """
        return getattr(self._d, name)

    def __call__(self, *args, **kwargs):
        """
        支援 d(text="...") 這種選擇器語法。
        直接轉發給原始 device 物件。
        """
        # 這裡也可以選擇檢查暫停，視需求而定
        # bot_state.check_pause(self._ip)
        return self._d(*args, **kwargs)

    def sleep(self, seconds: float):
        """
        自定義的可打斷休眠。
        將長時間休眠拆解成小段，每段都檢查暫停標誌。
        """
        end_time = time.time() + seconds
        
        # 只有大於 5 秒的休眠才記錄 Log，避免洗版
        if seconds > 5:
            bot_state.update_state(self._ip, log=f"休眠 {seconds} 秒...")
        
        while time.time() < end_time:
            # 隨時檢查暫停
            bot_state.check_pause(self._ip)
            
            # 檢查是否收到跳過指令
            if bot_state.check_skip_sleep(self._ip):
                bot_state.update_state(self._ip, log="休眠已跳過")
                break
                
            # 每次休眠一小段，提高反應速度
            time.sleep(0.5)
            if time.time() >= end_time:
                break
