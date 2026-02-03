from device import device
import mask
import cv2
import numpy as np
import time
import uiautomator2 as u2
import img_tools
from json_manager import create_store_manager
import datetime

class spin_wheel(device):
    def __init__(self, device: u2.Device,cnn_model=None,devices_serial=None):
        self.device = device
        self.cnn_model = cnn_model
        self.devices_serial = devices_serial
    def gold_check(self):
        # if "emulator" not in self.devices_serial and '7fe98fc6' not in self.devices_serial:
        #     self.device.click(39,80)
        # else:
        #     self.device.click(39,40)
        # time.sleep(1)
        img = self.capture_screenshot()
        img = img[327:405, 61:90]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 紅色
        mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
        if np.sum(mask_img) > 10000 and np.sum(mask_img) < 20000:
            return True
        return False
    def gold_send(self):
            img_tools.click_str_by_server(self.device,'好友',y_range=(327,405))
            time.sleep(1)
            img_tools.click_str_by_server(self.device,'一键领取',y_range=(660,715))
            time.sleep(1.5)
            img_tools.click_str_by_server(self.device,'恭喜獲得',y_range=(0,400))
            time.sleep(1)
            self.device.click(272,843)

    def spin_and_send_gold(self):
        # 先用 JSON 判斷是否需要執行，避免不必要的 UI 點擊
        serial = self.devices_serial or 'unknown'
        store_manager = create_store_manager(serial)

        # 判斷 gold_send 是否今日已做，以及 spin 成功是否已超過每日上限
        try:
            gold_done = False
            try:
                gold_done = store_manager.is_purchased_today("spin_gold_send", period="day")
            except Exception:
                # 若檢查失敗，保守視為尚未做
                gold_done = False

            # 取得今日 spin 計數
            title_cnt = "spin_and_send_count"
            rec = store_manager.get_purchase_record(title_cnt) or {}
            try:
                last_dt = store_manager._extract_last_time_as_local(rec) if rec else None
            except Exception:
                last_dt = None
            try:
                today_local = datetime.datetime.now(store_manager.timezone).date()
            except Exception:
                today_local = datetime.datetime.now().date()

            if last_dt is None or last_dt.date() != today_local:
                curr_count = 0
            else:
                try:
                    curr_count = int(rec.get("count", 0))
                except Exception:
                    curr_count = 0

            spin_allowed = curr_count < 10

            # 若既不需要 gold_send，也不允許 spin（次數已滿），則不點擊 UI，直接回傳
            if gold_done is True and not spin_allowed:
                print("今日 gold_send 已執行且 spin 次數達上限，略過不執行")
                return False
        except Exception as e:
            print(f"檢查 JSON 狀態時發生錯誤，將繼續執行以避免漏掉操作: {e}")

        # 若判斷需要執行，才進行 UI 點擊與後續流程
        if "emulator" not in self.devices_serial and '7fe98fc6' not in self.devices_serial:
            self.device.click(39,80)
        else:
            self.device.click(39,40)
        time.sleep(1)

        img = self.capture_screenshot()
        # gold_send 每天只執行一次（此處再檢查畫面與 JSON）
        if self.gold_check():
            try:
                if not store_manager.is_purchased_today("spin_gold_send", period="day"):
                    self.gold_send()
                    try:
                        store_manager.record_purchase("spin_gold_send", {"purchased": True})
                    except Exception:
                        pass
                else:
                    print("今日已執行 gold_send，跳過")
            except Exception:
                print("檢查是否今日已執行 gold_send 時發生錯誤，跳過執行以避免重複")

        img = img[736:905, 61:90]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 紅色
        mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
        if np.sum(mask_img) > 10000 and np.sum(mask_img) < 20000:
            # self.device.click(60, 755)
            img_tools.click_str_by_server(self.device,'轉盤',y_range=(657,900))
            time.sleep(1)
            self.device.click(272, 613)
            time.sleep(5)
            for i in range(3):
                self.device.click(272, 718)
                time.sleep(1)
            # 每天允許最多 10 次成功的 spin_and_send_gold（計數儲存在 store_manager）
            try:
                title_cnt = "spin_and_send_count"
                rec = store_manager.get_purchase_record(title_cnt) or {}
                # 使用 StoreDataManager 的內部解析工具取得本地時間（若可用）
                try:
                    last_dt = store_manager._extract_last_time_as_local(rec) if rec else None
                except Exception:
                    last_dt = None

                try:
                    today_local = datetime.datetime.now(store_manager.timezone).date()
                except Exception:
                    today_local = datetime.datetime.now().date()

                if last_dt is None or last_dt.date() != today_local:
                    curr_count = 0
                else:
                    try:
                        curr_count = int(rec.get("count", 0))
                    except Exception:
                        curr_count = 0

                if curr_count >= 10:
                    print("今日已達 spin_and_send 上限 10 次，略過此次回傳")
                    return False

                curr_count += 1
                try:
                    store_manager.record_purchase(title_cnt, {"count": curr_count})
                except Exception:
                    # 回退：直接 load/save
                    try:
                        data = store_manager.load_data()
                        rec2 = data.get(title_cnt, {})
                        rec2.update({"count": curr_count})
                        data[title_cnt] = rec2
                        store_manager.save_data(data)
                    except Exception:
                        pass
            except Exception as e:
                print(f"更新 spin_and_send 記錄失敗: {e}")
            return True
        return False
if __name__ == "__main__":
    d = u2.connect_usb('7fe98fc6')
    sw = spin_wheel(d,devices_serial='7fe98fc6')
    print(sw.spin_and_send_gold()
)