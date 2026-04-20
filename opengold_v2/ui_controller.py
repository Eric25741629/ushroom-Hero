"""
UI 控制器 - 處理遊戲介面操作

包含點擊、像素比對、頁面判斷等功能
"""

import time
import numpy as np
from typing import Optional, Tuple
from .config import OpenGoldConfig


class UIController:
    """遊戲 UI 控制器"""
    
    def __init__(self, device, config: Optional[OpenGoldConfig] = None):
        self.device = device
        self.config = config or OpenGoldConfig()
    
    def click_and_wait(self, x: int, y: int, wait_time: float = 1.0):
        """點擊並等待"""
        self.device.click(x, y)
        time.sleep(wait_time)
    
    def capture_screenshot(self) -> np.ndarray:
        """截取畫面"""
        if hasattr(self.device, 'screenshot'):
            return self.device.screenshot(format='opencv')
        elif hasattr(self.device, 'capture_screenshot'):
            return self.device.capture_screenshot()
        else:
            raise AttributeError("裝置不支援 screenshot 或 capture_screenshot")
    
    def _pixel_sum_close(
        self, 
        img: np.ndarray, 
        x: int, 
        y: int, 
        expected_bgr: Tuple[int, int, int]
    ) -> bool:
        """檢查像素值是否接近預期值"""
        actual = img[y, x]
        return abs(int(np.sum(actual)) - int(np.sum(expected_bgr))) <= self.config.lamp_pixel_sum_tolerance
    
    def _match_pixel_profile(self, img: np.ndarray, pixel_profile) -> bool:
        """比對像素配置檔"""
        return all(
            self._pixel_sum_close(img, x, y, expected_bgr)
            for (x, y), expected_bgr in pixel_profile
        )
    
    def is_lamp_sell_page(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否為全部出售頁面"""
        if img is None:
            img = self.capture_screenshot()
        
        return any(
            self._match_pixel_profile(img, profile)
            for profile in self.config.lamp_sell_page_profiles
        )
    
    def is_lamp_ready_page(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否為神燈就緒頁面"""
        if img is None:
            img = self.capture_screenshot()
        
        return any(
            self._match_pixel_profile(img, profile)
            for profile in self.config.lamp_ready_page_profiles
        )
    
    def has_confirm_dialog(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷是否有確認對話框"""
        if img is None:
            img = self.capture_screenshot()
        
        # 檢查確認按鈕的像素特徵
        pixels = self.config.confirm_button_pixels
        return all(
            self._pixel_sum_close(img, x, y, color)
            for (x, y), color in pixels
        )
    
    def click_confirm_if_needed(self) -> bool:
        """若有確認對話框，點擊確認"""
        if self.has_confirm_dialog():
            self.click_and_wait(204, 552, 1)
            return True
        return False
    
    def navigate_to_lamp(self):
        """導航到神燈頁面"""
        # 初始點擊進入神燈介面
        self.click_and_wait(447, 801, 2)
        self.click_and_wait(281, 636, 1)
    
    def exit_lamp(self):
        """退出神燈頁面"""
        self.click_and_wait(447, 801, 2)
        self.click_and_wait(273, 560, 2)
    
    def click_all_sell(self) -> bool:
        """點擊全部出售"""
        # 這裡需要根據實際情況實現
        # 可以整合 img_tools.click_str_by_server
        try:
            from img_tools import click_str_by_server
            return click_str_by_server(self.device, "全部出售")
        except Exception as e:
            print(f"[UIController] 點擊全部出售失敗: {e}")
            return False
    
    def get_gold_num(self) -> Optional[int]:
        """取得神燈數量"""
        try:
            from img_tools import get_all_text
            
            y1, y2, x1, x2 = self.config.gold_num_roi
            img = self.capture_screenshot()
            roi = img[y1:y2, x1:x2]
            
            ocr_results = get_all_text(roi)
            if ocr_results:
                # 嘗試解析第一個結果為數字
                text = str(ocr_results[0]).strip()
                # 去除非數字字元
                num_str = ''.join(c for c in text if c.isdigit())
                if num_str:
                    return int(num_str)
            return None
        except Exception as e:
            print(f"[UIController] 取得神燈數量失敗: {e}")
            return None
    
    def click_lamp_button(self):
        """點擊開神燈按鈕"""
        self.click_and_wait(271, 576, 5)
    
    def click_sell_button(self):
        """點擊出售按鈕（不需要的組合）"""
        self.click_and_wait(227, 798, 1)
        self.click_confirm_if_needed()
    
    def click_keep_button(self):
        """點擊保留/換裝按鈕"""
        self.click_and_wait(376, 798, 0.3)
        self.click_and_wait(227, 798, 1)
        self.click_confirm_if_needed()
    
    def open_stage_menu(self):
        """開啟階段選單"""
        self.click_and_wait(518, 16, 1)
        self.click_and_wait(419, 720, 3)
        self.click_and_wait(272, 796, 1)
        self.click_and_wait(281, 350, 2)
    
    def select_stage(self, index: int):
        """選擇階段"""
        if index == 0:
            self.click_and_wait(281, 350, 1)
        else:
            click_y = 412 + (index - 1) * 49
            self.click_and_wait(266, click_y, 1)
    
    def open_upgrade_panel(self):
        """開啟升級面板"""
        self.click_and_wait(378, 721, 1)  # 切換按鈕
        self.click_and_wait(268, 869, 1)  # 關閉方案選單
        self.click_and_wait(282, 584, 1)  # 點開開到裝備
    
    def close_upgrade_panel(self):
        """關閉升級面板"""
        self.click_and_wait(347, 721, 1)
        self.click_and_wait(268, 869, 1)
        self.click_and_wait(441, 805, 1)
        self.click_and_wait(271, 634, 1)
        time.sleep(3)
    
    def get_skill_roi(self) -> np.ndarray:
        """取得技能資訊區域的圖片"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.skill_roi
        return img[y1:y2, x1:x2]
    
    def get_stage_roi(self) -> np.ndarray:
        """取得階段列表區域的圖片"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.stage_roi
        return img[y1:y2, x1:x2]
    
    def get_stage_roi_recheck(self) -> np.ndarray:
        """取得重新識別階段區域的圖片（較小範圍）"""
        img = self.capture_screenshot()
        y1, y2, x1, x2 = self.config.stage_roi_recheck
        return img[y1:y2, x1:x2]
    
    def get_rolled_rois(self) -> list:
        """取得開出詞條的 ROI 列表"""
        img = self.capture_screenshot()
        rois = []
        for y1, y2, x1, x2 in self.config.rolled_roi_coords:
            rois.append(img[y1:y2, x1:x2])
        return rois
    
    def get_original_rois(self, device_ip: str = None) -> list:
        """取得原有詞條的 ROI 列表"""
        img = self.capture_screenshot()
        coords = self.config.get_roi_for_device(device_ip)
        rois = []
        for y1, y2, x1, x2 in coords:
            rois.append(img[y1:y2, x1:x2])
        return rois
