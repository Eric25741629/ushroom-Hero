import numpy as np
import cv2
import time
import uiautomator2 as u2


def click_white(d: u2.Device):
    d.click(509, 56)
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
            if abs(np.sum(img[234, 189]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[236, 363]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[249, 132]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[264, 139]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10 and abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10 and abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10:
                self.devices.click(509, 56)
                time.sleep(1)
                continue
            if img is not None:
                break
        return img
    def click_white(self):
        self.devices.click(509, 56)
        time.sleep(1)