import time
from tools import click_white
import numpy as np
import os
import cv2
def click_str(str1: str, d, easyocr_reader):
    if not os.path.exists("oracle"):
        os.mkdir("oracle")

    img = d.screenshot(format='opencv')
    cv2.imwrite("oracle/ocracle{time.time()}", img)
    result = easyocr_reader.readtext(img)
    for i in result:
        if str1 in str(i[1]):
            [x1, x2, x3, x4] = i[0]
            center = [int((x1[0]+x3[0])/2), int((x1[1]+x3[1])/2)]
            d.click(center[0], center[1])
            return True
    return False


# def oralce(d, easyocr_reader):
#     d.click(321, 919)
#     retry = 0
#     while (retry < 5):
#         img = d.screenshot(format='opencv')
#         conditions = [abs(np.sum(img[502, 284]) - np.sum([228, 217, 59])) <= 10, abs(np.sum(img[511, 79]) - np.sum([186, 154, 47])) <= 10, abs(np.sum(img[620, 87]) - np.sum([87, 166, 205])) <= 10, abs(np.sum(img[377, 384]) - np.sum([91, 120, 157])) <=
#                       10, abs(np.sum(img[239, 263]) - np.sum([95, 152, 119])) <= 10, abs(np.sum(img[71, 166]) - np.sum([102, 122, 103])) <= 10, abs(np.sum(img[810, 82]) - np.sum([157, 203, 244])) <= 10, abs(np.sum(img[798, 322]) - np.sum([190, 186, 54])) <= 10]
#         if all(conditions):
#             break
#         retry += 1
#         click_white(d)
#     if retry == 5:
#         return
#     time.sleep(1)
#     d.click(101, 158)
#     time.sleep(3)
#     click_white(d)
#     d.click(500, 174)
#     time.sleep(3)
#     d.click(272, 752)
#     time.sleep(3)
#     click_str("確定", d, easyocr_reader)
#     time.sleep(3)
#     click_white(d)
#     click_white(d)
#     d.click(500, 913)
#     time.sleep(3)
#     d.click(321, 919)
#     time.sleep(3)
