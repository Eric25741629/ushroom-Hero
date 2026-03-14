import time
import uiautomator2 as u2
import os
import cv2
import numpy as np
from typing import Iterable, Tuple
import random
import img_tools
from typing_extensions import Literal
Mode = Literal["collected", "not_collected"]
def sample_rgbs_from_opencv_image(
    img_bgr: np.ndarray,
    points_xy: Iterable[Tuple[int, int]],
    clamp: bool = True
) -> list[Tuple[int, int, int]]:
    """
    img_bgr: OpenCV 圖 (H,W,3) BGR
    points_xy: (x,y) 座標（注意是 x,y）
    回傳: RGB list
    """
    h, w = img_bgr.shape[:2]
    rgbs = []
    for x, y in points_xy:
        if clamp:
            x = max(0, min(w - 1, int(x)))
            y = max(0, min(h - 1, int(y)))
        else:
            x, y = int(x), int(y)
            if not (0 <= x < w and 0 <= y < h):
                raise ValueError(f"point out of bounds: {(x,y)} (w={w}, h={h})")

        b, g, r = img_bgr[y, x]   # OpenCV 用 y,x 索引
        rgbs.append((int(r), int(g), int(b)))
    return rgbs
def find_car(img):
    img1 = img[744:788]
    img = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
    # mask = cv2.inRange(img, (37, 0, 0), (179, 255, 255))
    mask = cv2.inRange(img, (0, 101, 114), (179, 255, 255))
    # 膨脹
    mask = cv2.dilate(mask, None, iterations=2)
    # 侵蝕
    mask = cv2.erode(mask, None, iterations=1)
    # 計算輪廓
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 顯示偵測到的遮罩與原始區域以便除錯（非阻塞）
    # try:
    #     cv2.imshow("find_car_bgr", img1)
    #     cv2.imshow("find_car_mask", mask)
    #     cv2.waitKey(0)
    # except Exception:
    #     # 在某些 headless 或非 GUI 環境下，imshow 會失敗；忽略該錯誤以維持流程
    #     pass
    contours = [cv2.boundingRect(
        contour) for contour in contours if cv2.contourArea(contour) > 1000]
    return contours
def star_score_rgb(rgb: Tuple[int, int, int]) -> float:
    r, g, b = rgb
    return (r + g) / 2.0 - b
def classify_star_rgb(
    rgbs: Iterable[Tuple[int, int, int]],
    threshold: float = 110.0
) -> tuple[Mode, float]:
    rgbs = list(rgbs)
    if not rgbs:
        raise ValueError("rgbs 不能是空的")
    scores = [star_score_rgb(rgb) for rgb in rgbs]
    avg = float(np.mean(scores))
    return ("collected" if avg >= threshold else "not_collected"), avg
def car_name(d):
    img = d.screenshot(format='opencv')[132:165,39:171]
    result = img_tools.analyze_skill_via_http(img)
    if result.get('success') != True:
        print("OCR服務無法連接")
    else:
        # print("OCR結果:")
        for item in result['ocr_results']:
            print(item['text'])
            return item['text']
    return ''
def create_white_mask(img):
    hsv_img = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 定義白色範圍
    lower_blue = np.array([0, 0, 200])
    upper_blue = np.array([180, 30, 255])
    mask = cv2.inRange(hsv_img, lower_blue, upper_blue)
    return mask
def if_already_parked(img, x, y, w, h):

    roi = img[y+720:y + h+700, x+w-20:x + w]
    # cv2.imshow("ROI", roi)
    # cv2.waitKey(0)
    mask = create_white_mask(roi)
    white_area = cv2.countNonZero(mask)

    if white_area >30:
        print("有停車")
        return True
    else:
        print("沒停車")
        return False
def check_if_start(d):
    # === 用你提供的那些座標來取樣 ===
    points = [(57,197), (59,191), (58,195), (60,196), (59,196), (62,194), (57,188), (61,190)]

    img = d.screenshot(format='opencv')
    rgbs = sample_rgbs_from_opencv_image(img, points)
    mode, score = classify_star_rgb(rgbs)
    if mode == "collected":
        return True
    else:
        return False
def park_time(d):
    img = d.screenshot(format='opencv')
    img = img[495:530,:]
    result = img_tools.analyze_skill_via_http(img)
    if result.get('success') != True:
        print("OCR服務無法連接")
    else:
        print("OCR結果:")
        for item in result['ocr_results']:
            print(item['text'])
            #今日累計停車時間246分鐘
            # 取出數字
            if '今日累計停車時間' in item['text'] and '分鐘' in item['text']:
                minutes = 0
                text = item['text']
                minutes = ''.join(filter(str.isdigit, text))
                print("今日累計停車時間:", minutes, "分鐘")
                return int(minutes)
            elif '今日累計停車時間' in item['text'] and '小時' in item['text']:
                text = item['text']
                hours_part = text.split('小時')[0].replace('今日累計停車時間','').strip()
                minutes_part = text.split('小時')[1].replace('分鐘','').strip()
                hours = ''.join(filter(str.isdigit, hours_part))
                minutes = ''.join(filter(str.isdigit, minutes_part))
                # 處理空字串的情況
                hours = hours if hours else '0'
                minutes = minutes if minutes else '0'
                total_minutes = int(hours)*60 + int(minutes)
                print("今日累計停車時間:", total_minutes, "分鐘")
                return total_minutes
    return 0
