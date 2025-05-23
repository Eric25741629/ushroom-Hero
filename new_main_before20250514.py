import subprocess

from requests import get
import miner
import torch
import os
from adb_devices import *
import datetime
import json
import miner.simplecnn
# from oralce_manger import oralce
import easyocr
import point
import uiautomator2 as u2
import time
import numpy as np
from device import get_adb_devices
import cv2
import mask
from Skill import *
from park import *
from family import Family_manager
import new_battle
import random
from Spin_Wheel import spin_wheel
from Mission import mission
from State import state
from Assistant import assistant
import cnn_model
from miner.Mining import MiningPlanner
from cnn_model import ClassName_cnn_model
import logging
import pytz
global lock
lock = False

ADB = "adb"
def Martial_Soul(d):
    img = d.screenshot(format='opencv')[472:590, 455:528]
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
        d.click(495, 582)
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
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            cv2.imwrite(
                "reward_get/reward_get_{}.jpg".format(time.time()), img)
            click_white(d)
            time.sleep(1)
        d.click(330, 725)
        time.sleep(5)
        click_white(d)
        time.sleep(1)
    click_white(d)


def stage_by_str(d, ocr_str):
    if "公告" in ocr_str:
        img = d.screenshot(format='opencv')
        if not os.path.exists("announcement"):
            os.makedirs("announcement")
        cv2.imwrite("announcement/announcement_{}.jpg".format(time.time()), img)
        return "公告"
    if "方案" in ocr_str:
        img = d.screenshot(format='opencv')
        if not os.path.exists("main"):
            os.makedirs("main")
        cv2.imwrite("main/main_{}.jpg".format(time.time()), img)
        return "主頁面"
    if "放置獎勵" in ocr_str or "離線獎勵" in ocr_str:
        img = d.screenshot(format='opencv')
        if not os.path.exists("reward"):
            os.makedirs("reward")
        cv2.imwrite("reward/reward_{}.jpg".format(time.time()), img)
        return "放置獎勵"
    if '家族商店' in ocr_str or '家族亂鬥' in ocr_str or '鬱鬱胖頭魚' in ocr_str:
        img = d.screenshot(format='opencv')
        if not os.path.exists("family"):
            os.makedirs("family")
        return "家族"
    if "征戰熔岩巨獸" in ocr_str and "掃蕩" in ocr_str:
        if not os.path.exists("boss"):
            os.makedirs("boss")
        cv2.imwrite("boss/boss_{}.jpg".format(time.time()), img)
        return "征戰熔岩巨獸"
    return "未知"


def click_str(str1: str, d, easyocr_reader):
    img = d.screenshot(format='opencv')
    if not os.path.exists("other_str"):
        os.makedirs("other_str")
    cv2.imwrite("other_str/other_str_{}.jpg".format(time.time()), img)
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
    if not os.path.exists("find_img"):
        os.makedirs("find_img")
    # cv2.imwrite("find_img/find_img_{}.jpg".format(time.time()), img)

    findImg = cv2.imread(findImgPath)
    res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
    loc = np.where(res >= threshold)
    if len(loc[0]) > 0:
        center = [int(loc[1][0] + findImg.shape[1] / 2),
                  int(loc[0][0] + findImg.shape[0] / 2)]
        d.click(center[0] + x, center[1] + y)
        print(center[0] + x, center[1] + y)
        return True
    else:
        return False


def buy_seed(d):

    d.click(375, 915)
    time.sleep(2)
    d.click(160, 428)
    time.sleep(0.5)
    for i in range(3):
        d.click(335+random.randint(-5, 5), 465+random.randint(-5, 5))
    time.sleep(0.5)
    d.click(273, 558)
    time.sleep(0.5)
    d.click(475, 800)
    time.sleep(0.5)
    d.click(384, 428)
    time.sleep(0.5)
    for i in range(2):
        d.click(335+random.randint(-5, 5), 465+random.randint(-5, 5))
    time.sleep(0.5)
    d.click(273, 558)
    time.sleep(0.5)
    d.click(475, 800)
    click_white(d)
    click_white(d)
    time.sleep(3)


