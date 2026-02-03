import os
import time
import cv2
import numpy as np
import uiautomator2 as u2
import datetime
from device import device
from tools import click_white
from json_manager import create_time_manager, create_family_market_manager
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

    # def _evaluate_purchase_state(self, mode: str, target_count: int) -> dict:
        """整合每日與每週的購買判斷邏輯，回傳詳細狀態。"""
        if mode not in ('daily', 'weekly'):
            raise ValueError("mode 必須是 'daily' 或 'weekly'")

        timestamp, buy_num = self.get_buy_data(mode)
        current_date = self.get_taiwan_date()

        result = {
            "mode": mode,
            "timestamp": timestamp,
            "buy_num": buy_num,
            "current_date": current_date,
            "last_date": None,
            "days_diff": None,
            "should_buy": True,
            "effective_buy_num": 0,
            "reset": True,
            "target_count": target_count,
        }

        label = 'Daily' if mode == 'daily' else 'Weekly'

        if timestamp <= 0:
            print(f"{label} - 尚未有購買紀錄，準備執行購買。")
            return result

        try:
            last_date = datetime.datetime.fromtimestamp(timestamp, tz=self.taiwan_tz).date()
        except (OSError, OverflowError, ValueError, TypeError):
            print(f"{label} - 無法解析購買時間，視為需要購買。")
            return result

        days_diff = (current_date - last_date).days

        result.update({
            "last_date": last_date,
            "days_diff": days_diff,
        })

        if mode == 'daily':
            print(f"Daily - Last date: {last_date}, Current date: {current_date}, Buy num: {buy_num}, Daily list length: {target_count}")
            reset = last_date != current_date
            if reset:
                result.update({"reset": True, "should_buy": True, "effective_buy_num": 0})
            else:
                should_buy = buy_num < target_count
                result.update({
                    "reset": False,
                    "should_buy": should_buy,
                    "effective_buy_num": buy_num,
                })
        else:
            is_monday = current_date.weekday() == 0 
            print(f"Weekly - Last date: {last_date}, Current date: {current_date}, Buy num: {buy_num}, Weekly list length: {target_count}")
            print(f"Days diff: {days_diff}, Is Monday: {is_monday}")
            reset = days_diff >= 7 or (is_monday and days_diff >= 1)
            if reset:
                result.update({"reset": True, "should_buy": True, "effective_buy_num": 0})
            else:
                should_buy = buy_num < target_count and days_diff >= 3
                result.update({
                    "reset": False,
                    "should_buy": should_buy,
                    "effective_buy_num": buy_num,
                })

        return result

    # def record(self, mode, buy_num=0):
    #     """使用統一的 json_manager 記錄每日或每週的購買資訊。"""
    #     try:
    #         self.family_data_manager.set_state(mode, buy_num)
    #         print(f"Timestamp and buy number recorded for {self.device_ip} ({mode}).")
    #     except ValueError:
    #         raise
    #     except Exception as e:
    #         print(f"Error while recording data for {self.device_ip} ({mode}): {e}")

    # def get_buy_data(self, mode='daily'):
    #     """取得最後一次購買紀錄的時間戳與購買次數。"""
    #     try:
    #         state = self.family_data_manager.get_state(mode)
    #         return state.timestamp, state.buy_num
    #     except ValueError:
    #         raise
    #     except Exception as e:
    #         print(f"Error while loading buy data for {self.device_ip} ({mode}): {e}")
    #         return 0, 0
    # def check(self, mode='daily'):
    #     """檢查是否需要購買，封裝在評估函式中。"""
    #     try:
    #         target_count = len(self.daily_imglist) if mode == 'daily' else len(self.weekly_imglist)
    #         evaluation = self._evaluate_purchase_state(mode, target_count)
    #         return evaluation["should_buy"]
    #     except Exception as e:
    #         print(f"Error while checking {mode} timestamp for {self.device_ip}: {e}")
    #         return True
    # def buy(self,mode = 'daily'):
    #     """
    #     在商店中寻找并购买物品，並保存匹配嘗試的圖像。
    #     """
    #     err = 0
    #     found = 0
    #     self.device.click(515, 268)
    #     time.sleep(10)

    #     if not os.path.exists("buy_results"):
    #         os.mkdir("buy_results")
    #     if mode =='daily':
    #         current_imglist = self.daily_imglist
    #     elif mode =='weekly':
    #         current_imglist = self.weekly_imglist
    #     screen = self.capture_screenshot()
    #     pixel_sum_at_coord = sum(int(c) for c in screen[97, 177])
    #     target_sum_for_check = sum([190, 105, 57])
    #     if pixel_sum_at_coord - target_sum_for_check > 10:
    #         print("沒有進入商店")
    #         return 0
        
    #     start = time.time()
    #     if mode == 'weekly':
    #         for i in range(9):
    #             self.device.swipe(0.5+random.random()/20,0.8,0.5+random.random()/10,0.1+random.random()/10,0.1+random.random()/10)
    #             time.sleep(0.5)
    #             self.device.click(272,543)
            
    #         buy_result = BUY.buy_items(self.device, ['靈契之符','符石還原劑'],no_scroll=True)
    #     else:
    #         if self.device_ip == 'emulator-5558':
    #             #一三五才買符石阻斷劑-台灣時間
    #             today = self.get_taiwan_now().date()
    #             if today.weekday() in [0,2,4]:
    #                 buy_result =BUY.buy_items(self.device, ['符石阻斷劑','神力水晶','神力水晶','覺醒捲軸','神燈金鑰','鑽石金鑰','焚焰金鑰'],buy_duplicates=True)
    #             else:
    #                 buy_result =BUY.buy_items(self.device, ['神力水晶','神力水晶','覺醒捲軸','神燈金鑰','鑽石金鑰','焚焰金鑰'],buy_duplicates=True)
    #         else:
    #             buy_result =BUY.buy_items(self.device, ['神羽幣', '符石阻斷劑','神力水晶','神力水晶','覺醒捲軸','神燈金鑰','鑽石金鑰','焚焰金鑰'],buy_duplicates=True)
    #     found = len(buy_result['bought'])
    #     # for img_path in current_imglist:
    #         # img_name = os.path.splitext(os.path.basename(img_path))[0]
    #         # attempt = 0
    #         # while (time.time() - start < 300):
    #         #     screen = self.capture_screenshot()
    #         #     want_to_buy = cv2.imread(img_path)
    #         #     res = cv2.matchTemplate(
    #         #         screen, want_to_buy, cv2.TM_CCOEFF_NORMED)
    #         #     min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    #         #     h, w = want_to_buy.shape[:-1]
    #         #     top_left = max_loc
    #         #     # 儲存匹配區塊
    #         #     match_crop = screen[top_left[1]:top_left[1]+h, top_left[0]:top_left[0]+w]
    #         #     save_path = f"buy_results/{img_name}_try{attempt+1}_score_{max_val:.2f}.jpg"
    #         #     if match_crop.shape[0] > 0 and match_crop.shape[1] > 0:
    #         #         cv2.imwrite(save_path, match_crop)
                
    #         #     if max_val > 0.8 and top_left[1] < 650:
    #         #         print('Found item:', img_path)
    #         #         self.device.click(top_left[0] + w / 2, top_left[1] + 120)
    #         #         time.sleep(1)
    #         #         self.device.click(262, 552)
    #         #         time.sleep(2)
    #         #         self.device.click(483, 145)
    #         #         time.sleep(2)
    #         #         found += 1
    #         #         break
    #         #     else:
    #         #         print('Not found', err)
    #         #         time.sleep(1)
    #         #         self.device.swipe(0.5, 0.8, 0.5, 0.67, 0.5)
    #         #         self.device.click(273, 773)
    #         #         err += 1
    #         #         attempt += 1
    #         #         if err > 6:
    #         #             for _ in range(5):
    #         #                 self.device.swipe(0.5, 0.3, 0.5, 0.9, 0.05)
    #         #                 time.sleep(1)
    #         #             break
                
    #         # err = 0
    #     self.device.click(273, 844)
    #     time.sleep(2)
    #     return found
    def attack(self):
        time_manager = create_time_manager(device_id=self.device_ip)
        seed_record = time_manager.get_time_record("attack_snow_countury")

        is_next_week = True
        if seed_record:
            is_next_week = seed_record.get("is_next_week")

            if is_next_week is None:
                timestamp = seed_record.get("timestamp")
                if timestamp:
                    try:
                        recorded_dt = datetime.datetime.fromtimestamp(timestamp, self.taiwan_tz)
                        recorded_year_week = recorded_dt.date().isocalendar()[:2]
                        current_year_week = self.get_taiwan_now().date().isocalendar()[:2]
                        is_next_week = recorded_year_week != current_year_week
                    except (OSError, OverflowError, ValueError, TypeError):
                        is_next_week = True
                else:
                    is_next_week = True

        should_buy_seed = seed_record is None or is_next_week
        if should_buy_seed:
            print("跨週或首次攻擊，需要購買種子")
        else:
            print("仍在同一週，不需要購買種子")

        return should_buy_seed
    # def main_buy(self):
    #     """
    #     主逻辑：检查是否需要购买并记录购买次数。
    #     """
    #     modes = [
    #         ('daily', self.daily_imglist),
    #         ('weekly', self.weekly_imglist),
    #     ]

    #     for mode, img_list in modes:
    #         try:
    #             evaluation = self._evaluate_purchase_state(mode, len(img_list))
    #         except Exception as e:
    #             print(f"Error while evaluating {mode} purchase for {self.device_ip}: {e}")
    #             evaluation = {"should_buy": True, "effective_buy_num": 0}

    #         if evaluation.get("should_buy", True):
    #             previous_buy_num = evaluation.get("effective_buy_num", 0)
    #             new_buy_num = self.buy(mode) + previous_buy_num
    #             self.record(mode=mode, buy_num=new_buy_num)
    #         else:
    #             print(f"Skip {mode} buying for {self.device_ip}.")
    # 
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
        new_battle.fight_snow_country(self.device, self.device_ip)
        
        self.device.click(391, 938)
        time.sleep(3)
        
        # 記錄執行時間，重置冷卻
        time_manager.record_time("go_to_family_cooldown")