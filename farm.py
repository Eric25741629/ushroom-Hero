import time
import os
import cv2
from tools import find_and_click
from device import device
from cnn_model import predict_image
import random
import numpy as np
import uiautomator2 as u2

import json
def find_and_click(d, findImgPath, threshold=0.8, x=0, y=0):
    img = d.screenshot(format='opencv')
    if not os.path.exists("find_img"):
        os.makedirs("find_img")
    cv2.imwrite("find_img/find_img_{}.jpg".format(time.time()), img)

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
def buy_seed(d):

    d.click(375,915)
    time.sleep(2)
    d.click(160,428)
    time.sleep(0.5)
    for i in range(3):
        d.click(335+random.randint(-5,5),465+random.randint(-5,5))
    time.sleep(0.5)
    d.click(273,558)
    time.sleep(0.5)
    d.click(475,800)
    time.sleep(0.5)
    d.click(384,428)
    time.sleep(0.5)
    for i in range(2):
        d.click(335+random.randint(-5,5),465+random.randint(-5,5))
    time.sleep(0.5)
    d.click(273,558)
    time.sleep(0.5)
    d.click(475,800)
    click_white(d)
    click_white(d)
    time.sleep(3)
def farm_card(d:u2.Device):
    # 當星期 一 三 五 的時候 執行

    if find_and_click(d, r'getting.jpg'):
        time.sleep(7)
    if find_and_click(d, r'get_all.jpg'):
        time.sleep(3)
    d.click(480, 929)
    time.sleep(3)
    # 前往車廠 進行購買
    d.click(470,446)
    time.sleep(3)
    d.click(400,910)
    time.sleep(2)
    start_time = time.time()
    want_to_buy = cv2.imread("farm_card.jpg")
    while (time.time() - start_time < 300):  # 限時 5 分鐘
        img = d.screenshot()
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
            error += 1
            d.swipe(0.5, 0.8, 0.5, 0.6, 1)
            d.click(273, 773)
            break
    d.click(380, 926)
    time.sleep(1)
    d.click(471,915)
    time.sleep(3)
    d.click(210,690)
    #前往農場連續種植30次
    for i in range(5):
        find_and_click(d, r'plants.jpg')
        time.sleep(0.5)
        d.click(383,442)
        time.sleep(0.5)
        find_and_click(d, r'put.jpg')
        time.sleep(4)
        d.click(73,823) #施肥
        time.sleep(0.5)
        d.click(365,440) #確認肥料
        time.sleep(0.5)
        d.click(276,606) #使用
        time.sleep(2)
        d.click(73,823) #getting
        time.sleep(7)
        d.click(73,823) #get all
        time.sleep(2)

def farm(d,ip,Cnn_model):
    d.click(321, 920)
    save_time = 0

    try:
        cnn_s =time.time()
        while(1):
            img = d.screenshot(format='opencv')
            if predict_image(Cnn_model,d.screenshot(format='pillow')) == "homeplace":
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
    cv2.imwrite("homeplace/homeplace_{}.jpg".format(time.time()), d.screenshot(format='opencv'))
    d.click(208, 584)
    time.sleep(5)
    if not os.path.exists("farm"):
        os.makedirs("farm")
    cv2.imwrite("farm/farm{}.jpg".format(time.time()), d.screenshot(format='opencv'))
    img = d.screenshot(format='opencv')[780:864, :]
    start = time.time()
    current_time = time.localtime()
    if current_time.tm_hour ==0:
        buy_seed(d)
    date = time.localtime()
    if date.tm_wday == 1 or date.tm_wday == 3 or date.tm_wday == 6 and date.tm_hour == 2:
        farm_card(d)
    while (time.time()-start < 60):
        if find_and_click(d, r'getting.jpg'):
            time.sleep(7)
        elif find_and_click(d, r'get_all.jpg'):
            time.sleep(3)

        elif  current_time.tm_hour<12 or ip == "emulator-5568" and current_time.tm_hour < 12 :
            if find_and_click(d, r'plants.jpg'):
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
        elif current_time.tm_hour > 12 and ip != "emulator-5568":
            if find_and_click(d, r'plants.jpg'):
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
    try:
        cnn_s =time.time()
        while(1):
            if predict_image(Cnn_model,d.screenshot(format='pillow')) == "main":
                cnn_p = time.time()
                print("save time {}".format(3-(cnn_p-cnn_s)))
                save_time += 3-(cnn_p-cnn_s)
                break
            if time.time()-cnn_s > 60:
                break
    except:
        time.sleep(3)
    return save_time