"""
ADB 操作模組
整合常用的 ADB 和 uiautomator2 操作
"""
import subprocess
import shlex
import time
import random
import logging
from typing import Union, List
import uiautomator2 as u2

# 預設 logger
default_logger = logging.getLogger(__name__)


def run_adb(cmd: Union[str, List[str]], device_serial: str = None) -> str:
    """
    在終端執行 adb 指令並返回輸出。
    
    Args:
        cmd: 可以是完整的 shell 命令字符串 (shlex 語法) 或已拆好的參數列表 (List[str])
        device_serial: 設備序號，如果指定則用 -s 參數鎖定設備
        
    Returns:
        命令執行的標準輸出
        
    Raises:
        RuntimeError: 當 ADB 命令執行失敗時
    """
    base = ['adb']
    if device_serial:
        base += ['-s', device_serial]

    # 如果 cmd 是字符串，用 shlex.split 處理引號；若已是列表，直接用
    if isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = cmd

    full_cmd = base + args

    result = subprocess.run(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"ADB Error: {result.stderr.strip()}")
    return result.stdout.strip()


def safe_log(logger: logging.Logger, level: str, msg: str, *args, **kwargs):
    """Call logger methods but swallow exceptions raised by handler emit (e.g., OSError on flush).

    This prevents a logging handler failure from crashing the worker thread.
    """
    try:
        if level == 'info':
            logger.info(msg, *args, **kwargs)
        elif level == 'warning':
            logger.warning(msg, *args, **kwargs)
        elif level == 'error':
            logger.error(msg, *args, **kwargs)
        elif level == 'debug':
            logger.debug(msg, *args, **kwargs)
        else:
            logger.log(logging.INFO, msg, *args, **kwargs)
    except Exception:
        try:
            # Fallback to printing minimal message to stderr
            print(f"[LOG-{level.upper()}] {msg}", file=sys.stderr)
        except Exception:
            pass


def wait_for_device_ready(serial: str, timeout: int = 60, logger: logging.Logger = None) -> bool:
    """
    等待設備完全啟動 (檢查 sys.boot_completed)
    
    Args:
        serial: 設備序號
        timeout: 最大等待秒數
        logger: 日誌記錄器
        
    Returns:
        bool: 設備是否就緒
    """
    if logger is None:
        logger = default_logger
        
    start_time = time.time()
    logger.info(f"[{serial}] 等待系統啟動 (sys.boot_completed)...")
    
    while time.time() - start_time < timeout:
        try:
            # 檢查 boot_completed 屬性
            output = run_adb('shell getprop sys.boot_completed', device_serial=serial)
            if output == '1':
                logger.info(f"[{serial}] 系統已完全啟動")
                return True
        except Exception:
            pass
        time.sleep(2)
        
    logger.warning(f"[{serial}] 等待系統啟動超時 ({timeout}s)")
    return False


def connect_u2_with_retries(serial: str, max_retries: int = 5, initial_delay: int = 5, logger: logging.Logger = None) -> u2.Device:
    """
    嘗試用 uiautomator2 連線，多次重試並採用指數退避。
    連線前會先確認設備系統已完全啟動。
    
    Args:
        serial: 設備序號或 IP 地址
        max_retries: 最大重試次數
        initial_delay: 初始延遲秒數
        logger: 日誌記錄器
        
    Returns:
        連線成功的 device 物件
        
    Raises:
        Exception: 當達到最大重試次數後仍連線失敗
    """
    if logger is None:
        logger = default_logger

    # 先等待系統啟動 (僅針對 emulator 類型設備加強檢查)
    if 'emulator' in serial or '127.0.0.1' in serial:
        wait_for_device_ready(serial, timeout=30, logger=logger)

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            safe_log(logger, 'info', f"[{serial}] 嘗試連線 (attempt {attempt}/{max_retries})")
            d = u2.connect(serial)
            # 確認基本資訊可取得，避免回傳半連線物件
            _ = d.info
            safe_log(logger, 'info', f"[{serial}] 連線成功")
            return d
        except Exception as e:
            last_exc = e
            safe_log(logger, 'warning', f"[{serial}] 連線失敗: {e}")
            if attempt == max_retries:
                safe_log(logger, 'error', f"[{serial}] 已達最大重試次數 {max_retries}")
                break
            sleep_time = initial_delay * (2 ** (attempt - 1))
            safe_log(logger, 'info', f"[{serial}] {sleep_time}s 後重試...")
            time.sleep(sleep_time)

    # 最後再拋出最後一次的例外，讓呼叫端決定如何處理
    raise last_exc


def unlock_screen(d: u2.Device):
    """
    解鎖螢幕 (向右滑動)
    
    Args:
        d: uiautomator2 Device 物件
    """
    d.swipe(0.05, 0.7, 0.9, 0.7, 0.05)


