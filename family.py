import os
import datetime
import time
import cv2
import numpy as np
import uiautomator2 as u2
import os
import json
import datetime
import time
import cv2
from device import device
from tools import click_white

class Family_manager(device):
    def __init__(self, device: u2.Device, ip: str):
        super().__init__(device)
        self.device_ip = ip
        self.device = device
        self.data_file = self.device_ip + '.json'
        self.imglist = ['godcoin.jpg', 'screw.jpg', 'scroll.jpg', 'Gold_Key.jpg', 'Diamond_Key.jpg',
                   'Flaming_Gold_Key.jpg']
        if self.device_ip == 'emulator-5566':
            self.imglist = ['scroll.jpg',
                   'Flaming_Gold_Key.jpg', 'Gold_Key.jpg',]
        if self.device_ip == 'emulator-5568':
            self.imglist = ['godcoin.jpg',  'scroll.jpg', 'Gold_Key.jpg',
                   'Flaming_Gold_Key.jpg']
    def find_and_click(self, findImgPath, threshold=0.8, x=0, y=0):
        """尋找圖像並點擊"""
        img = self.capture_screenshot()
        findImg = cv2.imread(findImgPath)
        res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        if len(loc[0]) > 0:
            center = [int(loc[1][0] + findImg.shape[1] / 2),
                      int(loc[0][0] + findImg.shape[0] / 2)]
            self.device.click(center[0] + x, center[1] + y)
            return True
        return False

    def record(self, buy_num=0):
        """
        记录当前时间戳和购买次数到对应的 JSON 文件中。
        """
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
            else:
                data = {}  # 如果文件不存在，创建一个空字典
            data["family_market_timestamp"] = datetime.datetime.now().timestamp()
            data["family_market_buy_num"] = buy_num

            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=4)
            print(f"Timestamp and buy number recorded for {self.device_ip}.")
        except Exception as e:
            print(f"Error while recording data for {self.device_ip}: {e}")

    def get_buy_data(self):
        """
        获取文件中的最后一次记录的时间戳和购买次数。
        如果文件不存在或内容无效，返回默认值 (0, 0)。
        """
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    timestamp = data.get("family_market_timestamp", 0)
                    buy_num = data.get("family_market_buy_num", 0)
                    return timestamp, buy_num
            else:
                return 0, 0
        except (FileNotFoundError, json.JSONDecodeError):
            # 文件不存在或格式错误，返回默认值
            return 0, 0

    def check(self):
        """
        检查指定 IP 文件中的时间戳是否与当前日期跨天，以及购买次数是否小于 6。
        """
        try:
            timestamp, buy_num = self.get_buy_data()
            last_date = datetime.datetime.fromtimestamp(timestamp).date()
            current_date = datetime.datetime.now().date()
            # 如果日期不同或购买次数小于 6，需要执行操作
            return last_date != current_date or buy_num < len(self.imglist)
        except Exception as e:
            print(f"Error while checking timestamp for {self.device_ip}: {e}")
            return True

    def buy(self):
        """
        在商店中寻找并购买物品。
        """
        err = 0
        found = 0
        self.device.click(515, 284)
        time.sleep(10)

        start = time.time()
        for img in self.imglist :
            while time.time() - start < 300:
                screen = self.capture_screenshot()
                want_to_buy = cv2.imread(img)
                res = cv2.matchTemplate(
                    screen, want_to_buy, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                h, w = want_to_buy.shape[:-1]
                top_left = max_loc

                if max_val > 0.9 and top_left[1] < 629:
                    print('Found item: ' + img)
                    self.device.click(top_left[0] + w / 2, top_left[1] + 120)
                    time.sleep(1)
                    self.device.click(262, 552)
                    time.sleep(2)
                    self.device.click(483, 145)
                    time.sleep(2)
                    found += 1
                    break
                else:
                    print('Not found', err)
                    time.sleep(1)
                    self.device.swipe(0.5, 0.8, 0.5, 0.55, 0.5)
                    self.device.click(273, 773)
                    err += 1
                    if err > 7:
                        for _ in range(5):
                            self.device.swipe(0.5, 0.3, 0.5, 0.9, 0.05)
                            time.sleep(1)
                        break
            err = 0

        self.device.click(273, 844)
        time.sleep(2)
        return found

    def main_buy(self):
        """
        主逻辑：检查是否需要购买并记录购买次数。
        """
        if self.check():
            timestamp, previous_buy_num = self.get_buy_data()
            last_date = datetime.datetime.fromtimestamp(timestamp).date()
            current_date = datetime.datetime.now().date()
            if last_date != current_date:
                previous_buy_num = 0
            new_buy_num = self.buy() + previous_buy_num
            self.record(new_buy_num)
        else:
            print(f"Skip buying for {self.device_ip}.")

    def  go_to_family(self):
        """執行家族相關操作"""
        self.device.click(407, 894)
        time.sleep(5)
        self.main_buy()
        self.device.click(96, 600)
        time.sleep(5)
        target_color = [172, 112, 69]
        pixel_value = self.capture_screenshot()[781, 228]
        target_sum = sum(target_color)
        pixel_sum = sum(pixel_value)

        if abs(pixel_sum - target_sum) <= 5 and self.find_and_click(r'sweep.png'):
            time.sleep(6)
            print("找到掃蕩")
            self.device.click(291, 552)
            time.sleep(3)
            self.device.click(271, 782)
            time.sleep(3)
            self.device.click(271, 782)
            time.sleep(3)
        self.device.click(366, 561)
        time.sleep(5)
        while True:
            if self.find_and_click(r'family_get.jpg'):
                time.sleep(2)
                print("找到高級寶箱")
                self.device.click(271, 782)
                time.sleep(1)
            else:
                print("沒有高級寶箱")
                break
        if self.find_and_click(r'family_get_all.jpg'):
            time.sleep(2)
            print("找到寶箱")
            self.device.click(271, 782)
        else:
            print("沒有寶箱")
        for _ in range(2):
            self.device.click(486, 21)
            time.sleep(3)
        self.device.click(407, 894)
        time.sleep(3)
