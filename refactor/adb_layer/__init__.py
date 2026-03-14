"""
ADB 設備通信層 - 初始化文件

提供所有 ADB 相關功能的統一入口
"""

from .adb_base import (
    execute_adb_command,
    list_devices,
    list_users,
    user_has_package,
    resolve_main_activity,
    launch_clone,
    set_screen_density,
    set_screen_size,
    reset_screen_settings,
    ADBCommandError,
    ADBNotFoundError,
)

from .device_manager import (
    DeviceManager,
    DeviceInfo,
    DeviceConnectionError,
)

__all__ = [
    # adb_base
    'execute_adb_command',
    'list_devices',
    'list_users',
    'user_has_package',
    'resolve_main_activity',
    'launch_clone',
    'set_screen_density',
    'set_screen_size',
    'reset_screen_settings',
    'ADBCommandError',
    'ADBNotFoundError',
    
    # device_manager
    'DeviceManager',
    'DeviceInfo',
    'DeviceConnectionError',
]