def start_game_by_icon(d: u2.Device, ip: str, logger: logging.Logger = None) -> bool:
    """
    透過點擊桌面圖示啟動遊戲 (模擬真人操作)
    
    Args:
        d: uiautomator2 Device 物件
        ip: 設備 IP 或序號 (用於日誌)
        logger: 日誌記錄器
        
    Returns:
        是否成功透過圖示啟動 (True=圖示啟動, False=備用方式)
    """
    if logger is None:
        logger = default_logger
        
    try:
        # 回到桌面
        for i in range(2):
            d.press("home")
        time.sleep(1 + random.random())
        
        # 嘗試點擊「菇勇者傳說」圖示
        if d.xpath('//*[@text="菇勇者傳說"]').exists:
            logger.info(f"[{ip}] 找到遊戲圖示,點擊啟動")
            d.xpath('//*[@text="菇勇者傳說"]').click()
            time.sleep(2 + random.random())
            return True
        else:
            logger.warning(f"[{ip}] 未找到遊戲圖示,使用備用啟動方式")
            # 備用方案: 使用 app_start
            d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(2)
            return False
    except Exception as e:
        logger.error(f"[{ip}] 點擊圖示啟動失敗: {e}, 使用備用方式")
        d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
        time.sleep(2)
        return False


def check_in_game(d: u2.Device, package_name: str = "com.mxdzz.tw.and") -> bool:
    """
    檢查是否在遊戲中
    
    Args:
        d: uiautomator2 Device 物件
        package_name: 遊戲包名
        
    Returns:
        是否在遊戲中
    """
    return d.app_current().get("package") == package_name


def click_random(d: u2.Device, x: int, y: int, rand_range: int = 5):
    """
    在指定座標附近隨機點擊 (模擬真人操作)
    
    Args:
        d: uiautomator2 Device 物件
        x: X 座標
        y: Y 座標
        rand_range: 隨機偏移範圍
    """
    d.click(x + random.randint(-rand_range, rand_range), 
            y + random.randint(-rand_range, rand_range))


def safe_click(d: u2.Device, x: int, y: int, delay: float = 0.5, rand_delay: bool = True):
    """
    安全點擊 (帶延遲和隨機性)
    
    Args:
        d: uiautomator2 Device 物件
        x: X 座標
        y: Y 座標
        delay: 基礎延遲秒數
        rand_delay: 是否添加隨機延遲
    """
    d.click(x, y)
    if rand_delay:
        time.sleep(delay + random.random())
    else:
        time.sleep(delay)


def get_screen_on_status(d: u2.Device) -> bool:
    """
    獲取螢幕開啟狀態
    
    Args:
        d: uiautomator2 Device 物件
        
    Returns:
        螢幕是否開啟
    """
    return d.info.get('screenOn', False)


def ensure_screen_on(d: u2.Device, logger: logging.Logger = None):
    """
    確保螢幕開啟，如果未開啟則解鎖
    
    Args:
        d: uiautomator2 Device 物件
        logger: 日誌記錄器
    """
    if logger is None:
        logger = default_logger
        
    if not get_screen_on_status(d):
        logger.info("螢幕未開啟，嘗試解鎖")
        d.unlock()
        time.sleep(1)


def stop_app(d: u2.Device, package_name: str = "com.mxdzz.tw.and"):
    """
    停止應用程式
    
    Args:
        d: uiautomator2 Device 物件
        package_name: 應用包名
    """
    d.app_stop(package_name)


def screenshot_opencv(d: u2.Device):
    """
    截取螢幕 (OpenCV 格式)
    
    Args:
        d: uiautomator2 Device 物件
        
    Returns:
        OpenCV 格式的圖像
    """
    return d.screenshot(format='opencv')


def screenshot_pillow(d: u2.Device):
    """
    截取螢幕 (Pillow 格式)
    
    Args:
        d: uiautomator2 Device 物件
        
    Returns:
        Pillow 格式的圖像
    """
    return d.screenshot(format='pillow')


def set_screen_for_game(device_serial: str, logger=None):
    """
    為遊戲設置屏幕密度和尺寸（240 DPI, 540x960）
    
    Args:
        device_serial: 設備序號
        logger: 日誌記錄器（可選）
    """
    if logger:
        logger.info(f"[{device_serial}] 設置遊戲屏幕配置")
    
    run_adb('shell wm density 240', device_serial=device_serial)
    run_adb('shell wm size 540x960', device_serial=device_serial)


def reset_screen_settings(device_serial: str, logger=None):
    """
    重置屏幕密度和尺寸為默認值
    
    Args:
        device_serial: 設備序號
        logger: 日誌記錄器（可選）
    """
    if logger:
        logger.info(f"[{device_serial}] 重置屏幕配置")
    
    run_adb('shell wm density reset', device_serial=device_serial)
    run_adb('shell wm size reset', device_serial=device_serial)
