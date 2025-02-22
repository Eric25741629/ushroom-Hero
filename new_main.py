

from oralce_manger import oralce
import easyocr
import point
import uiautomator2 as u2
import time
import numpy as np
import cv2
import mask
from Skill import *
from park import *
from family import Family_manager
import new_battle
import random
from Spin_Wheel import spin_wheel
from Mission import mission
import red_envelope
from State import state
from Assistant import assistant


def Martial_Soul(d):
    img = d.screenshot(format='opencv')[472:538, 455:528]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower = mask.red_mask_lower
    upper = mask.red_mask_upper
    mask1 = cv2.inRange(hsv, lower, upper)
    # 計算面積
    contours, _ = cv2.findContours(
        mask1, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    num_boxes = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 50:
            num_boxes += 1
    if num_boxes > 0:
        return True
    return False


def Martial_Soul2(d):
    img = d.screenshot(format='opencv')[760:791, 483:525]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower = mask.red_mask_lower
    upper = mask.red_mask_upper
    mask1 = cv2.inRange(hsv, lower, upper)
    # 計算面積
    contours, _ = cv2.findContours(
        mask1, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    num_boxes = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 50:
            num_boxes += 1
    if num_boxes > 0:
        return True
    return False


def get_Martial_Soul(d):
    if Martial_Soul(d):
        d.click(495, 512)
        time.sleep(3)
        if Martial_Soul2(d):
            d.click(451, 792)
            time.sleep(3)
            d.click(289, 272)
            time.sleep(3)
            d.click(474, 254)
            time.sleep(3)
            d.click(55, 48)
            time.sleep(3)
            d.click(486, 908)
            time.sleep(3)
        d.click(271, 883)
        time.sleep(3)


def reward(d, easyocr_reader):
    d.click(162, 725)
    time.sleep(5)
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img, detail=0)
    if "領取" in result or "放置獎勵" in result:
        print(img[328, 135])
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            click_white(d)
            time.sleep(1)
        d.click(330, 725)
        time.sleep(5)
        click_white(d)
        time.sleep(1)
    click_white(d)


def stage_by_str(ocr_str):
    if "公告" in ocr_str:
        return "公告"
    if "方案" in ocr_str:
        return "主頁面"
    if "放置獎勵" in ocr_str or "離線獎勵" in ocr_str:
        return "放置獎勵"
    if '家族商店' in ocr_str or '家族亂鬥' in ocr_str or '鬱鬱胖頭魚' in ocr_str:
        return "家族"
    if "征戰熔岩巨獸" in ocr_str and "掃蕩" in ocr_str:
        return "征戰熔岩巨獸"
    return "未知"


def click_str(str1: str, d, easyocr_reader):
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img)
    for i in result:
        if str1 in str(i[1]):
            [x1, x2, x3, x4] = i[0]
            center = [int((x1[0]+x3[0])/2), int((x1[1]+x3[1])/2)]
            d.click(center[0], center[1])
            return True
    return False


boss_time = 0
reward_time = 0
err = 0
other_time = 0
one_day_action = 0


def unlock(d):
    d.swipe(0.05, 0.7, 0.9, 0.7, 0.05)


seed_timme = 0
check = True
# 檢測當前應用


def check_in_game(d):
    if d.app_current().get("package") == "com.mxdzz.tw.and":
        return True
    else:
        return False


def find_and_click(d, findImgPath, threshold=0.8, x=0, y=0):
    img = d.screenshot(format='opencv')
    findImg = cv2.imread(findImgPath)
    res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    if len(loc[0]) > 0:
        center = [int(loc[1][0] + findImg.shape[1] / 2),
                  int(loc[0][0] + findImg.shape[0] / 2)]
        d.click(center[0] + x, center[1] + y)
        return True
    else:
        return False


def farm(d):
    d.click(321, 920)
    time.sleep(5)
    d.click(208, 584)
    time.sleep(5)
    img = d.screenshot(format='opencv')[780:864, :]
    start = time.time()
    while (time.time()-start < 60):
        if find_and_click(d, r'getting.jpg'):
            time.sleep(7)
        elif find_and_click(d, r'get_all.jpg'):
            time.sleep(3)
        elif find_and_click(d, r'plants.jpg'):
            time.sleep(2)
            img = d.screenshot(format='opencv')
            target_sum = sum([173, 112, 68])
            pixel_sum = sum(img[437, 199])
            if (abs(pixel_sum - target_sum) <= 5):
                d.click(199, 437)
                time.sleep(2)
                d.click(126, 588)
                time.sleep(1)
                d.click(165, 460)
                time.sleep(1)
            if find_and_click(d, r'put.jpg'):
                time.sleep(5)
        else:
            break
    d.click(480, 929)
    time.sleep(4)
    d.click(321, 920)
    time.sleep(3)


