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
from json_manager import ParkMarketDataManager
import BUY
class ParkMarket(device):
    def __init__(self, device: u2.Device, device_ip):
        super().__init__(device)
        self.device_ip = device_ip
        self.data_manager = ParkMarketDataManager(device_ip)  # 使用新的JSON管理器
        
    def load_data(self):
        """
        載入數據，使用新的JSON管理器
        向後兼容，返回與原始方法相同的格式
        """
        return self.data_manager.load_data()
    def get_buy_data(self):
        """
        使用新的JSON管理器獲取購買數據
        返回格式與原始方法相同
        """
        return self.data_manager.get_buy_data()

    def record(self, mode='daily', buy_num=0, check_time=0):
        """
        使用新的JSON管理器記錄購買數據
        
        Args:
            mode: 'daily' 或 'weekly'
            buy_num: 購買數量
            check_time: 檢查次數（不是時間）
        """
        self.data_manager.record_purchase(mode, buy_num, check_time)

    def check(self, mode='daily'):
        """
        使用新的JSON管理器檢查是否需要購買
        
        Args:
            mode: 'daily' 或 'weekly'
            
        Returns:
            是否需要購買
        """
        return self.data_manager.should_purchase(mode, max_purchases=3)

    def buy(self,mode):
        self.device.click(380, 926)
        time.sleep(2)
        if mode == 'daily':
            img_list = ['car_book.jpg', 'offline_jump_card.jpg']
        else:
            img_list = ['digger.jpg','battle_card.jpg', ]
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
                try:
                    self.device.click(380, 926)
                    time.sleep(2)
                    buy_result =BUY.buy_items(self.device, ['改裝指南', '神力水晶', '離線躍遷卡'])
                    self.device.click(380, 926)
                    time.sleep(2)
                    buy_num = len(buy_result['bought'])
                except Exception as e:
                    print(f"Error in BUY.buy_items: {e}")
                    buy_num = self.buy('daily')
                # 記錄實際購買的次數，讓 data_manager 自動處理累加和重置邏輯
                self.record('daily', buy_num, check_time=1)
        except Exception as e:
            print(f"Error in daily purchase flow: {e}")

        # weekly
        try:
            if self.check('weekly'):
                try:
                    self.device.click(380, 926)
                    time.sleep(2)
                    buy_result =BUY.buy_items(self.device, ['礦洞直通卡', '暗黑試煉卡'])
                    buy_num = len(buy_result['bought'])
                    self.device.click(380, 926)
                    time.sleep(2)
                except Exception as e:
                    print(f"Error in BUY.buy_items: {e}")
                    buy_num = self.buy('weekly')
                # 記錄實際購買的次數，讓 data_manager 自動處理累加和重置邏輯
                self.record('weekly', buy_num, check_time=1)
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
        self.unpark_templates = [cv2.imread(os.path.join('./unpark', file)) 
                    for file in os.listdir('./unpark') 
                    if file.startswith('unpark') and file.endswith('.jpg')]
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
        lower = np.array([15,  0, 180])   # H, S, V 下界
        upper = np.array([35, 65, 255])   # H, S, V 上界（S 限制在 65 以下）
        mask = self.process_hsv(img, lower, upper, roi)
        # 膨脹
        mask = cv2.dilate(mask, None, iterations=2)
        # 侵蝕
        mask = cv2.erode(mask, None, iterations=2)
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
        if "fc65396d" in self.device_ip :
            tol = 15
            checks = [
                abs(np.sum(img[99, 425]) - np.sum([46, 50, 175])) <= tol,
                abs(np.sum(img[90, 175]) - np.sum([52, 63, 191])) <= tol,
                abs(np.sum(img[175, 87]) - np.sum([196, 226, 237])) <= tol,
                abs(np.sum(img[177, 127]) - np.sum([197, 227, 238])) <= tol,
                abs(np.sum(img[195, 22]) - np.sum([41, 66, 100])) <= tol,
                abs(np.sum(img[329, 527]) - np.sum([40, 65, 99])) <= tol,
            ]

            return sum(checks) >= 3
        
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
        
        while error < 10:
            parked = False
            img = self.capture_screenshot()
            if int(np.sum(img[149,149]))-262 > 20:
                break
            self.device.click(466,182)
            time.sleep(0.1)
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
                    for i in range(len(self.unpark_templates)):
                        unpark_template = self.unpark_templates[i]
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
                center = (int(x + w / 2), int(y + h / 2)+625+32)
                self.device.click(center[0], center[1])
                time.sleep(2)
                img = self.capture_screenshot()
                if self.check_start_button(img) and not self.full_park(img):
                    self.device.click(272, 792)
                    time.sleep(1)
                    img = self.capture_screenshot()
                    if self.check_cost(img) and ( self.device_ip == "emulator-5558" or "7fe98fc6" in self.device_ip or 'fc65396d' in self.device_ip):
                        self.device.click(402, 501)
                        time.sleep(0.5)
                    #     self.device.click(402, 549)
                    #     time.sleep(0.5)
                    #     self.device.click(277, 616)
                    # elif self.check_cost(img):
                    if self.check_cost(img):
                        self.device.click(277, 616)
                    elif abs(int(np.sum(img[149,149]))-129) < 10:
                        self.device.click(375, 554)
                    else:
                        pass
                    time.sleep(2)
                    parked = True
                    break
                else:
                    #print("繼續尋找可用車")
                    error += 1
                    time.sleep(1)
            if parked:
                break
            self.device.swipe( 383, 705 ,10, 705, 0.5)
            self.device.click(50, 705) #選車
            time.sleep(1.5)
    def goto_park(self):
        self.device.click(321, 913)
        time.sleep(0.5)
        start_time = time.time()
        while(time.time()-start_time<30):
            cnn_result = cnn_model.predict_image(
                self.cnn_model, self.device.screenshot(format='pillow'))
            if cnn_result == 'homeplace':
                break
            time.sleep(1.0)
        self.device.click(451, 451)
        time.sleep(3)
    def goto_home(self):        
        self.device.click(470, 922)
        time.sleep(2)
        self.device.click(470, 922)
        time.sleep(2)
        self.device.click(321, 913)
        time.sleep(3)
    def go_home(self):        
        self.device.click(470, 922)
        time.sleep(2)
        self.device.click(321, 913)
        time.sleep(3)
    def check_and_park(self,protect=False):
        """停車主流程 main"""
        self.protect = protect
        self.goto_park()
        # if '5556' not in self.device_ip:
        import new_park
        if_check_park = new_park.new_park_way(self.device,ip =self.device_ip)
        self.go_home()
        if not if_check_park:
            return False
        else:
            return True
        # self.market.main_buy()
        # time.sleep(2)
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
        elif self.protect and 'emulator-5558' not in self.device_ip and ("7fe98fc6" not in self.device_ip and 'fc65396d' not in self.device_ip):
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
        self.device.click(275, 913) #找車位
        time.sleep(2)
        start = time.time()
        while (5-car_count):
            if time.time() - start > 300:
                break
            img = self.capture_screenshot()
            if not self.check_if_in_friend(img):
                self.device.click(272, 929) #找車位的按鈕
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
                    img = self.capture_screenshot()
                    if self.check_if_in_friend(img):
                        self.device.press("back")
                        img = self.capture_screenshot()
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
            result = self.reader.readtext(img[372:442, 380:465], detail=0)
            print("ocr:", result)
            if 'O/' in str( result) or '0/' in str(result):
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
        img1 = img[652+40:705+30]
        img = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
        # mask = cv2.inRange(img, (37, 0, 0), (179, 255, 255))
        mask = cv2.inRange(img, (0, 101, 114), (179, 255, 255))
        # 膨脹
        mask = cv2.dilate(mask, None, iterations=2)
        # 侵蝕
        mask = cv2.erode(mask, None, iterations=1)
        # 計算輪廓
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # 顯示偵測到的遮罩與原始區域以便除錯（非阻塞）
        # try:
        #     cv2.imshow("find_car_bgr", img1)
        #     cv2.imshow("find_car_mask", mask)
        #     cv2.waitKey(0)
        # except Exception:
        #     # 在某些 headless 或非 GUI 環境下，imshow 會失敗；忽略該錯誤以維持流程
        #     pass
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
        roi = (0, 624, img.shape[1], 648)
        mask = self.process_hsv(img, hr_mask_lower, hr_mask_upper, roi)
        return np.sum(mask) > 150
        
if __name__ == "__main__":
    # d = u2.connect("emulator-5554")
    d = u2.connect("7fe98fc6")
    easyocr_reader = easyocr.Reader(['ch_tra', 'en'])
    Cnn_model = cnn_model.load_cnn_model("cnn_model.pth")
    ParkingManagers = ParkingManager(d,easyocr_reader,'7fe98fc6',Cnn_model,False)
    img = ParkingManagers.check_if_12hour()
    # check_if_12hour
    # print(len(ParkingManagers.count_cars(img)))

    # img = ParkingManagers.capture_screenshot()[500:720,]
    # cv2.imshow("img",img)
    # cv2.waitKey(0)
    # print(ParkingManagers.check_and_park())
