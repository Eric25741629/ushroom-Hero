# buy_store
import json
import os
import time
import random
import cnn_model
import torch
import uiautomator2 as u2
import easyocr
from tools import android_devices
import img_tools
# 導入新的JSON管理器，保持向後兼容
from json_manager import create_store_manager
def load_cnn_model(model_path, num_classes=10):
    # 載入模型
    model = cnn_model.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model
def record_json(ip, title, content):
    """向後兼容的記錄函數，使用新的JSON管理器"""
    manager = create_store_manager(ip)
    manager.record_purchase(title, content)

def check_json(ip,title):
    """向後兼容的檢查函數，使用新的JSON管理器"""
    manager = create_store_manager(ip)
    return manager.get_purchase_record(title)
import os
import cv2
import numpy as np
import uiautomator2 as u2
import time
import random
from typing import List, Tuple, Dict, Optional

def detect_and_crop_regions(
    device: u2.Device,
    hsv_ranges: Optional[List[Tuple[np.ndarray, np.ndarray]]] = None,
    area_thresh: int = 500,
    kernel_size: int = 4,
    morph_close_iter: int = 3,
    save_dir: Optional[str] = None,
    show_preview: bool = False,
    show_summary_windows: bool = False,
    screenshot_format: str = 'opencv'
) -> Dict[str, object]:
    """
    連線到裝置截圖，使用 HSV 範圍偵測目標區域，做形態學收斂、找輪廓與裁切。

    參數:
        device_addr: uiautomator2 連線位址。
        hsv_ranges: 需偵測的 HSV 範圍清單，每個元素是 (lower, upper) 的 np.array。
                    若為 None，預設使用你的紅色範圍 [[0,150,160] ~ [10,180,185]]。
                    你也可傳入多段範圍（例如紅色兩段：0~10 與 170~180）。
        area_thresh: 最小面積門檻（像素）。
        kernel_size: 形態學閉運算 kernel 的大小（正方形）。
        morph_close_iter: 閉運算迭代次數。
        save_dir: 若提供路徑，會將裁切的區域圖存到此資料夾。
        show_preview: 是否逐一用 cv2.imshow 預覽每個裁切區域（會阻塞直到關閉）。
        show_summary_windows: 是否顯示標註後的主圖與遮罩視窗。
        screenshot_format: d.screenshot 的格式，通常用 'opencv'。

    回傳:
        {
            'annotated': 標註過的 BGR 影像 (np.ndarray),
            'mask': 最終使用的二值遮罩 (np.ndarray),
            'crops': List[np.ndarray]，每個裁切影像 (BGR),
            'boxes': List[Tuple[int,int,int,int]]，對應的 (x, y, w, h),
            'areas': List[float]，對應的輪廓面積
        }
    """
    # 1) 連線 + 截圖
    img = device.screenshot(format=screenshot_format)  # BGR (opencv)

    # 2) 轉 HSV
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # 3) 建立遮罩（支援多段範圍 OR 合併）
    if hsv_ranges is None:
        hsv_ranges = [
            (np.array([0, 150, 160], dtype=np.uint8), np.array([10, 180, 185], dtype=np.uint8))
        ]
        # 如需紅色另一段，可自行加上：
        # hsv_ranges.append((np.array([170, 150, 160], dtype=np.uint8), np.array([180, 180, 185], dtype=np.uint8)))

    mask_total = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in hsv_ranges:
        mask_total = cv2.bitwise_or(mask_total, cv2.inRange(hsv, lower, upper))

    # 4) 形態學閉運算去除雜訊/填洞
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.morphologyEx(mask_total, cv2.MORPH_CLOSE, kernel, iterations=morph_close_iter)

    # 5) 找輪廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 6) 處理每個區域
    annotated = img.copy()
    crops: List[np.ndarray] = []
    boxes: List[Tuple[int, int, int, int]] = []
    areas: List[float] = []

    if save_dir is not None:
        os.makedirs(save_dir, exist_ok=True)

    for i, cnt in enumerate(contours):
        area = float(cv2.contourArea(cnt))
        if area <= area_thresh:
            continue

        x, y, w, h = cv2.boundingRect(cnt)
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)

        crop = img[y-3:y+h+3, x-3:x+w+3]  # 加點邊界避免切到頭尾
        crops.append(crop)
        boxes.append((x, y, w, h))
        areas.append(area)

        # 儲存
        if save_dir is not None:
            filename = os.path.join(save_dir, f"crop_{i}.png")
            # cv2.imwrite(filename, crop)
            print(f"儲存: {filename}, 面積: {area:.1f}")
        else:
            print(f"偵測到區域 #{i}，面積: {area:.1f} (未設定儲存路徑)")

        # 預覽
        if show_preview:
            cv2.imshow(f"crop_{i}", crop)
            # 可視需要取消以下 waitKey(1)；若想逐張檢視可改成 waitKey(0)
            cv2.waitKey(1)

    # 7) 顯示總覽視窗
    if show_summary_windows:
        cv2.imshow("Detected Area", annotated)
        cv2.imshow("Mask", mask)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    else:
        # 若前面 show_preview=True 有開視窗，這裡幫忙關掉
        if show_preview:
            cv2.destroyAllWindows()
    return {
        'annotated': annotated,
        'mask': mask,
        'crops': crops,
        'boxes': boxes,           # (x, y, w, h)
        'areas': areas,
        'top_lefts': [(x, y) for (x, y, w, h) in boxes]  # 每個區域左上角
    }

