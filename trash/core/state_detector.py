# -*- coding: utf-8 -*-
"""
狀態檢測器 - 智能檢測遊戲狀態
"""
import logging
import time
import numpy as np
from typing import Optional, Dict, Any, List, Tuple
from core.screenshot_manager import ScreenshotManager
from config.game_config import config

class StateDetector:
    """遊戲狀態檢測器"""
    
    def __init__(self, device, device_id: str, cnn_model, easyocr_reader):
        self.device = device
        self.device_id = device_id
        self.cnn_model = cnn_model
        self.easyocr_reader = easyocr_reader
        self.screenshot_manager = ScreenshotManager(device, device_id)
        self.logger = logging.getLogger(f"StateDetector-{device_id}")
        
        # 狀態緩存
        self._state_cache = {}
        self._cache_timeout = 1  # 狀態緩存超時時間
        
        # 檢測結果統計
        self._detection_stats = {
            'cnn_success': 0,
            'cnn_failed': 0,
            'ocr_success': 0,
            'ocr_failed': 0,
            'pixel_success': 0,
            'pixel_failed': 0
        }
    
    def get_current_stage(self, force_refresh: bool = False) -> str:
        """
        獲取當前遊戲狀態
        
        Args:
            force_refresh: 是否強制刷新，不使用緩存
            
        Returns:
            當前狀態字符串
        """
        current_time = time.time()
        
        # 檢查緩存
        if not force_refresh and 'stage' in self._state_cache:
            cache_time, cached_stage = self._state_cache['stage']
            if current_time - cache_time < self._cache_timeout:
                return cached_stage
        
        # 使用多種方法檢測狀態
        stage = self._detect_stage_multi_method()
        
        # 更新緩存
        self._state_cache['stage'] = (current_time, stage)
        
        return stage
    
    def _detect_stage_multi_method(self) -> str:
        """使用多種方法檢測狀態"""
        
        # 方法1: 快速像素檢測（用於主頁面）
        img = self.screenshot_manager.take_screenshot()
        if img is not None:
            if self._is_main_page_by_pixels(img):
                self._detection_stats['pixel_success'] += 1
                self.logger.debug("像素檢測: 主頁面")
                return "主頁面"
        
        # 方法2: CNN模型檢測
        cnn_result = self._detect_by_cnn()
        if cnn_result and cnn_result != "未知":
            self._detection_stats['cnn_success'] += 1
            
            # CNN結果映射
            stage_mapping = {
                "main": "主頁面",
                "reward": "放置獎勵", 
                "homeplace": "家園",
                "family": "家族"
            }
            
            mapped_stage = stage_mapping.get(cnn_result, cnn_result)
            self.logger.debug(f"CNN檢測: {mapped_stage}")
            
            # 對於主頁面，再次用像素驗證
            if mapped_stage == "主頁面" and img is not None:
                if not self._is_main_page_by_pixels(img):
                    self.logger.warning("CNN檢測主頁面但像素驗證失敗")
                    # 保存不一致的截圖
                    self.screenshot_manager.save_screenshot(img, "other_stage")
            
            return mapped_stage
        else:
            self._detection_stats['cnn_failed'] += 1
        
        # 方法3: OCR文字檢測
        ocr_result = self._detect_by_ocr(img)
        if ocr_result and ocr_result != "未知":
            self._detection_stats['ocr_success'] += 1
            self.logger.debug(f"OCR檢測: {ocr_result}")
            return ocr_result
        else:
            self._detection_stats['ocr_failed'] += 1
        
        # 如果所有方法都失敗，保存截圖並返回未知
        if img is not None:
            self.screenshot_manager.save_screenshot(img, "other_stage")
        
        self.logger.warning("所有檢測方法都失敗")
        return "未知"
    
    def _is_main_page_by_pixels(self, img: np.ndarray) -> bool:
        """使用像素檢測主頁面"""
        try:
            return self.screenshot_manager.compare_pixels(img, config.MAIN_PAGE_PIXELS)
        except Exception as e:
            self.logger.error(f"像素檢測失敗: {e}")
            return False
    
    def _detect_by_cnn(self) -> Optional[str]:
        """使用CNN模型檢測"""
        try:
            pillow_img = self.screenshot_manager.take_screenshot(format='pillow')
            if pillow_img is None:
                return None
                
            result = self.cnn_model.predict_image(self.cnn_model, pillow_img)
            return result
            
        except Exception as e:
            self.logger.error(f"CNN檢測失敗: {e}")
            return None
    
    def _detect_by_ocr(self, img: Optional[np.ndarray] = None) -> str:
        """使用OCR檢測"""
        try:
            if img is None:
                img = self.screenshot_manager.take_screenshot()
                if img is None:
                    return "未知"
            
            # OCR識別
            result = self.easyocr_reader.readtext(img, detail=0)
            
            # 根據文字內容判斷狀態
            return self._classify_stage_by_text(result, img)
            
        except Exception as e:
            self.logger.error(f"OCR檢測失敗: {e}")
            return "未知"
    
    def _classify_stage_by_text(self, ocr_result: List[str], img: np.ndarray) -> str:
        """根據OCR結果分類狀態"""
        text_content = " ".join(ocr_result).lower()
        
        # 狀態檢測規則
        if "公告" in text_content:
            self.screenshot_manager.save_screenshot(img, "announcement")
            return "公告"
        
        if "方案" in text_content:
            self.screenshot_manager.save_screenshot(img, "main")
            return "主頁面"
        
        if any(keyword in text_content for keyword in ["放置獎勵", "離線獎勵"]):
            self.screenshot_manager.save_screenshot(img, "reward")
            return "放置獎勵"
        
        if any(keyword in text_content for keyword in ["家族商店", "家族亂鬥", "鬱鬱胖頭魚"]):
            self.screenshot_manager.save_screenshot(img, "family")
            return "家族"
        
        if "征戰熔岩巨獸" in text_content and "掃蕩" in text_content:
            self.screenshot_manager.save_screenshot(img, "boss")
            return "征戰熔岩巨獸"
        
        if any(keyword in text_content for keyword in ["你的帳號在另一個地方登錄", "退出遊戲"]):
            self.screenshot_manager.save_screenshot(img, "other_login")
            return "其他登錄"
        
        return "未知"
    
    def is_in_game(self) -> bool:
        """檢查是否在遊戲中"""
        try:
            current_app = self.device.app_current()
            return current_app.get("package") == config.PACKAGE_NAME
        except Exception as e:
            self.logger.error(f"檢查遊戲狀態失敗: {e}")
            return False
    
    def wait_for_stage(self, target_stage: str, timeout: int = 60) -> bool:
        """
        等待指定狀態
        
        Args:
            target_stage: 目標狀態
            timeout: 超時時間（秒）
            
        Returns:
            是否成功到達目標狀態
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            current_stage = self.get_current_stage(force_refresh=True)
            
            if current_stage == target_stage:
                self.logger.info(f"成功到達目標狀態: {target_stage}")
                return True
            
            time.sleep(1)
        
        self.logger.warning(f"等待狀態超時: {target_stage}")
        return False
    
    def detect_special_elements(self, img: Optional[np.ndarray] = None) -> Dict[str, bool]:
        """
        檢測特殊元素
        
        Returns:
            檢測結果字典
        """
        if img is None:
            img = self.screenshot_manager.take_screenshot()
            if img is None:
                return {}
        
        results = {}
        
        try:
            # 檢測武魂
            results['martial_soul'] = self._detect_martial_soul(img)
            
            # 檢測武魂2
            results['martial_soul2'] = self._detect_martial_soul2(img)
            
            # 檢測紅包
            # results['red_envelope'] = self._detect_red_envelope(img)
            
        except Exception as e:
            self.logger.error(f"特殊元素檢測失敗: {e}")
        
        return results
    
    def _detect_martial_soul(self, img: np.ndarray) -> bool:
        """檢測武魂"""
        try:
            import mask
            region = (472, 590, 455, 528)  # (y1, y2, x1, x2)
            color_range = (mask.red_mask_lower, mask.red_mask_upper)
            return self.screenshot_manager.detect_color_area(img, region, color_range)
        except Exception as e:
            self.logger.error(f"武魂檢測失敗: {e}")
            return False
    
    def _detect_martial_soul2(self, img: np.ndarray) -> bool:
        """檢測武魂2"""
        try:
            import mask
            region = (760, 791, 483, 525)  # (y1, y2, x1, x2)
            color_range = (mask.red_mask_lower, mask.red_mask_upper)
            return self.screenshot_manager.detect_color_area(img, region, color_range)
        except Exception as e:
            self.logger.error(f"武魂2檢測失敗: {e}")
            return False
    
    def get_detection_stats(self) -> Dict[str, Any]:
        """獲取檢測統計信息"""
        total_cnn = self._detection_stats['cnn_success'] + self._detection_stats['cnn_failed']
        total_ocr = self._detection_stats['ocr_success'] + self._detection_stats['ocr_failed']
        total_pixel = self._detection_stats['pixel_success'] + self._detection_stats['pixel_failed']
        
        stats = {
            'cnn_success_rate': self._detection_stats['cnn_success'] / max(total_cnn, 1),
            'ocr_success_rate': self._detection_stats['ocr_success'] / max(total_ocr, 1),
            'pixel_success_rate': self._detection_stats['pixel_success'] / max(total_pixel, 1),
            'total_detections': total_cnn + total_ocr + total_pixel
        }
        
        return stats
    
    def clear_cache(self):
        """清空狀態緩存"""
        self._state_cache.clear()
        self.screenshot_manager.clear_cache()
        self.logger.debug("狀態緩存已清空")
