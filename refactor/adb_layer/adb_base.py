"""
ADB 基礎層 - 所有 ADB 命令執行的最底層
負責與 Android Debug Bridge 直接交互

提取自: adb_devices.py
"""

import subprocess
import shlex
import re
import logging
import time
from typing import Union, List, Optional

logger = logging.getLogger(__name__)

# ADB 工具位置（如果不在 PATH 可修改為絕對路徑）
ADB_PATH = "adb"


class ADBCommandError(Exception):
    """ADB 命令執行失敗"""
    pass


class ADBNotFoundError(Exception):
    """ADB 工具未找到"""
    pass


def execute_adb_command(
    cmd: Union[str, List[str]], 
    device_serial: Optional[str] = None,
    timeout: int = 30,
    max_retries: int = 3
) -> str:
    """
    執行 ADB 命令（帶重試邏輯）
    
    Args:
        cmd: ADB 命令字符串或參數列表
        device_serial: 設備 ID（如 'emulator-5554'）
        timeout: 命令超時時間（秒）
        max_retries: 最大重試次數
    
    Returns:
        命令輸出字符串
    
    Raises:
        ADBNotFoundError: ADB 工具未找到
        ADBCommandError: 命令執行失敗
    """
    
    # 構建基礎命令
    base_cmd = [ADB_PATH]
    if device_serial:
        base_cmd.extend(['-s', device_serial])
    
    # 解析命令參數
    if isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = list(cmd)
    
    full_cmd = base_cmd + args
    
    # 記錄命令（隱藏敏感信息）
    cmd_log = ' '.join(full_cmd)
    if device_serial:
        cmd_log = cmd_log.replace(device_serial, '***')
    logger.debug(f"執行 ADB 命令: {cmd_log}")
    
    # 重試邏輯
    last_error = None
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                check=False
            )
            
            # 檢查命令是否成功
            if result.returncode == 0:
                return result.stdout.strip() if result.stdout else ""
            else:
                # 某些命令即使返回非 0 也可能成功（如 logcat）
                if result.stderr:
                    last_error = result.stderr.strip()
                    logger.warning(f"ADB 警告: {last_error}")
                # 仍然返回輸出
                return result.stdout.strip() if result.stdout else ""
        
        except FileNotFoundError:
            raise ADBNotFoundError(
                f"未找到 ADB 工具。請確保 '{ADB_PATH}' 在系統 PATH 中，"
                f"或在 adb_base.py 中修改 ADB_PATH 為絕對路徑"
            )
        
        except subprocess.TimeoutExpired:
            last_error = f"命令超時（{timeout}秒）"
            logger.warning(f"ADB 命令超時（嘗試 {attempt + 1}/{max_retries}）")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指數退避
            continue
        
        except Exception as e:
            last_error = str(e)
            logger.error(f"ADB 命令執行異常: {e}")
            raise ADBCommandError(f"ADB 命令失敗: {e}")
    
    # 所有重試都失敗
    raise ADBCommandError(f"ADB 命令在 {max_retries} 次重試後仍失敗: {last_error}")


def list_devices() -> List[str]:
    """
    列出所有已連接的 Android 設備
    
    Returns:
        設備 ID 列表，如 ['emulator-5554', 'fc65396d']
    """
    try:
        output = execute_adb_command('devices')
        devices = []
        for line in output.split('\n'):
            line = line.strip()
            # 跳過空行和標題行
            if not line or line.startswith('List'):
                continue
            # 格式: "device_id    device" 或 "device_id    offline"
            if '\t' in line:
                device_id = line.split()[0]
                status = line.split()[1]
                if status == 'device':  # 只取在線的設備
                    devices.append(device_id)
        return devices
    except Exception as e:
        logger.error(f"列出設備失敗: {e}")
        return []


