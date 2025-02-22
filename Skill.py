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
    num_boxes = 0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 100:
            return True
    return False


def get_skill_and_partner(d: u2.Device):
    if check_skill_and_parner(d):
        d.click(500, 900)
        time.sleep(5)
        d.click(150, 547)
        time.sleep(1)
        click_white(d)
        time.sleep(1)
        d.click(72, 830)
        for i in range(5):
            d.click(105, 620)
            time.sleep(3)
            d.click(264, 890)
            time.sleep(3)
        d.click(352, 101)
        time.sleep(1)
        for i in range(5):
            d.click(105, 620)
            time.sleep(3)
            d.click(264, 890)
            time.sleep(3)
        d.click(500, 900)


# check_skill_and_parner()
if __name__ == '__main__':
    d = u2.connect()
    get_skill_and_partner(d)
