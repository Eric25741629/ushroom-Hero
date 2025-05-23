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