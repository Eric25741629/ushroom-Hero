import cv2
import numpy as np
import uiautomator2 as u2
import time
import easyocr
from tools import *
from mask import *


class ParkingManager:
    def __init__(self, device: u2.Device, reader):
        self.device = device
        self.reader = reader

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        return self.device.screenshot(format='opencv')

    def process_hsv(self, img, lower, upper, roi=None):
        """HSV 過濾處理，支持 ROI（感興趣區域）"""
        if roi:
            img = img[roi[1]:roi[3], roi[0]:roi[2]]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, lower, upper)
        return mask

    def detect_contours(self, mask, min_area=1500):
        """檢測輪廓，根據最小面積過濾"""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return [contour for contour in contours if cv2.contourArea(contour) > min_area]

    def swipe_screen(self, start, end, steps=40, delay=0.01):
        """模擬滑動螢幕"""
        self.device.touch.down(*start)
        for i in range(steps):
            current = (
                start[0] + (end[0] - start[0]) * i // steps,
                start[1] + (end[1] - start[1]) * i // steps,
            )
            self.device.touch.move(*current)
            time.sleep(delay)
        self.device.touch.up(*end)

    def count_cars(self, img):
        """計算停車數量"""
        roi = (0, 138, img.shape[1], 259)  # 限定感興趣區域
        mask = self.process_hsv(img, np.array([19, 26, 191]), np.array([34, 73, 255]), roi)
        contours = self.detect_contours(mask)
        return len(contours)

    def find_parking_spots(self, img):
        """根據模板匹配檢測可用車位"""
        park_template = cv2.imread('park.jpg')
        result = cv2.matchTemplate(img, park_template, cv2.TM_CCOEFF_NORMED)
        locations = np.where(result >= 0.8)
        locations = non_max_suppression(np.array(locations).T, 10)

        available_spots = []
        for loc in locations:
            x, y = loc[1], loc[0]
            roi = img[y:y + park_template.shape[0], x:x + park_template.shape[1]]
            mask = self.process_hsv(roi, np.array([26, 95, 167]), np.array([85, 208, 255]))
            if not self.detect_contours(mask, min_area=55):
                available_spots.append((x, y))
        return available_spots

    def check_and_park(self):
        """停車主流程"""
        img = self.capture_screenshot()
        car_count = self.count_cars(img)
        print(f"目前停車數量: {car_count}")

        if car_count >= 5:
            print("停車位已滿")
            self.device.click(475, 919)  # 返回按鈕
            time.sleep(2)
            return

        available_spots = self.find_parking_spots(img)
        if not available_spots:
            print("沒有找到車位，滑動重新搜尋")
            self.swipe_screen((300, 200), (100, 200))
            time.sleep(1)
            return

        for spot in available_spots:
            print(f"嘗試停車位置: {spot}")
            self.device.click(spot[0], spot[1])  # 點擊車位
            time.sleep(1)

            img = self.capture_screenshot()
            collapse_status = self.check_collapse(img)
            if collapse_status:
                print("車位已被佔用或發生問題，嘗試下一個位置")
                continue

            if self.perform_parking():
                print("停車成功！")
                break

    def check_collapse(self, img):
        """檢查是否有障礙物或車位問題"""
        roi = (514, 180, img.shape[1], 218)
        collapse_template = cv2.imread('collapse.jpg')
        res = cv2.matchTemplate(img[roi[1]:roi[3], roi[0]:roi[2]], collapse_template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= 0.8)
        return len(loc[0]) > 0

    def perform_parking(self):
        """執行停車操作"""
        error_count = 0
        while error_count < 10:
            img = self.capture_screenshot()
            start_button_detected = self.check_start_button(img)
            full_parking_status = self.full_park(img)

            if start_button_detected and not full_parking_status:
                self.device.click(272, 792)  # 點擊開始停車
                time.sleep(1)
                img = self.capture_screenshot()
                result = self.reader.readtext(img, detail=0)
                if "取消" in result:
                    self.device.click(375, 554)  # 確認停車
                else:
                    self.device.click(277, 616)  # 返回主畫面
                return True
            else:
                print("找不到可用停車位，滑動繼續搜尋")
                self.swipe_screen((186, 670), (186 - 78, 670))
                time.sleep(1)
                error_count += 1

        print("停車失敗，錯誤次數過多")
        return False

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
    device = u2.connect('emulator-5554')  # 連接設備
    reader = easyocr.Reader(['ch_sim', 'en'])  # 初始化 OCR
    manager = ParkingManager(device, reader)
    