def new_stage_check(img):
    if [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]:
        return True
    return False


def oralce(d, easyocr_reader):
    d.click(321, 919)
    retry = 0
    while (retry < 5):
        img = d.screenshot(format='opencv')
        conditions = [abs(np.sum(img[502, 284]) - np.sum([228, 217, 59])) <= 10, abs(np.sum(img[511, 79]) - np.sum([186, 154, 47])) <= 10, abs(np.sum(img[620, 87]) - np.sum([87, 166, 205])) <= 10, abs(np.sum(img[377, 384]) - np.sum([91, 120, 157])) <=
                      10, abs(np.sum(img[239, 263]) - np.sum([95, 152, 119])) <= 10, abs(np.sum(img[71, 166]) - np.sum([102, 122, 103])) <= 10, abs(np.sum(img[810, 82]) - np.sum([157, 203, 244])) <= 10, abs(np.sum(img[798, 322]) - np.sum([190, 186, 54])) <= 10]
        if all(conditions):
            break
        retry += 1
        click_white(d)
    if retry == 5:
        return
    time.sleep(1)
    d.click(101, 158)
    time.sleep(3)
    click_white(d)
    d.click(500, 174)
    time.sleep(3)
    d.click(272, 752)
    time.sleep(3)
    click_str("確定", d, easyocr_reader)
    time.sleep(3)
    click_white(d)
    click_white(d)
    d.click(500, 913)
    time.sleep(3)
    d.click(321, 919)
    time.sleep(3)

def get_stage(d, easyocr_reader):
    """ 截圖並判斷目前所在的頁面 """
    img = d.screenshot(format='opencv')
    if new_stage_check(img):
        print("新方法")
        return "主頁面"
    result = easyocr_reader.readtext(img, detail=0)
    return stage_by_str(result)
import os
import json
import time
from datetime import datetime, timezone, timedelta

# 設定台灣時區
TPE = timezone(timedelta(hours=8))

def time_recording(ip, name=''):
    filename = ip + "test.json"

    # 取得當前台灣時間
    now = datetime.now(TPE)
    current_time = time.time()
    current_date = now.strftime("%Y-%m-%d")  # 以 YYYY-MM-DD 格式存儲日期

    # 如果檔案不存在，建立一個空的資料字典
    if not os.path.exists(filename):
        data = {}
    else:
        with open(filename, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

    # 以 name 為鍵，存儲時間戳記與日期
    data[name] = {
        "timestamp": current_time,
        "date": current_date
    }

    # 將更新後的資料寫回檔案
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)


def return_time(ip, name=''):
    filename = ip + "test.json"

    if not os.path.exists(filename):
        return None  # 檔案不存在，直接回傳 None

    with open(filename, "r") as f:
        try:
            data = json.load(f)  # 讀取 JSON
        except json.JSONDecodeError:
            return None  # JSON 格式錯誤，直接回傳 None

    record = data.get(name)

    if not record:
        return None  # 如果 key 不存在，回傳 None

    # **修正錯誤：確保 record 是 dict**
    if isinstance(record, float):
        # **如果 record 是 float，表示舊格式，轉換為新的格式**
        return {
            "timestamp": record,
            "recorded_date": datetime.fromtimestamp(record, TPE).strftime("%Y-%m-%d"),
            "is_next_day": False  # 舊資料無法判斷跨日，預設為 False
        }

    if isinstance(record, dict):
        # **新版格式**
        timestamp = record.get("timestamp")
        recorded_date = record.get("date")

        if not timestamp or not recorded_date:
            return None  # 若有任何欄位缺失，則回傳 None

        # 取得當前日期
        current_date = datetime.now(TPE).strftime("%Y-%m-%d")
        is_next_day = (recorded_date != current_date)  # 是否跨日

        return {
            "timestamp": timestamp,
            "recorded_date": recorded_date,
            "is_next_day": is_next_day
        }

    return None  # 若格式不符，回傳 None
import time
from datetime import datetime, timezone, timedelta

# 設定台灣時區
TPE = timezone(timedelta(hours=8))

def is_expired(last_park_time, expired_time=60 * 60 * 3 +  55*60 ):
    # 計算當前時間
    now = time.time()

    # 計算是否超過 3 小時 30 分鐘
    time_exceeded = (now - last_park_time) > expired_time

    # 取得台灣當前日期
    current_date = datetime.now(TPE).strftime("%Y-%m-%d")
    recorded_date = datetime.fromtimestamp(last_park_time, TPE).strftime("%Y-%m-%d")

    # 判斷是否跨日
    is_next_day = (recorded_date != current_date)

    # 只要符合其中一個條件就回傳 True
    return time_exceeded or is_next_day