def farm_card(d: u2.Device):
    # 當星期 一 三 五 的時候 執行
    return
    if find_and_click(d, r'getting.jpg'):
        time.sleep(7)
    if find_and_click(d, r'get_all.jpg'):
        time.sleep(3)
    d.click(480, 929)
    time.sleep(3)
    # 前往車廠 進行購買
    d.click(470, 446)
    time.sleep(3)
    d.click(400, 910)
    time.sleep(2)
    start_time = time.time()
    want_to_buy = cv2.imread("farm_card.jpg")
    while (time.time() - start_time < 300):  # 限時 5 分鐘
        img = d.screenshot(format='opencv')
        res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        h, w = want_to_buy.shape[:-1]
        top_left = max_loc
        if max_val > 0.9 and top_left[1] < 552:
            d.click(
                top_left[0] + w//2, top_left[1] + h//2 + 100)
            time.sleep(2)
            d.click(277, 551)
            time.sleep(2)
            d.click(514, 16)  # 空白处
            time.sleep(2)
            break
        else:
            d.swipe(0.5, 0.8, 0.5, 0.6, 1)
            d.click(273, 773)
    d.click(380, 926)
    time.sleep(1.5)
    d.click(471, 915)
    time.sleep(3)
    d.click(210, 690)
    time.sleep(3)
    # 前往農場連續種植30次
    corrent = 0
    while (corrent < 5):
        if not find_and_click(d, r'plants.jpg'):
            continue
        time.sleep(1.2)
        d.click(383, 442)
        time.sleep(1.2)
        d.click(267, 564)
        time.sleep(6)
        d.click(73, 823)  # 施肥
        time.sleep(2)
        d.click(365, 440)  # 確認肥料
        time.sleep(2)
        d.click(276, 606)  # 使用
        time.sleep(2.2)
        d.click(73, 823)  # getting
        time.sleep(8)
        d.click(73, 823)  # get all
        time.sleep(2.2)
        corrent += 1


def farm(d, ip, Cnn_model):
    d.click(321, 920)
    save_time = 0

    try:
        cnn_s = time.time()
        while (1):
            img = d.screenshot(format='opencv')
            if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "homeplace":
                cnn_p = time.time()
                print("save time {}".format(5-(cnn_p-cnn_s)))
                save_time += 5-(cnn_p-cnn_s)
                break
            if time.time()-cnn_s > 60:
                break
    except:
        time.sleep(5)
    if not os.path.exists("homeplace"):
        os.makedirs("homeplace")
    cv2.imwrite("homeplace/homeplace_{}.jpg".format(time.time()),
                d.screenshot(format='opencv'))
    d.click(208, 584)
    time.sleep(5)
    if not os.path.exists("farm"):
        os.makedirs("farm")
    cv2.imwrite("farm/farm{}.jpg".format(time.time()),
                d.screenshot(format='opencv'))
    img = d.screenshot(format='opencv')[780:864, :]
    start = time.time()
    current_time = time.localtime()
    if current_time.tm_hour == 0:
        buy_seed(d)
    date = time.localtime()
    # if (date.tm_wday == 1 or date.tm_wday == 3 or date.tm_wday == 5) and date.tm_hour == 2:
    #     farm_card(d)
    while (time.time()-start < 30):
        if find_and_click(d, r'getting.jpg'):
            time.sleep(7)
        elif find_and_click(d, r'get_all.jpg'):
            time.sleep(3)

        elif current_time.tm_hour < 12 or ip == "emulator-5568" and current_time.tm_hour < 12:
            if find_and_click(d, r'plants.jpg'):
                time.sleep(2)
                img = d.screenshot(format='opencv')
                target_sum = sum([173, 112, 68])
                pixel_sum = sum(int(x) for x in img[437, 199])
                if (abs(pixel_sum - target_sum) <= 5):
                    d.click(199, 437)
                    time.sleep(2)
                    d.click(126, 588)
                    time.sleep(1)
                    d.click(165, 460)
                    time.sleep(1)
                if find_and_click(d, r'put.jpg'):
                    time.sleep(5)
        elif current_time.tm_hour > 12 and ip != "emulator-5568":
            if find_and_click(d, r'plants.jpg'):
                time.sleep(2)
                img = d.screenshot(format='opencv')
                target_sum = sum([173, 112, 68])
                pixel_sum = sum(int(x) for x in img[437, 199])
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
    try:
        cnn_s = time.time()
        while (1):
            if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "main":
                cnn_p = time.time()
                print("save time {}".format(3-(cnn_p-cnn_s)))
                save_time += 3-(cnn_p-cnn_s)
                break
            if time.time()-cnn_s > 60:
                break
    except:
        time.sleep(3)
    return save_time


def new_stage_check(img):
    if [abs(np.sum(img[955, 535]) - np.sum([47, 138, 123])) <= 10, abs(np.sum(img[902, 39]) - np.sum([146, 232, 232])) <= 10, abs(np.sum(img[956, 6]) - np.sum([50, 140, 117])) <= 10, abs(np.sum(img[921, 135]) - np.sum([41, 21, 218])) <= 10, abs(np.sum(img[908, 223]) - np.sum([160, 165, 164])) <= 10, abs(np.sum(img[731, 27]) - np.sum([139, 170, 201])) <= 10, abs(np.sum(img[759, 30]) - np.sum([111, 143, 179])) <= 10, abs(np.sum(img[794, 37]) - np.sum([38, 60, 88])) <= 10, abs(np.sum(img[825, 380]) - np.sum([37, 58, 86])) <= 10]:
        return True
    return False


def oralce(d: u2.Device, easyocr_reader, oralce_Planner: MiningPlanner, ip):
    d.click(321, 919)
    retry = 0
    while (retry < 5):
        img = d.screenshot(format='opencv')
        # 顏色檢測 - 家園
        # color check - homeland
        conditions = [abs(np.sum(img[502, 284]) - np.sum([228, 217, 59])) <= 10, abs(np.sum(img[511, 79]) - np.sum([186, 154, 47])) <= 10, abs(np.sum(img[620, 87]) - np.sum([87, 166, 205])) <= 10, abs(np.sum(img[377, 384]) - np.sum([91, 120, 157])) <=
                      10, abs(np.sum(img[239, 263]) - np.sum([95, 152, 119])) <= 10, abs(np.sum(img[71, 166]) - np.sum([102, 122, 103])) <= 10, abs(np.sum(img[810, 82]) - np.sum([157, 203, 244])) <= 10, abs(np.sum(img[798, 322]) - np.sum([190, 186, 54])) <= 10]
        if all(conditions):
            if not os.path.exists("homeland"):
                os.makedirs("homeland")
            cv2.imwrite("homeland/homeland{}.jpg".format(time.time()), img)
            break
        retry += 1
        click_white(d)
    if retry == 5:
        return
    time.sleep(1)
    d.click(101, 158)
    time.sleep(3)
    try:
        # raise Exception("test")
        cnn_s = time.time()
        for i in range(50):
            img = d.screenshot(format='opencv')[80:120, 126:300]
            results = easyocr_reader.readtext(img, detail=1)
            num_before_slash = int(results[0][1].split(
                '/')[0].rsplit(':', 1)[-1].strip())
            if num_before_slash <= 0:
                break
            time_stream = time.time()
            timestamp_str = "{:.6f}".format(
                time_stream).replace('.', '')  # 保留6位數秒，小數點去掉

            # 建立完整路徑
            filename = os.path.join(
                "A:/recording", f"{ip}_{timestamp_str}.jpg")
            print(f"檔案名稱: {filename}")
            # 儲存圖片
            d.screenshot(filename=filename)
            img = d.screenshot(format='opencv')
            board, moves, value = oralce_Planner.plan(
                img, r'A:\recording/{}_new2.jsonl'.format(ip), image_path=filename)
            last_dy = 0                      # 0 = 沒捲動；-1 = 向上位移 1 列
            prev_xy = None                   # 紀錄前一步

            for idx, move in enumerate(moves):        # idx = 0,1,2…
                x, y = move["origin"]
                times = move["repeat"]
                # 如上一輪挖在最底列且本輪不是同一格，代表畫面已經下捲
                if prev_xy is not None and prev_xy[1] == 6 and times != 2:
                    last_dy -= 1             # 所有 y 座標往上平移一格

                # 轉成螢幕座標
                img_x, img_y = oralce_Planner.board_to_image_coords(
                    x, y + last_dy, offset=(44, 44))

                print(
                    f"Dig {idx+1} at board ({x},{y}) → pixel ({img_x},{img_y})")
                for _ in range(times):
                    d.click(img_x, img_y)
                time.sleep(1+random.random())

                prev_xy = (x, y)
            else:
                print("無 3 步內路徑，建議挖底層捲動")
            time.sleep(4)

            if time.time()-cnn_s > 600:
                break
    except:
        if not os.path.exists("oralce"):
            os.makedirs("oralce")
        cv2.imwrite("oralce/oralce{}.jpg".format(time.time()),
                    d.screenshot(format='opencv'))
        click_white(d)
        if not os.path.exists("oralce"):
            os.makedirs("oralce")
        cv2.imwrite("oralce/oralce_{}.jpg".format(time.time()),
                    d.screenshot(format='opencv'))
        d.click(500, 174)
        time.sleep(3)
        if not os.path.exists("main5"):
            os.makedirs("main5")
        cv2.imwrite("main5/main5_{}.jpg".format(time.time()),
                    d.screenshot(format='opencv'))
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


def get_stage(d, Cnn_model, easyocr_reader):
    """ 截圖並判斷目前所在的頁面 """
    cnn_result = cnn_model.predict_image(
        Cnn_model, d.screenshot(format='pillow'))
    img = d.screenshot(format='opencv')
    if new_stage_check(img):
        print("新方法")
        # if not os.path.exists("main"):
        #     os.makedirs("main")
        # cv2.imwrite("main/main_{}.jpg".format(time.time()), img)
        if cnn_result != "main":
            if not os.path.exists("other_stage"):
                os.makedirs("other_stage")
            cv2.imwrite(
                "other_stage/other_stage_{}.jpg".format(time.time()), img)
        return "主頁面"
    result = easyocr_reader.readtext(img, detail=0)
    stage_withocr = stage_by_str(d, result)
    if cnn_result != stage_withocr:
        if not os.path.exists("other_stage"):
            os.makedirs("other_stage")
        cv2.imwrite("other_stage/other_stage_{}.jpg".format(time.time()), img)
    return stage_withocr


# 設定台灣時區
TPE = datetime.timezone(datetime.timedelta(hours=8))


def time_recording(ip, name=''):
    filename = ip + "test.json"

    # 取得當前台灣時間
    now = datetime.datetime.now(TPE)
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
            "recorded_date": datetime.datetime.fromtimestamp(record, TPE).strftime("%Y-%m-%d"),
            "is_next_day": False  # 舊資料無法判斷跨日，預設為 False
        }

    if isinstance(record, dict):
        # **新版格式**
        timestamp = record.get("timestamp")
        recorded_date = record.get("date")

        if not timestamp or not recorded_date:
            return None  # 若有任何欄位缺失，則回傳 None
        TPE = pytz.timezone('Asia/Taipei')
        # 取得當前日期
        current_date = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
        is_next_day = (recorded_date != current_date)  # 是否跨日

        return {
            "timestamp": timestamp,
            "recorded_date": recorded_date,
            "is_next_day": is_next_day
        }

    return None  # 若格式不符，回傳 None


