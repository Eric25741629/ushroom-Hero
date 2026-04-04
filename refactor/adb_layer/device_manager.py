"""
設備管理層 - 統一管理 Android 設備連接和操作
依賴於 adb_base.py

負責：
- 連接/斷開設備
- 維護設備狀態
- 提供高層設備操作接口
"""

import logging
import time
import uiautomator2 as u2
from typing import Optional, List, Dict
from . import adb_base

logger = logging.getLogger(__name__)


class DeviceConnectionError(Exception):
    """設備連接失敗"""
    pass


class DeviceInfo:
    """設備信息"""
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.status = "unknown"  # connected, disconnected, offline
        self.u2_device: Optional[u2.Device] = None
        self.last_connected = None
        self.connection_attempts = 0
    
    def __repr__(self):
        return f"DeviceInfo(id={self.device_id}, status={self.status})"


class DeviceManager:
    """設備管理器"""
    
    def __init__(self, logger_instance: logging.Logger = None, max_retries: int = 3):
        """
        初始化設備管理器
        
        Args:
            logger_instance: 日誌記錄器
            max_retries: 連接重試次數
        """
        self.logger = logger_instance or logger
        self.max_retries = max_retries
        self.devices: Dict[str, DeviceInfo] = {}
        self._discover_devices()
    
    def _discover_devices(self) -> List[str]:
        """
        發現所有已連接的設備
        
        Returns:
            設備 ID 列表
        """
        try:
            device_ids = adb_base.list_devices()
            for device_id in device_ids:
                if device_id not in self.devices:
                    self.devices[device_id] = DeviceInfo(device_id)
                self.devices[device_id].status = "discovered"
            
            self.logger.info(f"發現 {len(device_ids)} 個設備: {device_ids}")
            return device_ids
        except Exception as e:
            self.logger.error(f"發現設備失敗: {e}")
            return []
    
    def connect(self, device_id: str, timeout: int = 30) -> u2.Device:
        """
        連接到指定設備
        
        Args:
            device_id: 設備 ID（如 'emulator-5554'）
            timeout: 連接超時時間
        
        Returns:
            uiautomator2 Device 對象
        
        Raises:
            DeviceConnectionError: 連接失敗
        """
        
        if device_id not in self.devices:
            self.devices[device_id] = DeviceInfo(device_id)
        
        device_info = self.devices[device_id]
        
        # 如果已連接，直接返回
        if device_info.u2_device is not None:
            try:
                # 驗證連接仍然有效
                device_info.u2_device.info
                self.logger.debug(f"[{device_id}] 已連接")
                return device_info.u2_device
            except Exception:
                # 連接無效，重新連接
                device_info.u2_device = None
        
        # 重試連接
        last_error = None
        for attempt in range(self.max_retries):
            try:
                self.logger.info(f"[{device_id}] 嘗試連接（{attempt + 1}/{self.max_retries}）...")
                
                # 使用 uiautomator2 連接
                device = u2.connect(
                    device_id,
                    adb_path=adb_base.ADB_PATH,
                    cache_dir=None  # 禁用緩存，每次建立新連接
                )
                
                # 驗證連接
                device.info
                
                device_info.u2_device = device
                device_info.status = "connected"
                device_info.last_connected = time.time()
                device_info.connection_attempts = 0
                
                self.logger.info(f"[{device_id}] 已成功連接")
                return device
            
            except Exception as e:
                last_error = str(e)
                self.logger.warning(f"[{device_id}] 連接失敗: {e}")
                
                device_info.connection_attempts += 1
                
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # 指數退避
                    self.logger.debug(f"[{device_id}] 等待 {wait_time} 秒後重試...")
                    time.sleep(wait_time)
        
        device_info.status = "connection_failed"
        raise DeviceConnectionError(
            f"無法連接到設備 {device_id} 在 {self.max_retries} 次重試後: {last_error}"
        )
    
    def disconnect(self, device_id: str) -> None:
        """
        斷開指定設備的連接
        
        Args:
            device_id: 設備 ID
        """
        if device_id in self.devices:
            device_info = self.devices[device_id]
            device_info.u2_device = None
            device_info.status = "disconnected"
            self.logger.info(f"[{device_id}] 已斷開連接")
    
    def get_device(self, device_id: str) -> Optional[u2.Device]:
        """
        獲取已連接的設備（不自動連接）
        
        Args:
            device_id: 設備 ID
        
        Returns:
            Device 對象或 None
        """
        if device_id in self.devices:
            return self.devices[device_id].u2_device
        return None
    
    def list_all_devices(self) -> List[str]:
        """
        列出所有已發現的設備 ID
        
        Returns:
            設備 ID 列表
        """
        return list(self.devices.keys())
    
    def get_device_status(self, device_id: str) -> str:
        """
        獲取設備狀態
        
        Args:
            device_id: 設備 ID
        
        Returns:
            狀態字符串
        """
        if device_id in self.devices:
            return self.devices[device_id].status
        return "unknown"
    
    def execute_adb_command(
        self,
        device_id: str,
        cmd: str,
        timeout: int = 30
    ) -> str:
        """
        在設備上執行 ADB 命令
        
        Args:
            device_id: 設備 ID
            cmd: 命令字符串
            timeout: 超時時間
        
        Returns:
            命令輸出
        """
        try:
            return adb_base.execute_adb_command(
                cmd,
                device_serial=device_id,
                timeout=timeout
            )
        except Exception as e:
            self.logger.error(f"[{device_id}] ADB 命令失敗: {e}")
            raise
    
    def set_screen_config(
        self,
        device_id: str,
        density: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> None:
        """
        設置設備屏幕配置
        
        Args:
            device_id: 設備 ID
            density: 屏幕密度（可選）
            width: 屏幕寬度（可選）
            height: 屏幕高度（可選）
        """
        try:
            if density:
                adb_base.set_screen_density(density, device_id)
            
            if width and height:
                adb_base.set_screen_size(width, height, device_id)
            
            self.logger.info(f"[{device_id}] 屏幕配置已設置")
        except Exception as e:
            self.logger.error(f"[{device_id}] 設置屏幕配置失敗: {e}")
            raise
    
    def reset_screen_config(self, device_id: str) -> None:
        """
        重置設備屏幕配置
        
        Args:
            device_id: 設備 ID
        """
        try:
            adb_base.reset_screen_settings(device_id)
            self.logger.info(f"[{device_id}] 屏幕配置已重置")
        except Exception as e:
            self.logger.error(f"[{device_id}] 重置屏幕配置失敗: {e}")
            raise
    
    def launch_app(
        self,
        device_id: str,
        package_name: str,
        use_clone: bool = False,
        clone_index: int = 1
    ) -> None:
        """
        啟動應用
        
        Args:
            device_id: 設備 ID
            package_name: 應用包名
            use_clone: 是否啟動克隆版
            clone_index: 克隆序號
        """
        try:
            device = self.get_device(device_id)
            if device is None:
                device = self.connect(device_id)
            
            if use_clone:
                # 啟動克隆版本
                adb_base.launch_clone(package_name, clone_index, device_id)
            else:
                # 啟動普通版本
                device.app_start(package_name)
            
            self.logger.info(f"[{device_id}] 應用 {package_name} 已啟動")
        except Exception as e:
            self.logger.error(f"[{device_id}] 啟動應用失敗: {e}")
            raise
    
    def stop_app(self, device_id: str, package_name: str) -> None:
        """
        停止應用
        
        Args:
            device_id: 設備 ID
            package_name: 應用包名
        """
        try:
            device = self.get_device(device_id)
            if device:
                device.app_stop(package_name)
                self.logger.info(f"[{device_id}] 應用 {package_name} 已停止")
        except Exception as e:
            self.logger.error(f"[{device_id}] 停止應用失敗: {e}")
            raise
    
    def get_screenshot(self, device_id: str, format: str = 'opencv'):
        """
        獲取屏幕截圖
        
        Args:
            device_id: 設備 ID
            format: 格式（'opencv', 'pillow'）
        
        Returns:
            截圖數據
        """
        try:
            device = self.get_device(device_id)
            if device is None:
                device = self.connect(device_id)
            
            return device.screenshot(format=format)
        except Exception as e:
            self.logger.error(f"[{device_id}] 獲取截圖失敗: {e}")
            raise


if __name__ == "__main__":
    # 測試設備管理器
    logging.basicConfig(level=logging.INFO)
    
    manager = DeviceManager()
    
    # 列出所有設備
    devices = manager.list_all_devices()
    print(f"找到設備: {devices}")
    
    # 連接第一個設備
    if devices:
        device_id = devices[0]
        try:
            device = manager.connect(device_id)
            print(f"已連接: {device_id}")
            
            # 獲取設備信息
            print(f"設備信息: {device.info}")
        except Exception as e:
            print(f"連接失敗: {e}")
