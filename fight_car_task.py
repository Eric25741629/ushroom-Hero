import re
import time
import random
import numpy as np
import uiautomator2 as u2
import img_tools
from datetime import datetime, timedelta

# ================= 設定區域 =================
# 伺服器定位參數
SERVER_OFFSET_Y = 177
SERVER_OFFSET_X = 127

# 車位定位參數
SPOT_OFFSET_Y = 177
SPOT_OFFSET_X = 119
BUTTON_X_GLOBAL = 480 
Y_ADJUST = 10 

# 狀態辨識點位 (BGR 格式)
POINTS_STATE1 = [
    (161, 325, (204, 230, 236)),
    (341, 449, (204, 230, 236)),
    (134, 432, (204, 230, 236)),
    (78, 742, (175, 204, 213)),
    (448, 743, (158, 185, 205)),
    (13, 444, (103, 105, 105)),
    (10, 564, (103, 105, 105)),
    (230, 593, (204, 230, 236)),
    (121, 248, (204, 230, 236)),
    (88, 273, (204, 230, 236)), 
    (369, 264, (204, 230, 236)), 
    (274, 252, (204, 230, 236)), 
    (334, 276, (204, 230, 236)), 
    (93, 297, (203, 229, 235)), 
    (93, 324, (204, 230, 236)), 
    (86, 235, (204, 230, 236))
]

POINTS_STATE2 = [
    (112, 387, (173, 203, 214)),
    (128, 449, (174, 204, 215)),
    (90, 648, (106, 118, 122)),
    (97, 734, (91, 105, 111)),
    (22, 730, (48, 48, 48)),
    (20, 586, (55, 55, 55)),
    (27, 396, (57, 57, 57)),
    (393, 200, (100, 116, 122)),
    (524, 748, (46, 46, 46)),
    (402, 770, (90, 106, 112)),
    (516, 895, (8, 13, 16)),
]
# ===========================================

def _score_points(img, points, tol=18):
    coords = np.array([(x, y) for x, y, _ in points], dtype=np.int32)
    target = np.array([c for _, _, c in points], dtype=np.int16)
    xs = coords[:, 0]
    ys = coords[:, 1]
    pix = img[ys, xs].astype(np.int16)
    diff = np.abs(pix - target)
    ok = (diff <= tol).all(axis=1)
    hit_rate = ok.mean()
    mean_err = diff.mean()
    return hit_rate, mean_err

def detect_state(img, points1, points2, tol=18, min_hit=0.7):
    h1, e1 = _score_points(img, points1, tol=tol)
    h2, e2 = _score_points(img, points2, tol=tol)
    if (h1 < min_hit) and (h2 < min_hit):
        return "unknown", {"state1": (h1, e1), "state2": (h2, e2)}
    if h1 != h2:
        return ("state1" if h1 > h2 else "state2"), {"state1": (h1, e1), "state2": (h2, e2)}
    return ("state1" if e1 < e2 else "state2"), {"state1": (h1, e1), "state2": (h2, e2)}

def find_server(device, target_id):
    print(f"正在尋找伺服器: {target_id} ...")
    for _ in range(5):  # 嘗試次數
        img = device.screenshot(format='opencv')[SERVER_OFFSET_Y:542, SERVER_OFFSET_X:190]
        try:
            results = img_tools.analyze_skill_via_http(img)['ocr_results']
        except Exception as e:
            print(f"OCR 請求失敗: {e}")
            time.sleep(1)
            continue
            
        for res in results:
            text = res['text']
            match = re.search(r'([sS]\d+)', text)
            if match:
                skill_id = match.group(1).upper()
                if skill_id == target_id.upper():
                    bbox = res['bbox']
                    rel_center_x = (bbox[0] + bbox[2]) // 2
                    rel_center_y = (bbox[1] + bbox[3]) // 2
                    global_x = rel_center_x + SERVER_OFFSET_X
                    global_y = rel_center_y + SERVER_OFFSET_Y
                    print(f"✅ 找到伺服器 [{skill_id}]，點擊中心: ({global_x+300}, {global_y})")
                    device.click(global_x+300, global_y)
                    return True
        print("未在當前畫面找到伺服器，嘗試滑動...")
        device.swipe_ext("up", scale=0.5)
        time.sleep(1)
    return False

