"""
ADB 操作模組
整合常用的 ADB 和 uiautomator2 操作
"""
import subprocess
import shlex
import time
import random
import logging
import sys
from typing import Union, List
import uiautomator2 as u2

try:
    from utils.mumu_control import discover_control_exe, MuMuController
except Exception:
    discover_control_exe = None
    MuMuController = None

# 預設 logger
default_logger = logging.getLogger(__name__)
_mumu_controller = None


def tap_device(d, x: int, y: int, *args, **kwargs):
    """統一封裝點擊動作，優先使用 tap，其次回退到 click。"""
    tap_fn = getattr(d, "tap", None)
    if callable(tap_fn):
        return tap_fn(x, y, *args, **kwargs)

    click_fn = getattr(d, "click", None)
    if callable(click_fn):
        return click_fn(x, y, *args, **kwargs)

    raise AttributeError("Device does not support tap/click")


def swipe_device(d, *args, **kwargs):
    """統一封裝滑動動作，優先使用 swipe，其次回退到 gesture_swipe。"""
    swipe_fn = getattr(d, "swipe", None)
    if callable(swipe_fn):
        return swipe_fn(*args, **kwargs)

    gesture_swipe_fn = getattr(d, "gesture_swipe", None)
    if callable(gesture_swipe_fn):
        return gesture_swipe_fn(*args, **kwargs)

    raise AttributeError("Device does not support swipe/gesture_swipe")


def xpath_click_device(d, xpath_expr: str, *args, **kwargs):
    """統一封裝 XPath 點擊，讓上層只依賴單一介面。"""
    xpath_fn = getattr(d, "xpath", None)
    if not callable(xpath_fn):
        raise AttributeError("Device does not support xpath")

    node = xpath_fn(xpath_expr)
    click_fn = getattr(node, "click", None)
    if callable(click_fn):
        click_fn(*args, **kwargs)
        return True

    raise AttributeError("XPath node does not support click")


def _is_emulator_offline_error(msg: str) -> bool:
    text = (msg or "").lower()
    markers = [
        "not online",
        "not found",
        "device offline",
        "offline",
        "cannot connect",
        "connection",
        "timeout",
    ]
    return any(m in text for m in markers)


def _get_mumu_controller(logger: logging.Logger = None):
    global _mumu_controller
    if _mumu_controller is not None:
        return _mumu_controller

    if discover_control_exe is None or MuMuController is None:
        return None

    exe = discover_control_exe()
    if not exe:
        if logger:
            safe_log(logger, 'warning', "[Watchdog] 找不到 MuMuManager.exe，無法執行模擬器重啟")
        return None

    _mumu_controller = MuMuController(exe)
    return _mumu_controller


def _try_restart_emulator(serial: str, logger: logging.Logger = None) -> bool:
    controller = _get_mumu_controller(logger)
    if controller is None:
        return False

    action = controller.restart(serial)
    if action.ok:
        if logger:
            safe_log(logger, 'info', f"[{serial}] MuMu 重啟成功: rc={action.returncode}, cost={action.duration_sec:.2f}s")
        return True

    if logger:
        safe_log(
            logger,
            'error',
            f"[{serial}] MuMu 重啟失敗: rc={action.returncode}, stderr={action.stderr or '<empty>'}"
        )
    return False


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


