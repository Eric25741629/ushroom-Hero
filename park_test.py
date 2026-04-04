import cv2
import numpy as np
import uiautomator2 as u2
import time
import datetime
from tools import *
from mask import *
from device import device
import random
import json
import os
import new_cnn.cnn_model as cnn_model
import easyocr
class ParkMarket(device):
    def __init__(self, device: u2.Device, device_ip):
        super().__init__(device)
        self.device_ip = device_ip
        self.data_file = self.device_ip + '.json'  # 使用 JSON 文件來存儲資料

    def load_data(self):
        """
        保证总是返回统一结构：
        {
          "daily": {"car_market_timestamp": 0.0, "car_market_buy_num": 0},
          "weekly": {"car_market_timestamp": 0.0, "car_market_buy_num": 0}
        }
        如果文件不存在会创建并写入默认结构；如果文件损坏，则覆盖为默认结构并返回默认。
        """
        default = {
            'daily': {'car_market_timestamp': 0.0, 'car_market_buy_num': 0},
            'weekly': {'car_market_timestamp': 0.0, 'car_market_buy_num': 0}
        }

        if not os.path.exists(self.data_file):
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(default, f, indent=4)
            return default

        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            # 文件壞掉：覆寫回預設並返回
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(default, f, indent=4)
            return default

        # 兼容旧格式/不完整数据：确保有 daily/weekly 且字段存在
        changed = False
        if 'daily' not in data or not isinstance(data.get('daily'), dict):
            data['daily'] = default['daily']
            changed = True
        else:
            if 'car_market_timestamp' not in data['daily']:
                data['daily']['car_market_timestamp'] = 0.0; changed = True
            if 'car_market_buy_num' not in data['daily']:
                data['daily']['car_market_buy_num'] = 0; changed = True

        if 'weekly' not in data or not isinstance(data.get('weekly'), dict):
            data['weekly'] = default['weekly']
            changed = True
        else:
            if 'car_market_timestamp' not in data['weekly']:
                data['weekly']['car_market_timestamp'] = 0.0; changed = True
            if 'car_market_buy_num' not in data['weekly']:
                data['weekly']['car_market_buy_num'] = 0; changed = True

        if changed:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        return data
    def get_buy_data(self):
        """
        一律返回 4 个值 (daily_timestamp, daily_buy_num, weekly_timestamp, weekly_buy_num)
        并把 timestamp 保证为 float（若为非数值则重置为 0.0）
        对 weekly 使用 ISO 周号来判断是否需要重置 buy_num（更可靠）
        """
        data = self.load_data()
        # 读取并在必要时进行类型/存在性修正
        try:
            daily_ts = float(data['daily'].get('car_market_timestamp', 0.0))
        except (TypeError, ValueError):
            daily_ts = 0.0
        try:
            daily_num = int(data['daily'].get('car_market_buy_num', 0))
        except (TypeError, ValueError):
            daily_num = 0

        try:
            weekly_ts = float(data['weekly'].get('car_market_timestamp', 0.0))
        except (TypeError, ValueError):
            weekly_ts = 0.0
        try:
            weekly_num = int(data['weekly'].get('car_market_buy_num', 0))
        except (TypeError, ValueError):
            weekly_num = 0

        # 如果 timestamp 为 0.0，视为未购买（不报错）
        today = datetime.datetime.now().date()
        # daily 重置：若最后日期不是今天则重置为 0
        try:
            last_daily_date = datetime.datetime.fromtimestamp(daily_ts).date() if daily_ts > 0 else None
        except (OSError, OverflowError, ValueError, TypeError):
            last_daily_date = None

        if last_daily_date != today:
            daily_num = 0

        # weekly 重置：用 ISO 周 (year, week) 比较
        try:
            last_weekly_date = datetime.datetime.fromtimestamp(weekly_ts).date() if weekly_ts > 0 else None
        except (OSError, OverflowError, ValueError, TypeError):
            last_weekly_date = None

        if last_weekly_date is None:
            weekly_num = 0
        else:
            # 比較 ISO week/year
            y1, w1, _ = last_weekly_date.isocalendar()
            y2, w2, _ = today.isocalendar()
            if (y1, w1) != (y2, w2):
                weekly_num = 0

        return daily_ts, daily_num, weekly_ts, weekly_num
   
    def record(self, mode='daily', buy_num=0):
        """
        把 timestamp 與 buy_num 保存到 data_file 的 mode 分支中。
        确保 mode 是 'daily' 或 'weekly'，并且 timestamp 为 float。
        """
        if mode not in ('daily', 'weekly'):
            raise ValueError("mode 必須是 'daily' 或 'weekly'")

        try:
            data = self.load_data()
            now_ts = datetime.datetime.now().timestamp()
            # 确保子字典存在
            if mode not in data or not isinstance(data[mode], dict):
                data[mode] = {'car_market_timestamp': 0.0, 'car_market_buy_num': 0}

            data[mode]['car_market_timestamp'] = float(now_ts)
            data[mode]['car_market_buy_num'] = int(buy_num)

            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"Recorded {mode} timestamp and buy_num for {self.device_ip}")
        except Exception as e:
            print(f"Error recording data: {e}")

    def check(self, mode='daily'):
        """
        返回是否需要再购买（True/False）。
        daily: 若最后记录日期不是今天且 buy_num < 2 则返回 True
        weekly: 若最后记录 ISO 周不是当前周且 buy_num < 2 则返回 True
        """
        if mode not in ('daily', 'weekly'):
            raise ValueError("mode 必須是 'daily' 或 'weekly'")

        daily_ts, daily_num, weekly_ts, weekly_num = self.get_buy_data()
        today = datetime.datetime.now().date()

        if mode == 'daily':
            try:
                last_daily_date = datetime.datetime.fromtimestamp(daily_ts).date() if daily_ts > 0 else None
                print(last_daily_date, today, daily_num)
            except Exception:
                last_daily_date = None
            return (last_daily_date != today) or (daily_num < 2)
        else:  # weekly
            try:
                last_weekly_date = datetime.datetime.fromtimestamp(weekly_ts).date() if weekly_ts > 0 else None
            except Exception:
                last_weekly_date = None
            if last_weekly_date is None:
                
                return weekly_num < 2
            y1, w1, _ = last_weekly_date.isocalendar()
            y2, w2, _ = today.isocalendar()
            return (y1, w1) != (y2, w2) or (weekly_num < 2)

    def buy(self,mode):
        self.device.click(380, 926)
        time.sleep(2)
        if mode == 'daily':
            img_list = ['car_book.jpg', 'offline_jump_card.jpg']
        else:
            img_list = ['battle_card.jpg', 'digger.jpg']
        buy_num = 0
        start_time = time.time()
        for img_name in img_list:
            error = 0
            want_to_buy = cv2.imread(img_name)
            if want_to_buy is None:
                # 如果圖片檔案不存在就跳過，避免 None 進入 matchTemplate
                print(f"Warning: image {img_name} not found; skipping.")
                continue
            while (time.time() - start_time < 300):  # 限時 5 分鐘
                img = self.capture_screenshot()
                if img is None:
                    print("Warning: capture_screenshot() returned None")
                    time.sleep(1)
                    continue
                res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                h, w = want_to_buy.shape[:-1]
                top_left = max_loc
                #將找到的圖片存起來
                
                # cv2.imwrite(f"debug{time.time()}.jpg", img[top_left[1]:top_left[1]+h, top_left[0]:top_left[0]+w])
                if max_val > 0.9 and top_left[1] < 552:
                    buy_num += 1
                    self.device.click(top_left[0] + w//2, top_left[1] + h//2 + 100)
                    time.sleep(2)
                    self.device.click(394, 462)
                    time.sleep(0.5)
                    self.device.click(277, 551)
                    time.sleep(2)
                    self.device.click(514, 16)  # 空白处
                    time.sleep(2)
                    break
                else:
                    error += 1
                    # 使用相對座標的 swipe（保留原本長度與次數）
                    try:
                        self.device.swipe(0.5, 0.8, 0.5, 0.6, 1)
                    except Exception:
                        # 若 device.swipe 接受絕對座標，允許 device.click 作為回退
                        pass
                    self.device.click(273, 773)
                    if error > 3:
                        for _ in range(5):
                            try:
                                self.device.swipe(0.5, 0.3, 0.5, 0.9, 0.05)
                            except Exception:
                                pass
                            time.sleep(1)
                        break
        self.device.click(380, 926)
        time.sleep(2)
        return buy_num

    def main_buy(self):
        # # daily
        try:
            if self.check('daily'):
                # 取得當前已紀錄的 daily buy 次數
                current_daily_num = self.get_buy_data()[1]
                buy_num = self.buy( 'daily')
                # 記錄新的次數（累加）
                self.record('daily', buy_num + current_daily_num)
        except Exception as e:
            print(f"Error in daily purchase flow: {e}")

        # weekly
        try:
            if self.check('weekly'):
                # 取得當前已紀錄的 weekly buy 次數
                current_weekly_num = self.get_buy_data()[3]
                buy_num = self.buy('weekly')
                self.record('weekly', buy_num + current_weekly_num)
        except Exception as e:
            print(f"Error in weekly purchase flow: {e}")

class ParkingManager:
    def __init__(self, device: u2.Device, reader, ip, cnn_model: cnn_model.SimpleCNN, protect=False):
        self.device = device
        self.reader = reader
        self.device_ip = ip
        self.market = ParkMarket(device,  ip)
        self.cnn_model = cnn_model
        self.protect = protect

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        while True:
            img = self.device.screenshot(format='opencv')
            if abs(np.sum(img[234, 189]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[236, 363]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[249, 132]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[264, 139]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10 and abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10 and abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10:
                self.device.click(509, 56)
                time.sleep(1)
                continue
            if img is not None:
                break
        if not os.path.exists("park_manager"):
            os.mkdir("park_manager")
        # cv2.imwrite(f"park_manager/{self.device_ip}_{time.time()}.jpg", img)
        return img

    def process_hsv(self, img, lower, upper, roi=None):
        """HSV 過濾處理，支持 ROI（感興趣區域）"""
        if roi:
            img = img[roi[1]:roi[3], roi[0]:roi[2]]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, lower, upper)
        return mask

    def detect_contours(self, mask, min_area=1500):
        """檢測輪廓，根據最小面積過濾"""
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # #print([cv2.contourArea(contour) for contour in contours])
        return [contour for contour in contours if cv2.contourArea(contour) > min_area]

    def swipe_screen(self, start, end, steps=40, delay=0.01):
        """模擬滑動螢幕"""
        try:
            # 將相對座標轉換為絕對像素座標
            screen_width, screen_height = self.device.window_size()
            if start[0] < 1 and start[1] < 1:
                start = (int(start[0] * screen_width),
                         int(start[1] * screen_height))
            if end[0] < 1 and end[1] < 1:
                end = (int(end[0] * screen_width), int(end[1] * screen_height))
            # 開始滑動
            self.device.touch.down(*start)
            for i in range(steps + 1):  # 加 1，確保包含最後一步
                current_x = start[0] + (end[0] - start[0]) * i / steps
                current_y = start[1] + (end[1] - start[1]) * i / steps
                current = (int(current_x), int(current_y))  # 確保是整數
                self.device.touch.move(*current)
                time.sleep(delay/20)
            time.sleep(delay)
            self.device.touch.up(*end)
            return True
        except Exception as e:
            #print(f"滑動螢幕失敗: {e}")
            return False

    def count_cars(self, img):
        """計算停車數量"""
        roi = (0, 138, img.shape[1], 259)  # 限定感興趣區域
        mask = self.process_hsv(img, np.array(
            [19, 26, 191]), np.array([34, 73, 255]), roi)
        # 膨脹
        mask = cv2.dilate(mask, None, iterations=3)
        # 侵蝕
        mask = cv2.erode(mask, None, iterations=3)
        contours = self.detect_contours(mask, min_area=1400)
        return contours

    def check_if_in_cooling(self, img):
        """檢查是否在冷卻中"""
        roi = (0, 138, img.shape[1], 259)
        mask = self.process_hsv(img, np.array(
            [16, 106, 222]), np.array([23, 167, 255]), roi)
        if self.detect_contours(mask, min_area=10):
            return True
        return False

    def find_parking_spots(self, img):
        """根據模板匹配檢測可用車位"""
        park_template = cv2.imread('park.jpg')  # 載入車位模板
        result = cv2.matchTemplate(
            img, park_template, cv2.TM_CCOEFF_NORMED)  # 模板匹配
        locations = np.where(result >= 0.8)  # 找到匹配位置
        locations = non_max_suppression(np.array(locations).T, 10)  # 非極大值抑制

        available_spots = []  # 存放可用車位
        for loc in locations:
            x, y = loc[1], loc[0]
            roi = img[y:y + park_template.shape[0],
                      x:x + park_template.shape[1]]  # 車位 ROI
            # HSV 篩選，用於判斷是否已經停了車
            mask = self.process_hsv(roi, np.array(
                [26, 95, 167]), np.array([85, 208, 255]))
            if self.detect_contours(mask, min_area=55):  # 如果有車，跳過
                continue
            # 檢測是否「冷卻中」
            roi = img[y:y + park_template.shape[0], 170:270]
            mask = self.process_hsv(roi, np.array(
                [16, 106, 222]), np.array([23, 167, 255]))
            if self.detect_contours(mask, min_area=10):
                # #print("冷卻中，跳過該區域")
                continue
            # 檢測是否「滿了」
            img2 = img[y - 10:y + park_template.shape[0], :119]  # 全區域 ROI
            full_template = cv2.imread('full.jpg')  # 載入「滿了」模板
            res = cv2.matchTemplate(img2, full_template, cv2.TM_CCOEFF_NORMED)
            full_loc = np.where(res >= 0.8)  # 判斷是否顯示「滿了」
            if len(full_loc[0]) > 0:
                # #print("滿了車位，跳過該區域")
                continue

            # 如果車位可用，加入列表
            available_spots.append((x, y))

        return available_spots

    def find_park_place(self, img):
        """
        找尋可以停車的區域，分成四個區塊進行檢測。
        """
        # 定義區域範圍
        y_coords = [208, 433]
        x_coords = [84, 389]
        place = []
        place_num = 1

        # HSV 範圍
        lower = np.array([6, 0, 85])
        upper = np.array([41, 139, 108])

        for y in y_coords:
            for x in x_coords:
                # 提取感興趣區域 (ROI)
                roi = (x, y, x + 100, y + 28)
                mask = self.process_hsv(img, lower, upper, roi)

                # 膨脹與腐蝕操作
                mask = cv2.erode(mask, None, iterations=1)
                mask = cv2.dilate(mask, None, iterations=30)

                # 檢查符合條件的區域
                if np.sum(mask) < 352000:
                    place.append(place_num)
                place_num += 1

        return place

    def check_if_any_parking(self, img):
        return (np.abs(np.sum(img[191, 19]) - np.sum([53, 74, 106])) <= 10 and
                np.abs(np.sum(img[209, 17]) - np.sum([57, 71, 119])) <= 10 and
                np.abs(np.sum(img[199, 6]) - np.sum([58, 69, 107])) <= 10)

    def check_if_in_friend(self, img):
        #print(np.abs(np.sum(img[99, 425]) - np.sum([46, 50, 175])) <= 10,
            #   np.abs(np.sum(img[90, 175]) - np.sum([52, 63, 191])) <= 10,
            #   np.abs(np.sum(img[175, 87]) - np.sum([196, 226, 237])) <= 10,
            #   np.abs(np.sum(img[177, 127]) - np.sum([197, 227, 238])) <= 10,
            #   np.abs(np.sum(img[195, 22]) - np.sum([41, 66, 100])) <= 10,
            #   np.abs(np.sum(img[329, 527]) - np.sum([40, 65, 99])) <= 10)

        return (np.abs(np.sum(img[99, 425]) - np.sum([46, 50, 175])) <= 10 and
                np.abs(np.sum(img[90, 175]) - np.sum([52, 63, 191])) <= 10 and
                np.abs(np.sum(img[175, 87]) - np.sum([196, 226, 237])) <= 10 and
                np.abs(np.sum(img[177, 127]) - np.sum([197, 227, 238])) <= 10 and
                np.abs(np.sum(img[195, 22]) - np.sum([41, 66, 100])) <= 10 and
                np.abs(np.sum(img[329, 527]) - np.sum([40, 65, 99])) <= 10)

    def park_car(self):
        """停車選車流程"""
        error = 0
        # 載入所有 unpark 模板
        unpark_templates = [cv2.imread(f'./unpark/unpark{i}.jpg') for i in range(1, 11)]
        while error < 10:
            parked = False
            img = self.capture_screenshot()
            find_car_point = self.find_car(img)
            find_car_point.sort(key=lambda x: x[0])
            if not find_car_point:
                break
            img = self.capture_screenshot()

            for point in find_car_point:
                x, y, w, h = point
                img1 = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[
                    y+625:y+65+625, x:x+65]
                img2 = img[y+625:y+65+625, x:x+65]
                # 讀取unpark 和unpark2 的圖片進行相似度比
                try:
                    unpark = False
                    for i in range(len(unpark_templates)):
                        unpark_template = unpark_templates[i]
                        res = cv2.matchTemplate(img2, unpark_template, cv2.TM_CCOEFF_NORMED)
                        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                        # print(f"unpark{i} 相似度", max_val)
                        # cv2.imwrite(f'unpark{i}_match_{time.time()}.jpg', img2)
                        if max_val > 0.65:
                            unpark = True
                            break
                except Exception as e:
                    print(f"Error: {e}")
                if unpark:
                    continue
                mask = cv2.inRange(img1, (56, 42, 139), (71, 147, 255))
                # 膨脹
                mask = cv2.dilate(mask, None, iterations=3)
                countours = self.detect_contours(mask, min_area=500)
                if len(countours) > 0:
                    continue

                center = (int(x + w / 2), int(y + h / 2)+625)
                self.device.click(center[0], center[1])
                time.sleep(2)
                img = self.capture_screenshot()
                if self.check_start_button(img) and not self.full_park(img):
                    self.device.click(272, 792)
                    time.sleep(1)
                    img = self.capture_screenshot()
                    # if self.check_cost(img) and (self.device_ip == "3a8d31f2" or self.device_ip == "emulator-5554"):
                    #     self.device.click(402, 501)
                    #     time.sleep(0.5)
                    #     self.device.click(402, 549)
                    #     time.sleep(0.5)
                    #     self.device.click(277, 616)
                    # elif self.check_cost(img):
                    if self.check_cost(img):
                        self.device.click(277, 616)
                    else:
                        self.device.click(375, 554)
                    time.sleep(2)
                    parked = True
                    break
                else:
                    #print("繼續尋找可用車")
                    error += 1
                    time.sleep(1)
            if parked:
                break
            self.swipe_screen(
                (383, 705), (186 - 78-78, 705), delay=0.5)
            time.sleep(1.5)

    def check_and_park(self,protect=False):
        """停車主流程"""
        self.protect = protect
        self.device.click(321, 913)
        while(1):
            cnn_result = cnn_model.predict_image(
                self.cnn_model, self.device.screenshot(format='pillow'))
            if cnn_result == 'homeplace':
                break
        self.device.click(451, 451)
        time.sleep(3)
        self.market.main_buy()
        time.sleep(2)
        img = self.capture_screenshot()
        if self.device_ip =='emulator-5558' or not self.protect :
            if not self.check_if_any_parking(img):
                self.device.click(29, 213)
                time.sleep(2)
                self.device.click(368, 515)
                time.sleep(2)
                self.device.click(509, 56)
                time.sleep(2)
            self.swipe_screen((300, 200), (100, 200))
            time.sleep(1)
            self.check_if_12hour()
            time.sleep(2)
            self.device.click(509, 56)      # 點擊空白處
            time.sleep(1)
            img = self.capture_screenshot()
            car_count = len(self.count_cars(img))
        #保護
        elif self.protect and self.device_ip !='emulator-5558':
            if not self.check_if_any_parking(img):
                self.device.click(29, 213)
                time.sleep(2)
                for _ in range(3):
                    rand = random.randint(-5, 5)
                    self.device.click(133+rand, 450+rand)
                time.sleep(2)
                self.device.click(368, 515)
                time.sleep(2)
                self.device.click(364,550)
                time.sleep(2)
                for _ in range(2):
                    self.device.click(533, 1) # 點擊空白處
                    time.sleep(1)
            car_count = 0
       
        #print(f"目前停車數量: {car_count}")

        if car_count >= 5:
            #print("停車位已滿")
            self.device.click(475, 919)  # 返回按鈕
            time.sleep(2)
            self.device.click(321, 913)  # 返回主畫面
            time.sleep(2)
            return False
        self.device.click(275, 913)
        time.sleep(2)
        start = time.time()
        while (5-car_count):
            if time.time() - start > 300:
                break
            img = self.capture_screenshot()
            if not self.check_if_in_friend(img):
                self.device.click(272, 929)
                time.sleep(2)
                continue

            available_spots = self.find_parking_spots(img)
            # #print(f"找到 {len(available_spots)} 個可用車位")
            if not available_spots:
                # #print("沒有找到車位，滑動重新搜尋")
                self.swipe_screen((0.5, 0.75), (0.5, 0.22),
                                  delay=0.4)  # 執行停車主流程
                time.sleep(1)
                continue
            for spot in available_spots:
                # #print(f"嘗試停車位置: {spot}")
                self.device.click(int(spot[0])+30, int(spot[1])+10)
                time.sleep(2)
                self.device.click(528, 200)  # 點擊「收起已停車」
                time.sleep(1)
                img = self.capture_screenshot()
                can_park = self.find_park_place(img)
                if not can_park:
                    break
                parkpoint = [[124, 316], [430, 316], [124, 536], [430, 536]]
                parkpoint = [parkpoint[i-1] for i in can_park]
                point = parkpoint[0]
                self.device.click(point[0], point[1])
                time.sleep(2)
                self.park_car()
                img = self.capture_screenshot()[500:720,]
                if self.detect_contours(self.process_hsv(img, np.array([30, 36, 68]), np.array([56, 236, 217])), min_area=3000):
                    self.device.click(509, 56)#空白處
                    time.sleep(2)
                    self.device.click(275, 900)
                    time.sleep(2)
                    self.swipe_screen((300, 200), (100, 200))
                    time.sleep(2)
                    self.device.click(509, 56)
                    time.sleep(0.1)
                    img = self.capture_screenshot()
                    car_count = len(self.count_cars(img))
                    if car_count >= 5:
                        break
                    self.device.click(281, 892)#點找車位的那個按鈕
                    time.sleep(1)
                else:
                    self.device.click(272, 929)
                    time.sleep(2)
            if car_count >= 5:
                break
        self.device.click(470, 922)
        time.sleep(2)
        self.device.click(470, 922)
        time.sleep(2)
        self.device.click(321, 913)
        time.sleep(3)
        return True

    def check_cost(self, img):
        """檢查是否有費用提示"""
        img = img[531:725,]
        if self.detect_contours(self.process_hsv(img, np.array([0, 144, 139]), np.array([9, 255, 212])), min_area=3000):
            return False
        return True

    def check_if_12hour(self):
        if datetime.datetime.now().hour < 12:
            return
        img = self.capture_screenshot()
        for car in self.count_cars(img):
            x, y, w, h = cv2.boundingRect(car)
            # 計算中心點座標
            center = (int(x + w / 2), int(y + h / 2)+138)
            self.device.click(center[0], center[1])
            time.sleep(2)
            img = self.capture_screenshot()
            result = self.reader.readtext(img[413:442, 359:422], detail=0)
            if 'O/m' in result or '0/m' in result:
                #print("full")
                self.device.click(375, 744)
                time.sleep(2)
                self.device.click(379, 552)
                time.sleep(2)
            self.device.click(509, 56)
            time.sleep(2)

    def check_collapse(self, img):
        """檢查是否有障礙物或車位問題"""
        roi = (514, 180, img.shape[1], 218)
        collapse_template = cv2.imread('collapse.jpg')
        res = cv2.matchTemplate(
            img[roi[1]:roi[3], roi[0]:roi[2]], collapse_template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= 0.8)
        return len(loc[0]) > 0

    def find_car(self, img):
        img = self.capture_screenshot()
        img1 = img[625:705]
        
        img = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
        # mask = cv2.inRange(img, (37, 0, 0), (179, 255, 255))
        mask = cv2.inRange(img, (0, 101, 114), (179, 255, 255))
        # 膨脹
        mask = cv2.dilate(mask, None, iterations=3)
        # 侵蝕
        mask = cv2.erode(mask, None, iterations=3)
        # 計算輪廓
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [cv2.boundingRect(
            contour) for contour in contours if cv2.contourArea(contour) > 1000]
        return contours

    def check_start_button(self, img):
        """檢查開始按鈕是否可用"""
        roi = (0, 741, img.shape[1], 819)
        mask = self.process_hsv(img, green_mask_lower, green_mask_upper, roi)
        return np.sum(mask) > 150

    def full_park(self, img):
        """檢查是否顯示車位已滿"""
        roi = (0, 595, img.shape[1], 622)
        mask = self.process_hsv(img, hr_mask_lower, hr_mask_upper, roi)
        return np.sum(mask) > 150
    
    
if __name__ == "__main__":
    # Example usage
    # 測試用 不連線
    d = u2.connect('adb-fc65396d-4LPqmI (2)._adb-tls-connect._tcp')  # 連接到指定設備
    reader = easyocr.Reader(['ch_sim', 'en'])  # 初始化 OCR
    manager = ParkMarket(d, '7fe98fc6')
    print(manager.check())
    

