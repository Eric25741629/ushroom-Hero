# -*- coding: utf-8 -*-
"""
設備管理器 - 處理設備連接和基本操作
"""
import uiautomator2 as u2
import subprocess
import time
import logging
import shlex
from typing import Optional, Union, List, Dict, Any
from config.game_config import config

class DeviceManager:
    """設備管理器"""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.device: Optional[u2.Device] = None
        self.logger = logging.getLogger(f"Device-{device_id}")
        self.device_config = config.DEVICE_CONFIGS.get(device_id, {})
        self._connection_retry_count = 0
        self._max_retry_count = 3
    
    def connect(self) -> bool:
        """
        連接設備
        
        Returns:
            是否連接成功
        """
        for attempt in range(self._max_retry_count):
            try:
                self.device = u2.connect(self.device_id)
                self.logger.info(f"設備連接成功: {self.device_id}")
                self._connection_retry_count = 0
                return True
                
            except Exception as e:
                self._connection_retry_count += 1
                self.logger.warning(f"設備連接失敗 (嘗試 {attempt + 1}/{self._max_retry_count}): {e}")
                
                if attempt < self._max_retry_count - 1:
                    time.sleep(2 ** attempt)  # 指數退避
        
        self.logger.error(f"設備連接失敗，已達最大重試次數: {self.device_id}")
        return False
    
    def is_connected(self) -> bool:
        """檢查設備是否已連接"""
        try:
            if self.device is None:
                return False
            
            # 嘗試獲取設備資訊來驗證連接
            self.device.device_info
            return True
            
        except Exception:
            return False
    
    def ensure_connection(self) -> bool:
        """確保設備連接"""
        if not self.is_connected():
            return self.connect()
        return True
    
    def is_game_running(self) -> bool:
        """檢查遊戲是否正在運行"""
        try:
            if not self.ensure_connection():
                return False
                
            current_app = self.device.app_current()
            return current_app.get("package") == config.PACKAGE_NAME
            
        except Exception as e:
            self.logger.error(f"檢查遊戲狀態失敗: {e}")
            return False
    
    def start_game(self) -> bool:
        """
        啟動遊戲
        
        Returns:
            是否啟動成功
        """
        try:
            if not self.ensure_connection():
                return False
            
            # 特殊設備處理
            if self.device_config.get('need_clone'):
                self._launch_clone_app()
                time.sleep(1)
                self._set_device_resolution()
            else:
                self.device.app_start(package_name=config.PACKAGE_NAME, use_monkey=True)
            
            time.sleep(20)  # 等待遊戲啟動
            
            # 等待遊戲完全加載
            return self._wait_for_game_loaded()
            
        except Exception as e:
            self.logger.error(f"啟動遊戲失敗: {e}")
            return False
    
    def stop_game(self):
        """停止遊戲"""
        try:
            if self.ensure_connection():
                self.device.app_stop(config.PACKAGE_NAME)
                self.logger.info("遊戲已停止")
        except Exception as e:
            self.logger.error(f"停止遊戲失敗: {e}")
    
    def unlock_device(self):
        """解鎖設備（針對實體設備）"""
        if not self.device_config.get('need_unlock'):
            return
        
        try:
            if self.ensure_connection():
                self.device.unlock()
                time.sleep(0.3)
                self.device.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05)
                time.sleep(0.3)
                self.logger.debug("設備已解鎖")
        except Exception as e:
            self.logger.error(f"解鎖設備失敗: {e}")
    
    def screen_off(self):
        """關閉螢幕"""
        if not self.device_config.get('screen_off'):
            return
        
        try:
            if self.ensure_connection():
                self.device.screen_off()
                self.logger.debug("螢幕已關閉")
        except Exception as e:
            self.logger.error(f"關閉螢幕失敗: {e}")
    
    def _launch_clone_app(self):
        """啟動克隆應用（針對特殊設備）"""
        try:
            # 這裡需要實現launch_clone函數的邏輯
            # output = launch_clone(config.PACKAGE_NAME, 2, device_serial=self.device_id)
            self.logger.debug("克隆應用已啟動")
        except Exception as e:
            self.logger.error(f"啟動克隆應用失敗: {e}")
    
    def _set_device_resolution(self):
        """設置設備解析度"""
        try:
            self.run_adb_command('shell wm density 240 && wm size 540x960')
            self.logger.debug("設備解析度已設置")
        except Exception as e:
            self.logger.error(f"設置解析度失敗: {e}")
    
    def _reset_device_resolution(self):
        """重置設備解析度"""
        if not self.device_config.get('density_reset'):
            return
        
        try:
            self.run_adb_command('shell wm density reset && wm size reset')
            self.logger.debug("設備解析度已重置")
        except Exception as e:
            self.logger.error(f"重置解析度失敗: {e}")
    
    def _wait_for_game_loaded(self, timeout: int = 60) -> bool:
        """等待遊戲加載完成"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.is_game_running():
                # 這裡可以添加更詳細的遊戲載入檢查
                time.sleep(5)  # 額外等待確保完全加載
                return True
            time.sleep(1)
        
        self.logger.warning("等待遊戲加載超時")
        return False
    
    def run_adb_command(self, cmd: Union[str, List[str]]) -> str:
        """
        執行ADB命令
        
        Args:
            cmd: ADB命令字符串或參數列表
            
        Returns:
            命令輸出結果
        """
        base = ['adb']
        if self.device_id:
            base += ['-s', self.device_id]
        
        # 處理命令參數
        if isinstance(cmd, str):
            args = shlex.split(cmd)
        else:
            args = cmd
        
        full_cmd = base + args
        
        try:
            result = subprocess.run(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=30  # 30秒超時
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"ADB命令執行失敗: {result.stderr.strip()}")
            
            return result.stdout.strip()
            
        except subprocess.TimeoutExpired:
            self.logger.error("ADB命令執行超時")
            raise RuntimeError("ADB命令執行超時")
        except Exception as e:
            self.logger.error(f"ADB命令執行失敗: {e}")
            raise
    
    def click_safe(self, x: int, y: int, retry_count: int = 2) -> bool:
        """
        安全點擊（帶重試）
        
        Args:
            x, y: 點擊坐標
            retry_count: 重試次數
            
        Returns:
            是否點擊成功
        """
        for attempt in range(retry_count + 1):
            try:
                if self.ensure_connection():
                    self.device.click(x, y)
                    return True
            except Exception as e:
                self.logger.warning(f"點擊失敗 (嘗試 {attempt + 1}/{retry_count + 1}): {e}")
                if attempt < retry_count:
                    time.sleep(0.5)
        
        return False
    
    def swipe_safe(self, x1: float, y1: float, x2: float, y2: float, 
                   duration: float = 0.1, retry_count: int = 2) -> bool:
        """
        安全滑動（帶重試）
        
        Returns:
            是否滑動成功
        """
        for attempt in range(retry_count + 1):
            try:
                if self.ensure_connection():
                    self.device.swipe(x1, y1, x2, y2, duration)
                    return True
            except Exception as e:
                self.logger.warning(f"滑動失敗 (嘗試 {attempt + 1}/{retry_count + 1}): {e}")
                if attempt < retry_count:
                    time.sleep(0.5)
        
        return False
    
    def cleanup(self):
        """清理資源"""
        try:
            self.stop_game()
            self._reset_device_resolution()
            self.screen_off()
            self.logger.info("設備清理完成")
        except Exception as e:
            self.logger.error(f"設備清理失敗: {e}")
    
    def get_device_info(self) -> Dict[str, Any]:
        """獲取設備資訊"""
        try:
            if self.ensure_connection():
                info = self.device.device_info
                return {
                    'device_id': self.device_id,
                    'connected': True,
                    'game_running': self.is_game_running(),
                    'device_info': info
                }
        except Exception as e:
            self.logger.error(f"獲取設備資訊失敗: {e}")
        
        return {
            'device_id': self.device_id,
            'connected': False,
            'game_running': False,
            'device_info': None
        }
