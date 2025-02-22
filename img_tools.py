import cv2
import numpy as np
import uiautomator2 as u2
import time
class img_tools:
    '''暫時不可用'''
    def find_and_click(self,img, findImgPath, threshold=0.8, x=0, y=0):
        """尋找圖像並點擊"""
        img = self.device.screenshot(format='opencv')
        findImg = cv2.imread(findImgPath)
        res = cv2.matchTemplate(img, findImg, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        if len(loc[0]) > 0:
            center = [int(loc[1][0] + findImg.shape[1] / 2),
                      int(loc[0][0] + findImg.shape[0] / 2)]
            self.device.click(center[0] + x, center[1] + y)
            return True
        return False