"""
截圖記錄器 - 在開神燈過程中記錄截圖以供後續分析

上限 100 張，超過時自動刪除最舊的
"""

import os
import time
import cv2
import numpy as np
from typing import Optional
from .config import OpenGoldConfig


class ScreenshotLogger:
    """截圖記錄器"""
    
    def __init__(self, config: Optional[OpenGoldConfig] = None):
        self.config = config or OpenGoldConfig()
        self.folder = self.config.screenshot_folder
        self.max_files = self.config.screenshot_max_files
        
        # 確保資料夾存在
        os.makedirs(self.folder, exist_ok=True)
    
    def _cleanup_old_files(self):
        """清理舊檔案，確保數量不超過上限"""
        try:
            files = [
                os.path.join(self.folder, f) 
                for f in os.listdir(self.folder) 
                if os.path.isfile(os.path.join(self.folder, f))
                and f.endswith(('.jpg', '.jpeg', '.png'))
            ]
            
            if len(files) >= self.max_files:
                # 依修改時間排序
                files.sort(key=lambda x: os.path.getmtime(x))
                # 刪除最舊的，直到數量低於上限
                to_delete = len(files) - self.max_files + 1
                for i in range(to_delete):
                    try:
                        os.remove(files[i])
                        print(f"[ScreenshotLogger] 刪除舊截圖: {files[i]}")
                    except Exception as e:
                        print(f"[ScreenshotLogger] 刪除舊截圖失敗: {e}")
        except Exception as e:
            print(f"[ScreenshotLogger] 清理舊檔案失敗: {e}")
    
    def save_screenshot(
        self, 
        img: np.ndarray, 
        prefix: str = "lamp",
        suffix: str = ""
    ) -> Optional[str]:
        """儲存截圖
        
        參數:
        - img: OpenCV 圖片 (numpy array)
        - prefix: 檔名前綴
        - suffix: 檔名後綴
        
        回傳:
        - 儲存的檔案路徑，失敗時回傳 None
        """
        try:
            if img is None:
                print("[ScreenshotLogger] 警告：無法儲存 None 圖片")
                return None
            
            # 清理舊檔案
            self._cleanup_old_files()
            
            # 產生檔名
            timestamp = int(time.time() * 1000)
            filename = f"{prefix}_{timestamp}"
            if suffix:
                filename += f"_{suffix}"
            filename += ".jpg"
            
            filepath = os.path.join(self.folder, filename)
            
            # 儲存圖片
            cv2.imwrite(filepath, img)
            print(f"[ScreenshotLogger] 已儲存截圖: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"[ScreenshotLogger] 儲存截圖失敗: {e}")
            return None
    
    def save_screenshot_from_device(
        self, 
        device,
        prefix: str = "lamp",
        suffix: str = ""
    ) -> Optional[str]:
        """從裝置取得截圖並儲存
        
        參數:
        - device: 支援 screenshot(format='opencv') 或 capture_screenshot() 的裝置物件
        - prefix: 檔名前綴
        - suffix: 檔名後綴
        """
        try:
            # 取得圖片
            if hasattr(device, 'screenshot'):
                img = device.screenshot(format='opencv')
            elif hasattr(device, 'capture_screenshot'):
                img = device.capture_screenshot()
            else:
                print("[ScreenshotLogger] 裝置不支援 screenshot 或 capture_screenshot")
                return None
            
            return self.save_screenshot(img, prefix, suffix)
            
        except Exception as e:
            print(f"[ScreenshotLogger] 從裝置取得截圖失敗: {e}")
            return None
    
    def get_screenshot_count(self) -> int:
        """取得目前截圖數量"""
        try:
            files = [
                f for f in os.listdir(self.folder)
                if os.path.isfile(os.path.join(self.folder, f))
                and f.endswith(('.jpg', '.jpeg', '.png'))
            ]
            return len(files)
        except Exception:
            return 0
    
    def clear_all(self):
        """清除所有截圖"""
        try:
            files = [
                os.path.join(self.folder, f)
                for f in os.listdir(self.folder)
                if os.path.isfile(os.path.join(self.folder, f))
                and f.endswith(('.jpg', '.jpeg', '.png'))
            ]
            for f in files:
                os.remove(f)
            print(f"[ScreenshotLogger] 已清除 {len(files)} 張截圖")
        except Exception as e:
            print(f"[ScreenshotLogger] 清除截圖失敗: {e}")