def encode_image(img):
        """將圖片編碼為 base64"""
        _, buffer = cv2.imencode('.jpg', img)
        img_base64 = base64.b64encode(buffer).decode('utf-8')
        return img_base64
def analyze_skill_via_http(img_roi, ocr_server_url: Optional[str] = None):
        """透過 HTTP 分析技能

        如果 ocr_server_url 未提供，會嘗試從全域 OCR_SERVER_URL 取得（若在 main 中設定）。
        回傳 response.json() 或 404 表示失敗。
        """
        try:
            url = ocr_server_url if ocr_server_url is not None else globals().get('OCR_SERVER_URL')
            if not url:
                raise RuntimeError("OCR server URL 未設定")
            img_base64 = encode_image(img_roi)
            response = requests.post(
                f"{url}/analyze_skill",
                json={'image': img_base64},
                timeout=15
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"HTTP 錯誤: {response.status_code}")
                return 404
        except Exception as e:
            print(f"HTTP 請求失敗: {e}")
            return 404


def gather_detections(device: u2.Device,
                      hsv_ranges: Optional[List[Tuple[np.ndarray, np.ndarray]]] = None,
                      area_thresh: int = 500,
                      filter_y_max: Optional[int] = None,
                      ocr_server_url: Optional[str] = None) -> List[Dict]:
    """
    執行 detect_and_crop_regions、對每個 crop 呼叫 OCR，回傳偵測列表。

    回傳列表元素格式：{
      'text': <第一個 OCR 結果或空字串>,
      'box': (x,y,w,h),
      'center': (cx,cy),
      'raw_ocr': <原始 analyze_skill_via_http 回傳>
    }
    """
    result = detect_and_crop_regions(
        device=device, # 將 device 傳遞下去
        hsv_ranges=hsv_ranges,
        area_thresh=area_thresh,
        show_preview=False,
        show_summary_windows=False,
        screenshot_format='opencv'
    )

    boxes = result.get('boxes', [])
    crops = result.get('crops', [])
    top_lefts = result.get('top_lefts', [])

    # 同步排序 (y, x)
    triples = list(zip(top_lefts, boxes, crops))
    triples.sort(key=lambda t: (t[0][1], t[0][0]))
    if filter_y_max is not None:
        triples = [t for t in triples if t[0][1] <= filter_y_max]

    detections: List[Dict] = []
    for tl, box, crop in triples:
        x, y, w, h = box
        cx = x + w // 2
        cy = y + h // 2
        analysis = analyze_skill_via_http(crop, ocr_server_url=ocr_server_url)
        text = ''
        if isinstance(analysis, dict):
            ocr_res = analysis.get('ocr_results')
            if isinstance(ocr_res, list) and len(ocr_res) > 0:
                first_result = ocr_res[0]
                if isinstance(first_result, dict):
                    # 正確地從字典中提取 'text' 鍵的值
                    text = first_result.get('text', '')
                elif isinstance(first_result, str):
                    # 處理 OCR 直接回傳字串列表的情況
                    text = first_result
            elif isinstance(analysis.get('text'), str):
                 text = analysis.get('text')


        detections.append({'text': text, 'box': box, 'center': (cx, cy), 'raw': analysis})
    return detections


