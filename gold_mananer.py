# gold_mananer.py
import time
from tools import click_white
import numpy as np
import os
import cv2
import uiautomator2 as u2
from tools import android_devices


class GoldManager(android_devices):

    def __init__(self, devices: u2.Device, reader):
        super().__init__(devices)
        self.devices = devices
        self.reader = reader

    def campare(self):
        img = self.capture_screenshot()
        conditions = [abs(np.sum(img[576, 375]) - np.sum([180, 208, 219])) <= 10, abs(np.sum(img[700, 121]) - np.sum([178, 209, 218]))
                      <= 10, abs(np.sum(img[795, 408]) - np.sum([42, 155, 111])) <= 10, abs(np.sum(img[790, 217]) - np.sum([58, 65, 198])) <= 10]
        return conditions

    def open_golden(self):
        t_time = time.time()
        while time.time() - t_time < 10 * 60:
            if all(self.campare()):
                click_time = time.time()
                time.sleep(1)
                img = self.capture_screenshot()
                result = self.reader.readtext(img[634:744, 291:367], detail=0)
                result = [i.replace("學", "擊").replace("舉", "擊")
                          for i in result]
                print(result)
                if self.needed(result, ['技能暴擊'], ['擊暈']):
                    self.goto_change("技暈")
                elif self.needed(result, ['連擊'], ['回復']):
                    self.goto_change("連回")
                elif self.needed(result, ['擊暈'], ['回復']):
                    self.goto_change("暈回")
                elif self.needed(result, ['反擊'], ['回復']):
                    self.goto_change("反回")
                elif self.needed(result, ['反擊'], ['爆擊']):
                    self.goto_change("反爆")
                elif self.needed(result, ['連擊'], ['爆擊']):
                    self.goto_change("連爆")
                elif self.needed(result, ['連擊'], ['回復']):
                    self.goto_change("連回")
                elif self.needed(result, ['閃避'], ['回復']):
                    self.goto_change("閃回")
                elif self.needed(result, ['閃避'], ['連擊']):
                    self.goto_change("連閃")
                elif self.needed(result, ['技能暴擊'], ['回復']):
                    self.goto_change("技回")

    def goto_change(self, str1):
        self.click_white()
        time.sleep(1)

    # 賣出
    def sell(self):
        print("不是需要的")
        time.sleep(5)
        self.devices.click(227, 798)
        time.sleep(1)
        img = self.capture_screenshot()
        conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                      <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
        if all(conditions):
            self.devices.click(204, 552)
            time.sleep(1)
        #     self.click_str(str1)
