import os
import sys
import time
import cv2
import random
import logging
import uiautomator2 as u2
import numpy as np

# 添加父目錄到系統路徑，以便導入上層模組
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import img_tools
from tools import click_white
from json_manager import create_time_manager
from utils.screenshot_helpers import save_error_screenshot

logger = logging.getLogger(__name__)
def check_if_partime(d):
    try:
        img = d.screenshot(format='opencv')
        print((np.sum(img[710,211]) -np.sum([57, 65, 196])) )

        if np.sum(img[713,339]) -np.sum([52, 64, 200]) <10  and (abs(np.sum(img[710,211]) -np.sum([57, 65, 196])) <10) :
            print("打工")
            return True
        else:
            print('未打工')
            return False
    except Exception as e:
        print(e)
def buy_seed(d: u2.Device):
    """購買種子"""
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
    for i in range(3):
        d.click(335+random.randint(-5, 5), 465+random.randint(-5, 5))
    time.sleep(0.5)
    d.click(273, 558)
    time.sleep(0.5)
    d.click(475, 800)
    click_white(d)
    click_white(d)
    d.click(379, 917)
    time.sleep(3)

def farm_card(d: u2.Device):
    d.click(479,207) #點擊打工按鈕
    time.sleep(2)
    parttime = check_if_partime(d)
    if parttime:
        d.click(252,707) #點擊取消打工
        time.sleep(random.random()+2)
    d.click(272,868) #點擊關閉
    time.sleep(2)
    if img_tools.find_and_click(d, r'fertilize.jpg'): #施肥
        time.sleep(2)
        d.click(362,430)
        time.sleep(2)
        d.click(270,600)
        time.sleep(3)
    """農場卡片相關操作 - 當星期一、三、五的時候執行"""
    if img_tools.find_and_click(d, r'getting.jpg'):
        time.sleep(7)
    if img_tools.find_and_click(d, r'get_all.jpg'):
        time.sleep(3)
    d.click(480, 929)
    time.sleep(3)
    # 前往車廠 進行購買
    d.click(470, 446)
    time.sleep(3)
    d.click(380, 910)
    time.sleep(2)
    start_time = time.time()
    want_to_buy = cv2.imread("farm_card.jpg")
    err = 0
    while (time.time() - start_time < 300):  # 限時 5 分鐘
        img = d.screenshot(format='opencv')
        res = cv2.matchTemplate(img, want_to_buy, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        h, w = want_to_buy.shape[:-1]
        top_left = max_loc
        if max_val > 0.7 and top_left[1] < 552:
            d.click(
                top_left[0] + w//2, top_left[1] + h//2 + 100)
            time.sleep(2)
            d.click(398,466)
            time.sleep(1.5)
            d.click(277, 551)
            time.sleep(2)
            d.click(514, 16)  # 空白处
            time.sleep(2)
            break
        else:
            d.swipe(0.5, 0.8, 0.5, 0.6, 1)
            d.click(273, 773)
            time.sleep(1)
            err += 1
            if err > 10:
                break
    d.click(380, 926)
    time.sleep(1.5)
    d.click(471, 915)
    time.sleep(3)
    d.click(210, 690)
    time.sleep(3)
    # 前往農場連續種植30次
    current = 0
    start = time.time()
    while (current < 15 and time.time()-start < 600):
        if not img_tools.find_and_click(d, r'plants.jpg'):
            continue
        time.sleep(1.2)
        d.click(357, 442)
        time.sleep(1.2)
        d.click(267, 564)
        time.sleep(6)
        d.click(73+random.randint(-5, 5), 823+random.randint(-5, 5))  # 施肥
        time.sleep(2)
        d.click(365+random.randint(-5, 5), 440+random.randint(-5, 5))  # 確認肥料
        time.sleep(2)
        d.click(276+random.randint(-5, 5), 606+random.randint(-5, 5))  # 使用
        time.sleep(2.2)
        d.click(73, 823)  # getting
        time.sleep(8)
        d.click(73, 823)  # get all
        time.sleep(2.2)
        current += 1
    if parttime:
        d.click(479,207)    #點擊打工按鈕
        time.sleep(1)
        d.click(252,707)  #點擊打工
        time.sleep(random.random())
        d.click(272,868)  #點擊關閉
        time.sleep(1)
def farm(d, ip, Cnn_model):
    """主程式"""
    time_manager = create_time_manager(ip)
    seed_record = time_manager.get_time_record("farm_seed_purchase")
    should_buy_seed = not seed_record or seed_record.get("is_next_day", True)
    
    is_same_day = time_manager.is_same_day("farm_plant_click")
    daily_count = time_manager.get_numeric_value("farm_plant_click", "count", 0) if is_same_day else 0

    if not should_buy_seed and daily_count >= 2:
        return 60

    d.click(321, 920)
    save_time = 0

    try:
        cnn_s = time.time()
        while (1):
            img = d.screenshot(format='opencv')
            if Cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) == "homeplace":
                cnn_p = time.time()
                logger.info("save time {}".format(5-(cnn_p-cnn_s)))
                save_time += 5-(cnn_p-cnn_s)
                break
            if time.time()-cnn_s > 60:
                break
    except:
        time.sleep(5)
    # if not os.path.exists("homeplace"):
    #     os.makedirs("homeplace")
    # cv2.imwrite("homeplace/homeplace_{}.jpg".format(time.time()),
    #             d.screenshot(format='opencv'))
    d.click(208, 584)
    time.sleep(5)
    if not os.path.exists("farm"):
        os.makedirs("farm")
    # cv2.imwrite("farm/farm{}.jpg".format(time.time()),
    #             d.screenshot(format='opencv'))
    img = d.screenshot(format='opencv')[780:864, :]
    start = time.time()
    time_manager = create_time_manager(ip)
    seed_record = time_manager.get_time_record("farm_seed_purchase")
    should_buy_seed = not seed_record or seed_record.get("is_next_day", True)
    if should_buy_seed:
        buy_seed(d)
        time_manager.record_time("farm_seed_purchase")
    # d.click(479,207) #點擊打工按鈕
    # time.sleep(2)
    # parttime = check_if_partime(d)
    current_time = time.localtime()
    # d.click(272,868) #點擊關閉
    # time.sleep(1.2)
    date = time.localtime()
    if ( not time_manager.is_same_week("farm_card_weekly")
    ):
        farm_card(d)
        time_manager.record_time("farm_card_weekly")
    while (time.time()-start < 25):
        if img_tools.find_and_click(d, r'getting.jpg'):
            time.sleep(7)
        elif img_tools.find_and_click(d, r'get_all.jpg'):
            time.sleep(3)
        elif img_tools.find_and_click(d, "new_get.jpg", threshold=0.6, x=10, y=100):
            time.sleep(7)
        if current_time.tm_hour >= 8:
            is_same_day = time_manager.is_same_day("farm_plant_click")
            daily_count = time_manager.get_numeric_value("farm_plant_click", "count", 0) if is_same_day else 0

            if daily_count < 2:
                if img_tools.find_and_click(d, r'plants.jpg'):
                    daily_count += 1
                    time_manager.record_timestamp("farm_plant_click", {"count": daily_count})
                    time.sleep(2)
                    img = d.screenshot(format='opencv')
                    target_sum = sum([173, 112, 68])
                    pixel_sum = sum(int(x) for x in img[437, 231])
                    if (abs(pixel_sum - target_sum) <= 10):
                        d.click(199, 437)
                        time.sleep(2)
                        d.click(126, 588)
                        time.sleep(1)
                        d.click(165, 460)
                        time.sleep(1)
                    if img_tools.find_and_click(d, r'put.jpg'):
                        time.sleep(5)

    # 退出農場 → 家園 → 主頁面，最多重試 60 秒
    # CNN 類別: main / homeplace / 其他(含農場介面)
    # - main      : 成功，跳出
    # - homeplace : 點 (321,920) 家園關閉鈕回主頁
    # - 其他      : 點 (480,929) 農場右下退出鈕到家園
    exit_start = time.time()
    last_state = "__init__"
    attempt = 0
    reached_main = False
    while time.time() - exit_start < 60:
        attempt += 1
        try:
            state = Cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow'))
        except Exception as e:
            logger.warning(f"[farm] 退出時 CNN 預測失敗 #{attempt}: {e}")
            state = None

        state_changed = state != last_state
        if state_changed:
            elapsed = time.time() - exit_start
            logger.info(f"[farm] 退出嘗試 #{attempt}, CNN={state}, elapsed={elapsed:.1f}s")
            try:
                save_error_screenshot(d, ip, str(state), f"farm_exit_attempt_{attempt}")
            except Exception as e:
                logger.debug(f"[farm] 截圖保存失敗: {e}")
            last_state = state

        if state == "main":
            elapsed = time.time() - exit_start
            save_time += max(0, 3 - elapsed)
            logger.info(f"[farm] 已回到主頁面，耗時 {elapsed:.1f}s")
            reached_main = True
            break
        elif state == "homeplace":
            d.click(321 + random.randint(-5, 5), 920 + random.randint(-3, 3))
            time.sleep(2)
        else:
            d.click(480 + random.randint(-5, 5), 929 + random.randint(-3, 3))
            time.sleep(2)

    if not reached_main:
        logger.warning(f"[farm] 退出超時 60s，最後 state={last_state}，fallback click_white")
        try:
            save_error_screenshot(d, ip, str(last_state), "farm_exit_timeout")
        except Exception as e:
            logger.debug(f"[farm] 超時截圖保存失敗: {e}")
        for _ in range(3):
            click_white(d)
            time.sleep(1)
    return save_time
if __name__ == "__main__":
    import  new_cnn.cnn_model as cnn_model
    # Cnn_model = cnn_model.load_cnn_model("cnn_model.pth")
    d = u2.connect('emulator-5554')  # 連接到指定設備
    farm_card(d)
    # save_time = farm_manager.farm(d, ip, Cnn_model)