def click_items_in_order(desired_list: List[str],
                         device: u2.Device,
                         hsv_ranges: Optional[List[Tuple[np.ndarray, np.ndarray]]] = None,
                         area_thresh: int = 500,
                         filter_y_max: Optional[int] = 561,
                         ocr_server_url: Optional[str] = None,
                         click_delay: float = 0.6) -> Dict[str, Dict]:
    """
    按照 desired_list 的順序去點擊出現在畫面的項目。

    規則：
      - 先做一次偵測 + OCR，建立現有偵測文字集合。
      - 逐一按照 desired_list 檢查：如果該項目存在，點擊並記錄結果；
        若該項目不存在，但後序的某個項目存在，則自動判定該項目已被購買(跳過)。

    回傳一個字典，鍵為 desired item，值為執行結果字典，例如：
      {'clicked': True/False, 'reason': 'clicked'/'skipped_bought'/'not_found', 'center': (x,y) or None}
    """
    detections = gather_detections(device=device, # 將 device 傳遞下去
                                   hsv_ranges=hsv_ranges,
                                   area_thresh=area_thresh,
                                   filter_y_max=filter_y_max,
                                   ocr_server_url=ocr_server_url)
    print ("偵測結果:", detections)

    # --- 標準化處理 ---
    # 1. 對偵測到的文字進行標準化
    for d in detections:
        d['normalized_text'] = d.get('text', '').strip().lower()

    # 2. 建立標準化文字到原始 detection 的映射
    mapping = {}
    for d in detections:
        if d['normalized_text'] and d['normalized_text'] not in mapping:
            mapping[d['normalized_text']] = d
            
    found_normalized_set = set(mapping.keys())

    # 直接使用傳入的 device
    dconn = device
    results = {}
    # 如果畫面上出現某個後序項目，我們可以推斷之前缺少的前序項目可能已被消耗（已購買）
    # 例如 desired = [A, B]，若 B 在 found_set 且 A 不在 found_set，則預先把 A 標為 consumed
    inferred_consumed = set()
    for idx, item in enumerate(desired_list):
        normalized_item = item.strip().lower()
        if normalized_item in found_normalized_set:
            # 所有在此 idx 之前但不在 found_set 的項目都推定為已被購買
            for j in range(0, idx):
                prev_item = desired_list[j]
                if prev_item.strip().lower() not in found_normalized_set:
                    inferred_consumed.add(prev_item)
    # 預先填入推論結果，讓後面的迴圈可以直接跳過
    for it in inferred_consumed:
        results[it] = {'clicked': False, 'reason': 'inferred_by_later_presence', 'center': None, 'consumed': True}
    n = len(desired_list)
    for idx, item in enumerate(desired_list):
        # 對當前要尋找的 item 也進行標準化
        normalized_item = item.strip().lower()

        # 如果先前已被推論為已消耗，直接跳過
        if results.get(item, {}).get('consumed'):
            continue

        if normalized_item in found_normalized_set:
            det = mapping[normalized_item]
            cx, cy = det['center']
            try:
                dconn.click(int(cx), int(cy)+150)
                time.sleep(click_delay + random.random() )
                for i in range(4):
                    dconn.click(389,458)
                time.sleep(0.5)
                dconn.click(276,555)
                time.sleep(1.5+random.random())
                dconn.click(523,10)
                results[item] = {'clicked': True, 'reason': 'clicked', 'center': (int(cx), int(cy)), 'raw': det['raw'], 'consumed': True}
            except Exception as e:
                results[item] = {'clicked': False, 'reason': f'click_failed: {e}', 'center': (int(cx), int(cy)), 'consumed': False}
        else:
            # 若後面有出現的項目，則視為此項目已被購買或不存在（跳過）
            later_found = any(later.strip().lower() in found_normalized_set for later in desired_list[idx+1:])
            if later_found:
                # 視為已被前序執行/購買，標記為已消耗(so remove from desired)
                results[item] = {'clicked': False, 'reason': 'skipped_bought', 'center': None, 'consumed': True}
            else:
                results[item] = {'clicked': False, 'reason': 'not_found', 'center': None, 'consumed': False}
    return results
