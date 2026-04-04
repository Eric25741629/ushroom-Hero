# -*- coding: utf-8 -*-
"""
截圖管理器 - 處理所有截圖相關操作
"""
import cv2
import numpy as np
import time
import os
import logging
from typing import Optional, Tuple, Dict, Any
from PIL import Image
import threading
from config.game_config import config

class ScreenshotManager:
    """截圖管理器"""
    
    def __init__(self, device, device_id: str):
        self.device = device
        self.device_id = device_id
        self.logger = logging.getLogger(f"Screenshot-{device_id}")
        self._screenshot_cache = {}
        self._cache_timeout = 2  # 緩存超時時間（秒）
        self._lock = threading.Lock()
        
        # 確保目錄存在
        self._ensure_directories()
    
    def _ensure_directories(self):
        """確保所有截圖目錄存在"""
        for dir_name in config.SCREENSHOT_DIRS:
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
    
    def take_screenshot(self, format='opencv', use_cache=True) -> Optional[np.ndarray]:
        """
        截圖
        
        Args:
            format: 截圖格式 ('opencv', 'pillow')
            use_cache: 是否使用緩存
            
        Returns:
            截圖數據或None
        """
        cache_key = f"{format}_{int(time.time())}"
        
        if use_cache:
            with self._lock:
                # 檢查緩存
                for key, (timestamp, data) in self._screenshot_cache.items():
                    if time.time() - timestamp < self._cache_timeout and key.startswith(format):
                        return data
        
        try:
            # 帶超時的截圖
            screenshot = self._take_screenshot_with_timeout(format)
            
            if screenshot is not None and use_cache:
                with self._lock:
                    # 清理過期緩存
                    self._cleanup_cache()
                    # 添加新緩存
                    self._screenshot_cache[cache_key] = (time.time(), screenshot)
            
            return screenshot
            
        except Exception as e:
            self.logger.error(f"截圖失敗: {e}")
            return None
    
    def _take_screenshot_with_timeout(self, format) -> Optional[np.ndarray]:
        """帶超時的截圖"""
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError("截圖超時")
        
        try:
            # 設置超時
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(config.SCREENSHOT_TIMEOUT)
            
            # 執行截圖
            screenshot = self.device.screenshot(format=format)
            
            # 取消超時
            signal.alarm(0)
            
            return screenshot
            
        except TimeoutError:
            self.logger.warning("截圖超時")
            return None
        except Exception as e:
            signal.alarm(0)  # 確保取消超時
            self.logger.error(f"截圖異常: {e}")
            return None
    
    def _cleanup_cache(self):
        """清理過期緩存"""
        current_time = time.time()
        expired_keys = [
            key for key, (timestamp, _) in self._screenshot_cache.items()
            if current_time - timestamp > self._cache_timeout
        ]
        for key in expired_keys:
            del self._screenshot_cache[key]
    
    def save_screenshot(self, img: np.ndarray, category: str, 
                       filename: Optional[str] = None) -> str:
        """
        保存截圖
        
        Args:
            img: 圖像數據
            category: 分類目錄
            filename: 檔案名（可選）
            
        Returns:
            保存的檔案路徑
        """
        if filename is None:
            filename = f"{category}_{time.time()}.jpg"
        
        file_path = os.path.join(category, filename)
        
        try:
            cv2.imwrite(file_path, img)
            self.logger.debug(f"截圖已保存: {file_path}")
            return file_path
        except Exception as e:
            self.logger.error(f"保存截圖失敗: {e}")
            return ""
    
    def crop_screenshot(self, img: np.ndarray, region: Tuple[int, int, int, int]) -> np.ndarray:
        """
        裁切截圖
        
        Args:
            img: 原始圖像
            region: 裁切區域 (y1, y2, x1, x2)
            
        Returns:
            裁切後的圖像
        """
        try:
            y1, y2, x1, x2 = region
            return img[y1:y2, x1:x2]
        except Exception as e:
            self.logger.error(f"裁切圖像失敗: {e}")
            return img
    
    def compare_pixels(self, img: np.ndarray, pixel_configs: list) -> bool:
        """
        比較像素點
        
        Args:
            img: 圖像數據
            pixel_configs: 像素配置列表 [(x, y, [r, g, b]), ...]
            
        Returns:
            是否所有像素點都匹配
        """
        try:
            for x, y, expected_rgb in pixel_configs:
                if y >= img.shape[0] or x >= img.shape[1]:
                    return False
                    
                actual_rgb = img[y, x]
                expected_sum = sum(expected_rgb)
                actual_sum = sum(actual_rgb)
                
                if abs(actual_sum - expected_sum) > config.PIXEL_THRESHOLD:
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"像素比較失敗: {e}")
            return False
    
    def find_template(self, img: np.ndarray, template_path: str, 
                     threshold: float = 0.8) -> Optional[Tuple[int, int]]:
        """
        模板匹配
        
        Args:
            img: 源圖像
            template_path: 模板圖像路徑
            threshold: 匹配閾值
            
        Returns:
            匹配位置的中心點坐標或None
        """
        try:
            if not os.path.exists(template_path):
                self.logger.warning(f"模板文件不存在: {template_path}")
                return None
            
            template = cv2.imread(template_path)
            if template is None:
                self.logger.warning(f"無法讀取模板: {template_path}")
                return None
            
            res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
            loc = np.where(res >= threshold)
            
            if len(loc[0]) > 0:
                # 返回第一個匹配的中心點
                center_x = int(loc[1][0] + template.shape[1] / 2)
                center_y = int(loc[0][0] + template.shape[0] / 2)
                return (center_x, center_y)
            
            return None
            
        except Exception as e:
            self.logger.error(f"模板匹配失敗: {e}")
            return None
    
    def detect_color_area(self, img: np.ndarray, region: Tuple[int, int, int, int],
                         color_range: Tuple[np.ndarray, np.ndarray]) -> bool:
        """
        檢測指定區域的顏色
        
        Args:
            img: 圖像數據
            region: 檢測區域 (y1, y2, x1, x2)
            color_range: 顏色範圍 (lower, upper)
            
        Returns:
            是否檢測到目標顏色
        """
        try:
            cropped = self.crop_screenshot(img, region)
            hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
            
            lower, upper = color_range
            mask = cv2.inRange(hsv, lower, upper)
            
            # 計算輪廓面積
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area > 50:  # 面積閾值
                    return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"顏色檢測失敗: {e}")
            return False
    
    def clear_cache(self):
        """清空緩存"""
        with self._lock:
            self._screenshot_cache.clear()
            self.logger.debug("截圖緩存已清空")
