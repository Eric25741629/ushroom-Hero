import random
import datetime
import cv2
import numpy as np
import uiautomator2 as u2
import time
from mask import *
import json
import os

class device:
    def __init__(self, device: u2.Device):
        self.device = device

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        max_attempts = 10  # 避免死循环
        attempts = 0
        while attempts < max_attempts:
            img = self.device.screenshot(format='opencv')
            if img is None:
                raise ValueError("Failed to capture screenshot")

            conditions = [
                abs(np.sum(img[234, 189]) - np.sum([179, 91, 70])) < 10,
                abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10,
                abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10,
                abs(np.sum(img[236, 363]) - np.sum([179, 91, 70])) < 10,
                abs(np.sum(img[249, 132]) - np.sum([162, 75, 57])) < 10,
                abs(np.sum(img[264, 139]) - np.sum([162, 75, 57])) < 10,
                abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10,
                abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10,
                abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10,
            ]

            if all(conditions):
                self.device.click(509, 56)
                time.sleep(1)
                attempts += 1
                continue
            break

        if attempts == max_attempts:
            raise RuntimeError(
                "Maximum attempts reached without capturing valid screenshot")
        return img


class mission(device):
    def __init__(self, device: u2.Device, ip):
        super().__init__(device)
        self.device_ip = ip
        self.data_file=self.device_ip + '.json'
    def load_data(self):
        """
        加载数据文件，如果文件不存在则返回默认数据。
        """
        if not os.path.exists(self.data_file):
            # 新增文件
            with open(self.data_file, 'w') as f:
                json.dump({'mission_timestamp': 0,
                          'mission_num': 0}, f)
            return {'mission_timestamp': 0, 'mission_num': 0}  # 返回默认值

        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON data: {e}")
            # 文件格式错误，返回默认值
            return {'mission_timestamp': 0, 'mission_num': 0}

    def record(self, buy_num=0):
        """
        记录当前时间戳和购买次数到对应的 IP JSON 文件中。
        """
        try:
            now = datetime.datetime.now()
            timestamp = now.timestamp()

            # 讀取現有的資料
            data = self.load_data()
            # 更新數據
            data['mission_timestamp'] = timestamp
            data['mission_num'] = buy_num

            # 寫入 JSON 文件
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=4)

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
    def is_color_match(self,img, coord, color, tolerance=10):
        return abs(np.sum(img[coord]) - np.sum(color)) < tolerance

    def done_mission(self):
        img = self.capture_screenshot()

        # 定義條件
        mission_conditions = [
            ((347, 434), [168, 111, 72]),
            ((353, 372), [173, 113, 67]),
            ((352, 366), [166, 117, 61]),
            ((360, 375), [172, 109, 75]),
            ((359, 428), [176, 114, 73]),
            ((347, 425), [175, 114, 64]),
            ((342, 436), [179, 115, 74]),
            ((356, 437), [172, 114, 65]),
            ((354, 290), [105, 122, 148]),
            ((352, 269), [105, 124, 145]),
            ((362, 416), [213, 243, 254]),
            ((362, 438), [213, 243, 254])
        ]

        additional_conditions = [
            ((267, 77), [195, 225, 236]),
            ((267, 381), [206, 237, 246]),
            ((860, 476), [244, 255, 255])
        ]

        # 檢查條件
        if any(self.is_color_match(img, coord, color) for coord, color in mission_conditions) and all(self.is_color_match(img, coord, color) for coord, color in additional_conditions):
            return False
        return True

    def do_mission1(self):
        """
        執行任務。
        """
        self.device.click(46, 140)
        time.sleep(2)
        start = time.time()
        print(self.done_mission())
        while (self.done_mission() and time.time() - start < 60):
            self.device.click(420,  352)
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
        if np.sum(mask) > 0 and abs(np.sum(mask)-12240) < 100:
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
        img = self.device.screenshot(format='opencv')
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
    d = u2.connect('emulator-5560')
    m = mission(d, 'emulator-5560')
    m.do_allmission()
