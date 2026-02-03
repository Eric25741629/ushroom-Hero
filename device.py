import numpy as np
import uiautomator2 as u2
import time
import subprocess


class device:
    def __init__(self, device: u2.Device):
        self.device = device

    def capture_screenshot(self):
        """取得當前螢幕截圖"""
        max_attempts = 10  # 避免死循环
        attempts = 0
        while attempts < max_attempts:
            img = self.device.screenshot(format='opencv')
            if img is None:
                raise ValueError("Failed to capture screenshot")

            conditions = [
                abs(np.sum(img[234, 189]) - np.sum([179, 91, 70])) < 10,
                abs(np.sum(img[218, 236]) - np.sum([254, 241, 225])) < 10,
                abs(np.sum(img[228, 318]) - np.sum([254, 241, 225])) < 10,
                abs(np.sum(img[236, 363]) - np.sum([179, 91, 70])) < 10,
                abs(np.sum(img[249, 132]) - np.sum([162, 75, 57])) < 10,
                abs(np.sum(img[264, 139]) - np.sum([162, 75, 57])) < 10,
                abs(np.sum(img[329, 154]) - np.sum([194, 219, 227])) < 10,
                abs(np.sum(img[361, 370]) - np.sum([193, 218, 226])) < 10,
                abs(np.sum(img[337, 451]) - np.sum([44, 155, 111])) < 10,
            ]

            if all(conditions):
                self.device.click(509, 56)
                time.sleep(1)
                attempts += 1
                continue
            break

        if attempts == max_attempts:
            raise RuntimeError(
                "Maximum attempts reached without capturing valid screenshot")
        return img
def get_adb_devices():

    """
    Retrieves a list of connected Android devices using ADB.

    Returns:
        A list of device serial numbers (strings).  Returns an empty list
        if ADB is not found or no devices are connected.
    """
    try:
        # Run the adb devices command and capture the output
        result = subprocess.check_output(['adb', 'devices'], universal_newlines=True)

        # Parse the output to extract device serial numbers
        devices = []
        for line in result.splitlines():
            parts = line.split('\t')
            if len(parts) == 2 and parts[1] == 'device':
                devices.append(parts[0])
        return devices
    except FileNotFoundError:
        print("ADB not found. Please ensure ADB is installed and in your system's PATH.")
        return []
    except subprocess.CalledProcessError as e:
        print(f"ADB command failed with error: {e}")
        return []
    
def close_nofication(d):
    try:
        d.open_quick_settings()
        if not d.xpath('//*[@content-desc="方向鎖定"]').info.get("checked"):
            d.xpath('//*[@content-desc="方向鎖定"]').click()   
        if not d.xpath('//*[@content-desc="勿擾模式"]').info.get("checked"):
            d.xpath('//*[@content-desc="勿擾模式"]').click()  
        d.click(0.71, 0.016)
        if d(className="android.widget.FrameLayout", packageName="mrv.masked.com.facebook.orca").exists:
            print("有FB")
            point = d(className="android.widget.FrameLayout", packageName="mrv.masked.com.facebook.orca").info.get("bounds")
            x1,y1,x2,y2 = point.get("left"),point.get("top"),point.get("right"),point.get("bottom")
            print(x1,y1,x2,y2)
            print(point)
            middle_x = (x1 + x2) / 2
            middle_y = (y1 + y2) / 2
            print(middle_x, middle_y)
            d.swipe(middle_x, middle_y,500, 2141,duration=0.2)
    except Exception as e:
        print(f"An error occurred: {e}")  
def open_nofication(d):
    try:
        d.open_quick_settings()
        if not d.xpath('//*[@content-desc="方向鎖定"]').info.get("checked"):
            d.xpath('//*[@content-desc="方向鎖定"]').click()   
        if d.xpath('//*[@content-desc="勿擾模式"]').info.get("checked"):
            d.xpath('//*[@content-desc="勿擾模式"]').click()  
        d.click(0.71, 0.016)
        time.sleep(1)
    except Exception as e:
        print(f"An error occurred: {e}")