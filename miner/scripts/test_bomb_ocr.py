import cv2
import requests
import base64
import json
import os
import numpy as np

# 優先嘗試本地 localhost，這通常比內網 IP 更可靠
OCR_URLS = ["http://127.0.0.1:5001/ocr", "http://127.0.0.1:5000/ocr", "http://100.64.0.5:5000/ocr"]

def test_bomb_ocr(image_path):
    if not os.path.exists(image_path):
        print(f"找不到圖片: {image_path}")
        return

    img = cv2.imread(image_path)
    # 炸彈座標: [915:945, 370:410]
    roi = img[915:945, 370:410]
    
    # 1. 原始辨識
    print("--- 1. 原始圖片辨識 ---")
    call_ocr(roi, "original")

    # 2. 灰階 + 二值化 (我剛剛加入的邏輯)
    print("\n--- 2. 二值化預處理辨識 ---")
    roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, roi_bin = cv2.threshold(roi_gray, 150, 255, cv2.THRESH_BINARY_INV)
    call_ocr(roi_bin, "thresholded")

def call_ocr(roi_img, label):
    # 儲存圖片供檢查
    filename = f"debug_bomb_{label}.png"
    cv2.imwrite(filename, roi_img)
    
    _, buffer = cv2.imencode('.png', roi_img)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    payload = {"image": img_base64}

    success = False
    for url in OCR_URLS:
        try:
            response = requests.post(url, json=payload, timeout=2)
            print(f"URL [{url}] 回傳:")
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            success = True
            break
        except Exception:
            continue
    
    if not success:
        print("無法連線到任何 OCR 伺服器地址。")

if __name__ == "__main__":
    img_path = "debug_img/MuMu-20260130-201408-661.png"
    test_bomb_ocr(img_path)