def connect_u2_with_retries(
    serial: str,
    max_retries: int = 5,
    initial_delay: int = 5,
    logger: logging.Logger = None,
    auto_restart_on_offline: bool = True,
) -> u2.Device:
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
        wait_for_device_ready(serial, timeout=5, logger=logger)

    last_exc = None
    restart_attempted = False
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

            # 對 emulator 的 offline 錯誤，允許自動重啟一次再重試
            if (
                auto_restart_on_offline
                and serial.startswith('emulator-')
                and not restart_attempted
                and _is_emulator_offline_error(str(e))
            ):
                safe_log(logger, 'warning', f"[{serial}] 偵測到離線錯誤，嘗試自動重啟模擬器...")
                if _try_restart_emulator(serial, logger=logger):
                    restart_attempted = True
                    safe_log(logger, 'info', f"[{serial}] 重啟後等待 8s 再重試連線")
                    time.sleep(8)
                    continue

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
    swipe_device(d, 0.05, 0.7, 0.9, 0.7, 0.05)


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
        # 先確保已進入桌面
        if not ensure_on_launcher(d, ip, logger):
            logger.warning(f"[{ip}] 無法進入桌面，嘗試強制返回並繼續")
            # 嘗試多次返回
            for _ in range(5):
                d.press("back")
                time.sleep(0.3)
            d.press("home")
            time.sleep(1)

        # 回到桌面
        for i in range(2):
            d.press("home")
        time.sleep(1 + random.random())

        # 再次確認在桌面
        if not is_on_launcher(d, logger):
            logger.warning(f"[{ip}] 按 home 後仍不在桌面，嘗試解鎖")
            try:
                if not d.info.get('screenOn'):
                    d.unlock()
                    time.sleep(0.5)
                    d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
                    time.sleep(1)
            except Exception:
                pass

        # 嘗試點擊「菇勇者傳說」圖示
        if d.xpath('//*[@text="菇勇者傳說"]').exists:
            logger.info(f"[{ip}] 找到遊戲圖示,點擊啟動")
            xpath_click_device(d, '//*[@text="菇勇者傳說"]')
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
    tap_device(d,
               x + random.randint(-rand_range, rand_range),
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
    tap_device(d, x, y)
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


def get_battery_level(device_serial: str, logger: logging.Logger = None) -> int:
    """
    獲取設備電池電量百分比

    Args:
        device_serial: 設備序號
        logger: 日誌記錄器（可選）

    Returns:
        int: 電池電量百分比 (0-100)，如果獲取失敗返回 -1
    """
    if logger is None:
        logger = default_logger

    try:
        output = run_adb('shell dumpsys battery', device_serial=device_serial)
        # 解析 level: XX
        for line in output.split('\n'):
            if 'level:' in line.lower():
                level_str = line.split(':')[-1].strip()
                return int(level_str)
        logger.warning(f"[{device_serial}] 無法從 dumpsys battery 解析電量")
        return -1
    except Exception as e:
        logger.error(f"[{device_serial}] 獲取電量失敗: {e}")
        return -1


def is_on_launcher(d: u2.Device, logger: logging.Logger = None) -> bool:
    """
    檢查設備是否已進入桌面（Launcher）

    Args:
        d: uiautomator2 Device 物件
        logger: 日誌記錄器（可選）

    Returns:
        bool: 是否在桌面
    """
    if logger is None:
        logger = default_logger

    try:
        current_app = d.app_current()
        package = current_app.get('package', '')

        # 常見的桌面 Launcher 包名
        launcher_packages = [
            'com.android.launcher',
            'com.android.launcher2',
            'com.android.launcher3',
            'com.google.android.launcher',
            'com.miui.home',  # 小米
            'com.huawei.android.launcher',  # 華為
            'com.sec.android.app.launcher',  # 三星
            'com.oppo.launcher',  # OPPO
            'com.vivo.launcher',  # vivo
            'com.asus.launcher',  # 華碩
            'com.sonyericsson.home',  # Sony
            'com.htc.launcher',  # HTC
            'com.lge.launcher',  # LG
            'com.google.android.apps.nexuslauncher',  # Pixel
            'com.microsoft.launcher',
            'com.teslacoilsw.launcher',  # Nova Launcher
            'com.actionlauncher.playstore',  # Action Launcher
        ]

        is_launcher = any(pkg in package for pkg in launcher_packages)

        if is_launcher:
            logger.debug(f"[{d.serial}] 檢測到桌面: {package}")
        else:
            logger.debug(f"[{d.serial}] 當前應用不是桌面: {package}")

        return is_launcher
    except Exception as e:
        logger.warning(f"[{d.serial}] 檢查桌面狀態失敗: {e}")
        return False


def ensure_on_launcher(d: u2.Device, ip: str, logger: logging.Logger = None, max_attempts: int = 5) -> bool:
    """
    確保設備已進入桌面，如果不在桌面則嘗試返回

    Args:
        d: uiautomator2 Device 物件
        ip: 設備 IP 或序號（用於日誌）
        logger: 日誌記錄器（可選）
        max_attempts: 最大嘗試次數

    Returns:
        bool: 是否成功進入桌面
    """
    if logger is None:
        logger = default_logger

    for attempt in range(max_attempts):
        if is_on_launcher(d, logger):
            logger.info(f"[{ip}] 已進入桌面")
            return True

        logger.info(f"[{ip}] 未在桌面，嘗試返回 (attempt {attempt + 1}/{max_attempts})")

        # 嘗試返回桌面
        d.press("home")
        time.sleep(0.5 + random.random() * 0.5)

        # 嘗試解鎖（如果在鎖定畫面）
        try:
            if not d.info.get('screenOn'):
                d.unlock()
                time.sleep(0.5)
                d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.1)
                time.sleep(0.5)
        except Exception:
            pass

        # 再次檢查
        if is_on_launcher(d, logger):
            logger.info(f"[{ip}] 成功進入桌面")
            return True

        time.sleep(1)

    logger.warning(f"[{ip}] 無法進入桌面，已達最大嘗試次數")
    return False

