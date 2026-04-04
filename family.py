import os
import time
import cv2
import numpy as np
import uiautomator2 as u2
import datetime
from device import device
from tools import click_white
from json_manager import create_time_manager, create_family_market_manager
from img_tools import click_str_by_server, check_str_in_region
import new_cnn.cnn_model as cnn_model
import random
import pytz
import BUY
import img_tools 
import new_battle

class Family_manager(device):
    def __init__(self, device: u2.Device, ip: str, cnn_model: cnn_model.SimpleCNN):
        super().__init__(device)
        self.device_ip = ip
        self.device = device
        self.cnn_model = cnn_model
        self.family_data_manager = create_family_market_manager(self.device_ip)

        self.imglist = ['godcoin.jpg', 'screw.jpg', 'Flaming_reset.jpg', 'scroll.jpg', 'Gold_Key.jpg', 'Diamond_Key.jpg',
                        'Flaming_Gold_Key.jpg']
        self.end_img = 'Gold_coin.jpg'

        # 定義每日和每週的購買物品列表
        self.daily_imglist = ['godcoin.jpg', 'Flaming_reset.jpg', 'scroll.jpg', 'Gold_Key.jpg', 'Diamond_Key.jpg',
                              'Flaming_Gold_Key.jpg']
        self.weekly_imglist = ['Spirit Talisman.jpg', 'Flaming_restart.jpg']

        # if self.device_ip == 'emulator-5556':
        #     self.imglist = ['scroll.jpg',
        #                     'Flaming_Gold_Key.jpg', 'Gold_Key.jpg',]
        if self.device_ip == 'emulator-5558':
            self.imglist = ['Flaming_reset.jpg', 'scroll.jpg', 'Gold_Key.jpg', 'Diamond_Key.jpg',
                            'Flaming_Gold_Key.jpg']
            self.daily_imglist = ['Flaming_reset.jpg', 'scroll.jpg', 'Gold_Key.jpg', 'Diamond_Key.jpg',
                                  'Flaming_Gold_Key.jpg']

        # 台灣時區
        self.taiwan_tz = pytz.timezone('Asia/Taipei')
    
    def get_taiwan_now(self):
        """獲取台灣當前時間"""
        return datetime.datetime.now(self.taiwan_tz)
    
    def get_taiwan_date(self):
        """獲取台灣當前日期"""
        return self.get_taiwan_now().date()

    def find_and_click(self, findImgPath, threshold=0.8, x=0, y=0):
        """尋找圖像並點擊"""
        img = self.capture_screenshot()
        findImg = cv2.imread(findImgPath)
        res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)

        # 創建資料夾
        # if not os.path.exists("res"):
        #     os.mkdir("res")

        h, w = findImg.shape[:2]

        # 儲存所有匹配位置 (取前 N 個最大值)
        num_save = 10
        res_flat = res.flatten()
        top_indices = res_flat.argsort()[::-1][:num_save]  # 取前 num_save 高分
        res_rows, res_cols = res.shape
        # 點擊第一個高於 threshold 的位置
        loc = np.where(res >= threshold)
        if len(loc[0]) > 0:
            center_x = int(loc[1][0] + w / 2)
            center_y = int(loc[0][0] + h / 2)
            self.device.click(center_x + x, center_y + y)
            return True

        return False

    def donate_family(self):
        img_tools.click_str_by_server(self.device,'家族大廳',shift_y=-50)
        time.sleep(2)
        img_tools.click_str_by_server(self.device,'捐獻',y_range=(746,809))
        time.sleep(1)

        for i in range(10):
            # img_tools.click_str_by_server(self.device,'家族捐獻',y_range=(302,339),shift_y=555-321)
            self.device.click(270, 556)
            time.sleep(1)
        self.device.click(269, 319)
        time.sleep(3)
        self.device.click(275,844)
        time.sleep(2)

    def go_to_family(self):
        """執行家族相關操作 (增加 1 小時冷卻)"""
        time_manager = create_time_manager(device_id=self.device_ip)
        
        # 檢查冷卻時間 (1小時 = 3600秒)
        record = time_manager.get_time_record("go_to_family_cooldown")
        if record and record.get("timestamp"):
            last_ts = float(record["timestamp"])
            if (time.time() - last_ts) < 3600:
                print(f"{self.device_ip} 家族操作冷卻中 (上次執行: {record.get('datetime', 'unknown')})")
                return

        time.sleep(0.3)
        self.device.click(391, 938)
        time.sleep(1)
        self.device.click(9,154)
        time.sleep(1)
        self.device.click(20,154)
        current_time = self.get_taiwan_now().time()
        if not img_tools.click_str_by_server(self.device,'家族大廳',y_range=(0,230),shift_y=300+current_time.hour%5*45):
            img_tools.click_str_by_server(self.device,'家族大庭',y_range=(0,230),shift_y=300+current_time.hour%5*45)
        if img_tools.check_str_in_region(self.device, '朋友圈', y_range=(723,772)):
            self.device.click(270,883)
            time.sleep(1)
        if current_time.hour < 6:
            time.sleep(5)
        time.sleep(7)
        # 每日只執行一次捐獻，使用 json_manager 的 TimeRecordDataManager 管理
        try:
            rec = time_manager.get_time_record("donate_family")
        except Exception:
            rec = None

        do_donate = True
        if rec and isinstance(rec, dict):
            # 若有 is_next_day 欄位，且為 False，表示尚未到隔日，跳過
            is_next_day = rec.get("is_next_day")
            if is_next_day is False:
                do_donate = False

        if do_donate:
            self.donate_family()
            try:
                time_manager.record_time("donate_family")
            except Exception:
                # 非致命，僅記 log
                print(f"無法寫入 donate_family 時間紀錄: {self.device_ip}")
        else:
            print(f"{self.device_ip} 今日已捐獻過家族，跳過 donate_family()")
        
        # if current_time.hour > 2:
        #     self.main_buy()

        self.device.click(96, 600)
        time.sleep(5)
        # target_color = [172, 112, 69]
        # pixel_value = self.capture_screenshot()[781, 228]
        # target_sum = sum(target_color)
        # pixel_sum = sum(int(c) for c in pixel_value)

        # if abs(pixel_sum - target_sum) <= 10 and self.find_and_click(r'sweep.png'):
        #     time.sleep(6)
        #     print("找到掃蕩")
        #     self.device.click(291, 552)
        #     time.sleep(3)
        #     self.device.click(271, 782)
        #     time.sleep(3)
        #     self.device.click(271, 782)
        #     time.sleep(3)
        self.device.click(366, 561)
        time.sleep(5)
        for _ in range(10):
            img = self.capture_screenshot()
            clicked = check_str_in_region(img, "領取", y_range=(400, 582))
            if clicked:
                self.device.click(334,533)
                time.sleep(2)
                print("找到高級寶箱")
                self.device.click(271, 782)
                time.sleep(1)
            else:
                print("沒有高級寶箱")
                break
        clicked = click_str_by_server(self.device, "一鍵領取", y_range=(682, 900))    
        if clicked:
            time.sleep(2)
            print("找到寶箱")
            self.device.click(271, 782)
        else:
            print("沒有寶箱")
        for _ in range(2):
            self.device.click(486, 21)
            time.sleep(3)
        new_battle.fight_snow_country(self.device, self.device_ip)
        
        self.device.click(391, 938)
        time.sleep(3)
        
        # 記錄執行時間，重置冷卻
        time_manager.record_time("go_to_family_cooldown")