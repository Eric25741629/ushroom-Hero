"""
截圖記錄器 - 在開神燈過程中記錄截圖以供後續分析

- 每個裝置獨立子目錄：opengold_v2/screenshots/{device_id}/
- 每個子目錄上限 100 張，超過時自動刪除最舊的
- 支援全畫面截圖與 ROI 小圖保存
"""

import os
import re
import time
import cv2
import numpy as np
from typing import Optional
from .config import OpenGoldConfig


def _safe_device_id(device_ip: Optional[str]) -> str:
    """將 device_ip 轉為合法目錄名稱"""
    if not device_ip:
        return "unknown"
    return re.sub(r'[^\w\-.]', '_', device_ip)


class ScreenshotLogger:
    """截圖記錄器"""

    def __init__(self, config: Optional[OpenGoldConfig] = None, device_ip: Optional[str] = None):
        self.config = config or OpenGoldConfig()
        self.base_folder = self.config.screenshot_folder
        self.max_files = self.config.screenshot_max_files
        self.device_id = _safe_device_id(device_ip)
        self.folder = os.path.join(self.base_folder, self.device_id)
        os.makedirs(self.folder, exist_ok=True)

    def _list_image_files(self) -> list:
        """列出目錄內的所有圖片檔案，回傳完整路徑清單"""
        return [
            os.path.join(self.folder, f)
            for f in os.listdir(self.folder)
            if os.path.isfile(os.path.join(self.folder, f))
            and f.endswith(('.jpg', '.jpeg', '.png'))
        ]

    def _cleanup_old_files(self):
        """清理舊檔案，確保數量不超過上限"""
        try:
            files = self._list_image_files()
            if len(files) >= self.max_files:
                files.sort(key=lambda x: os.path.getmtime(x))
                to_delete = len(files) - self.max_files + 1
                for i in range(to_delete):
                    try:
                        os.remove(files[i])
                    except Exception:
                        pass
        except Exception as e:
            print(f"[ScreenshotLogger] 清理舊檔案失敗: {e}")

    def _make_path(self, prefix: str, suffix: str) -> str:
        timestamp = int(time.time() * 1000)
        name = f"{prefix}_{timestamp}"
        if suffix:
            name += f"_{suffix}"
        name += ".jpg"
        return os.path.join(self.folder, name)

    def save_screenshot(
        self,
        img: np.ndarray,
        prefix: str = "lamp",
        suffix: str = ""
    ) -> Optional[str]:
        """儲存截圖（全畫面或任意 ndarray）"""
        try:
            if img is None:
                return None
            self._cleanup_old_files()
            filepath = self._make_path(prefix, suffix)
            cv2.imwrite(filepath, img)
            print(f"[ScreenshotLogger] 已儲存: {filepath}")
            return filepath
        except Exception as e:
            print(f"[ScreenshotLogger] 儲存截圖失敗: {e}")
            return None

    def save_roi(
        self,
        roi: np.ndarray,
        prefix: str = "roi",
        suffix: str = ""
    ) -> Optional[str]:
        """儲存 ROI 小圖（放大 3x 方便檢視）"""
        try:
            if roi is None or roi.size == 0:
                return None
            self._cleanup_old_files()
            h, w = roi.shape[:2]
            scale = max(1, min(4, 200 // max(h, w, 1)))
            enlarged = cv2.resize(roi, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
            filepath = self._make_path(prefix, suffix)
            cv2.imwrite(filepath, enlarged)
            print(f"[ScreenshotLogger] 已儲存 ROI: {filepath}")
            return filepath
        except Exception as e:
            print(f"[ScreenshotLogger] 儲存 ROI 失敗: {e}")
            return None

    def save_screenshot_from_device(
        self,
        device,
        prefix: str = "lamp",
        suffix: str = ""
    ) -> Optional[str]:
        """從裝置取得截圖並儲存"""
        try:
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
            return len(self._list_image_files())
        except Exception:
            return 0

    def clear_all(self):
        """清除所有截圖"""
        try:
            files = self._list_image_files()
            for f in files:
                os.remove(f)
            print(f"[ScreenshotLogger] 已清除 {len(files)} 張截圖")
        except Exception as e:
            print(f"[ScreenshotLogger] 清除截圖失敗: {e}")
