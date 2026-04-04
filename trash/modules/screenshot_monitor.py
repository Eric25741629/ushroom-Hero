"""
截圖與監控模組 - 負責遊戲狀態監控和截圖管理
確保每個截圖都被保存，用於監控遊戲狀態的穩定性
"""
import cv2
import time
import os
import numpy as np
import logging
import uiautomator2 as u2
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class ScreenshotMonitor:
    """螢幕截圖與監控管理器"""
    
    def __init__(self, device: u2.Device, base_path: str = "."):
        self.device = device
        self.base_path = base_path
        self._ensure_directories()
    
    def _ensure_directories(self):
        """確保所有必要的目錄都存在"""
        directories = [
            "debug_screenshots",
            "homeplace", 
            "farm",
            "find_img",
            "found_matches",
            "other_str",
            "other_stage",
            "reward_get",
            "other_login"
        ]
        
        for directory in directories:
            path = os.path.join(self.base_path, directory)
            if not os.path.exists(path):
                os.makedirs(path)
                logger.info(f"Created directory: {path}")
    
    def take_screenshot(self, save_path: str = None, description: str = "") -> np.ndarray:
        """
        拍攝截圖並保存 - 每個截圖都是必要的，不得省略
        
        Args:
            save_path: 保存路徑，如果為None則自動生成
            description: 截圖描述
        
        Returns:
            截圖的numpy陣列
        """
        try:
            img = self.device.screenshot(format='opencv')
            
            if save_path is None:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                save_path = f"debug_screenshots/screenshot_{timestamp}.jpg"
            
            # 確保目錄存在
            directory = os.path.dirname(save_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # 保存截圖 - 這是必要的，用於監控遊戲狀態
            cv2.imwrite(save_path, img)
            logger.info(f"Screenshot saved: {save_path} - {description}")
            
            return img
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None
    
    def save_debug_image(self, stage_name: str, img: np.ndarray) -> str:
        """
        保存調試圖片 - 用於分析遊戲狀態
        
        Args:
            stage_name: 階段名稱
            img: 圖片陣列
        
        Returns:
            保存的檔案路徑
        """
        dir_path = f"debug_{stage_name}"
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
        
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{dir_path}/{stage_name}_{timestamp}.jpg"
        cv2.imwrite(filename, img)
        logger.info(f"Saved debug image for stage '{stage_name}' to {filename}")
        return filename
    
    def save_matched_region(self, template_name: str, matched_region: np.ndarray) -> str:
        """
        保存匹配區域圖片
        
        Args:
            template_name: 模板名稱
            matched_region: 匹配的區域圖片
        
        Returns:
            保存的檔案路徑
        """
        if not os.path.exists("found_matches"):
            os.makedirs("found_matches")
        
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        save_path = os.path.join("found_matches", f"matched_{template_name}_{timestamp}.jpg")
        cv2.imwrite(save_path, matched_region)
        logger.info(f"Matched region saved to {save_path}")
        return save_path
    
    def save_reward_screenshot(self, img: np.ndarray) -> str:
        """
        保存獎勵相關截圖
        
        Args:
            img: 圖片陣列
        
        Returns:
            保存的檔案路徑
        """
        if not os.path.exists("reward_get"):
            os.makedirs("reward_get")
        
        save_path = f"reward_get/reward_get_{time.time()}.jpg"
        cv2.imwrite(save_path, img)
        logger.info(f"Reward screenshot saved: {save_path}")
        return save_path
    
    def save_login_issue_screenshot(self, img: np.ndarray) -> str:
        """
        保存登入問題相關截圖
        
        Args:
            img: 圖片陣列
        
        Returns:
            保存的檔案路徑
        """
        if not os.path.exists("other_login"):
            os.makedirs("other_login")
        
        save_path = f"other_login/other_login_{time.time()}.jpg"
        cv2.imwrite(save_path, img)
        logger.info(f"Login issue screenshot saved: {save_path}")
        return save_path
    
    def save_stage_screenshot(self, stage_name: str, img: np.ndarray) -> str:
        """
        保存特定階段的截圖
        
        Args:
            stage_name: 階段名稱
            img: 圖片陣列
        
        Returns:
            保存的檔案路徑
        """
        directory = stage_name.lower().replace(" ", "_")
        if not os.path.exists(directory):
            os.makedirs(directory)
        
        save_path = f"{directory}/{stage_name}_{time.time()}.jpg"
        cv2.imwrite(save_path, img)
        logger.info(f"Stage screenshot saved: {save_path}")
        return save_path
    
    def save_ocr_screenshot(self, img: np.ndarray, ocr_text: str = "") -> str:
        """
        保存OCR相關截圖
        
        Args:
            img: 圖片陣列
            ocr_text: 識別到的文字
        
        Returns:
            保存的檔案路徑
        """
        if not os.path.exists("other_str"):
            os.makedirs("other_str")
        
        save_path = f"other_str/other_str_{time.time()}.jpg"
        cv2.imwrite(save_path, img)
        logger.info(f"OCR screenshot saved: {save_path} - Text: {ocr_text}")
        return save_path
    
    def monitor_screen_state(self) -> bool:
        """
        監控螢幕狀態 - 確保遊戲穩定運行
        
        Returns:
            True if screen is on, False otherwise
        """
        try:
            screen_on = self.device.info.get('screenOn')
            logger.info(f"Screen state: {'ON' if screen_on else 'OFF'}")
            return screen_on
        except Exception as e:
            logger.error(f"Failed to check screen state: {e}")
            return False
    
    def check_pixel_color(self, img: np.ndarray, x: int, y: int, 
                         target_color: list, tolerance: int = 10) -> bool:
        """
        檢查特定像素的顏色是否符合預期
        
        Args:
            img: 圖片陣列
            x, y: 像素座標
            target_color: 目標顏色 [R, G, B]
            tolerance: 容差值
        
        Returns:
            True if color matches within tolerance
        """
        try:
            pixel_color = img[y, x]
            pixel_sum = sum(int(x) for x in pixel_color)
            target_sum = sum(target_color)
            
            matches = abs(pixel_sum - target_sum) <= tolerance
            logger.debug(f"Pixel check at ({x},{y}): {pixel_color} vs {target_color}, matches: {matches}")
            return matches
        except Exception as e:
            logger.error(f"Failed to check pixel color at ({x},{y}): {e}")
            return False