# --- 範例用法 ---
import base64
import requests
def buy_all(d):
    if d.adb_device.info.get('serialno') is not None and d.adb_device.info.get('serialno') !='emulator-5558':
        desired = ['小麦', '美味果', '靈花', '爱心果',"絕密打工指南","精華","活力精華","神燈金鑰","鑽石金鑰",'神力水晶']
    else:
        desired = ['小麥', '美味果', '靈花', '愛心果',"絕密打工指南","精華","活力精華",'神力水晶']
    # desired = ["金幣", "神燈","技能抽卡券","同伴抽卡券","時序金鑰","守護金鑰"]
    # 準備累積統計（整體統計在 while 結束後一次列印）
    bought_set = set()
    skipped_set = set()
    not_found_set = set()
    status = {} 
    Error =0

    while Error<5:
        # 傳入已建立的 device 物件 d，確保 detect/gather/click 等流程共用同一個連線
        res = click_items_in_order(
            desired_list=desired,
            device=d,
            ocr_server_url='http://100.64.0.5:5000',
            filter_y_max=561,
            area_thresh=500,
        )
        # 用「後來者覆蓋前者」的原則更新狀態：
        # 若這輪點到了（clicked=True）或判定 consumed=True，就覆蓋掉舊紀錄
        for item, v in res.items():
            prev = status.get(item)
            if (prev is None) or v.get('clicked') or v.get('consumed'):
                status[item] = v

        # 從 desired 中移除已 consumed 的
        desired = [item for item in desired if not res.get(item, {}).get('consumed', False)]
        if not desired:
            break
        d.swipe(250, 675, 250, 250, duration=0.5)
        time.sleep(0.01)
        d.click(270, 444)
        time.sleep(2)
        Error+=1

        # === while 結束後，用「最後狀態」產生總結 ===
    bought_final   = sorted([k for k, v in status.items() if v.get('clicked')])
    skipped_final  = sorted([k for k, v in status.items() if (not v.get('clicked')) and v.get('consumed')])
    not_found_final = sorted([k for k, v in status.items() if v.get('reason') == 'not_found'])
    return len(bought_final)
def buy_store(d,Cnn_model):
    Android_devices = android_devices(d)
    d.click(321, 919) #點擊家園
    while(cnn_model.predict_image(Cnn_model, d.screenshot(format='pillow')) != "homeplace"):
        Android_devices.click_white()
        time.sleep(1)
    d.click(269, 662) #點擊商店
    time.sleep(2)
    img_tools.click_str_by_server(d,'家園管家',y_range=(180,242))
    time.sleep(2)
    if img_tools.check_str_in_region(d,target_str="雇傭", y_range=(311,381),x_range=(319,460)):
        print("購物管家")
        img_tools.click_str_by_server(d,'雇傭',y_range=(300,381),x_range=(319,460),wait_timeout=5)
        img_tools.click_str_by_server(d,'30天',y_range=(423,471),wait_timeout=5)
        img_tools.click_str_by_server(d,'購買',y_range=(535,592),wait_timeout=5)
        time.sleep(2)
    if img_tools.check_str_in_region(d,target_str="雇傭", y_range=(490,541),x_range=(319,460)):
        print("副本管家")
        img_tools.click_str_by_server(d,'雇傭',y_range=(490,541),x_range=(319,460),wait_timeout=5)
        img_tools.click_str_by_server(d,'30天',y_range=(423,471),wait_timeout=5)
        img_tools.click_str_by_server(d,'購買',y_range=(535,592),wait_timeout=5)
        time.sleep(2)
    img_tools.click_str_by_server(d, '購物管家', y_range=(258,296))
    img_tools.click_str_by_server(d, '採購', y_range=(690,740))
    time.sleep(2)
    d.click(502,37)
    img_tools.click_str_by_server(d, '副本管家', y_range=(773,839))
    time.sleep(2)
    img_tools.click_str_by_server(d, '掃蕩', y_range=(690,740))
    time.sleep(2)
    for i in range(6):
        d.click(502,37)
    time.sleep(2)
    d.click(487,923) #點擊返回
    time.sleep(1)
    img_tools.click_str_by_server(d,'家園管家',y_range=(180,242))
    time.sleep(1)
    d.click(321, 919) #點擊家園返回
    time.sleep(3)

if __name__ == "__main__":
    d=u2.connect('7fe98fc6')
    Cnn_model = cnn_model.load_cnn_model("cnn_model.pth")
    easyocr_reader = easyocr.Reader(['ch_tra', 'en'])
    buy_store(d, '7fe98fc6', Cnn_model, easyocr_reader)  # 替換為實際的IP地址
    