# 設定台灣時區
TPE = datetime.timezone(datetime.timedelta(hours=8))


def is_expired(last_park_time, expired_time=60 * 60 * 3 + 55*60):
    # 計算當前時間
    now = time.time()

    # 計算是否超過 3 小時 30 分鐘
    time_exceeded = (now - last_park_time) > expired_time

    # 取得台灣當前日期
    current_date = datetime.datetime.now(TPE).strftime("%Y-%m-%d")
    recorded_date = datetime.datetime.fromtimestamp(
        last_park_time, TPE).strftime("%Y-%m-%d")

    # 判斷是否跨日
    is_next_day = (recorded_date != current_date)

    # 只要符合其中一個條件就回傳 True
    return time_exceeded or is_next_day


def check_on_line(Cnn_model, easyocr_reader):
    # 檢查是否在線上
    try:
        d = u2.connect('emulator-5554')
    except Exception as e:
        print("連線失敗:", e)
    d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)

    start_time = time.time()
    while (time.time() - start_time) < 60:
        try:
            screen_stage = cnn_model.predict_image(
                Cnn_model, d.screenshot(format='pillow'))
            print("目前頁面:", screen_stage)
            if screen_stage == "main":
                print("in game")
                time.sleep(5)
                break
            elif cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "reward":
                reward(d, easyocr_reader)
            else:
                print("not in game")
                time.sleep(5)
                d.click(0.99, 0.01)
        except Exception as e:
            print("連線失敗:", e)
            d.app_stop("com.mxdzz.tw.and")
            return True
    if cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "main":
        d.click(0.05, 0.01)
        time.sleep(1)
        d.click(54, 364)
        time.sleep(3)
        while (1):
            img = d.screenshot(format='opencv')[181:260, 60:366]
            result = str(easyocr_reader.readtext(img, detail=0))
            if "咩修敢某" in result:
                print("正確的人")
                if "上" in result:
                    print("pass")
                    d.app_stop("com.mxdzz.tw.and")
                    return False
                else:
                    d.app_stop("com.mxdzz.tw.and")
                    return True


