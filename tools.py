import numpy as np
import cv2
import time
import uiautomator2 as u2
from adb_operations import tap_device
from utils.main_page_guard import is_main_page_with_popup


def click_white(d: u2.Device):
    tap_device(d, 509, 56)
    time.sleep(1)


def non_max_suppression(coords, min_dist=10):
    # 如果座標列表為空，直接返回
    if len(coords) == 0:
        return []

    # 儲存結果的清單
    kept_coords = []

    # 按照座標順序進行處理
    for point in coords:
        if not kept_coords:  # 如果結果清單為空，直接加入
            kept_coords.append(point)
            continue

        # 檢查當前點與清單中所有已保存點的距離
        distances = np.linalg.norm(
            np.array(kept_coords) - np.array(point), axis=1)
        if np.all(distances >= min_dist):  # 如果與所有已保存點的距離都大於等於閾值
            kept_coords.append(point)

    return kept_coords


class android_devices:
    def __init__(self, devices: u2.Device):
        self.devices = devices

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        while True:
            img = self.devices.screenshot(format='opencv')
            if is_main_page_with_popup(img):
                self.devices.click(509, 56)
                time.sleep(1)
                continue
            if img is not None:
                break
        return img
    def click_white(self):
        tap_device(self.devices, 509, 56)
        time.sleep(1)