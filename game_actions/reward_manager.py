import time
import numpy as np
import os
from tools import click_white
from utils.logging_utils import logger

def reward(d, easyocr_reader):
    d.click(162, 725)
    time.sleep(5)
    img = d.screenshot(format='opencv')
    result = easyocr_reader.readtext(img, detail=0)
    if "領取" in result or "放置獎勵" in result:
        logger.info(img[328, 135])
        if abs(np.sum(img[328, 135])-np.sum([206, 237, 247])) > 12:
            if not os.path.exists("reward_get"):
                os.makedirs("reward_get")
            # cv2.imwrite(
            #     "reward_get/reward_get_{}.jpg".format(time.time()), img)
            click_white(d)
            time.sleep(1)
        d.click(330, 725)
        time.sleep(5)
        click_white(d)
        time.sleep(1)
