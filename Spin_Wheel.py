from device import device
import mask
import cv2
import numpy as np
import time
import uiautomator2 as u2


class spin_wheel(device):
    def __init__(self, device: u2.Device):
        self.device = device

    def spin(self):
        self.device.click(39, 40)
        time.sleep(1)
        img = self.capture_screenshot()
        img = img[736:771, 61:90]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 紅色
        mask_img = cv2.inRange(hsv, mask.red_mask_lower, mask.red_mask_upper)
        if np.sum(mask_img) > 10000 and np.sum(mask_img) < 20000:
            self.device.click(60, 755)
            time.sleep(1)
            self.device.click(272, 613)
            time.sleep(5)
            for i in range(3):
                self.device.click(272, 718)
                time.sleep(1)
            return True
        return False