def list_users(device_serial: str) -> List[str]:
    """
    列出設備上的所有用戶
    
    Args:
        device_serial: 設備 ID
    
    Returns:
        用戶 ID 列表
    """
    raw = execute_adb_command("shell pm list users", device_serial=device_serial)
    return re.findall(r"UserInfo\{(\d+):", raw)


def user_has_package(user: str, pkg: str, device_serial: str) -> bool:
    """
    檢查用戶是否安裝了指定包
    
    Args:
        user: 用戶 ID
        pkg: 包名稱
        device_serial: 設備 ID
    
    Returns:
        是否安裝
    """
    raw = execute_adb_command(
        f"shell pm list packages --user {user}",
        device_serial=device_serial
    )
    return any(line.strip().endswith(pkg) for line in raw.splitlines())


def resolve_main_activity(pkg: str, device_serial: str) -> str:
    """
    解析應用的主 Activity
    
    Args:
        pkg: 包名稱
        device_serial: 設備 ID
    
    Returns:
        完整的 Activity 名稱（如 com.example/.MainActivity）
    """
    raw = execute_adb_command(
        f"shell cmd package resolve-activity --brief "
        f"-c android.intent.category.LAUNCHER {pkg}",
        device_serial=device_serial
    ).strip()
    
    for line in raw.splitlines():
        if "/" in line:
            return line.strip()
    
    raise ADBCommandError(f"無法解析 {pkg} 的主 Activity，輸出：\n{raw}")


def launch_clone(pkg: str, clone_index: int, device_serial: str) -> str:
    """
    啟動應用的克隆版本（多開）
    
    Args:
        pkg: 包名稱
        clone_index: 克隆序號（1-based）
        device_serial: 設備 ID
    
    Returns:
        命令輸出
    """
    users = list_users(device_serial)
    clones = [u for u in users if user_has_package(u, pkg, device_serial)]
    
    if not clones:
        raise ADBCommandError(f"包 {pkg} 未在任何用戶下安裝")
    
    if not (1 <= clone_index <= len(clones)):
        raise ValueError(f"clone_index 超出範圍 (1–{len(clones)})")
    
    target_user = clones[clone_index - 1]
    comp = resolve_main_activity(pkg, device_serial)
    
    cmd = (
        f"shell am start --user {target_user} "
        f"-a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER "
        f"-n {comp}"
    )
    
    return execute_adb_command(cmd, device_serial=device_serial)


def set_screen_density(density: int, device_serial: str) -> None:
    """
    設置屏幕密度
    
    Args:
        density: 密度值（如 240）
        device_serial: 設備 ID
    """
    execute_adb_command(
        f"shell wm density {density}",
        device_serial=device_serial
    )
    logger.info(f"[{device_serial}] 屏幕密度設置為 {density}")


def set_screen_size(width: int, height: int, device_serial: str) -> None:
    """
    設置屏幕尺寸
    
    Args:
        width: 寬度
        height: 高度
        device_serial: 設備 ID
    """
    execute_adb_command(
        f"shell wm size {width}x{height}",
        device_serial=device_serial
    )
    logger.info(f"[{device_serial}] 屏幕尺寸設置為 {width}x{height}")


def reset_screen_settings(device_serial: str) -> None:
    """
    重置屏幕設置（密度和尺寸）
    
    Args:
        device_serial: 設備 ID
    """
    execute_adb_command(
        "shell wm density reset && wm size reset",
        device_serial=device_serial
    )
    logger.info(f"[{device_serial}] 屏幕設置已重置")


if __name__ == "__main__":
    # 測試 ADB 基礎層
    logging.basicConfig(level=logging.DEBUG)
    
    try:
        # 列出設備
        devices = list_devices()
        print(f"找到 {len(devices)} 個設備: {devices}")
        
        # 如果有設備，嘗試連接
        if devices:
            device_id = devices[0]
            print(f"\n測試設備: {device_id}")
    except Exception as e:
        print(f"錯誤: {e}")
