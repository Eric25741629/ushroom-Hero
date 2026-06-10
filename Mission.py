import random
import datetime
import cv2
import numpy as np
import uiautomator2 as u2
import time
from mask import *
from img_tools import click_str_by_server
import json
import os
from typing import Optional, Union
from json_manager import JsonDataManager
from utils.main_page_guard import is_main_page_with_popup

class device:
    def __init__(self, device: Optional[u2.Device], test_image: Optional[np.ndarray] = None):
        self.device = device
        self.test_image = test_image

    def set_test_image(self, image: Union[str, np.ndarray]):
        """Set a local image for offline testing."""
        if isinstance(image, str):
            loaded = cv2.imread(image)
            if loaded is None:
                raise ValueError(f"Failed to load test image: {image}")
            self.test_image = loaded
            return

        if isinstance(image, np.ndarray):
            self.test_image = image
            return

        raise TypeError("image must be a file path or numpy.ndarray")

    def _get_screenshot(self):
        """Return injected test image first; fallback to device screenshot."""
        if self.test_image is not None:
            return self.test_image.copy()

        if self.device is None:
            raise ValueError("device is None and no test image provided")

        img = self.device.screenshot(format='opencv')
        if img is None:
            raise ValueError("Failed to capture screenshot")
        return img

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        max_attempts = 10  # 避免死循环
        attempts = 0
        while attempts < max_attempts:
            img = self._get_screenshot()

            if is_main_page_with_popup(img):
                if self.test_image is not None:
                    break
                self.device.click(509, 56)
                time.sleep(1)
                attempts += 1
                continue
            break

        if attempts == max_attempts:
            raise RuntimeError(
                "Maximum attempts reached without capturing valid screenshot")
        if not os.path.exists("mission"):
            os.mkdir("mission")
        # 儲存截圖
        # cv2.imwrite(f"mission/mission_{time.time()}.png", img)
        return img


