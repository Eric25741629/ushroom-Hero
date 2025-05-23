import time
import uiautomator2 as u2
import easyocr
import numpy as np
from tools import click_white
import mask
import cv2
import random
import os


class BattleManager:
    def __init__(self, device: u2.Device, reader: easyocr.Reader,cnn_model=None):
        self.device = device
        self.reader = reader

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
            print(f"滑動螢幕失敗: {e}")
            return False

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        img = self.device.screenshot(format='opencv')
        if abs(np.sum(img[234, 189]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10 and abs(np.sum(img[236, 363]) - np.sum([179,  91,  70])) < 10 and abs(np.sum(img[249, 132]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[264, 139]) - np.sum([162,  75,  57])) < 10 and abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10 and abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10 and abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10:
            self.device.click(509, 56)
            time.sleep(1)
            img = self.device.screenshot(format='opencv')
        return img

    def click_text(self, text: str) -> bool:
        """
        點擊包含指定文字的螢幕區域。
        """
        img = self.capture_screenshot()
        result = self.reader.readtext(img)
        for item in result:
            if text in str(item[1]):
                [x1, _, x3, _] = item[0]
                center = [int((x1[0] + x3[0]) / 2), int((x1[1] + x3[1]) / 2)]
                self.device.click(center[0], center[1])
                return True
        return False

    def find_battle_instance(self, battle_name: str, check: bool = False) -> bool:
        """
        找到指定戰鬥實例的座標，並執行點擊。
        """
        img = self.capture_screenshot()
        result = self.reader.readtext(img)
        print(f"OCR結果：{result}")
        find = False
        for item in result:
            if battle_name in item[1]:
                [x1, _, x3, _] = item[0]
                find = True
                break

        if not find:
            print(f"未找到戰鬥實例：{battle_name}")
            return False

        # 計算中心點座標
        center = [int((x1[0] + x3[0]) / 2) + 279,
                  int((x1[1] + x3[1]) / 2) + 70]

        # 如果需要檢查是否可以進入
        if check:
            cropped_img = img[center[1] - 40:center[1] +
                              40, center[0] - 40:center[0] + 30]
            cropped_result = self.reader.readtext(cropped_img, detail=0)
            print(f"檢查結果：{cropped_result}")
            if '入場' not in str(cropped_result) or '場' not in str(cropped_result):
                return False

        # 點擊戰鬥實例
        self.device.click(center[0], center[1])
        time.sleep(3)
        return True

    def handle_battle(self, battle_name: str):
        """
        根據不同的戰鬥實例執行特定邏輯。
        """
        start_time = time.time()
        if battle_name == "突襲神燈小偷" or battle_name == "挑戰冰巢龍穴":
            while time.time() - start_time < 5:
                self.device.click(221+random.randint(0, 5),  702+random.randint(0, 5))
            time.sleep(1)
            self.device.click(267, 812)
        if battle_name == "守衛":
            img = self.capture_screenshot()
            conditions = [
                abs(np.sum(img[749, 51]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[740, 273]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[827, 283]) - np.sum([196, 226, 237])) < 10,
                abs(np.sum(img[693, 483]) - np.sum([197, 227, 238])) < 10,
                abs(np.sum(img[803, 486]) - np.sum([197, 227, 238])) < 10
            ]
            if any(conditions):
                while time.time() - start_time < 5:
                    self.device.click(221,  772)
                time.sleep(1)
                self.click_text("確定")
                time.sleep(1)
                self.device.click(267, 903)
                time.sleep(3)
                self.click_text("確定")
        elif battle_name == "暗黑試煉":
            for _ in range(5):
                self.click_text("快速通關")
                time.sleep(3)
                self.click_text("確認")
                time.sleep(3)
                self.device.click(516, 80)
                self.click_text("重置")
                time.sleep(3)
                img = self.capture_screenshot()
                if np.abs(np.sum(img[593, 142]) - np.sum([171, 200, 215])) <= 10 and np.abs(np.sum(img[594, 196]) - np.sum([172, 201, 215])) <= 10 and np.abs(np.sum(img[595, 122]) - np.sum([174, 204, 215])) <= 10 and np.abs(np.sum(img[379, 202]) - np.sum([174, 204, 215])) <= 10 and np.abs(np.sum(img[479, 176]) - np.sum([167, 202, 212])) <= 10 and np.abs(np.sum(img[472, 386]) - np.sum([173, 203, 214])) <= 10:
                    print("還有次數")
                    self.click_text("確定")
                    time.sleep(3)
                elif (np.abs(np.sum(img[554, 120]) - np.sum([57, 64, 197])) <= 10 and np.abs(np.sum(img[557, 225]) - np.sum([58, 65, 198])) <= 10 and np.abs(np.sum(img[570, 169]) - np.sum([61, 65, 200])) <= 10) or (np.abs(np.sum(img[590, 123]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[589, 218]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[606, 208]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[605, 138]) - np.sum([57, 65, 196])) <= 10 and np.abs(np.sum(img[592, 106]) - np.sum([57, 66, 194])) <= 10):
                    print("沒有次數")
                    self.device.click(509, 56)
                    time.sleep(1)
                    break
            self.device.click(272, 878)
            time.sleep(2)
            self.swipe_screen((0.5, 0.8), (0.5, 0.65), delay=0.5)
            time.sleep(2)
        elif battle_name == "探秘焚焰神殿":
            while time.time() - start_time < 5:
                self.device.click(235, 739)
            time.sleep(1)
            self.device.click(267, 812)
        elif battle_name =="神樹試煉":
            img = self.capture_screenshot()[160:254 ,9:99]
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
            if np.sum(mask_img) > 30000 and np.sum(mask_img) < 35000:
                self.device.click(47+random.randint(0, 5), 211+random.randint(0, 5))
                time.sleep(2)
                self.device.click(302+random.randint(0, 5), 583+random.randint(0, 5))
                time.sleep(2)
                self.device.click(509, 56) #空白處
                time.sleep(1)
                self.device.click(509, 56) #空白處
                time.sleep(1)
                self.device.click(487,922)
        else:

            while time.time() - start_time < 5:
                self.device.click(225, 702)
            time.sleep(1)
            self.device.click(267, 812)

    def execute_all_battles(self, check: bool = False):
        """
        執行所有戰鬥實例。
        """
        battle_names = ["突襲神燈小偷", "挑戰冰巢龍穴", "守衛", "顛倒時序塔", "探秘焚焰神殿", "暗黑試煉","神樹試煉"]
        check_list = [True, True, False, True, True, False, False]
        self.device.swipe(0.5, 0.2, 0.5, 0.8)
        time.sleep(3)
        for name, check in zip(battle_names, check_list):
            print(f"開始處理戰鬥實例：{name}")
            if self.find_battle_instance(name, check):
                self.handle_battle(name)
            time.sleep(2)
            self.swipe_screen((0.5, 0.8), (0.5, 0.65), delay=0.5)
            time.sleep(2)
        # 返回主頁
        self.device.click(227, 915)
        time.sleep(1)

if __name__ == "__main__":
    # 初始化設備和OCR讀取器
    device = u2.connect('emulator-5554')  # 替換為你的設備ID
    img = device.screenshot(format='opencv')
    # img = img [160:254, 9:99]
    # hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
    # print(np.sum(mask_img))
    # if np.sum(mask_img) > 30000 and np.sum(mask_img) < 35000:
    #     print("紅色區域存在")
    # cv2.imshow("img", mask_img)
    # cv2.waitKey(0)
    reader = easyocr.Reader(['ch_tra', 'en'])
    battle_manager = BattleManager(device, reader)
    # img = battle_manager.capture_screenshot()
    # result = reader.readtext(img)
    # print(f"OCR結果：{result}")
    # battle_manager.handle_battle("神樹試煉")
    # # 執行所有戰鬥實例
    battle_manager.execute_all_battles()