def load_cnn_model(model_path, num_classes=10):
    # 載入模型
    model = cnn_model.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model


def load_oracle_cnn_model(model_path, num_classes=23):
    # 載入模型
    model = miner.simplecnn.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model
from typing import Union, List

def run_adb(cmd: Union[str, List[str]], device_serial: str = None) -> str:
    """
    在终端执行 adb 指令并返回输出。
    cmd 可以是：
      - 一个完整的 shell 命令字符串 (shlex 语法)
      - 已拆好的参数列表 (List[str])
    如果指定了 device_serial，就用 -s 参数锁定设备。
    """
    base = ['adb']
    if device_serial:
        base += ['-s', device_serial]

    # 如果 cmd 是字符串，用 shlex.split 处理引号；若已是列表，直接用
    if isinstance(cmd, str):
        args = shlex.split(cmd)
    else:
        args = cmd

    full_cmd = base + args

    result = subprocess.run(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"ADB Error: {result.stderr.strip()}")
    return result.stdout.strip()


def main(ip, easyocr_reader: easyocr.Reader, Cnn_model, oralce_cnn_model):
    d = u2.connect(ip)
    wake_up_time = time.time()
    # manager = ParkingManager(device=d, reader=easyocr_reader, ip=ip)
    # battle_manager = new_battle.BattleManager(device=d, reader=easyocr_reader)
    # wheel_manager = spin_wheel(device=d)
    # mission_manager = mission(device=d, ip=ip)
    # family_manager = Family_manager(device=d, ip=ip)
    # state_manager = state(device=d)
    # assistant_manager = assistant(d=d)
    manager = ParkingManager(
        device=d, reader=easyocr_reader, ip=ip, cnn_model=Cnn_model)
    battle_manager = new_battle.BattleManager(
        device=d, reader=easyocr_reader, cnn_model=Cnn_model)
    wheel_manager = spin_wheel(device=d, cnn_model=Cnn_model)
    mission_manager = mission(device=d, ip=ip)
    family_manager = Family_manager(device=d, ip=ip, cnn_model=Cnn_model)
    state_manager = state(device=d, cnn_model=Cnn_model)
    assistant_manager = assistant(d=d, cnn_model=Cnn_model)
    planner = MiningPlanner(oralce_cnn_model,
                            device='cuda')
    while (1):
        if 'fc65396d' in ip:
            # 解鎖螢幕
            d.unlock()
            time.sleep(0.3)
            d.swipe(0.5, 0.8, 0.5, 0.2, duration=0.05)
            time.sleep(0.3)

        if 'emulator-5568' in ip:
            for i in range(6):
                if check_on_line(Cnn_model, easyocr_reader):
                    break
                time.sleep(60*5)
        if 'emulator-5566' in ip or 'emulator-5554' in ip:
            time.sleep(60*5)
        if 'emulator-5560' in ip:
            time.sleep(30*1)
        start = time.time()
        img = d.screenshot(format='opencv')
        # 進行ocr
        if state_manager.get_state() == "滑動解除節電模式'":
            unlock(d)
        if check_in_game(d) :
            print("in game")
        else:
            print("not in game")
            if 'fc65396d' in ip:
                output = launch_clone("com.mxdzz.tw.and", 2,device_serial=ip)
                time.sleep(1)
                run_adb(
                'shell wm density 240 && wm size 540x960',
                device_serial=ip
            )
            else:
                d.app_start(package_name="com.mxdzz.tw.and", use_monkey=True)
            time.sleep(20)
            wait_time = time.time()
            while (1):
                if stage_by_str(d, easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "主頁面" or stage_by_str(d, easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "公告" or stage_by_str(d, easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "放置獎勵" or stage_by_str(d, easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "家族" or stage_by_str(d, easyocr_reader.readtext(d.screenshot(format='opencv'), detail=0)) == "離線獎勵":
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
            if not os.path.exists("other_login"):
                os.makedirs("other_login")
            cv2.imwrite(
                "other_login/other_login_{}.jpg".format(time.time()), img)
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
                time.sleep(3)
        img = d.screenshot(format='opencv')
        # if red_envelope.check_red_in_pic(img):
        # red_envelope.open_red_envelope(d)
        current_time = time.localtime()
        if get_stage(d, Cnn_model, easyocr_reader) == "主頁面":
            save_time = farm(d, ip, Cnn_model)
        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面":
            d.click(random.randint(261, 271), 369)  # 點擊寶箱
            time.sleep(1)
            reward(d, easyocr_reader)
            time.sleep(3)
        print("stage:", get_stage(d, Cnn_model, easyocr_reader))
        if stage_by_str(d, result) == "主頁面" or current_time.tm_hour == 23:

            family_manager.go_to_family()

        # stage=get_stage(d,Cnn_model, easyocr_reader)

        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面":
            get_Martial_Soul(d)
            time.sleep(3)

        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面" and ip != "emulator-5568":
            get_skill_and_partner(d)
            time.sleep(3)

        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面" and current_time.tm_hour % 4 == 0:
            assistant_manager.go_to_get_assistant()

        # 結束com.mxdzz.tw.and
        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面":
            last_park_time = return_time(ip, name="park")
            if last_park_time is None:
                last_park_time = 0
            try:
                last_park_time = last_park_time["timestamp"]
            except:
                last_park_time = 0
            status = manager.check_and_park()
            if status:
                time_recording(ip, name="park")

        if current_time.tm_hour % 4 == 0 or current_time.tm_hour == 23:
            stage = get_stage(d, Cnn_model, easyocr_reader)
            if stage == "主頁面":
                d.click(228, 926)
                time.sleep(2)
                battle_manager.execute_all_battles(check=True)

        stage=get_stage(d,Cnn_model, easyocr_reader)
        print("stage:", stage)
        if get_stage(d, Cnn_model, easyocr_reader) == "主頁面" and current_time.tm_hour % 4 == 0:
            oralce(d, easyocr_reader, planner, ip=ip)
            time.sleep(3)
        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面" and  current_time.tm_hour == 20:
            mission_manager.do_allmission()

        stage = get_stage(d, Cnn_model, easyocr_reader)
        if stage == "主頁面" and ip != "emulator-5568":
            wheel_manager.spin()

        d.app_stop("com.mxdzz.tw.and")
        if 'fc65396d' in ip:
            d.screen_off()
        end = time.time()
        last_park_time = return_time(ip, name="park")
        if last_park_time is None:
            last_park_time = 0
        else:
            last_park_time = last_park_time["timestamp"]
        if 'fc65396d' in ip:
            
            run_adb(
                'shell wm density reset && wm size reset',
                device_serial=ip)
        while True:
            time.sleep(10)
            current_time = time.localtime()

            # 當 ip 不是 "emulator-5568" 時：
            #   若整點 (tm_min == 0) 或 23:45 時就跳出迴圈
            # if ip =="emulator-5554":
            #     if current_time.tm_min == 0 or (current_time.tm_hour == 23 and current_time.tm_min == 45):
            #         break

            # 當 ip 是 "emulator-5568" 時：
            #   1. 若整點且小時為 4 的倍數，
            #   2. 或 23:45，
            #   3. 或 小時小於 8 且整點 (tm_min == 0)
            # 則跳出迴圈

            # else:
            if ((current_time.tm_min == 0 and current_time.tm_hour % 2 == 0) or
                (current_time.tm_hour < 9 and current_time.tm_min == 0) or
                (is_expired(last_park_time, expired_time=60 *
                 60 * 3 + 59*60) and current_time.tm_hour > 8)
                and time.time() - wake_up_time > 60*30) or ((current_time.tm_hour == 23 and current_time.tm_min == 0)
                                                            or (current_time.tm_hour == 23 and current_time.tm_min == 45)):
                break
        wake_up_time = time.time()


easyocr_reader = easyocr.Reader(['ch_tra', 'en'])
if __name__ == "__main__":
    # d = u2.connect('emulator-5560')
    #檢測devices

    d_list = get_adb_devices()
    #不要5560
    d_list = [d for d in d_list if (d != 'emulator-5566') ]
    print("devices:", d_list)
    # d_list = ['emulator-5562',  'emulator-5568',  'emulator-5566','emulator-5554','fc65396d']
    # d_list = ['fc65396d']
    # d_list = [ 'emulator-5554']
    Cnn_model = load_cnn_model("cnn_model.pth")
    oralce_cnn_model = load_oracle_cnn_model(
        "./miner/oralce_model.pth", num_classes=23)
    # main("emulator-5554", easyocr_reader,Cnn_model,oralce_cnn_model)
    import threading
    threads = []
    for ip in d_list:
        threads.append(threading.Thread(target=main, args=(
            ip, easyocr_reader, Cnn_model, oralce_cnn_model)))
    for ip in threads:
        ip.start()

        # 開始重構