def main(ip, easyocr_reader: easyocr.Reader):
    d = u2.connect(ip)
    manager = ParkingManager(device=d, reader=easyocr_reader, ip=ip)
    battle_manager = new_battle.BattleManager(device=d, reader=easyocr_reader)
    wheel_manager = spin_wheel(device=d)
    mission_manager = mission(device=d, ip=ip)
    family_manager = Family_manager(device=d, ip=ip)
    state_manager = state(device=d)
    assistant_manager = assistant(d=d)
    while (1):
        start = time.time()
        img = d.screenshot(format='opencv')
        # 進行ocr
        if state_manager.get_state() == "滑動解除節電模式'":
            unlock(d)
        if check_in_game(d):
            print("in game")
        else:
            print("not in game")
            d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(20)
            wait_time = time.time()
            while (1):
                if stage_by_str(easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "主頁面" or stage_by_str(easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "公告" or stage_by_str(easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "放置獎勵" or stage_by_str(easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "家族" or stage_by_str(easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "離線獎勵":
                    break
                time.sleep(1)
                if time.time()-wait_time > 60:
                    d.app_stop("com.mxdzz.tw.and")
                    d.app_start(package_name="com.mxdzz.tw.and",
                                use_monkey=True)
                    time.sleep(30)
                    wait_time = time.time()
            # 使用ocr檢測文字
        img = d.screenshot(format='opencv')
        # 進行ocr
        result = easyocr_reader.readtext(img, detail=0)
        if "你的帳號在另一個地方登錄" in result or "退出遊戲" in result:
            click_str("退出遊戲", d, easyocr_reader)
            time.sleep(5)
            click_str("確認登出", d, easyocr_reader)
            end = time.time()
            while (1):
                time.sleep(1)
                if time.time()-end > 60*28:
                    break
                print(time.time()-end)
            d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(30)

        elif "公告" in result:
            d.click(248, 812)
            time.sleep(1)
            click_white(d)
            time.sleep(1)
        img = d.screenshot(format='opencv')
        if state_manager.get_state() == "放置獎勵":
            print('new method')
            reward(d, easyocr_reader)
        else:
            # 仅在状态不符合时再进行 OCR 检测
            result = easyocr_reader.readtext(img, detail=0)
            if any(keyword in result for keyword in ["放置獎勵", "離線獎勵"]):
                reward(d, easyocr_reader)
        img = d.screenshot(format='opencv')
        if red_envelope.check_red_in_pic(img):
            red_envelope.open_red_envelope(d)
        current_time = time.localtime()
        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面":
            d.click(random.randint(261, 271), 369)  # 點擊寶箱
            time.sleep(1)
            reward(d, easyocr_reader)

        if current_time.tm_hour % 4 == 0 and current_time.tm_hour != 0 or current_time.tm_hour == 23:
            stage = stage_by_str(result)
            if stage == "主頁面":
                family_manager.go_to_family()

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面" and current_time.tm_hour % 8 == 0:
            oralce(d, easyocr_reader)

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面":
            farm(d)

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面":
            get_Martial_Soul(d)
            time.sleep(3)

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面" and ip != "emulator-5568":
            get_skill_and_partner(d)
            time.sleep(3)

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面" and current_time.tm_hour % 4 == 0:
            assistant_manager.go_to_get_assistant()

        # 結束com.mxdzz.tw.and
        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面":
            last_park_time = return_time(ip, name="park")
            if last_park_time is None:
                last_park_time = 0
            else:
                last_park_time = last_park_time["timestamp"]
            if is_expired(last_park_time):
                while (time.time()-last_park_time < 60*60*4 and  datetime.now(TPE).hour != 0):
                    print("waiting for {} seconds".format(60*60*4 - (time.time()-last_park_time)))
                    time.sleep(10)
                    click_white(d)
                manager.check_and_park()
                time_recording(ip, name="park")

        if current_time.tm_hour % 4 == 0 or current_time.tm_hour == 23:
            stage=get_stage(d, easyocr_reader)
            if stage == "主頁面":
                d.click(228, 926)
                time.sleep(2)
                battle_manager.execute_all_battles(check=True)

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面" and current_time.tm_hour == 23 or current_time.tm_hour == 20:
            mission_manager.do_allmission()

        stage=get_stage(d, easyocr_reader)
        if stage == "主頁面" and ip != "emulator-5568":
            wheel_manager.spin()

        d.app_stop("com.mxdzz.tw.and")
        end = time.time()
        last_park_time = return_time(ip, name="park")
        if last_park_time is None:
            last_park_time = 0
        else:
            last_park_time = last_park_time["timestamp"]

        while True:
            time.sleep(10)
            current_time = time.localtime()

            # 當 ip 不是 "emulator-5568" 時：
            #   若整點 (tm_min == 0) 或 23:45 時就跳出迴圈
            if ip != "emulator-5568":
                if current_time.tm_min == 0 or (current_time.tm_hour == 23 and current_time.tm_min == 45):
                    break

            # 當 ip 是 "emulator-5568" 時：
            #   1. 若整點且小時為 4 的倍數，
            #   2. 或 23:45，
            #   3. 或 小時小於 8 且整點 (tm_min == 0)
            # 則跳出迴圈

            else:
                if ((current_time.tm_min == 0 and current_time.tm_hour % 4 == 0) or
                    (current_time.tm_hour == 23 and current_time.tm_min == 45) or
                    (current_time.tm_hour < 8 and current_time.tm_min == 0)or
                    (is_expired(last_park_time, expired_time=60 * 60 * 3 + 59*60) and current_time.tm_hour > 8)):
                    break


easyocr_reader = easyocr.Reader(['ch_tra', 'en'])

if __name__ == "__main__":
    # d = u2.connect('emulator-5560')
    d_list = ['emulator-5562', 'emulator-5560', 'emulator-5568','emulator-5554']
    # 使用多執行緒
    # d_list = ['emulator-5568']
    import threading
    threads = []
    for i in d_list:
        threads.append(threading.Thread(target=main, args=(i, easyocr_reader)))
    for i in threads:
        i.start()
    # for i in d_list:
    #     d = u2.connect(i)
    #     if d.app_current().get("package") == "com.mxdzz.tw.and":
    #         break

    # result = easyocr_reader.readtext(img, detail=0)
    # stage = stage_by_str(result)
    # if stage == "家族":
    #     d.click(401,914)
    #     continue
    # if stage == "未知":
    #     click_white()
    #     err += 1
    #     continue

    # if time.time()-reward_time> 7200 and stage =="主頁面":
    #     d.click(261,369)
    #     time.sleep(1)
    #     reward()
    #     print('領取完畢')
    #     time.sleep(1)
    #     reward_time = time.time()
    # if stage == "主頁面" and time.time() - other_time > 7200:
    #     d.click(216,913)
    #     while(1):
    #         img = d.screenshot(format='opencv')
    #         if list(img[56,204])==[ 55  ,70 ,199]:
    #             break
    #     all_battle_instance(check=check)
    #     other_time = time.time()
    #     d.click(216,913)
    #     check = True
    #     time.sleep(1)
    # if time.time() - one_day_action > 86400 and stage == "主頁面":
    #     skill_and_partner()

    #     one_day_action = time.time()
    #     check = False
    #     time.sleep(1)
    #     stage = point.find_by_point(d)
    #     print(stage)
    #     if stage =="main_page":
    #         oralce()
    #         time.sleep(5)
    #     if stage =="main_page":
    #         mission()
# 8c7539351a4194cc6a27dcc746035a9715073520f84031f6
    # if stage == "未知":
    #     click_white()
    #     err += 1
    #     continue
    # #三個小時檢查一次

    # if time.time() - seed_timme > 3600 and stage == "主頁面":
    #     d.click(321,920)
    #     time.sleep(5)
    #     d.click(208,584)
    #     time.sleep(5)
    #     if click_str("採摘"):
    #         time.sleep(6)
    #         d.click(68,810)
    #         time.sleep(3)
    #         d.click(68,810)
    #         time.sleep(3)
    #         if (click_str("鍵種植")):
    #             pass
    #         else:
    #             d.click(250,561)
    #         time.sleep(3)
    #     d.click(470,920)
    #     time.sleep(5)
    #     d.click(321,920)

    # stage = point.find_by_point(d)
    # if stage =="main_page":
    #     pass
    # elif stage == "homeland":
    #     pass
    # elif stage == "skill_page":
    #     pass
    # elif stage == "partner_page":
    #     pass
    # elif stage == "ore":
    #     pass
    # elif stage == "follower":
    #     pass
    # elif stage == "garden":
    #     pass
    # elif stage == "park":
    #     pass
    # elif stage == "instance":
    #     pass
    # elif stage == "unknown":
    #     pass
    # time.sleep(1*60)
