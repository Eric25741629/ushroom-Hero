"""
遊戲狀態檢測模組 - 負責檢測當前遊戲頁面和狀態
每次檢測都會進行截圖以確保狀態監控的準確性
"""
import cv2
import time
import numpy as np
import logging
import uiautomator2 as u2
import easyocr
from typing import List, Tuple, Optional
from .screenshot_monitor import ScreenshotMonitor

logger = logging.getLogger(__name__)

class GameStateDetector:
    """遊戲狀態檢測器"""
    
    def __init__(self, device: u2.Device, ocr_reader: easyocr.Reader, 
                 cnn_model=None, screenshot_monitor: ScreenshotMonitor = None):
        self.device = device
        self.ocr_reader = ocr_reader
        self.cnn_model = cnn_model
        self.screenshot_monitor = screenshot_monitor or ScreenshotMonitor(device)
        
        # 定義各階段的關鍵字映射
        self.stage_keywords = {
            "公告": "公告",
            "方案": "主頁面",
            "放置獎勵": "放置獎勵",
            "離線獎勵": "放置獎勵",
            "家族商店": "家族",
            "家族亂鬥": "家族",
            "鬱鬱胖頭魚": "家族",
            "征戰熔岩巨獸": "征戰熔岩巨獸",
        }
    
    def get_current_stage(self) -> str:
        """
        獲取當前遊戲階段 - 會進行截圖以確保狀態監控
        
        Returns:
            當前階段名稱
        """
        # 必要的截圖 - 用於狀態監控
        img = self.screenshot_monitor.take_screenshot(
            description="Current stage detection"
        )
        
        if img is None:
            logger.error("Failed to take screenshot for stage detection")
            return "未知"
        
        # 使用新方法進行快速檢測
        if self._is_main_page_by_pixel_check(img):
            logger.info("Main page detected by pixel check")
            # 驗證CNN模型結果
            if self.cnn_model:
                try:
                    cnn_result = self.cnn_model.predict_image(
                        self.cnn_model, self.device.screenshot(format='pillow')
                    )
                    if cnn_result != "main":
                        # 保存不一致的截圖用於調試
                        self.screenshot_monitor.save_debug_image("inconsistent_main", img)
                        logger.warning(f"CNN result ({cnn_result}) doesn't match pixel check")
                except Exception as e:
                    logger.error(f"CNN model prediction failed: {e}")
            
            return "主頁面"
        
        # 使用OCR進行詳細檢測
        return self._detect_stage_by_ocr(img)
    
    def _is_main_page_by_pixel_check(self, img: np.ndarray) -> bool:
        """
        通過像素檢測判斷是否在主頁面
        
        Args:
            img: 截圖圖片
        
        Returns:
            True if on main page
        """
        # 定義主頁面的特徵像素點和預期顏色
        pixel_checks = [
            (535, 955, [47, 138, 123], 10),    # 底部UI元素
            (39, 902, [146, 232, 232], 10),    # 左上角元素
            (6, 956, [50, 140, 117], 10),      # 左下角元素
            (135, 921, [41, 21, 218], 10),     # 特殊UI元素
            (223, 908, [160, 165, 164], 10),   # 中間UI元素
            (27, 731, [139, 170, 201], 10),    # 頂部元素
            (30, 759, [111, 143, 179], 10),    # 
            (37, 794, [38, 60, 88], 10),       #
            (380, 825, [37, 58, 86], 10),      # 右側元素
        ]
        
        try:
            for x, y, target_color, tolerance in pixel_checks:
                if not self.screenshot_monitor.check_pixel_color(img, x, y, target_color, tolerance):
                    return False
            return True
        except Exception as e:
            logger.error(f"Pixel check failed: {e}")
            return False
    
    def _detect_stage_by_ocr(self, img: np.ndarray) -> str:
        """
        通過OCR檢測遊戲階段
        
        Args:
            img: 截圖圖片
        
        Returns:
            檢測到的階段名稱
        """
        try:
            # 進行OCR識別
            result = self.ocr_reader.readtext(img, detail=0)
            full_text = "".join(result)
            
            # 保存OCR截圖用於調試
            self.screenshot_monitor.save_ocr_screenshot(img, full_text)
            
            # 檢測各個階段
            for keyword, stage_name in self.stage_keywords.items():
                # 特殊條件：征戰熔岩巨獸
                if stage_name == "征戰熔岩巨獸":
                    if "征戰熔岩巨獸" in full_text and "掃蕩" in full_text:
                        self.screenshot_monitor.save_debug_image(stage_name, img)
                        return stage_name
                # 一般條件
                elif keyword in full_text:
                    self.screenshot_monitor.save_debug_image(stage_name, img)
                    return stage_name
            
            logger.info(f"Unknown stage detected, OCR result: {result}")
            return "未知"
            
        except Exception as e:
            logger.error(f"OCR detection failed: {e}")
            return "未知"
    
    def check_in_game(self) -> bool:
        """
        檢查是否在遊戲中 - 會進行截圖監控
        
        Returns:
            True if in game
        """
        try:
            # 截圖記錄當前狀態
            self.screenshot_monitor.take_screenshot(
                description="Check in game status"
            )
            
            current_app = self.device.app_current().get("package")
            in_game = current_app == "com.mxdzz.tw.and"
            
            logger.info(f"Current app: {current_app}, In game: {in_game}")
            return in_game
            
        except Exception as e:
            logger.error(f"Failed to check if in game: {e}")
            return False
    
    def check_for_login_conflict(self) -> bool:
        """
        檢查是否有登入衝突 - 會進行截圖記錄
        
        Returns:
            True if login conflict detected
        """
        # 必要的截圖 - 用於記錄登入問題
        img = self.screenshot_monitor.take_screenshot(
            description="Login conflict check"
        )
        
        if img is None:
            return False
        
        try:
            result = self.ocr_reader.readtext(img, detail=0)
            conflict_detected = any(
                text in result for text in ["你的帳號在另一個地方登錄", "退出遊戲"]
            )
            
            if conflict_detected:
                # 保存登入衝突截圖
                self.screenshot_monitor.save_login_issue_screenshot(img)
                logger.warning("Login conflict detected!")
            
            return conflict_detected
            
        except Exception as e:
            logger.error(f"Failed to check login conflict: {e}")
            return False
    
    def check_for_announcement(self) -> bool:
        """
        檢查是否有公告 - 會進行截圖記錄
        
        Returns:
            True if announcement present
        """
        img = self.screenshot_monitor.take_screenshot(
            description="Announcement check"
        )
        
        if img is None:
            return False
        
        try:
            result = self.ocr_reader.readtext(img, detail=0)
            has_announcement = "公告" in result
            
            if has_announcement:
                logger.info("Announcement detected")
                self.screenshot_monitor.save_debug_image("announcement", img)
            
            return has_announcement
            
        except Exception as e:
            logger.error(f"Failed to check announcement: {e}")
            return False
    
    def check_for_rewards(self) -> bool:
        """
        檢查是否有獎勵可領取 - 會進行截圖記錄
        
        Returns:
            True if rewards available
        """
        img = self.screenshot_monitor.take_screenshot(
            description="Rewards check"
        )
        
        if img is None:
            return False
        
        try:
            # 首先嘗試使用CNN模型快速檢測
            if self.cnn_model:
                try:
                    cnn_result = self.cnn_model.predict_image(
                        self.cnn_model, self.device.screenshot(format='pillow')
                    )
                    if cnn_result == "reward":
                        logger.info("Rewards detected by CNN")
                        self.screenshot_monitor.save_debug_image("rewards_cnn", img)
                        return True
                except Exception as e:
                    logger.error(f"CNN reward detection failed: {e}")
            
            # 使用OCR檢測
            result = self.ocr_reader.readtext(img, detail=0)
            has_rewards = any(
                keyword in result for keyword in ["放置獎勵", "離線獎勵", "領取"]
            )
            
            if has_rewards:
                logger.info("Rewards detected by OCR")
                self.screenshot_monitor.save_debug_image("rewards_ocr", img)
            
            return has_rewards
            
        except Exception as e:
            logger.error(f"Failed to check rewards: {e}")
            return False
    
    def wait_for_stage(self, target_stages: List[str], timeout: int = 60) -> str:
        """
        等待特定階段出現 - 持續截圖監控
        
        Args:
            target_stages: 目標階段列表
            timeout: 超時時間（秒）
        
        Returns:
            檢測到的階段名稱，如果超時則返回"timeout"
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_stage = self.get_current_stage()
            
            if current_stage in target_stages:
                logger.info(f"Target stage '{current_stage}' detected")
                return current_stage
            
            logger.debug(f"Waiting for stage, current: {current_stage}")
            time.sleep(1)
        
        logger.warning(f"Timeout waiting for stages: {target_stages}")
        return "timeout"
    
    def get_screen_state(self) -> dict:
        """
        獲取完整的螢幕狀態資訊 - 包含截圖記錄
        
        Returns:
            螢幕狀態字典
        """
        # 必要的截圖 - 用於狀態記錄
        img = self.screenshot_monitor.take_screenshot(
            description="Complete screen state check"
        )
        
        state = {
            "screen_on": self.screenshot_monitor.monitor_screen_state(),
            "in_game": self.check_in_game(),
            "current_stage": self.get_current_stage(),
            "has_rewards": self.check_for_rewards(),
            "has_announcement": self.check_for_announcement(),
            "login_conflict": self.check_for_login_conflict(),
            "screenshot_path": self.screenshot_monitor.save_stage_screenshot("screen_state", img) if img is not None else None,
            "timestamp": time.time()
        }
        
        logger.info(f"Screen state: {state}")
        return state
