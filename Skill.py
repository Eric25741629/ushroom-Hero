import cv2
import uiautomator2 as u2
import mask
import time
from tools import click_white

def check_skill_and_parner(d: u2.Device):
    img = d.screenshot(format='opencv')
    hsv = cv2.cvtColor(img[876:910, 461:541], cv2.COLOR_BGR2HSV)
    lower = mask.red_mask_lower
    upper = mask.red_mask_upper
    mask1 = cv2.inRange(hsv, lower, upper)
    # 計算面積
    contours, _ = cv2.findContours(
        mask1, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 100:
            return True
    return False

import os
def get_skill_and_partner(d: u2.Device):
    if check_skill_and_parner(d):
        d.click(500, 900)
        time.sleep(5)
        img = d.screenshot(format='opencv')
        if not os.path.exists("diamond"):
            os.mkdir("diamond")
            #使用時間戳作為文件名 ex:diamond_2025_05_02_02_46_00
        cv2.imwrite(f"diamond/diamond_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        d.click(150, 547)
        time.sleep(1)
        click_white(d)
        time.sleep(1)
        d.click(72, 830)
        img = d.screenshot(format='opencv')
        if not os.path.exists("skill"):
            os.mkdir("skill")
            #使用時間戳作為文件名 ex:diamond_2025_05_02_02_46_00
        cv2.imwrite(f"skill/skill_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        for i in range(5):
            d.click(105, 620)
            time.sleep(1)
            img = d.screenshot(format='opencv')
            cv2.imwrite(f"skill/skill_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
            time.sleep(2)
            d.click(264, 890)
            time.sleep(1)
            img = d.screenshot(format='opencv')
            cv2.imwrite(f"skill/skill_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
            time.sleep(2)
        d.click(352, 101)
        if not os.path.exists("partner"):
            os.mkdir("partner")
            #使用時間戳作為文件名 ex:diamond_2025_05_02_02_46_00
        cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)

        time.sleep(1)
        for i in range(5):
            d.click(105, 620)
            # time.sleep(3)
            time.sleep(1)
            img = d.screenshot(format='opencv')
            cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
            time.sleep(2)
            d.click(264, 890)
            time.sleep(1)
            img = d.screenshot(format='opencv')
            cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
            time.sleep(2)

        d.click(500, 900)
        time.sleep(3)
        # 如果是週末
        if time.localtime().tm_wday == 5 or time.localtime().tm_wday == 6:
            weekend_to_buy(d)

def weekend_to_buy(d: u2.Device):
    # 週末購買
    d.click(500, 900)
    time.sleep(5)
    for i in range(3):
        d.click(105, 760)
        time.sleep(1)
        img = d.screenshot(format='opencv')
        cv2.imwrite(f"skill/skill_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        time.sleep(2)
        d.click(264, 890)
        time.sleep(1)
        img = d.screenshot(format='opencv')
        cv2.imwrite(f"skill/skill_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        time.sleep(2)
    d.click(352, 101)
    if not os.path.exists("partner"):
        os.mkdir("partner")
        #使用時間戳作為文件名 ex:diamond_2025_05_02_02_46_00
    cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)

    time.sleep(1)
    for i in range(3):
        d.click(105, 760)
        # time.sleep(3)
        time.sleep(1)
        img = d.screenshot(format='opencv')
        cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        time.sleep(2)
        d.click(264, 890)
        time.sleep(1)
        img = d.screenshot(format='opencv')
        cv2.imwrite(f"partner/partner_{time.strftime('%Y_%m_%d_%H_%M_%S')}.jpg", img)
        time.sleep(2)




# check_skill_and_parner()
if __name__ == '__main__':
    d = u2.connect()
    get_skill_and_partner(d)
