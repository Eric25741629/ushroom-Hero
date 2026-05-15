"""
UI 控制器 - 處理遊戲介面操作

包含點擊、像素比對、頁面判斷等功能
"""

import inspect
import time
import numpy as np
from typing import Optional, Tuple
from .config import OpenGoldConfig


class UIController:
    """遊戲 UI 控制器"""

    def __init__(self, device, config: Optional[OpenGoldConfig] = None):
        self.device = device
        self.config = config or OpenGoldConfig()

    def _log_click(self, x: int, y: int, wait_time: float, reason: str) -> None:
        """印出每次 click 的座標／原因，方便人工核對是否該點。"""
        print(f"[CLICK] ({x:3d},{y:3d}) wait={wait_time}s  reason={reason}", flush=True)

    def click_and_wait(self, x: int, y: int, wait_time: float = 1.0):
        """點擊並等待。Reason 自動由呼叫者函式名取得，方便追蹤。"""
        try:
            reason = inspect.currentframe().f_back.f_code.co_name
        except Exception:
            reason = "?"
        self._log_click(x, y, wait_time, reason)
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
    
    def is_comparison_panel_visible(self, img: Optional[np.ndarray] = None) -> bool:
        """判斷比較面板是否已顯示（開燈成功）"""
        if img is None:
            img = self.capture_screenshot()
        pixels = self.config.comparison_panel_pixels
        return all(
            self._pixel_sum_close(img, x, y, color)
            for (x, y), color in pixels
        )

    def _got_item_blue_pct(self, img: np.ndarray) -> float:
        """中下 ROI 內落在「淺藍色」HSV 範圍的像素比例。"""
        import cv2
        y1, y2, x1, x2 = self.config.got_item_popup_roi
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lo = np.array(self.config.got_item_popup_hsv_lo, dtype=np.uint8)
        hi = np.array(self.config.got_item_popup_hsv_hi, dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        return float(mask.sum()) / 255.0 / mask.size

    def is_got_item_popup(self, img: Optional[np.ndarray] = None) -> bool:
        """中下部分背景為淺藍色 → 真的開到裝備了，這時才該點中間。

        校準自 flow-2026-05-03.json：等待動畫的淺藍像素比例 ≤ 0.048，
        開到裝備時 ≈ 0.170；用 0.10 為門檻可清楚分離（≥ 2x 安全邊際）。
        """
        if img is None:
            img = self.capture_screenshot()
        return self._got_item_blue_pct(img) >= self.config.got_item_popup_min_pct

    def _has_text(self, target: str, y_range: Optional[Tuple[int, int]] = None) -> bool:
        """單張截圖 OCR 探測：`target` 字串是否出現在指定 y_range 區段。

        失敗時回 False，不寫 log。供 click_*_confirm 等動作的前置檢查使用，
        避免 click_str_by_server 在彈窗根本不在時還等 2s 後才印 WARNING。
        """
        try:
            img = self.capture_screenshot()
            if y_range:
                img = img[y_range[0]:y_range[1], :]
            from img_tools import analyze_skill_via_http
            import opencc

            result = analyze_skill_via_http(img)
            if not result or not result.get("success"):
                return False
            conv = opencc.OpenCC("s2t")
            target_t = conv.convert(target)
            for item in result.get("ocr_results") or []:
                text = item.get("text", "") if isinstance(item, dict) else ""
                if target_t in conv.convert(text):
                    return True
            return False
        except Exception:
            return False

    def click_auto_mode_button(self) -> bool:
        """點擊「自動」按鈕（OCR 字串定位）。前置 probe 不在則靜默跳過。"""
        if not self._has_text("自動", y_range=(780, 870)):
            return False
        try:
            from img_tools import click_str_by_server
            clicked = click_str_by_server(
                self.device,
                "自動",
                y_range=(780, 870),
                wait_timeout=0,
            )
            if clicked:
                print("[CLICK ] click_auto_mode_button hit=True", flush=True)
                time.sleep(2)
                return True
            return False
        except Exception as e:
            print(f"[UIController] click_auto_mode_button 失敗: {e}")
            return False

    def click_start_confirm(self) -> bool:
        """點擊「開始」確認彈窗（OCR 字串定位）。前置 probe 不在則靜默跳過。

        校準自 7fe98fc6：到神燈頁時 auto 常常已經在跑、根本沒有「開始」彈窗，
        此時不應印 [CLICK?] / 也不應呼叫底層 click_str_by_server 等 2s 才 timeout。
        """
        if not self._has_text("開始", y_range=(500, 700)):
            return False
        try:
            from img_tools import click_str_by_server
            clicked = click_str_by_server(
                self.device,
                "開始",
                y_range=(500, 700),
                wait_timeout=0,
            )
            if clicked:
                print("[CLICK ] click_start_confirm hit=True", flush=True)
                time.sleep(2)
                return True
            return False
        except Exception as e:
            print(f"[UIController] click_start_confirm 失敗: {e}")
            return False

    def navigate_to_lamp(self):
        """導航到神燈頁面，並啟用自動模式"""
        # 初始點擊進入神燈介面
        self.click_and_wait(447, 801, 2)
        self.click_and_wait(281, 636, 1)
        # 點擊「自動」按鈕啟用自動開裝模式
        self.click_auto_mode_button()
        # 點擊彈出的「開始」確認視窗
        self.click_start_confirm()
    
    def exit_lamp(self):
        """退出神燈頁面"""
        self.click_and_wait(447, 801, 2)
        self.click_and_wait(273, 560, 2)
    
    def click_all_sell(self) -> bool:
        """點擊全部出售（OCR 字串定位）。"""
        try:
            from img_tools import click_str_by_server
            print("[CLICK?] click_all_sell — 找 '全部出售'", flush=True)
            hit = click_str_by_server(self.device, "全部出售")
            print(f"[CLICK ] click_all_sell hit={hit}", flush=True)
            return hit
        except Exception as e:
            print(f"[UIController] 點擊全部出售失敗: {e}")
            return False
    
    def _read_int_from_roi(self, y1: int, y2: int, x1: int, x2: int) -> Optional[int]:
        """擷取畫面 ROI 並 OCR 解析為 int；失敗回 None。"""
        try:
            from img_tools import get_all_text

            img = self.capture_screenshot()
            roi = img[y1:y2, x1:x2]
            if roi.size == 0:
                return None
            ocr_results = get_all_text(roi) or []
            for text in ocr_results:
                num_str = ''.join(c for c in str(text) if c.isdigit())
                if num_str:
                    return int(num_str)
            return None
        except Exception as e:
            print(f"[UIController] _read_int_from_roi 失敗: {e}")
            return None

    def get_gold_num(self) -> Optional[int]:
        """取得神燈剩餘數量（變數名稱沿用歷史；ROI 為魔法熔爐下方數字）。"""
        return self._read_int_from_roi(*self.config.gold_num_roi)

    def get_remaining_lamp_count(self) -> Optional[int]:
        """讀取神燈剩餘數量；與 get_gold_num 為同一個 ROI，命名僅為語意明確。"""
        return self._read_int_from_roi(*self.config.gold_num_roi)
    
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