def park_and_start(d,use_target=False, list_target=[],not_use_target=False,not_list_target=[],stop_car_name='螺旋小飛機'):
    stop = False
    start_time = time.time() # 記錄開始時間
    last_view_state = None # 紀錄上一輪的狀態
    
    while True:
        # 檢查是否超過 2 分鐘 (120 秒)
        if time.time() - start_time > 120:
            print("停車任務已執行超過 2 分鐘，自動結束。")
            break

        img = d.screenshot(format='opencv')
        contours = find_car(img)
        #排序 按照x座標由小到大
        contours = sorted(contours, key=lambda x: x[0])

        if len(contours) == 0:
            print("沒有車輛，等待3秒")
            time.sleep(3)
            continue

        current_round_names = [] # 用來儲存這一輪看到的車子名稱
        did_modify_view = False  # inner loop action(取消/新增) 會改變 UI，設定此旗標以避免做滑動並強制重新掃描
        for (x, y, w, h) in contours:
            print(f"車輛位置: x={x}, y={y}, w={w}, h={h}")
            # cv2.rectangle(img, (x, y+720), (x + w, y + h+744), (0, 255, 0), 2)
            d.click(x + w - 10, y + 720 + (h // 2))
            time.sleep(1.2) # 增加等待時間，確保 UI 更新完成再截圖 OCR
            img = d.screenshot(format='opencv')
            if_collect = check_if_start(d)
            name = car_name(d).strip()
            print('car_name:',name)
            current_round_names.append(name) # 紀錄名稱
            
            # 優先檢查是否為停止車輛，避免被後面的 continue 跳過
            if stop_car_name !='' and (name == stop_car_name or stop_car_name in name):
                print(f"到達停止車輛: {name}，結束程式")
                stop = True
                break

            if if_already_parked(img, x, y, w, h) and if_collect:
                # 取消蒐藏，按下後 UI 可能會重新整理，跳出本次 for-loop 讓外層重新掃描
                print("偵測到已蒐藏且已停車，嘗試取消蒐藏")
                d.click(59, 196)
                time.sleep(0.6)
                did_modify_view = True
                break
            if if_already_parked(img, x, y, w, h):
                continue
            elif if_collect: # 已經蒐藏還沒停車 
                continue

            if not_use_target and name in not_list_target:
                print("在排除目標車輛，跳過")
                continue
            if name not in list_target and use_target:
                print("非目標車輛，跳過")
                continue
            if park_time(d) >= 240:
                pass
            else:
                print("可選為停車車輛 加入蒐藏")
                d.click(62,198)
                time.sleep(0.6)
                if check_if_start(d):
                    print("已收藏")
                else:
                    print("收藏失敗")
                    stop = True
                    break
                # 收藏後 UI 可能會立刻刷新並改變列表順序，
                # 跳出本次 for-loop，讓外層 while 重新截圖並重新掃描可見車輛
                did_modify_view = True
                break
        
        # 檢查這一輪的名稱清單是否與上一輪完全相同
        if last_view_state is not None and current_round_names == last_view_state and len(current_round_names) > 0:
            print(f"偵測到車輛清單與上一輪完全相同: {current_round_names}，判定已到底，結束程式。")
            break
        last_view_state = current_round_names

        if stop:
            break
        # 如果內層迴圈做了會改變畫面的操作（例如取消蒐藏或新增蒐藏），
        # 我們不做滑動，直接重新掃描畫面以反映變化。
        if did_modify_view:
            time.sleep(0.5)
            continue

        d.swipe(414,720,50,720)
        d.click(67,764)
        time.sleep(0.5)
def new_park_way(d,ip):
    img_tools.click_str_by_server(d,'坐騎改裝')
    time.sleep(2)
    if 'emulator-5558' in ip:
        list_target = ['銀時的摩托車','遙遙领先','豐饒龍使','雲飘飘兮','霓蹤獨角獸', '圆圆蛙','青牛哞哞' ,'白虎' ,'極光戰龍' ,'七彩祥雲'  ,'来潜水鸭' ,'堕落摩托' ,'格拉尼' ,'風火輪' ,'蓮花寶座' ]
        park_and_start (d,use_target=True,list_target=list_target,stop_car_name='紫金葫蘆')
    elif 'fc65396d' in ip or '7fe98fc6' in ip:
        not_list_target = ['紫翼','月下彎','鼓得隆冬響']
        park_and_start(d,use_target=False,not_use_target=True,not_list_target=not_list_target,stop_car_name='紫金葫蘆')
    else:
        park_and_start(d,stop_car_name='夏日初荷')
    time.sleep(1)
    #凌晨12點刷新 重新蒐藏要停的車
    img_tools.click_str_by_server(d,'坐騎改裝',y_range=(0,100),shift_y=885-84)
    return True
if __name__ == "__main__":
    d = u2.connect('emulator-5560')
    new_park_way(d,'emulator-5560')