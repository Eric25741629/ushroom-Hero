from device import device
import uiautomator2 as u2
import numpy as np
import time
import cv2
import mask
# 跟班


class assistant(device):
    def __init__(self, d: device):
        super().__init__(d)

    def go_to_get_assistant(self):
        self.device.click(311, 915)
        time.sleep(3)
        self.device.click(450, 650)
        time.sleep(3)
        self.device.click(284, 860)
        time.sleep(3)
        img = self.capture_screenshot()
        # 轉為hsv
        hsv = cv2.cvtColor(img[782:832, 194:353], cv2.COLOR_BGR2HSV)
        # 使用mask中的green_lower, green_upper
        imgmask = cv2.inRange(hsv, mask.green_mask_lower,
                              mask.green_mask_upper)
        if np.sum(imgmask) > 1000000:
            self.device.click(245, 805)
            time.sleep(3)
            self.device.click(245, 805)
            time.sleep(1)
        self.device.click(399, 348)
        time.sleep(1)
        img = self.capture_screenshot()
        # 轉為hsv
        hsv = cv2.cvtColor(img[782:832, 194:353], cv2.COLOR_BGR2HSV)
        # 使用mask中的green_lower, green_upper
        imgmask = cv2.inRange(hsv, mask.green_mask_lower,
                              mask.green_mask_upper)
        if np.sum(imgmask) > 1000000:
            self.device.click(245, 805)
            time.sleep(3)
            self.device.click(245, 805)
            time.sleep(1)
        self.device.click(497, 48)
        time.sleep(1)
        self.device.click(497, 920)
        time.sleep(3)
        self.device.click(311, 915)
        time.sleep(3)