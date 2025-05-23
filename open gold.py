from park import ParkingManager
import time
import numpy as np
import easyocr
import uiautomator2 as u2
import winsound
reader = easyocr.Reader(['en', 'ch_tra'])
device_ips = ['emulator-5554', 'emulator-5562', 'emulator-5560',]
#    'emulator-5568']

# 初始索引
current_index = 0

while True:
    # 取得當前設備的 IP

    current_device_ip = device_ips[current_index]
    current_index = (current_index + 1) % len(device_ips)
    # 連接到當前設備
    try:
        print(f"正在連接到設備: {current_device_ip}")
        d = u2.connect(current_device_ip)
        print(f"成功連接到設備: {current_device_ip}")
    except Exception as e:
        print(f"連接設備 {current_device_ip} 時發生錯誤: {e}")
    parking = ParkingManager(d, reader, current_device_ip)
    click_time = time.time()
    flag = False
    flag1 = False
    t_time = time.time()
    start_time = time.time()
    while (time.time()-t_time < 10):
        img = parking.capture_screenshot()
        conditions = [abs(np.sum(img[576, 375]) - np.sum([180, 208, 219])) <= 10, abs(np.sum(img[700, 121]) - np.sum([178, 209, 218]))
                      <= 10, abs(np.sum(img[795, 408]) - np.sum([42, 155, 111])) <= 10, abs(np.sum(img[790, 217]) - np.sum([58, 65, 198])) <= 10]

        if all(conditions):
            click_time = time.time()
            time.sleep(1)
            img = parking.capture_screenshot()
            result = reader.readtext(img[634:744, 291:367], detail=0)
            result = [i.replace("學", "擊").replace(
                "舉", "擊").replace("擘", "擊暈") for i in result]
            print(result)
            if ('技能暴擊' in result and '反擊' in result) or ('技能暴擊' in result and '連擊' in result) or ('技能暴擊' in result and '暴擊' in result) or ('技能暴擊' in result and '閃避' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = parking.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                              <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            # or ('連擊' in result and '回復' in result)
            elif ('連擊' in result and '擊暈' in result) or ('連擊' in result and '反擊' in result) or ('連擊' in result and '回復' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = parking.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                              <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            elif ('擊暈' in result and '閃避' in result) or ('擊暈' in result and '連擊' in result) or ('擊暈' in result and '暴擊' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = parking.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                              <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
            elif ('反擊' in result and '擊暈' in result) or ('反擊' in result and '連擊' in result) or ('反擊' in result and '閃避' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = parking.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                              <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            elif ('暴擊' in result and '回復' in result) or ('暴擊' in result and '擊暈' in result) or ('暴擊' in result and '回復' in result):
                print("不是需要的")
                time.sleep(5)
                d.click(227, 798)
                time.sleep(1)
                img = parking.capture_screenshot()
                conditions = [abs(np.sum(img[554, 221]) - np.sum([58, 65, 198]))
                              <= 10, abs(np.sum(img[550, 424]) - np.sum([42, 154, 112])) <= 10]
                if all(conditions):
                    d.click(204, 552)
                    time.sleep(1)
                    continue
            else:
                if flag == False:
                    for _ in range(3):
                        winsound.Beep(1000, 200)
                        time.sleep(0.1)
                    time.sleep(1)
                    for _ in range(3):
                        winsound.Beep(1000, 200)  # 1000 Hz, 200 ms
                        time.sleep(0.1)  # 短暫間隔
                    flag = True
                    start_time = time.time()
        else:
            if not flag1:
                d.click(174, 812)
                flag1 = True