def find_spot(device, target_num):
    print(f"正在搜尋: 跨界車位{target_num} ...")
    
    # 嘗試 2 次 (原始 1 次 + 滑動後 1 次)
    for i in range(2):
        img = device.screenshot(format='opencv')[SPOT_OFFSET_Y:542, SPOT_OFFSET_X:265]
        try:
            results = img_tools.analyze_skill_via_http(img)['ocr_results']
        except Exception as e:
            print(f"OCR 請求失敗: {e}")
            time.sleep(1)
            results = []

        for res in results:
            text = res['text']
            if f"跨界車位{target_num}" in text:
                bbox = res['bbox']
                rel_center_y = (bbox[1] + bbox[3]) // 2
                global_y = rel_center_y + SPOT_OFFSET_Y + Y_ADJUST
                global_x = BUTTON_X_GLOBAL
                print(f"✅ 找到 [跨界車位{target_num}]，點擊位置: ({global_x}, {global_y})")
                return (global_x, global_y)
        
        # 如果是第一次嘗試失敗，執行滑動邏輯
        if i == 0:
            print(f"❌ 第 1 次搜尋未找到 [跨界車位{target_num}]，嘗試滑動...")
            device.swipe_ext("up", scale=0.3)
            time.sleep(0.1)
            device.click(267, 482)
            time.sleep(1.0) # 等待畫面穩定

    print(f"❌ 經過 2 次搜尋仍未找到 [跨界車位{target_num}]")
    return None

def fight_loop(device, target_time):
    # 1. 執行進入搶占畫面的初始點擊
    print("點擊進入搶占畫面 (198, 790)...")
    device.click(198, 790)
    time.sleep(0.5)

    # 2. 等待至目標時間前 3 秒
    start_fight_time = target_time - timedelta(seconds=3)
    print(f"目前已抵達搶占畫面，等待至 {start_fight_time.strftime('%H:%M:%S')} 正式啟動辨識...")
    
    while datetime.now() < start_fight_time:
        remaining = (start_fight_time - datetime.now()).total_seconds()
        if remaining > 5:
            print(f"\r距離正式啟動剩餘: {remaining:.1f}s", end="")
            time.sleep(1)
        else:
            time.sleep(0.01)

    print("\n🔥 時間到！啟動自動搶占狀態辨識...")
    start_time_limit = time.time()
    while (time.time() - start_time_limit < 60): 
        img = device.screenshot(format='opencv')
        state, debug = detect_state(img, POINTS_STATE1, POINTS_STATE2)
        
        if state == "state1":
            device.click(random.randint(244, 336), random.randint(730, 751))
            device.click(random.randint(323, 435), random.randint(544, 566))
            time.sleep(0.01)
        elif state == "state2":
            device.click(random.randint(323, 435), random.randint(544, 566))
            time.sleep(0.01)

def main():
    # ... (前面的解析邏輯保持不變)
    import sys
    if len(sys.argv) < 2:
        user_input = input("請輸入目標 (格式 s1470-7+14:30:00): ")
    else:
        user_input = " ".join(sys.argv[1:])
        
    try:
        # 解析輸入 (支援 s1470-7+14:30:00 或 s1470-7 14:30:00)
        # 允許後面接一個可選的偏移參數，例如 s1470-7+14:30:00 +2.5 或 -1
        match = re.match(r'([sS]\d+)-(\d+)[+\s]+(\d{2}:\d{2}:\d{2})(?:\s+([+-]?\d+(?:\.\d+)?))?', user_input)
        
        if not match:
            print("格式錯誤！請使用 s1470-7+14:30:00 [偏移秒數]")
            return
            
        target_server = match.group(1).upper()
        target_spot = match.group(2)
        target_time_str = match.group(3)
        offset_str = match.group(4)
        
        offset_seconds = float(offset_str) if offset_str else 0.0
        
        now = datetime.now()
        target_time = datetime.strptime(target_time_str, "%H:%M:%S").replace(
            year=now.year, month=now.month, day=now.day
        )
        
        # 應用偏移
        if offset_seconds != 0:
            target_time += timedelta(seconds=offset_seconds)
            print(f"已套用時間偏移: {offset_seconds:+.1f} 秒")
        
        # 如果目標時間已過，視為明天（或者報錯，這裡暫定為當天）

        print(f"目標: {target_server} 車位 {target_spot} 時間 {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 提前抵達時間 (目標前 2 分鐘)
        arrive_early_time = target_time - timedelta(minutes=2)
        
        # 連接設備
        device = u2.connect() 
        print(f"已連接設備: {device.serial}")
        
        # 1. 先定位伺服器與車位
        if not find_server(device, target_server):
            return
            
        spot_coords = find_spot(device, target_spot)
        if not spot_coords:
            return
            
        # 2. 等待「提早出發」的時間
        if datetime.now() < arrive_early_time:
            print(f"等待至 {arrive_early_time.strftime('%H:%M:%S')} 提早進入戰場...")
            while datetime.now() < arrive_early_time:
                time.sleep(1)
        
        # 3. 提早進入車位與搶占畫面
        print("提早抵達戰場：點擊進入車位...")
        device.click(spot_coords[0], spot_coords[1])
        time.sleep(1)
        
        # 4. 進入搶占等待與循環
        fight_loop(device, target_time)
        
        print("程序結束")
        
    except Exception as e:
        print(f"執行出錯: {e}")

if __name__ == "__main__":
    main()
