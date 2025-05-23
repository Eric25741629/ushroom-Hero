import cv2
import numpy as np
import uiautomator2 as u2
import time
import datetime
from tools import *
from mask import *
from device import device
import json
import os


class ParkMarket(device):
    def __init__(self, device: u2.Device, device_ip):
        super().__init__(device)
        self.device_ip = device_ip
        self.data_file = self.device_ip + '.json'  # 使用 JSON 文件來存儲資料

    def record(self, buy_num=0):
        """
        记录当前时间戳和购买次数到对应的 IP JSON 文件中。
        """
        try:
            now = datetime.datetime.now()
            timestamp = now.timestamp()

            # 讀取現有的資料
            data = self.load_data()
            if 'device_status' not in data:
                data['car_market_timestamp'] = 'inactive'
            if 'purchase_history' not in data:
                data['car_market_buy_num'] = 0
            # 更新數據
            data['car_market_timestamp'] = timestamp
            data['car_market_buy_num'] = buy_num
            # 寫入 JSON 文件
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=4)

            print(
                f"car_market_timestamp and buy number recorded for {self.device_ip}.")
        except Exception as e:
            # 新增或更新數據時出錯
            print(f"Error recording data: {e}")
            # 增加欄位

    def check(self):
        timestamp, buy_num = self.get_buy_data()
        last_date = datetime.datetime.fromtimestamp(timestamp).date()
        current_date = datetime.datetime.now().date()
        print(
            f"last_date: {last_date}, current_date: {current_date}, buy_num: {buy_num}")
        # 如果日期不同或購買次數小於 2，返回 True 表示需要再次購買
        return last_date != current_date or buy_num < 2

    def get_buy_data(self):
        """
        获取购买数据，如果文件不存在则返回默认值。
        """
        try:
            data = self.load_data()
            timestamp = data.get('car_market_timestamp', 0)
            buy_num = data.get('car_market_buy_num', 0)
            last_date = datetime.datetime.fromtimestamp(timestamp).date()
            current_date = datetime.datetime.now().date()
            # 仅在日期不匹配时将购买次数重置
            if last_date != current_date:
                buy_num = 0

            return timestamp, buy_num
        except (FileNotFoundError, ValueError) as e:
            # 文件不存在或格式错误，返回默认值
            return 0, 0

    def load_data(self):
        """
        加载数据文件，如果文件不存在则返回默认数据。
        """
        if not os.path.exists(self.data_file):
            # 新增文件
            with open(self.data_file, 'w') as f:
                json.dump({'car_market_timestamp': 0,
                          'car_market_buy_num': 0}, f)
            return {'car_market_timestamp': 0, 'car_market_buy_num': 0}  # 返回默认值

        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            #print(f"Error decoding JSON data: {e}")
            # 文件格式错误，返回默认值
            return {'car_market_timestamp': 0, 'car_market_buy_num': 0}

    def buy(self):
        self.device.click(380, 926)
        time.sleep(2)
        img_list = ['car_book.jpg', 'offline_jump_card.jpg']
        buy_num = 0
        start_time = time.time()
        for img_name in img_list:
            error = 0
            want_to_buy = cv2.imread(img_name)
            while (time.time() - start_time < 300):  # 限時 5 分鐘
                img = self.capture_screenshot()
                res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                h, w = want_to_buy.shape[:-1]
                top_left = max_loc
                if max_val > 0.9 and top_left[1] < 552:
                    buy_num += 1
                    self.device.click(
                        top_left[0] + w//2, top_left[1] + h//2 + 100)
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
                    self.device.swipe(0.5, 0.8, 0.5, 0.6, 1)
                    self.device.click(273, 773)
                    if error > 3:
                        for _ in range(5):
                            self.device.swipe(0.5, 0.3, 0.5, 0.9, 0.05)
                            time.sleep(1)
                        break
        self.device.click(380, 926)
        time.sleep(2)
        return buy_num

    def main_buy(self):
        if self.check():
            # 获取当前购买次数并执行购买
            num = self.get_buy_data()[1]
            buy_num = self.buy()
            # 记录新的购买次数
            self.record(buy_num + num)


class ParkingManager:
    def __init__(self, device: u2.Device, reader, ip,cnn_model):
        self.device = device
        self.reader = reader
        self.device_ip = ip
        self.market = ParkMarket(device,  ip)
        self.cnn_model = cnn_model

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
        contours = self.detect_contours(mask, min_area=2000)
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
        unpark_templates = [cv2.imread(f'unpark{i}.jpg') for i in range(1, 5)]
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
                    unpark_template = cv2.imread('unpark1.jpg')
                    res = cv2.matchTemplate(
                        img2, unpark_template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    # #print("相似度", max_val)
                    if max_val > 0.65:
                        # # cv2.imwrite('{}.jpg'.format(time.time()), img2)
                        continue
                    unpark_template = cv2.imread('unpark2.jpg')
                    res = cv2.matchTemplate(
                        img2, unpark_template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    # #print("相似度", max_val)
                    if max_val > 0.65:
                        # # cv2.imwrite('{}.jpg'.format(time.time()), img2)
                        continue

                    unpark_template = cv2.imread('unpark3.jpg')
                    res = cv2.matchTemplate(
                        img2, unpark_template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    #print("相似度", max_val)
                    if max_val > 0.65:
                        # # cv2.imwrite('{}.jpg'.format(time.time()), img2)
                        continue

                    unpark_template = cv2.imread('unpark4.jpg')
                    res = cv2.matchTemplate(
                        img2, unpark_template, cv2.TM_CCOEFF_NORMED)
                    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                    #print("相似度", max_val)
                    if max_val > 0.65:
                        # # cv2.imwrite('{}.jpg'.format(time.time()), img2)
                        continue

                except Exception as e:
                    print(f"Error: {e}")
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
                    if self.check_cost(img) and self.device_ip == "emulator-5554":
                        self.device.click(402, 501)
                        time.sleep(0.5)
                        self.device.click(402, 549)
                        time.sleep(0.5)
                        self.device.click(277, 616)
                    elif self.check_cost(img):
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

    def check_and_park(self):
        """停車主流程"""
        self.device.click(321, 913)
        time.sleep(3)
        self.device.click(451, 451)
        time.sleep(3)
        self.market.main_buy()
        time.sleep(2)
        img = self.capture_screenshot()
        if not self.check_if_any_parking(img):
            self.device.click(29, 204)
            time.sleep(2)
            self.device.click(368, 515)
            time.sleep(2)
            self.device.click(509, 56)
            time.sleep(2)
            self.swipe_screen((300, 200), (100, 200))
        time.sleep(1)
        self.check_if_12hour()
        time.sleep(2)
        self.device.click(509, 56)
        time.sleep(1)

        img = self.capture_screenshot()
        car_count = len(self.count_cars(img))

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
                    self.device.click(509, 56)
                    time.sleep(2)
                    self.device.click(281, 892)
                    time.sleep(1)
                    self.swipe_screen((300, 200), (100, 200))
                    time.sleep(2)
                    img = self.capture_screenshot()
                    car_count = len(self.count_cars(img))
                    #print(f"目前停車數量: {car_count}")
                    if car_count >= 5:
                        break
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
