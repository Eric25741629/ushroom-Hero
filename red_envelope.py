import cv2
import numpy as np
import time
import random
from uiautomator2 import Device
from mask import red_mask_lower, red_mask_upper
from tools import click_white


def open_red_envelope(d: Device):
    d.click(random.randint(50, 100), random.randint(828, 848))
    time.sleep(5)
    d.click(random.randint(36, 79), random.randint(688, 753))
    time.sleep(2)
    for i in range(3):
        d.click(random.randint(80, 135), random.randint(237, 312))
        time.sleep(random.random()*2)
        d.click(random.randint(46, 76), random.randint(783, 818))
        time.sleep(random.random()*2)
    click_white(d)
    d.click(random.randint(50, 100), random.randint(843, 848))
    click_white(d)


def check_red_in_pic(img):
    hsv_image = cv2.cvtColor(img[814:862,0:56], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv_image, red_mask_lower, red_mask_upper)

    if np.sum(mask) > 0:
        print('Found red envelope', np.sum(mask))
        return True
    else:
        return False