class mission(device):
    def __init__(self, device: Optional[u2.Device], ip, test_image: Optional[Union[str, np.ndarray]] = None):
        super().__init__(device)
        if test_image is not None:
            self.set_test_image(test_image)
        self.device_ip = ip
        self.data_file = self.device_ip + '.json'
        # 檔案 IO 委派給 JsonDataManager 取得原子寫 + 損毀備份；
        # 但保留 flat on-disk schema（mission_timestamp / mission_num 不可巢狀化）。
        self._json_mgr = JsonDataManager(self.device_ip)

    def load_data(self):
        """
        加载数据文件，如果文件不存在则返回默认数据。

        flat schema 保持不變：{'mission_timestamp': 0, 'mission_num': 0}。
        """
        return self._json_mgr.load_data(
            default_data={'mission_timestamp': 0, 'mission_num': 0}
        )

    def record(self, buy_num=0):
        """
        记录当前时间戳和购买次数到对应的 IP JSON 文件中。
        """
        try:
            now = datetime.datetime.now()
            timestamp = now.timestamp()

            # 讀取現有的資料
            data = self.load_data()
            # 更新數據（保留 flat keys，不改 on-disk schema）
            data['mission_timestamp'] = timestamp
            data['mission_num'] = buy_num

            # 原子寫回（temp + os.replace）
            self._json_mgr.save_data(data)

            print(
                f"mission_timestamp and buy number recorded for {self.device_ip}.")
        except Exception as e:
            # 新增或更新數據時出錯
            print(f"Error recording data: {e}")

    def get_buy_data(self):
        """
        获取购买数据，如果文件不存在则返回默认值。
        """
        try:
            data = self.load_data()
            timestamp = data.get('mission_timestamp', 0)
            buy_num = data.get('mission_num', 0)
            last_date = datetime.datetime.fromtimestamp(timestamp).date()
            current_date = datetime.datetime.now().date()

            # 仅在日期不匹配时将购买次数重置
            if last_date != current_date:
                buy_num = 0

            return timestamp, buy_num
        except (FileNotFoundError, ValueError) as e:
            # 文件不存在或格式错误，返回默认值
            return 0, 0


    def check(self):
        timestamp, buy_num = self.get_buy_data()
        last_date = datetime.datetime.fromtimestamp(timestamp).date()
        current_date = datetime.datetime.now().date()
        print(
            f"last_date: {last_date}, current_date: {current_date}, buy_num: {buy_num}")
        # 如果日期不同或購買次數小於 2，返回 True 表示需要再次購買
        return last_date != current_date or buy_num < 2



    def process_hsv(self, img, lower, upper, roi=None):
        """HSV 過濾處理，支持 ROI（感興趣區域）"""
        if roi:
            img = img[roi[1]:roi[3], roi[0]:roi[2]]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, lower, upper)
        return mask
    def _get_patch_mean_color(self, img, coord, patch=5):
        y, x = coord
        h, w = img.shape[:2]
        half = max(0, patch // 2)
        y0 = max(0, y - half)
        y1 = min(h, y + half + 1)
        x0 = max(0, x - half)
        x1 = min(w, x + half + 1)
        region = img[y0:y1, x0:x1]
        return np.mean(region, axis=(0, 1))

    def is_color_match(self, img, coord, color, tolerance=10, patch=5):
        mean_color = self._get_patch_mean_color(img, coord, patch=patch)
        return abs(np.sum(mean_color) - np.sum(color)) < tolerance

    def done_mission(self):
        img = self.capture_screenshot()

        # 定義條件
        mission_conditions = [
            ((353, 372), [173, 113, 67]),
            ((352, 366), [166, 117, 61]),
            ((360, 375), [172, 109, 75]),
            ((359, 428), [176, 114, 73]),
            ((342, 436), [179, 115, 74]),
            ((356, 437), [172, 114, 65]),
            ((354, 290), [105, 122, 148]),
            ((352, 269), [105, 124, 145]),
            ((362, 438), [213, 243, 254])
        ]

        additional_conditions = [
            ((267, 77), [195, 225, 236]),
            ((267, 381), [206, 237, 246]),
            ((860, 476), [244, 255, 255])
        ]
        print(
            [self.is_color_match(img, coord, color, patch=5) for coord, color in mission_conditions],
            [self.is_color_match(img, coord, color, patch=5) for coord, color in additional_conditions],
        )
        #印出不同位置的顏色值以供調試
        
        if any(self.is_color_match(img, coord, color, patch=5) for coord, color in mission_conditions) and all(self.is_color_match(img, coord, color, patch=5) for coord, color in additional_conditions):
            return False
        return True

    def do_mission1(self):
        """
        執行任務。
        """
        if "fc65396d" in self.device_ip:
            self.device.click(46, 178)
        else:
            self.device.click(46, 140)
        time.sleep(2)
        current_time = datetime.datetime.now()
        if current_time.weekday() == 0:
            self.device.click(420,83)
            time.sleep(2)
            self.device.click(270,300)
            time.sleep(2)
            self.device.click(465,216)
            time.sleep(2)
            self.device.click(18, 337)
            time.sleep(1)
        start = time.time()

        while time.time() - start < 60:
            clicked = click_str_by_server(self.device, "領取", x_range=(250, 540), y_range=(240, 470))
            if not clicked:
                break
            time.sleep(0.3)
            self.device.click(131, 766)
            time.sleep(0.3)
        self.device.click(276, 181)
        time.sleep(1)
        self.device.click(131, 766)
        time.sleep(0.5)
        self.device.click(276, 64)
        time.sleep(2)

    def do_mission2(self):
        """
        執行任務。
        """
        if "fc65396d" in self.device_ip:
            self.device.click(500, 182)
        else:
            self.device.click(500, 142)
        time.sleep(2)
        self.do_mission2_1()

        self.device.click(362, 778)
        time.sleep(2)
        img = self.capture_screenshot()
        img = img[257:311, 357:479]
        # 轉換為 HSV 顏色空間
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 進行 HSV 篩選紅色
        mask = cv2.inRange(hsv, red_mask_lower, red_mask_upper)
        # 使用 Python int 避免 numpy 無號類型在算術運算時溢位
        mask_sum = int(np.sum(mask))
        if mask_sum > 0 and abs(mask_sum - 12240) < 100:
            self.device.click(425, 289)
            time.sleep(2)
        self.device.click(118, 798)
        time.sleep(0.5)
        self.device.click(281, 714)
        time.sleep(2)
        self.device.click(118, 798)
        time.sleep(0.5)
        self.device.click(276, 64)
        time.sleep(1)

    def do_mission2_1(self):
        img = self._get_screenshot()
        conditions =[abs(np.sum(img[231,126]) - np.sum([137, 207, 220])) < 10, abs(np.sum(img[245,258]) - np.sum([76, 99, 191])) < 10, abs(np.sum(img[276,385]) - np.sum([23, 41, 112])) < 10, abs(np.sum(img[316,469]) - np.sum([47, 102, 163])) < 10, abs(np.sum(img[229,422]) - np.sum([95, 122, 203])) < 10, abs(np.sum(img[321,168]) - np.sum([215, 234, 255])) < 10, abs(np.sum(img[285,140]) - np.sum([192, 241, 255])) < 10, abs(np.sum(img[297,269]) - np.sum([108, 152, 241])) < 10, abs(np.sum(img[312,269]) - np.sum([109, 165, 236])) < 10, abs(np.sum(img[346,289]) - np.sum([175, 213, 225])) < 10, abs(np.sum(img[327,304]) - np.sum([111, 176, 221])) < 10, abs(np.sum(img[213,474]) - np.sum([3, 24, 216])) < 10]

        if all(conditions):
            self.device.click(random.randint(78,452), random.randint(218,330))
            time.sleep(2)
            self.device.click(357,900)
            time.sleep(2)
            for i in range(10):
                self.device.click(273,300)
                time.sleep(1)
            self.device.click(500,900)
            time.sleep(2)

    def do_allmission(self):
        """
        執行所有任務。
        """
        if self.check():
            self.do_mission1()
            self.do_mission2()
            timestamp = self.get_buy_data()[0]
            last_date = datetime.datetime.fromtimestamp(timestamp).date()
            current_date = datetime.datetime.now().date()
            if last_date != current_date:
                self.record(1)
            else:
                buy_num = self.get_buy_data()[1]
                self.record(buy_num+1)
if __name__ == '__main__':
    # Offline image test example:
    # m = mission(None, 'offline_test', test_image='mission/test_case.png')
    # print("done_mission:", m.done_mission())
    d = u2.connect('7fe98fc6')
    m = mission(d, '7fe98fc6')
    m.do_allmission()
    # m = mission(None, 'offline_test', test_image=r'C:\\Users\\Eric\\AppData\\Local\\Temp\\lamp.png')
    # print(m.done_mission())
